from __future__ import annotations

import hashlib
import json
from typing import Any

from agents_shipgate.core.domain import Action, Scope, Tool
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.lenses.action_surface import build_action
from agents_shipgate.core.risk_hints import derive_side_effect
from agents_shipgate.core.tool_identity import resolve_tool_selector
from agents_shipgate.schemas.capabilities import (
    CapabilityAuthority,
    CapabilityControls,
    CapabilityEffect,
    CapabilityEvidence,
    CapabilityEvidenceProvenanceKind,
    CapabilityFactV1,
    CapabilityHashes,
    CapabilityIdentity,
    capability_fact_sort_key,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.semantic import ToolSemanticEvidence
from agents_shipgate.schemas.surfaces import ActionFact, ActionSurfaceFacts


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
    semantic_evidence = _semantic_evidence(action)
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
        evidence_hash=_capability_stable_hash(
            {
                "evidence": evidence.model_dump(mode="json"),
                "semantic_assessment": (
                    semantic_evidence.model_dump(mode="json")
                    if semantic_evidence is not None
                    else None
                ),
            }
        ),
    )
    return CapabilityFactV1(
        id=f"cap_{hashes.identity_hash}",
        identity=identity,
        effect=effect,
        authority=authority,
        controls=controls,
        evidence=evidence,
        semantic_assessment=semantic_evidence,
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

    declarations = {}
    for entry in manifest.action_surface.actions:
        match = resolve_tool_selector(tools, entry)
        if match.resolved:
            declarations[match.matches[0].id] = entry
    controls_by_tool_id: dict[str, set[str]] = {}
    for control, entries in (
        ("approval", manifest.policies.require_approval_for_tools),
        ("confirmation", manifest.policies.require_confirmation_for_tools),
        ("idempotency", manifest.policies.require_idempotency_for_tools),
    ):
        for entry in entries:
            match = resolve_tool_selector(tools, entry)
            if match.resolved:
                controls_by_tool_id.setdefault(match.matches[0].id, set()).add(control)
    facts: list[CapabilityFactV1] = []
    for original in tools:
        tool = original.model_copy()
        tool.resolved_controls = sorted(
            set(tool.resolved_controls) | controls_by_tool_id.get(tool.id, set())
        )
        action = build_action(
            manifest,
            agent_id=agent_id,
            tool=tool,
            declaration=declarations.get(tool.id),
        )
        facts.append(
            capability_fact_from_action(
                action,
                tool,
                confirmation_required="confirmation" in tool.resolved_controls,
            )
        )
    _validate_unique_capability_ids(facts)
    return sorted(facts, key=capability_fact_sort_key)


def capability_fact_from_action_fact(action: ActionFact) -> CapabilityFactV1:
    """Build a comparable capability fact from public ``ActionFact`` data."""

    scopes = [Scope.parse(raw) for raw in action.required_scopes]
    identity = CapabilityIdentity(
        agent_id=action.agent_id,
        tool_id=action.tool_id,
        tool_name=action.tool_name,
        provider=action.provider,
        operation=action.operation,
        subject_kind="action",
        resource=_resource_identity(scopes),
        scope=_scope_identity(scopes),
    )
    side_effect = derive_side_effect(
        effect=action.effect,
        risk_tags=action.risk_tags,
        # Only positive idempotency evidence narrows the effect model.
        # Explicit False remains visible on controls.safeguard_idempotency.
        idempotency_known=(
            action.safeguards.idempotency
            if action.safeguards.idempotency is True
            else None
        ),
    )
    effect = CapabilityEffect(
        effect=side_effect.effect,
        externally_visible=side_effect.externally_visible,
        handles_sensitive_data=side_effect.handles_sensitive_data,
        financial=side_effect.financial,
        code_execution=side_effect.code_execution,
        reversibility=side_effect.reversibility,
        idempotency_known=side_effect.idempotency_known,
        high_risk=side_effect.is_high_risk,
    )
    authority = CapabilityAuthority(
        auth_type=(
            action.semantic_assessment.authority.auth_type
            if action.semantic_assessment is not None
            else None
        ),
        credential_mode=(
            action.semantic_assessment.authority.credential_mode
            if action.semantic_assessment is not None
            else None
        ),
        scopes=tuple(sorted(set(action.required_scopes))),
        broad_scopes=tuple(sorted(scope.raw for scope in scopes if scope.is_broad())),
    )
    controls = CapabilityControls(
        approval_required=action.approval_policy.required,
        approval_threshold=action.approval_policy.threshold,
        confirmation_required=False,
        safeguard_idempotency=action.safeguards.idempotency,
        safeguard_audit_log=action.safeguards.audit_log,
        safeguard_rollback=action.safeguards.rollback,
        safeguard_dry_run=action.safeguards.dry_run,
        evidence_owner=action.evidence.owner,
        evidence_runbook=action.evidence.runbook,
        evidence_approval_ticket=action.evidence.approval_ticket,
    )
    evidence = CapabilityEvidence(
        source_type=action.source_type,
        source_id=action.source_id,
        provenance_kind="static_declaration",
        confidence="medium",
    )
    semantic_evidence = action.semantic_assessment
    hashes = CapabilityHashes(
        identity_hash=_capability_stable_hash(identity.model_dump(mode="json")),
        effect_hash=_capability_stable_hash(effect.model_dump(mode="json")),
        authority_hash=_capability_stable_hash(authority.model_dump(mode="json")),
        control_hash=_capability_stable_hash(controls.model_dump(mode="json")),
        schema_hash=action.input_schema_hash,
        risk_hash=_capability_stable_hash({"risk_tags": sorted(set(action.risk_tags))}),
        evidence_hash=_capability_stable_hash(
            {
                "evidence": evidence.model_dump(mode="json"),
                "semantic_assessment": (
                    semantic_evidence.model_dump(mode="json")
                    if semantic_evidence is not None
                    else None
                ),
            }
        ),
    )
    return CapabilityFactV1(
        id=f"cap_{hashes.identity_hash}",
        identity=identity,
        effect=effect,
        authority=authority,
        controls=controls,
        evidence=evidence,
        semantic_assessment=semantic_evidence,
        risk_tags=tuple(sorted(set(action.risk_tags))),
        hashes=hashes,
    )


def capability_facts_from_action_surface_facts(
    facts: ActionSurfaceFacts,
) -> list[CapabilityFactV1]:
    capability_facts = [
        capability_fact_from_action_fact(action) for action in facts.actions
    ]
    _validate_unique_capability_ids(capability_facts)
    return sorted(capability_facts, key=capability_fact_sort_key)


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
    assessment = action.semantic_assessment
    scopes = tuple(
        sorted(
            set(
                assessment.authority.scopes
                if assessment is not None
                else action.scope_strings
            )
        )
    )
    return CapabilityAuthority(
        auth_type=(
            assessment.authority.auth_type
            if assessment is not None
            else tool.auth.type
            if tool
            else None
        ),
        credential_mode=(
            assessment.authority.credential_mode
            if assessment is not None
            else tool.auth.credential_mode
            if tool
            else None
        ),
        source=tool.auth.source if tool else None,
        scopes=scopes,
        broad_scopes=tuple(
            sorted(scope for scope in scopes if Scope.parse(scope).is_broad())
        ),
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


def _semantic_evidence(action: Action) -> ToolSemanticEvidence | None:
    if action.semantic_assessment is None:
        return None
    return ToolSemanticEvidence.model_validate(
        action.semantic_assessment.model_dump(mode="python")
    )


def _provenance_kind(tool: Tool) -> CapabilityEvidenceProvenanceKind:
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
    "capability_fact_from_action_fact",
    "capability_facts_from_action_surface_facts",
    "capability_fact_from_action",
]
