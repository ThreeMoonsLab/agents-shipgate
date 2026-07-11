from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from agents_shipgate.core.domain import (
    LoadedToolSource,
    SemanticClaim,
    SemanticIssue,
    Tool,
    ToolIdentityAssessment,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.manifest import (
    ToolIdentityBindingConfig,
    ToolIdentityConfig,
)

_MCP_LIKE = {
    "mcp",
    "codex_config_mcp",
    "codex_plugin_mcp_inventory",
    "n8n_mcp_client_tool",
}


@dataclass(frozen=True)
class SelectorResolution:
    matches: tuple[Tool, ...]
    kind: str | None = None
    message: str | None = None

    @property
    def resolved(self) -> bool:
        return len(self.matches) == 1 and self.kind is None


@dataclass(frozen=True)
class ToolSelectorIndex:
    """One-pass lookup index for repeated manifest selector resolution."""

    tools: tuple[Tool, ...]
    by_id: dict[str, Tool]
    by_name: dict[str, tuple[Tool, ...]]

    @classmethod
    def build(cls, tools: Sequence[Tool]) -> ToolSelectorIndex:
        by_name: dict[str, list[Tool]] = defaultdict(list)
        for tool in tools:
            by_name[tool.name].append(tool)
        return cls(
            tools=tuple(tools),
            by_id={tool.id: tool for tool in tools},
            by_name={
                name: tuple(sorted(matches, key=lambda tool: tool.id))
                for name, matches in by_name.items()
            },
        )

    def resolve(self, selector: Any) -> SelectorResolution:
        tool_id = _selector_value(selector, "tool_id")
        name = _selector_value(selector, "tool")
        provider = _selector_value(selector, "provider")
        source_type = _selector_value(selector, "source_type")
        source_id = _selector_value(selector, "source_id")

        if tool_id:
            match = self.by_id.get(tool_id)
            candidates: Sequence[Tool] = (match,) if match is not None else ()
        elif name:
            candidates = self.by_name.get(name, ())
        else:
            return SelectorResolution(
                (),
                "unresolved_tool_selector",
                "selector has no tool or tool_id",
            )
        if provider:
            candidates = tuple(tool for tool in candidates if tool.provider == provider)
        if source_type:
            candidates = tuple(
                tool for tool in candidates if tool.source_type == source_type
            )
        if source_id:
            candidates = tuple(tool for tool in candidates if tool.source_id == source_id)

        if len(candidates) == 1:
            return SelectorResolution((candidates[0],))
        rendered = _render_selector(selector)
        if not candidates:
            return SelectorResolution(
                (),
                "unresolved_tool_selector",
                f"Tool selector {rendered} matched no canonical tool",
            )
        return SelectorResolution(
            tuple(candidates),
            "ambiguous_tool_selector",
            f"Tool selector {rendered} matched {len(candidates)} canonical tools",
        )


def build_tool_identity_catalog(
    loaded_sources: list[LoadedToolSource],
    config: ToolIdentityConfig,
) -> tuple[list[Tool], list[str]]:
    """Build canonical tools without ever joining observations by name.

    Every adapter result first becomes a source-scoped observation. Observations
    are combined only when a reviewed ``tool_identity.bindings[]`` entry names
    every member exactly. Invalid bindings apply nowhere and make the affected
    identity non-pass-eligible.
    """

    observations = _observations(loaded_sources)
    by_member: dict[tuple[str, str, str], list[Tool]] = defaultdict(list)
    for tool in observations:
        by_member[(tool.source_type, tool.source_id or "", tool.name)].append(tool)

    selected_by_binding: dict[str, list[Tool]] = {}
    binding_issues: dict[str, list[SemanticIssue]] = defaultdict(list)
    observation_bindings: dict[str, list[str]] = defaultdict(list)
    warnings: list[str] = []

    for binding in config.bindings:
        selected: list[Tool] = []
        invalid_messages: list[str] = []
        for member in binding.members:
            matches = [
                tool
                for tool in observations
                if tool.source_id == member.source_id
                and tool.name == member.tool
                and (member.source_type is None or tool.source_type == member.source_type)
            ]
            if len(matches) != 1:
                invalid_messages.append(
                    f"member source_id={member.source_id!r}, tool={member.tool!r} "
                    f"matched {len(matches)} observations"
                )
                selected.extend(matches)
            else:
                selected.append(matches[0])
        if invalid_messages:
            message = f"Invalid tool binding {binding.id!r}: " + "; ".join(invalid_messages)
            warnings.append(message)
            for tool in selected or observations:
                if tool.observation_id:
                    binding_issues[tool.observation_id].append(
                        _identity_issue("invalid_tool_binding", message, binding.id)
                    )
            continue
        selected_by_binding[binding.id] = selected
        for tool in selected:
            assert tool.observation_id is not None
            observation_bindings[tool.observation_id].append(binding.id)

    for observation_id, binding_ids in observation_bindings.items():
        if len(binding_ids) <= 1:
            continue
        message = (
            f"Tool observation {observation_id} appears in multiple bindings: "
            + ", ".join(sorted(binding_ids))
        )
        warnings.append(message)
        binding_issues[observation_id].append(
            _identity_issue("invalid_tool_binding", message, "tool_identity.bindings")
        )

    consumed: set[str] = set()
    canonical: list[Tool] = []
    bindings_by_id = {binding.id: binding for binding in config.bindings}
    for binding_id in sorted(selected_by_binding):
        members = selected_by_binding[binding_id]
        if any(
            len(observation_bindings[tool.observation_id or ""]) != 1
            for tool in members
        ):
            continue
        binding = bindings_by_id[binding_id]
        primary = _binding_primary(binding, members)
        merged, merge_issues = _merge_bound_observations(primary, members)
        observation_ids = sorted(tool.observation_id or "" for tool in members)
        tool_id = _stable_id(
            "tool_v2",
            {"binding_id": binding.id, "provider": binding.provider},
        )
        issues = [
            *merge_issues,
            *(issue for oid in observation_ids for issue in binding_issues.get(oid, [])),
        ]
        merged.id = tool_id
        merged.provider = binding.provider
        merged.observation_ids = observation_ids
        merged.identity_assessment = _assessment(
            tool_id=tool_id,
            provider=binding.provider,
            status="conflicting" if issues else "declared",
            binding_id=binding.id,
            primary=primary,
            members=members,
            issues=issues,
        )
        canonical.append(merged)
        consumed.update(observation_ids)

    for tool in observations:
        observation_id = tool.observation_id or ""
        if observation_id in consumed:
            continue
        issues = binding_issues.get(observation_id, [])
        provider = tool.provider or tool.source_id or tool.source_type
        tool_id = _stable_id("tool_v2", {"observation_id": observation_id})
        tool.id = tool_id
        tool.provider = provider
        tool.observation_ids = [observation_id]
        tool.identity_assessment = _assessment(
            tool_id=tool_id,
            provider=provider,
            status="conflicting" if issues else "structural",
            binding_id=None,
            primary=tool,
            members=[tool],
            issues=issues,
        )
        canonical.append(tool)

    ids = [tool.id for tool in canonical]
    if len(ids) != len(set(ids)):
        raise RuntimeError("canonical tool identity hash collision")
    return sorted(canonical, key=lambda tool: (tool.id, tool.name)), list(dict.fromkeys(warnings))


def resolve_tool_selector(tools: Sequence[Tool], selector: Any) -> SelectorResolution:
    """Resolve a one-to-one manifest selector with no name fallback."""

    return ToolSelectorIndex.build(tools).resolve(selector)


def resolve_selectors_by_tool_id(
    tools: list[Tool],
    selectors: Iterable[Any],
    *,
    manifest_path: str,
    ambiguous_kind: str = "ambiguous_tool_selector",
    unresolved_kind: str = "unresolved_tool_selector",
    copy_tools: bool = True,
) -> tuple[dict[str, Any], list[Tool]]:
    """Resolve declarations and attach fail-closed issues to candidates."""

    resolved: dict[str, Any] = {}
    mutable = [tool.model_copy() for tool in tools] if copy_tools else tools
    selector_index = ToolSelectorIndex.build(mutable)
    by_id = selector_index.by_id
    for index, selector in enumerate(selectors):
        result = selector_index.resolve(selector)
        if result.resolved:
            tool = result.matches[0]
            if tool.id in resolved:
                _append_identity_issue(
                    by_id[tool.id],
                    _identity_issue(
                        "ambiguous_tool_selector",
                        f"Multiple {manifest_path} entries resolve to {tool.id}",
                        f"{manifest_path}/{index}",
                    ),
                )
                continue
            resolved[tool.id] = selector
            continue
        issue_kind = (
            ambiguous_kind
            if result.kind == "ambiguous_tool_selector"
            else unresolved_kind
        )
        issue = _identity_issue(
            issue_kind,
            result.message or "tool selector did not resolve",
            f"{manifest_path}/{index}",
        )
        # Ambiguous selectors poison every candidate because each could have
        # received the policy.  An unresolved selector applies nowhere; one
        # deterministic catalog-level witness is sufficient to fail closed
        # without multiplying the same configuration gap by tool count.
        targets = list(result.matches) or mutable[:1]
        for target in targets:
            _append_identity_issue(by_id[target.id], issue)
    return resolved, mutable


def _observations(loaded_sources: list[LoadedToolSource]) -> list[Tool]:
    observations: list[Tool] = []
    seen: set[tuple[str, str, str]] = set()
    for loaded in loaded_sources:
        source_id = loaded.source_id.strip()
        if not source_id:
            raise InputParseError("loaded tool source has a blank source_id")
        for original in loaded.tools:
            if original.source_id is not None and original.source_id != source_id:
                raise InputParseError(
                    f"Tool {original.name!r} source_id {original.source_id!r} does not match "
                    f"loaded source {source_id!r}"
                )
            # The extraction graph is no longer consumed after catalog
            # construction.  A shallow copy isolates identity fields while
            # avoiding a second recursive copy of every parameter schema.
            tool = original.model_copy()
            tool.source_id = source_id
            locator = _native_locator(tool)
            key = (tool.source_type, source_id, locator)
            if key in seen:
                raise InputParseError(
                    "Duplicate tool observation identity: "
                    f"source_type={tool.source_type!r}, source_id={source_id!r}, "
                    f"native_locator={locator!r}"
                )
            seen.add(key)
            observation_id = _stable_id(
                "obs_v1",
                {
                    "source_type": tool.source_type,
                    "source_id": source_id,
                    "native_locator": locator,
                },
            )
            tool.native_locator = locator
            tool.observation_id = observation_id
            tool.observation_ids = [observation_id]
            tool.provider = source_id
            observations.append(tool)
    return observations


def _native_locator(tool: Tool) -> str:
    method = tool.annotations.get("httpMethod")
    path = tool.annotations.get("path")
    if method and path:
        return f"{str(method).upper()} {path}"
    if tool.source_type in _MCP_LIKE:
        return tool.name
    if tool.source_ref:
        return f"{tool.source_ref}#{tool.name}"
    if tool.source_location:
        return f"{tool.source_location}#{tool.name}"
    if tool.source_pointer:
        return f"{tool.source_pointer}#{tool.name}"
    return tool.name


def _binding_primary(binding: ToolIdentityBindingConfig, members: list[Tool]) -> Tool:
    matches = [
        tool
        for tool in members
        if tool.source_id == binding.primary.source_id
        and tool.name == binding.primary.tool
        and (
            binding.primary.source_type is None
            or tool.source_type == binding.primary.source_type
        )
    ]
    if len(matches) != 1:
        raise RuntimeError("validated binding primary did not resolve uniquely")
    return matches[0]


def _merge_bound_observations(primary: Tool, members: list[Tool]) -> tuple[Tool, list[SemanticIssue]]:
    merged = primary.model_copy(deep=True)
    issues: list[SemanticIssue] = []
    for member in sorted(members, key=lambda tool: tool.observation_id or ""):
        if member.observation_id == primary.observation_id:
            continue
        if (
            member.input_schema
            and merged.input_schema
            and _schema_signature(member.input_schema) != _schema_signature(merged.input_schema)
        ):
            issues.append(
                _identity_issue(
                    "conflicting_tool_identity",
                    "bound observations expose different input schemas",
                    member.source_pointer or member.source_ref,
                )
            )
        for key, value in member.annotations.items():
            if key in merged.annotations and merged.annotations[key] != value:
                issues.append(
                    _identity_issue(
                        "conflicting_tool_identity",
                        f"bound observations disagree on annotation {key!r}",
                        member.source_pointer or member.source_ref,
                    )
                )
                continue
            merged.annotations[key] = value
        existing_hints = {
            json.dumps(hint.model_dump(mode="json"), sort_keys=True, default=str)
            for hint in merged.risk_hints
        }
        for hint in member.risk_hints:
            key = json.dumps(hint.model_dump(mode="json"), sort_keys=True, default=str)
            if key not in existing_hints:
                merged.risk_hints.append(hint.model_copy(deep=True))
                existing_hints.add(key)
        if merged.auth.type and member.auth.type and merged.auth.type != member.auth.type:
            issues.append(
                _identity_issue(
                    "conflicting_tool_identity",
                    "bound observations disagree on authentication type",
                    member.source_pointer or member.source_ref,
                )
            )
        if merged.auth.mode != "unknown" and member.auth.mode != "unknown" and merged.auth.mode != member.auth.mode:
            issues.append(
                _identity_issue(
                    "conflicting_tool_identity",
                    "bound observations disagree on authority mode",
                    member.source_pointer or member.source_ref,
                )
            )
        merged.auth.scopes = sorted(set(merged.auth.scopes) | set(member.auth.scopes))
        merged.auth.alternatives.extend(
            alternative.model_copy(deep=True) for alternative in member.auth.alternatives
        )
        merged.auth.invalid_annotations.extend(member.auth.invalid_annotations)
    return merged, issues


def _schema_signature(schema: Any) -> Any:
    """Binding identity uses the callable interface, not schema refinements.

    Reviewed inventories commonly add formats, bounds, item schemas, and
    ``additionalProperties: false`` that AST adapters cannot recover. Those
    refinements are compatible. Parameter names, requiredness, and coarse
    scalar/container types must still agree.
    """

    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return {
            "type": "object",
            "required": sorted(str(value) for value in schema.get("required") or []),
            "properties": {
                str(name): _coarse_schema_type(value)
                for name, value in sorted(properties.items())
            },
        }
    return {"type": _coarse_schema_type(schema)}


def _coarse_schema_type(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    schema_type = value.get("type")
    if schema_type in {"integer", "number"}:
        return "number"
    return str(schema_type or "unknown")


def _assessment(
    *,
    tool_id: str,
    provider: str,
    status: str,
    binding_id: str | None,
    primary: Tool,
    members: list[Tool],
    issues: list[SemanticIssue],
) -> ToolIdentityAssessment:
    claims = [
        SemanticClaim(
            dimension="identity",
            value=tool.observation_id or "",
            confidence="high",
            provenance_kind="static_declaration",
            source="tool_identity_binding" if binding_id else "source_observation",
            source_pointer=tool.source_pointer or tool.source_ref,
            evidence={
                "source_type": tool.source_type,
                "source_id": tool.source_id,
                "native_locator": tool.native_locator,
            },
        )
        for tool in sorted(members, key=lambda item: item.observation_id or "")
    ]
    unique_issues = _unique_issues(issues)
    return ToolIdentityAssessment(
        tool_id=tool_id,
        status=cast(Any, status),
        provider=provider,
        binding_id=binding_id,
        primary_observation_id=primary.observation_id or "",
        observation_ids=sorted(tool.observation_id or "" for tool in members),
        claims=claims,
        issues=unique_issues,
        pass_eligible=status in {"declared", "structural"} and not unique_issues,
    )


def _append_identity_issue(tool: Tool, issue: SemanticIssue) -> None:
    assessment = tool.identity_assessment
    if assessment is None:
        return
    issues = _unique_issues([*assessment.issues, issue])
    tool.identity_assessment = assessment.model_copy(
        update={
            "status": "conflicting" if issue.kind != "unresolved_tool_selector" else "partial",
            "issues": issues,
            "pass_eligible": False,
        }
    )


def _identity_issue(kind: str, message: str, pointer: str | None) -> SemanticIssue:
    return SemanticIssue(
        kind=cast(Any, kind),
        dimension="identity",
        message=message,
        source="tool_identity",
        source_pointer=pointer,
    )


def _unique_issues(issues: list[SemanticIssue]) -> list[SemanticIssue]:
    by_key = {
        (issue.kind, issue.message, issue.source, issue.source_pointer): issue
        for issue in issues
    }
    return [by_key[key] for key in sorted(by_key, key=lambda row: tuple(value or "" for value in row))]


def _selector_value(selector: Any, field: str) -> str | None:
    value = getattr(selector, field, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(selector, dict):
        raw = selector.get(field)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _render_selector(selector: Any) -> str:
    values = {
        field: _selector_value(selector, field)
        for field in ("tool", "tool_id", "provider", "source_type", "source_id")
    }
    return json.dumps({key: value for key, value in values.items() if value}, sort_keys=True)


def _stable_id(prefix: str, value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


__all__ = [
    "SelectorResolution",
    "ToolSelectorIndex",
    "build_tool_identity_catalog",
    "resolve_selectors_by_tool_id",
    "resolve_tool_selector",
]
