from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from agents_shipgate.core.domain import Action, Scope, Tool
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.lenses.action_surface import build_action
from agents_shipgate.core.privacy import redact_data, redact_text
from agents_shipgate.core.risk_hints import derive_side_effect
from agents_shipgate.core.tool_identity import ToolSelectorIndex
from agents_shipgate.schemas.capabilities import (
    CapabilityAuthority,
    CapabilityControls,
    CapabilityDefinition,
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
        identity_hash=capability_identity_hash(identity),
        binding_hash=capability_binding_hash(semantic_evidence),
        effect_hash=capability_effect_hash(effect),
        authority_hash=capability_authority_hash(authority),
        control_hash=capability_control_hash(controls),
        schema_hash=_capability_stable_hash(
            {
                "input_schema": action.input_schema,
                "parameters": action.parameters_for_hash,
            }
        ),
        risk_hash=capability_risk_hash(action.risk_tags),
        evidence_hash=capability_evidence_hash(evidence, semantic_evidence),
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
        definition=capability_definition(tool) if tool is not None else None,
    )


def capability_definition(tool: Tool) -> CapabilityDefinition:
    """Digest the definition ``tool`` publishes to the model.

    The description is stripped at both ends, so re-wrapping a docstring or a
    trailing newline in a tools file does not read as a new definition; any
    other edit to its text does. Annotations are the map as read, after the
    MCP reader has removed reserved binding keys.
    """

    return CapabilityDefinition(
        description_hash=_capability_stable_hash((tool.description or "").strip()),
        output_schema_hash=_capability_stable_hash(tool.output_schema),
        annotations_hash=_capability_stable_hash(tool.annotations),
    )


def build_capability_facts(
    manifest: AgentsShipgateManifest,
    *,
    agent_id: str,
    tools: list[Tool],
) -> list[CapabilityFactV1]:
    """Build deterministic internal capability facts for a tool list."""

    selector_index = ToolSelectorIndex.build(tools)
    declarations = {}
    for entry in manifest.action_surface.actions:
        match = selector_index.resolve(entry)
        if match.resolved:
            declarations[match.matches[0].id] = entry
    controls_by_tool_id: dict[str, set[str]] = {}
    for control, entries in (
        ("approval", manifest.policies.require_approval_for_tools),
        ("confirmation", manifest.policies.require_confirmation_for_tools),
        ("idempotency", manifest.policies.require_idempotency_for_tools),
    ):
        for entry in entries:
            match = selector_index.resolve(entry)
            if match.resolved:
                controls_by_tool_id.setdefault(match.matches[0].id, set()).add(control)
    facts: list[CapabilityFactV1] = []
    for original in tools:
        resolved_controls = sorted(
            set(original.resolved_controls)
            | controls_by_tool_id.get(original.id, set())
        )
        tool = (
            original
            if resolved_controls == original.resolved_controls
            else original.model_copy(update={"resolved_controls": resolved_controls})
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
        identity_hash=capability_identity_hash(identity),
        binding_hash=capability_binding_hash(semantic_evidence),
        effect_hash=capability_effect_hash(effect),
        authority_hash=capability_authority_hash(authority),
        control_hash=capability_control_hash(controls),
        schema_hash=action.input_schema_hash,
        risk_hash=capability_risk_hash(action.risk_tags),
        evidence_hash=capability_evidence_hash(evidence, semantic_evidence),
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


# --- The digest recipe ------------------------------------------------------
#
# One function per published digest, shared by both builders above and by
# ``redacted_capability_fact``, so a digest recomputed over redacted text is
# the digest a build over that text would have produced.


def capability_identity_hash(identity: CapabilityIdentity) -> str:
    """The identity digest; a capability's id is ``cap_<identity_hash>``."""

    return _capability_stable_hash(identity.model_dump(mode="json"))


def capability_binding_hash(semantic: ToolSemanticEvidence | None) -> str:
    return _capability_stable_hash(
        semantic.binding.model_dump(mode="json")
        if semantic is not None
        else {"status": "unknown"}
    )


def capability_effect_hash(effect: CapabilityEffect) -> str:
    return _capability_stable_hash(effect.model_dump(mode="json"))


def capability_authority_hash(authority: CapabilityAuthority) -> str:
    return _capability_stable_hash(authority.model_dump(mode="json"))


def capability_control_hash(controls: CapabilityControls) -> str:
    return _capability_stable_hash(controls.model_dump(mode="json"))


def capability_risk_hash(risk_tags: Iterable[str]) -> str:
    return _capability_stable_hash({"risk_tags": sorted(set(risk_tags))})


def capability_evidence_hash(
    evidence: CapabilityEvidence,
    semantic: ToolSemanticEvidence | None,
) -> str:
    return _capability_stable_hash(
        {
            "evidence": evidence.model_dump(mode="json"),
            "semantic_assessment": (
                semantic.model_dump(mode="json") if semantic is not None else None
            ),
        }
    )


# --- Publication (#1079) ----------------------------------------------------

#: Tuple fields that hold a sorted set. Redaction is many-to-one, so after it
#: two members can be equal or out of order; these are re-normalized, which is
#: also what ``CapabilityFactV1``'s semantic projection check expects of
#: ``authority.scopes``.
_SORTED_SET_FIELDS: dict[type[BaseModel], tuple[str, ...]] = {
    CapabilityIdentity: ("resource", "scope"),
    CapabilityAuthority: ("scopes", "broad_scopes"),
}


def redacted_capability_fact(fact: CapabilityFactV1) -> CapabilityFactV1:
    """``fact`` as the capability-lock family publishes it (#1079).

    Every string in the identity, effect, authority, controls, evidence, risk
    tags and semantic assessment goes through :func:`redact_data`, the rule
    set ``report.json`` uses. Each digest whose input redaction changed is
    recomputed over the redacted value by the recipe above, and the id follows
    ``identity_hash``, so every published digest describes the published text:
    a consumer that recomputes ``subject.key`` or a set digest from the payload
    agrees with it. A digest whose input redaction left alone keeps its stored
    value, byte for byte, so a fact with nothing to redact - including one read
    from an older lock whose digests came from an older recipe - is returned
    unchanged. ``schema_hash`` and ``definition`` are digests of text no lock
    publishes (the input schema, description, output schema and annotations),
    so they stay digests of the source, as before.
    """

    identity = _redacted_model(fact.identity)
    effect = _redacted_model(fact.effect)
    authority = _redacted_model(fact.authority)
    controls = _redacted_model(fact.controls)
    evidence = _redacted_model(fact.evidence)
    semantic = (
        _redacted_model(fact.semantic_assessment)
        if fact.semantic_assessment is not None
        else None
    )
    risk_tags = tuple(redact_text(tag) or "" for tag in fact.risk_tags)
    if risk_tags != fact.risk_tags:
        risk_tags = tuple(sorted(set(risk_tags)))
    if (identity, effect, authority, controls, evidence, semantic, risk_tags) == (
        fact.identity,
        fact.effect,
        fact.authority,
        fact.controls,
        fact.evidence,
        fact.semantic_assessment,
        fact.risk_tags,
    ):
        return fact
    updates: dict[str, str] = {}
    if identity != fact.identity:
        updates["identity_hash"] = capability_identity_hash(identity)
    if _binding(semantic) != _binding(fact.semantic_assessment):
        updates["binding_hash"] = capability_binding_hash(semantic)
    if effect != fact.effect:
        updates["effect_hash"] = capability_effect_hash(effect)
    if authority != fact.authority:
        updates["authority_hash"] = capability_authority_hash(authority)
    if controls != fact.controls:
        updates["control_hash"] = capability_control_hash(controls)
    if risk_tags != fact.risk_tags:
        updates["risk_hash"] = capability_risk_hash(risk_tags)
    if evidence != fact.evidence or semantic != fact.semantic_assessment:
        updates["evidence_hash"] = capability_evidence_hash(evidence, semantic)
    hashes = fact.hashes.model_copy(update=updates)
    return CapabilityFactV1(
        id=f"cap_{hashes.identity_hash}" if "identity_hash" in updates else fact.id,
        identity=identity,
        effect=effect,
        authority=authority,
        controls=controls,
        evidence=evidence,
        semantic_assessment=semantic,
        risk_tags=risk_tags,
        hashes=hashes,
        definition=fact.definition,
    )


def redacted_capability_facts(facts: Iterable[CapabilityFactV1]) -> list[CapabilityFactV1]:
    """:func:`redacted_capability_fact` over a set, never merging two facts.

    Redaction is many-to-one: two tools whose names differ only inside a
    credential redact to one identity, and so to one id. A diff keys facts by
    id, so publishing both under it would silently drop one of them from every
    comparison. When redaction makes two ids equal, each fact in that group
    keeps the identity digest and id of its source, the one exception to the
    published-digest rule above: the lock then shows two indistinguishable
    names, but two capabilities, and a change to either is still a row.
    """

    sources = list(facts)
    redacted = [redacted_capability_fact(fact) for fact in sources]
    by_id: dict[str, list[int]] = {}
    for index, fact in enumerate(redacted):
        by_id.setdefault(fact.id, []).append(index)
    for indexes in by_id.values():
        if len(indexes) < 2:
            continue
        for index in indexes:
            source = sources[index]
            fact = redacted[index]
            if fact.id == source.id:
                continue
            redacted[index] = fact.model_copy(
                update={
                    "id": source.id,
                    "hashes": fact.hashes.model_copy(
                        update={"identity_hash": source.hashes.identity_hash}
                    ),
                }
            )
    return sorted(redacted, key=capability_fact_sort_key)


def _redacted_model[M: BaseModel](model: M) -> M:
    dumped = model.model_dump(mode="python")
    redacted = redact_data(dumped)
    if redacted == dumped:
        return model
    for name in _SORTED_SET_FIELDS.get(type(model), ()):
        redacted[name] = tuple(sorted(set(redacted[name])))
    return type(model).model_validate(redacted)


def _binding(semantic: ToolSemanticEvidence | None) -> object:
    return semantic.binding if semantic is not None else None


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
    "capability_authority_hash",
    "capability_binding_hash",
    "capability_control_hash",
    "capability_effect_hash",
    "capability_evidence_hash",
    "capability_fact_from_action_fact",
    "capability_facts_from_action_surface_facts",
    "capability_fact_from_action",
    "capability_identity_hash",
    "capability_risk_hash",
    "redacted_capability_fact",
    "redacted_capability_facts",
]
