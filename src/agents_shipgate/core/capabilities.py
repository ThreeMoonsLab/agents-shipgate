from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.core.domain import Action, Scope, Tool
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.lenses.action_surface import build_action
from agents_shipgate.schemas.capability_change import CapabilitySubjectKind
from agents_shipgate.schemas.common import Confidence, ProvenanceKind
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.surfaces import ActionEffect


class CapabilityIdentity(BaseModel):
    """Stable semantic identity for an agent capability.

    This is an internal, non-wire substrate. Source location, evidence,
    controls, and schema details intentionally live outside identity so
    unrelated file moves or metadata edits do not churn capability ids.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    tool_id: str
    tool_name: str
    provider: str
    operation: str
    subject_kind: CapabilitySubjectKind = "action"
    resource: tuple[str, ...] = Field(default_factory=tuple)
    scope: tuple[str, ...] = Field(default_factory=tuple)


class CapabilityEffect(BaseModel):
    """Normalized side-effect view derived from ``SideEffect``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: ActionEffect
    externally_visible: bool = False
    handles_sensitive_data: bool = False
    financial: bool = False
    code_execution: bool = False
    reversibility: Literal["reversible", "irreversible", "unknown"] = "unknown"
    idempotency_known: bool | None = None
    high_risk: bool = False


class CapabilityAuthority(BaseModel):
    """Authority source and scope facts for a capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_type: str | None = None
    credential_mode: str | None = None
    source: str | None = None
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    broad_scopes: tuple[str, ...] = Field(default_factory=tuple)


class CapabilityControls(BaseModel):
    """Policy and safeguard controls already known to the action surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_required: bool | None = None
    approval_threshold: str | None = None
    confirmation_required: bool = False
    safeguard_idempotency: bool | None = None
    safeguard_audit_log: bool | None = None
    safeguard_rollback: bool | None = None
    safeguard_dry_run: bool | None = None
    evidence_owner: str | None = None
    evidence_runbook: str | None = None
    evidence_approval_ticket: str | None = None


class CapabilityEvidence(BaseModel):
    """Structured source provenance for a capability fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_location: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    provenance_kind: ProvenanceKind = "static_declaration"
    confidence: Confidence = "medium"


class CapabilityHashes(BaseModel):
    """Separate hashes for the future lock/diff substrate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_hash: str
    effect_hash: str
    authority_hash: str
    control_hash: str
    schema_hash: str
    risk_hash: str
    evidence_hash: str


class CapabilityFactV1(BaseModel):
    """Internal durable capability fact.

    Phase 0 deliberately keeps this out of report.json and CLI output. It
    is the substrate future lockfiles, policy matching, and governance
    benchmarks can build on without changing the current release gate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    identity: CapabilityIdentity
    effect: CapabilityEffect
    authority: CapabilityAuthority
    controls: CapabilityControls
    evidence: CapabilityEvidence
    risk_tags: tuple[str, ...] = Field(default_factory=tuple)
    hashes: CapabilityHashes


def capability_fact_from_action(
    action: Action,
    tool: Tool | None,
    *,
    confirmation_required: bool = False,
) -> CapabilityFactV1:
    """Build one internal capability fact from a typed action.

    The public two-argument call shape stays simple for tests and future
    callers; ``build_capability_facts`` supplies manifest-derived
    confirmation policy when it has that context.
    """

    identity = CapabilityIdentity(
        agent_id=action.agent_id,
        tool_id=action.tool_id,
        tool_name=action.tool_name,
        provider=action.provider,
        operation=action.operation,
        subject_kind="action",
        resource=_resource_identity(action.scopes),
        scope=_scope_identity(action.scopes),
    )
    effect = _capability_effect(action)
    authority = _capability_authority(action, tool)
    controls = _capability_controls(
        action,
        confirmation_required=confirmation_required,
    )
    evidence = _capability_evidence(action, tool)
    hashes = CapabilityHashes(
        identity_hash=_capability_stable_hash(identity.model_dump(mode="json")),
        effect_hash=_capability_stable_hash(effect.model_dump(mode="json")),
        authority_hash=_capability_stable_hash(authority.model_dump(mode="json")),
        control_hash=_capability_stable_hash(controls.model_dump(mode="json")),
        schema_hash=_capability_stable_hash(
            {
                "input_schema": action.input_schema,
                "parameters": action.parameters_for_hash,
            }
        ),
        risk_hash=_capability_stable_hash({"risk_tags": sorted(set(action.risk_tags))}),
        evidence_hash=_capability_stable_hash(evidence.model_dump(mode="json")),
    )
    return CapabilityFactV1(
        id=f"cap_{hashes.identity_hash}",
        identity=identity,
        effect=effect,
        authority=authority,
        controls=controls,
        evidence=evidence,
        risk_tags=tuple(sorted(set(action.risk_tags))),
        hashes=hashes,
    )


def build_capability_facts(
    manifest: AgentsShipgateManifest,
    *,
    agent_id: str,
    tools: list[Tool],
) -> list[CapabilityFactV1]:
    """Build deterministic internal capability facts for a tool list."""

    declarations = {entry.tool: entry for entry in manifest.action_surface.actions}
    confirmation_tools = manifest.policies.confirmation_tools()
    facts: list[CapabilityFactV1] = []
    for tool in tools:
        action = build_action(
            manifest,
            agent_id=agent_id,
            tool=tool,
            declaration=declarations.get(tool.name),
        )
        facts.append(
            capability_fact_from_action(
                action,
                tool,
                confirmation_required=tool.name in confirmation_tools,
            )
        )
    _validate_unique_capability_ids(facts)
    return sorted(facts, key=_capability_sort_key)


def _capability_sort_key(
    fact: CapabilityFactV1,
) -> tuple[str, str, str, str, str, str]:
    identity = fact.identity
    return (
        identity.agent_id,
        identity.provider,
        identity.operation,
        identity.tool_name,
        "\n".join(identity.scope),
        fact.id,
    )


def _capability_effect(action: Action) -> CapabilityEffect:
    side_effect = action.side_effect
    return CapabilityEffect(
        effect=side_effect.effect,
        externally_visible=side_effect.externally_visible,
        handles_sensitive_data=side_effect.handles_sensitive_data,
        financial=side_effect.financial,
        code_execution=side_effect.code_execution,
        reversibility=side_effect.reversibility,
        idempotency_known=side_effect.idempotency_known,
        high_risk=side_effect.is_high_risk,
    )


def _capability_authority(action: Action, tool: Tool | None) -> CapabilityAuthority:
    scopes = tuple(sorted(set(action.scope_strings)))
    return CapabilityAuthority(
        auth_type=tool.auth.type if tool else None,
        credential_mode=tool.auth.credential_mode if tool else None,
        source=tool.auth.source if tool else None,
        scopes=scopes,
        broad_scopes=tuple(sorted(scope.raw for scope in action.scopes if scope.is_broad())),
    )


def _capability_controls(
    action: Action,
    *,
    confirmation_required: bool,
) -> CapabilityControls:
    return CapabilityControls(
        approval_required=action.approval_required,
        approval_threshold=action.approval_threshold,
        confirmation_required=confirmation_required,
        safeguard_idempotency=action.safeguard_idempotency,
        safeguard_audit_log=action.safeguard_audit_log,
        safeguard_rollback=action.safeguard_rollback,
        safeguard_dry_run=action.safeguard_dry_run,
        evidence_owner=action.evidence_owner,
        evidence_runbook=action.evidence_runbook,
        evidence_approval_ticket=action.evidence_approval_ticket,
    )


def _validate_unique_capability_ids(facts: list[CapabilityFactV1]) -> None:
    by_id: dict[str, list[str]] = {}
    for fact in facts:
        by_id.setdefault(fact.id, []).append(fact.identity.tool_name)
    duplicates = {
        capability_id: sorted(tool_names)
        for capability_id, tool_names in by_id.items()
        if len(tool_names) > 1
    }
    if not duplicates:
        return
    details = "; ".join(
        f"{capability_id!r} used by {', '.join(tool_names)}"
        for capability_id, tool_names in sorted(duplicates.items())
    )
    raise ConfigError(f"Duplicate capability ids are not allowed: {details}.")


def _capability_evidence(action: Action, tool: Tool | None) -> CapabilityEvidence:
    if tool is None:
        return CapabilityEvidence(
            source_type=action.source_type,
            source_id=action.source_id,
            confidence="medium",
        )
    return CapabilityEvidence(
        source_type=tool.source_type,
        source_id=tool.source_id,
        source_ref=tool.source_ref,
        source_location=tool.source_location,
        source_path=tool.source_path,
        source_start_line=tool.source_start_line,
        source_end_line=tool.source_end_line,
        source_start_column=tool.source_start_column,
        source_pointer=tool.source_pointer,
        provenance_kind=_provenance_kind(tool),
        confidence=tool.extraction_confidence,
    )


def _provenance_kind(tool: Tool) -> ProvenanceKind:
    if tool.source_type in _AST_SOURCE_TYPES:
        return "ast_extraction"
    return "static_declaration"


def _scope_identity(scopes: list[Scope]) -> tuple[str, ...]:
    return tuple(sorted({scope.raw for scope in scopes if scope.raw}))


def _resource_identity(scopes: list[Scope]) -> tuple[str, ...]:
    return tuple(sorted({scope.resource for scope in scopes if scope.resource}))


def _capability_stable_hash(value: Any) -> str:
    canonical = _canonicalize_for_capability_hash(value)
    payload = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _canonicalize_for_capability_hash(value: Any) -> Any:
    """Canonical JSON-like shape for capability hashes.

    Deliberately separate from finding fingerprint canonicalization:
    capability hashes must not inherit the finding-specific exclusion
    list for keys such as ``observed`` or ``source_provenance``.
    """

    if isinstance(value, dict):
        return {
            key: _canonicalize_for_capability_hash(value[key])
            for key in sorted(value)
        }
    if isinstance(value, list):
        items = [_canonicalize_for_capability_hash(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if isinstance(value, tuple | set):
        return _canonicalize_for_capability_hash(list(value))
    return value


_AST_SOURCE_TYPES = frozenset(
    {
        # Python/static-AST extracted tools. Declarative inventories and
        # workflow JSON surfaces (``*_inventory``, n8n, MCP, OpenAPI,
        # OpenAI/Anthropic API artifacts) intentionally stay
        # ``static_declaration``.
        "crewai_class_tool",
        "crewai_function",
        "crewai_prebuilt_tool",
        "google_adk_function",
        "langchain_function",
        "langchain_structured_tool",
        "openai_agents_sdk",
        "sdk_function",
    }
)


__all__ = [
    "CapabilityAuthority",
    "CapabilityControls",
    "CapabilityEffect",
    "CapabilityEvidence",
    "CapabilityFactV1",
    "CapabilityHashes",
    "CapabilityIdentity",
    "build_capability_facts",
    "capability_fact_from_action",
]
