from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from agents_shipgate.core.action_semantics import (
    ACTION_EFFECT_RANK,
    normalize_declared_strings,
)
from agents_shipgate.core.domain import (
    DECLARATION_CLAIM_SOURCES,
    Action,
    Scope,
    Tool,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference, _stable_hash
from agents_shipgate.core.policy_evidence import (
    finding_support,
    policy_evidence_gap,
    predicate_evidence,
)
from agents_shipgate.core.risk_hints import (
    derive_side_effect,
    is_effectively_read_only,
    risk_tags,
)
from agents_shipgate.core.semantic_assessment import (
    acknowledged_effect_claim_ids,
    assess_tool_semantics,
    declaration_covers,
    resolve_action_scopes,
)
from agents_shipgate.core.surface_exclusions import (
    catalog_label_index,
    tool_label,
)
from agents_shipgate.core.tool_identity import (
    ToolSelectorIndex,
    action_identity_aliases,
    configured_tool_source,
)
from agents_shipgate.schemas.common import (
    Severity,
    SourceReference,
    confidence_rank,
)
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    ActionPolicyConfig,
    AgentsShipgateManifest,
    ToolSourceConfig,
)
from agents_shipgate.schemas.report import (
    EvidenceGap,
    Finding,
    FindingSupport,
    PolicyPredicateEvidence,
)
from agents_shipgate.schemas.semantic import ToolSemanticEvidence
from agents_shipgate.schemas.surfaces import (
    ActionApprovalFact,
    ActionEvidenceFact,
    ActionFact,
    ActionSafeguardsFact,
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ActionSurfaceDiffSummary,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
    ToolSurfaceDiffBase,
)

_RISK_TAG_MAP = {
    "read_only": "read_only",
    "write": "writes_data",
    "writes_data": "writes_data",
    "external_write": "external_communication",
    "external_communication": "external_communication",
    "customer_communication": "external_communication",
    "financial_action": "financial_write",
    "financial_write": "financial_write",
    "external_side_effect": "external_communication",
    "destructive": "destructive",
    "infrastructure_change": "production_ops",
    "production_operation": "production_ops",
    "production_ops": "production_ops",
    "sensitive_data_access": "privileged_data",
    "privileged_data_access": "privileged_data",
    "privileged_data": "privileged_data",
    "code_execution": "code_execution",
    "identity_access": "identity_access",
    "network_access": "network_access",
    "filesystem_write": "filesystem_write",
    "customer_data": "customer_data",
    "secret_access": "secret_access",
    "irreversible": "irreversible",
    "unknown_side_effect": "unknown_side_effect",
}
_CRITICAL_RISK_TAGS = {
    "financial_write",
    "destructive",
    "production_ops",
    "identity_access",
    "secret_access",
    "code_execution",
}
_SAFEGUARD_FIELDS = ("idempotency", "audit_log", "rollback", "dry_run")
_MISSING_PATH = object()


def _resolved_declarations_from_tools(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    *,
    selector_index: ToolSelectorIndex | None = None,
) -> dict[str, ActionDeclarationConfig]:
    selector_index = selector_index or ToolSelectorIndex.build(tools)
    resolved: dict[str, ActionDeclarationConfig] = {}
    for declaration in manifest.action_surface.actions:
        match = selector_index.resolve(declaration)
        if match.resolved:
            resolved[match.matches[0].id] = declaration
    return resolved


@dataclass(frozen=True)
class ActionSurfaceDiffReference:
    kind: str
    facts: ActionSurfaceFacts | None
    path: str | None = None
    report_schema_version: str | None = None
    baseline_schema_version: str | None = None
    notes: tuple[str, ...] = ()


def build_action_surface_facts(
    manifest: AgentsShipgateManifest,
    *,
    agent_id: str,
    tools: list[Tool],
    warnings: list[str] | None = None,
) -> ActionSurfaceFacts:
    """Build the typed action surface from ``tools`` + manifest declarations.

    Two distinct operations can derive the same ``action_id`` — most
    commonly two OpenAPI operations whose paths normalize identically
    (e.g. a trailing-slash variant of ``/sessions/{session_id}``), because
    the OpenAPI ``operation`` token is ``METHOD path`` only and drops the
    ``operationId``. A third-party spec must never crash a scan, so when a
    ``warnings`` sink is provided the colliding ids are disambiguated
    fail-soft and one ``source_warning`` is recorded per collision (same
    fail-soft principle as the symlink-loop and MCP-as-tools fixes).

    Callers that cannot tolerate a fail-soft snapshot (the redaction path,
    which has its own public-only ordinal disambiguator) pass no sink and
    keep the legacy hard :class:`ConfigError`.
    """
    declarations = _resolved_declarations_from_tools(manifest, tools)
    actions = [
        _action_from_tool(
            manifest,
            agent_id=agent_id,
            tool=tool,
            declaration=declarations.get(tool.id),
        )
        for tool in sorted(tools, key=lambda item: item.id)
    ]
    if warnings is None:
        _validate_unique_action_ids(actions)
    else:
        # Tools whose action_id carries a manifest-authored component. A
        # collision touching one of these stays a hard ConfigError even on
        # the fail-soft path — only inferred-vs-inferred collisions degrade.
        # `id`, `provider`, and `operation` all override action_id
        # components (see `build_action` / `_provider` / `_operation`), so a
        # declaration setting any of them is an explicit identity the engine
        # must never silently rewrite.
        explicit_tool_names = {
            tool.name
            for tool in tools
            if (entry := declarations.get(tool.id)) is not None
            and (entry.id or entry.provider or entry.operation)
        }
        warnings.extend(
            _disambiguate_duplicate_action_ids(actions, explicit_tool_names=explicit_tool_names)
        )
    return ActionSurfaceFacts(actions=sorted(actions, key=lambda item: item.action_id))


def enrich_action_surface_diff_with_source(
    diff: ActionSurfaceDiff,
    tool_source_index: dict[str, tuple[str | None, int | None]] | None,
) -> ActionSurfaceDiff:
    """Populate ``ActionSurfaceChange.source_path`` /
    ``source_start_line`` on each change row when the tool's
    structured source is known.

    Mutates the diff in place and returns it. ``tool_source_index``
    maps ``tool_name → (source_path, source_start_line)``; callers
    pass the index from the live ``Tool`` list (or from the post-scan
    ``tool_inventory`` rows). Without an index, the diff is returned
    untouched.

    The enrichment lands on structured fields rather than mutating
    ``reason``: ``ActionSurfaceChange.model_dump()`` is serialized
    into policy-finding ``evidence`` payloads in
    ``evaluate_action_surface_policies`` and ``finding_fingerprint``
    hashes ``evidence``. Baking line numbers into ``reason`` would
    therefore churn the finding fingerprint every time a tool moved
    in its source file, breaking baseline matches. Structured
    ``source_path``/``source_start_line`` carry the same reviewer
    information without entering the identity hash (downstream
    renderers read them explicitly).

    For safety, this function is only called on the PUBLIC diff
    (``cli/scan/sanitization.py``); the internal diff stays semantic so policy
    findings can be evaluated against unchanged evidence.
    """
    if not tool_source_index:
        return diff
    for row in (*diff.added, *diff.removed, *diff.modified):
        entry = tool_source_index.get(row.tool_id or "")
        if entry is None:
            entry = tool_source_index.get(row.tool_name or "")
        if entry is None:
            continue
        path, line = entry
        row.source_path = path
        row.source_start_line = line
    return diff


def compute_action_surface_diff(
    current: ActionSurfaceFacts,
    base: ActionSurfaceFacts | None,
    *,
    reference: ActionSurfaceDiffReference | None = None,
    warnings: list[str] | None = None,
) -> ActionSurfaceDiff:
    """Diff two action-surface snapshots.

    With a ``warnings`` sink, duplicate ``action_id`` values on either side
    degrade fail-soft instead of raising :class:`ConfigError`. By the time
    facts reach the diff they are engine-built artifacts, not user config:
    the manifest-authored identity contract was already enforced when each
    side was built (see :func:`build_action_surface_facts`). In practice the
    duplicates come from a ``--diff-from`` report or baseline serialized by
    a pre-collision-fix engine (the block/goose miner shape) — a gate must
    fail-safe with a warning on inputs it inferred itself, not crash the
    scan with a config error pointing at a manifest that is fine. Callers
    without a sink keep the legacy hard :class:`ConfigError`.
    """
    if base is None:
        notes = _action_notes(reference)
        if not notes:
            notes.append("No action-surface comparison source was provided.")
        return ActionSurfaceDiff(
            enabled=False,
            base=_diff_base(reference),
            notes=notes,
        )

    if warnings is None:
        current_by_id = _actions_by_id(current.actions)
        base_by_id = _actions_by_id(base.actions)
    else:
        current_by_id = _actions_by_id_fail_soft(current.actions, side="current", warnings=warnings)
        base_by_id = _actions_by_id_fail_soft(
            base.actions, side="base reference", warnings=warnings
        )
    added = [
        _action_added_change(current_by_id[action_id])
        for action_id in sorted(current_by_id.keys() - base_by_id.keys())
    ]
    removed = [
        _action_removed_change(base_by_id[action_id])
        for action_id in sorted(base_by_id.keys() - current_by_id.keys())
    ]
    modified: list[ActionSurfaceChange] = []
    for action_id in sorted(current_by_id.keys() & base_by_id.keys()):
        modified.extend(_modified_changes(current_by_id[action_id], base_by_id[action_id]))

    notes = ["Action renames are reported as one removed action plus one added action."]
    notes.extend(_action_notes(reference))
    return ActionSurfaceDiff(
        enabled=True,
        base=_diff_base(reference),
        summary=_summary(added, removed, modified),
        added=added,
        removed=removed,
        modified=modified,
        notes=notes,
    )


def _gap_subject(action: ActionFact, labels: Mapping[str, str]) -> str:
    """Name a policy-evidence gap the way every other tool-scoped gap names it.

    ``EvidenceGap.subject`` is a display label; identity travels in
    ``subject_id``, which every caller here also sets. These subjects used to
    render ``name [tool_id]``, putting a 64-hex digest straight into the CLI's
    ``Improve evidence:`` line — nothing a reader can act on, and a second
    spelling of a tool the exclusion ledger already labels ``name [provider]``
    (#403). ``support.search_kb`` reached one gap list under both at once.

    Resolved through the catalog by ``tool_id`` rather than rendered from this
    action's own fields, because ``ActionFact.provider`` is
    ``_normalize_token(provider or source_id or source_type)`` — it collapses
    whitespace and falls back differently from ``catalog_subject``. A source id
    of ``my api`` would otherwise label this gap ``create_refund [my_api]``
    while a catalog-backed gap for the same tool says ``create_refund [my api]``
    (PR #408 review).
    """

    return tool_label(action.tool_id, labels, name=action.tool_name) or action.tool_name


def evaluate_action_surface_policies(
    manifest: AgentsShipgateManifest,
    facts: ActionSurfaceFacts,
    diff: ActionSurfaceDiff,
    *,
    agent_id: str,
    tools: list[Tool] | None = None,
    tool_catalog: list[Tool] | None = None,
    policy_evidence_gaps: list[EvidenceGap] | None = None,
) -> list[Finding]:
    """Evaluate action-surface policies for a current action snapshot.

    ``tools`` is optional for callers that only have serialized action facts.
    When omitted, declaration-downgrade checks that compare declarations with
    inferred tool metadata are skipped; the scan pipeline always passes tools.
    """
    findings: list[Finding] = []
    # Labels come from the catalog, never from an action's own normalized
    # fields. ``tool_catalog`` is the full surface; ``tools`` is the reachable
    # subset every action is built from, which is enough for callers that have
    # only that.
    labels = catalog_label_index(tool_catalog or tools or [])
    by_action = {action.action_id: action for action in facts.actions}
    if manifest.action_surface.require_explicit_actions:
        if tools is not None:
            declared_tools = set(_resolved_declarations_from_tools(manifest, tools))
        else:
            declared_tools = set()
            for declaration in manifest.action_surface.actions:
                candidates = [
                    action
                    for action in facts.actions
                    if action.tool_name == declaration.tool
                    and (not declaration.tool_id or action.tool_id == declaration.tool_id)
                    and (not declaration.provider or action.provider == declaration.provider)
                    and (not declaration.source_type or action.source_type == declaration.source_type)
                    and (not declaration.source_id or action.source_id == declaration.source_id)
                ]
                if len(candidates) == 1:
                    declared_tools.add(candidates[0].tool_id)
        for action in facts.actions:
            if action.tool_id in declared_tools:
                continue
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-UNDECLARED",
                    title=f"{action.tool_name} is missing an action_surface declaration",
                    severity="high",
                    action=action,
                    agent_id=agent_id,
                    evidence={"action_id": action.action_id, "tool_name": action.tool_name},
                    recommendation=(
                        "Add action_surface.actions metadata for this tool or disable "
                        "action_surface.require_explicit_actions."
                    ),
                    blocks_release=True,
                )
            )

    if tools is not None:
        findings.extend(
            _declaration_downgrade_findings(
                manifest,
                facts,
                tools,
                agent_id=agent_id,
            )
        )

    if policy_evidence_gaps is not None:
        for action in facts.actions:
            support = _non_authoritative_effect_escalation_support(action)
            if support is None:
                continue
            policy_evidence_gaps.append(
                policy_evidence_gap(
                    status=support.status,
                    subject=_gap_subject(action, labels),
                    subject_id=action.tool_id,
                    policy_id="builtin-effect-control-applicability",
                    source_ref=action.source_ref,
                    support=support,
                    manifest_path=(
                        f"shipgate.yaml#action_surface.actions[tool={action.tool_name!r}].effect"
                    ),
                )
            )

    findings.extend(
        _builtin_policy_findings(
            manifest,
            facts,
            diff,
            by_action,
            agent_id=agent_id,
            tools=tools,
        )
    )
    for policy in manifest.action_surface.policies:
        for action in facts.actions:
            match_status, support = _assess_action_policy_match(policy, action)
            if match_status == "not_matched":
                continue
            if match_status != "matched" or not support.policy_eligible:
                if policy_evidence_gaps is not None:
                    policy_evidence_gaps.append(
                        policy_evidence_gap(
                            status=match_status,
                            subject=_gap_subject(action, labels),
                            subject_id=action.tool_id,
                            policy_id=policy.id,
                            source_ref=action.source_ref,
                            support=support,
                            manifest_path=f"shipgate.yaml#action_surface.policies/{policy.id}/match",
                        )
                    )
                continue
            missing, observed = _missing_requirements(policy, action)
            if not missing:
                continue
            for missing_item in missing:
                path = missing_item["path"]
                evidence: dict[str, Any] = {
                    "policy_id": policy.id,
                    "action_id": action.action_id,
                    "missing": [missing_item],
                }
                if path in observed:
                    evidence["observed"] = {path: observed[path]}
                findings.append(
                    _finding(
                        check_id="SHIP-ACTION-POLICY-VIOLATION",
                        title=policy.message
                        or (f"Action surface policy {policy.id} failed for {action.tool_name}"),
                        severity=policy.severity,
                        action=action,
                        agent_id=agent_id,
                        evidence=evidence,
                        recommendation=policy.recommendation
                        or (f"Satisfy action surface policy {policy.id} for {action.tool_name}."),
                        blocks_release=policy.block and support.blocking_eligible,
                        support=support,
                    )
                )
    return _dedupe_findings(findings)


def attach_action_surface_finding_summary(
    diff: ActionSurfaceDiff,
    findings: list[Finding],
) -> None:
    diff.summary.blocking_findings = sum(
        1
        for finding in findings
        if finding.blocks_release
        and not finding.suppressed
        and finding.baseline_status != "matched"
    )


def action_reference_from_scan_reference(
    reference: ToolSurfaceDiffReference | None,
) -> ActionSurfaceDiffReference | None:
    if reference is None:
        return None
    facts = getattr(reference, "action_facts", None)
    notes = tuple(getattr(reference, "action_notes", ()) or ())
    return ActionSurfaceDiffReference(
        kind=reference.kind,
        path=reference.path,
        facts=facts,
        report_schema_version=reference.report_schema_version,
        baseline_schema_version=reference.baseline_schema_version,
        notes=notes,
    )


def _tool_source_for(
    manifest: AgentsShipgateManifest,
    tool: Tool,
) -> ToolSourceConfig | None:
    """The configured ``tool_sources`` entry this action came from, if exactly one did.

    The same join the resolver uses, for the same reason: ``tool.source_id`` is
    minted by the adapter and is not a foreign key into ``tool_sources``.
    """

    return configured_tool_source(
        tool, {source.id: source for source in manifest.tool_sources}
    )


def build_action(
    manifest: AgentsShipgateManifest,
    *,
    agent_id: str,
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> Action:
    """Build the typed ``Action`` for a tool + optional declaration.

    Single source of truth for action construction. ``_action_from_tool``
    delegates here, then serializes the typed Action to ``ActionFact``
    via ``action_to_fact``. The two-step shape lets callers that want a
    typed view (future checks, follow-up refactors) bypass the wire
    serialization entirely.

    The output is deterministic: same inputs always produce the same
    Action, including ``risk_tags`` and ``scopes`` ordering.
    """
    provider = _provider(tool, declaration)
    operation = _operation(tool, declaration)
    action_id = declaration.id if declaration and declaration.id else _canonical_action_id(
        agent_id=agent_id,
        tool_id=tool.id,
        provider=provider,
        operation=operation,
    )
    source = _tool_source_for(manifest, tool)
    # Live scans resolve semantics exactly once after extraction and manifest
    # enrichment. Direct unit callers may still provide an unattached Tool,
    # so retain a compatibility fallback without creating a second live path.
    semantic_assessment = tool.semantic_assessment or assess_tool_semantics(
        tool,
        declaration,
        tool_source=source,
    )
    inferred_tags = _normalized_risk_tags(tool)
    declared_tags = (
        _normalize_risk_tag_values(declaration.risk_tags) if declaration is not None else []
    )
    risk_tag_values = sorted(set(inferred_tags) | set(declared_tags))
    # From the resolver itself, not a second derivation in its precedence,
    # because the capability standard binds this list to the semantic
    # authority's (#410 increment 3) — including the no-reviewed-record case,
    # where a bare ``scopes:`` list used to reach only this side.
    scope_strings = resolve_action_scopes(tool, declaration, source)
    effect = semantic_assessment.conservative_effect
    semantic_effects = {
        claim.value
        for claim in semantic_assessment.effect.claims
        if claim.policy_eligible and claim.value in ACTION_EFFECT_RANK
    }
    risk_tag_values = sorted(
        set(risk_tag_values)
        | {_risk_tag_for_effect(effect)}
        | {_risk_tag_for_effect(value) for value in semantic_effects}
    )
    approval = _approval_fact(manifest, tool, declaration)
    safeguards = _safeguards_fact(manifest, tool, declaration)
    evidence = _evidence_fact(tool, declaration)
    input_fields = sorted({parameter.name for parameter in tool.parameters})
    required_input_fields = sorted(
        {parameter.name for parameter in tool.parameters if parameter.required}
    )
    # Route through the shared ``derive_side_effect`` helper so the
    # tool-context path (``tool_side_effect(tool)``) and this manifest-
    # context path cannot drift on structural-field derivation. The
    # helper also feeds ``effect`` into every structural field, so a
    # manifest declaring ``effect: financial_write`` with no matching
    # ``risk_tags`` still yields ``financial=True``,
    # ``externally_visible=True``, and ``is_high_risk=True``.
    side_effect = derive_side_effect(
        effect=effect,
        risk_tags=risk_tag_values,
        idempotency_known=(safeguards.idempotency if safeguards.idempotency else None),
    )
    return Action(
        action_id=action_id,
        agent_id=agent_id,
        tool_id=tool.id,
        tool_name=tool.name,
        provider=provider,
        source_type=tool.source_type,
        source_id=tool.source_id,
        source_ref=tool.source_ref,
        source_location=tool.source_location,
        source_path=tool.source_path,
        source_start_line=tool.source_start_line,
        source_end_line=tool.source_end_line,
        source_start_column=tool.source_start_column,
        source_pointer=tool.source_pointer,
        operation=operation,
        side_effect=side_effect,
        risk_tags=risk_tag_values,
        scopes=[Scope.parse(raw) for raw in scope_strings],
        approval_required=approval.required,
        approval_threshold=approval.threshold,
        safeguard_idempotency=safeguards.idempotency,
        safeguard_audit_log=safeguards.audit_log,
        safeguard_rollback=safeguards.rollback,
        safeguard_dry_run=safeguards.dry_run,
        evidence_owner=evidence.owner,
        evidence_runbook=evidence.runbook,
        evidence_approval_ticket=evidence.approval_ticket,
        input_fields=input_fields,
        required_input_fields=required_input_fields,
        input_schema=tool.input_schema,
        parameters_for_hash=[parameter.model_dump(mode="json") for parameter in tool.parameters],
        semantic_assessment=semantic_assessment,
    )


def public_action_schema_hash(
    input_fields: list[str],
    required_input_fields: list[str],
) -> str:
    """Canonical hash of an action's input schema for PUBLIC facts.

    Derived only from the public, serialized schema fields
    (``input_fields`` / ``required_input_fields``) so the value is
    reproducible from a round-tripped ``ActionFact``. The base diff
    reference carries no raw ``input_schema`` / ``parameters`` (they are
    not fields on ``ActionFact``), so any formula reading those cannot be
    reproduced base-side — which previously left the fresh head fact and
    the round-tripped base fact hashing different inputs and flipping
    ``schema_hash`` for every capability on an identical tree. Both
    ``action_to_fact`` and ``_refresh_public_action_hashes`` route through
    here so the head and base sides can never drift onto two formulas.
    """
    return _stable_hash(
        {
            "input_fields": input_fields,
            "required_input_fields": required_input_fields,
        }
    )


def action_to_fact(action: Action) -> ActionFact:
    """Serialize a typed ``Action`` to its wire-shape ``ActionFact``.

    Hashes are computed here (not on ``Action``) because:

    - The ``ActionFact`` shape is the canonical hash input — any future
      typed-Action enrichment must not alter what gets hashed.
    - Hashes are a serialization concern, not a domain concern.

    ``schema_hash`` is derived from the public, serializable schema fields
    via ``public_action_schema_hash`` (not the raw ``input_schema`` /
    ``parameters``, which are absent from the round-tripped base fact) so
    a fresh head fact and a round-tripped base fact hash the same inputs.
    """
    approval = ActionApprovalFact(
        required=action.approval_required,
        threshold=action.approval_threshold,
    )
    safeguards = ActionSafeguardsFact(
        idempotency=action.safeguard_idempotency,
        audit_log=action.safeguard_audit_log,
        rollback=action.safeguard_rollback,
        dry_run=action.safeguard_dry_run,
    )
    evidence = ActionEvidenceFact(
        owner=action.evidence_owner,
        runbook=action.evidence_runbook,
        approval_ticket=action.evidence_approval_ticket,
    )
    schema_hash = public_action_schema_hash(action.input_fields, action.required_input_fields)
    policy_hash = _stable_hash(
        {
            "approval": approval.model_dump(mode="json"),
            "safeguards": safeguards.model_dump(mode="json"),
            "evidence": evidence.model_dump(mode="json"),
        }
    )
    risk_hash = _stable_hash(
        {
            "effect": action.effect,
            "risk_tags": action.risk_tags,
            "required_scopes": action.scope_strings,
            "semantic_assessment": (
                action.semantic_assessment.model_dump(mode="json")
                if action.semantic_assessment is not None
                else None
            ),
        }
    )
    semantic_evidence = (
        ToolSemanticEvidence.model_validate(action.semantic_assessment.model_dump(mode="python"))
        if action.semantic_assessment is not None
        else None
    )
    return ActionFact(
        action_id=action.action_id,
        agent_id=action.agent_id,
        tool_id=action.tool_id,
        tool_name=action.tool_name,
        provider=action.provider,
        source_type=action.source_type,
        source_id=action.source_id,
        source_ref=action.source_ref,
        source_location=action.source_location,
        source_path=action.source_path,
        source_start_line=action.source_start_line,
        source_end_line=action.source_end_line,
        source_start_column=action.source_start_column,
        source_pointer=action.source_pointer,
        operation=action.operation,
        effect=action.effect,
        semantic_assessment=semantic_evidence,
        risk_tags=action.risk_tags,
        required_scopes=action.scope_strings,
        approval_policy=approval,
        safeguards=safeguards,
        evidence=evidence,
        input_fields=action.input_fields,
        required_input_fields=action.required_input_fields,
        input_schema_hash=schema_hash,
        hashes=ActionSurfaceHashes(
            identity_hash=_stable_hash(action.action_id),
            schema_hash=schema_hash,
            policy_hash=policy_hash,
            risk_hash=risk_hash,
        ),
    )


def _action_from_tool(
    manifest: AgentsShipgateManifest,
    *,
    agent_id: str,
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> ActionFact:
    """Backward-compatible wrapper: build typed Action, then serialize.

    Kept as the entry point used by ``build_action_surface_facts`` so
    external callers and tests that import this private symbol keep
    working byte-for-byte. New call sites should prefer ``build_action``
    directly to get the typed view.
    """
    return action_to_fact(
        build_action(manifest, agent_id=agent_id, tool=tool, declaration=declaration)
    )


def _actions_by_id(actions: list[ActionFact]) -> dict[str, ActionFact]:
    _validate_unique_action_ids(actions)
    return {action.action_id: action for action in actions}


def _actions_by_id_fail_soft(
    actions: list[ActionFact],
    *,
    side: str,
    warnings: list[str],
) -> dict[str, ActionFact]:
    """Index actions by id, degrading duplicates instead of raising.

    Reuses :func:`_disambiguate_duplicate_action_ids` with no explicit
    tool names: at diff time both snapshots are engine output whose
    manifest-authored identities were validated at build time, so every
    surviving collision is inferred (most commonly a base report written
    before inferred collisions were disambiguated at build time). The
    tool-name suffix strategy matches what the head side's build-time
    disambiguator produces, so a degraded base lines up with a fresh head
    instead of flagging the whole surface as churned.
    """
    for message in _disambiguate_duplicate_action_ids(actions, explicit_tool_names=set()):
        warnings.append(
            f"action_surface_diff ({side}): {message} The colliding ids were "
            "read from an engine-generated snapshot, typically a --diff-from "
            "report or baseline produced by an older version."
        )
    return {action.action_id: action for action in actions}


def _validate_unique_action_ids(actions: list[ActionFact]) -> None:
    by_id: dict[str, list[str]] = {}
    for action in actions:
        by_id.setdefault(action.action_id, []).append(action.tool_name)
    duplicates = {
        action_id: sorted(tool_names)
        for action_id, tool_names in by_id.items()
        if len(tool_names) > 1
    }
    if duplicates:
        _raise_duplicate_action_ids(duplicates)


def _raise_duplicate_action_ids(duplicates: dict[str, list[str]]) -> None:
    details = "; ".join(
        f"{action_id!r} used by {', '.join(tool_names)}"
        for action_id, tool_names in sorted(duplicates.items())
    )
    raise ConfigError(
        "Duplicate action_surface action_id values are not allowed: "
        f"{details}. Set unique action_surface.actions[].id values or adjust "
        "provider/operation metadata."
    )


def _disambiguate_duplicate_action_ids(
    actions: list[ActionFact],
    *,
    explicit_tool_names: set[str],
) -> list[str]:
    """Fail-soft replacement for :func:`_validate_unique_action_ids`,
    scoped to *inferred* collisions only.

    A manifest-authored action identity — any ``action_surface.actions[]``
    declaration setting ``id``, ``provider``, or ``operation`` — is a
    contract: a collision that involves one (explicit-vs-explicit or
    explicit-vs-inferred) is a config mistake to fix, so it stays a hard
    :class:`ConfigError` (identical to the no-sink path).
    ``explicit_tool_names`` names the tools carrying such a declaration.

    Only collisions between purely *inferred* ids degrade — most commonly
    two OpenAPI operations whose paths normalize identically. Those are
    mutated in place so each ``action_id`` is unique, returning one
    human-readable warning per resolved collision. The first member of each
    colliding group (in ``tool_name`` order) keeps the bare id; the rest
    gain a ``#<operationId>`` suffix so distinct operations stay distinct in
    the diff. An ordinal fallback covers the pathological case where two
    colliding actions also share a ``tool_name``. Renamed actions get a
    refreshed ``identity_hash`` so downstream identity-hash consumers stay
    consistent.
    """
    by_id: dict[str, list[ActionFact]] = {}
    for action in actions:
        by_id.setdefault(action.action_id, []).append(action)
    colliding = {action_id: group for action_id, group in by_id.items() if len(group) > 1}

    # Hard-fail any collision that touches an explicitly declared id. These
    # are not third-party quirks the engine should silently rewrite.
    explicit_collisions = {
        action_id: sorted(item.tool_name for item in group)
        for action_id, group in colliding.items()
        if any(item.tool_name in explicit_tool_names for item in group)
    }
    if explicit_collisions:
        _raise_duplicate_action_ids(explicit_collisions)

    warnings: list[str] = []
    for action_id, group in sorted(colliding.items()):
        ordered = sorted(group, key=lambda item: item.tool_name)
        warnings.append(
            "Duplicate action_surface action_id "
            f"{action_id!r} derived from operations "
            f"{', '.join(repr(item.tool_name) for item in ordered)}; "
            "disambiguated with per-operation suffixes so distinct "
            "operations are not collapsed."
        )
        used_suffixes: dict[str, int] = {}
        for index, action in enumerate(ordered):
            if index == 0:
                continue
            suffix = action.tool_name or str(index)
            seen = used_suffixes.get(suffix, 0) + 1
            used_suffixes[suffix] = seen
            if seen > 1:
                suffix = f"{suffix}#{seen}"
            new_id = f"{action_id}#{suffix}"
            action.action_id = new_id
            action.hashes = action.hashes.model_copy(update={"identity_hash": _stable_hash(new_id)})
    return warnings


def _provider(tool: Tool, declaration: ActionDeclarationConfig | None) -> str:
    if tool.provider:
        return _normalize_token(tool.provider)
    if tool.source_id:
        return _normalize_token(tool.source_id)
    return _normalize_token(tool.source_type)


def _canonical_action_id(*, agent_id: str, tool_id: str, provider: str, operation: str) -> str:
    payload = json.dumps(
        {
            "agent_id": agent_id,
            "tool_id": tool_id,
            "provider": provider,
            "operation": operation,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{agent_id}:action_v2_{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _operation(tool: Tool, declaration: ActionDeclarationConfig | None) -> str:
    if declaration and declaration.operation:
        return _normalize_operation(declaration.operation)
    method = tool.annotations.get("httpMethod")
    path = tool.annotations.get("path")
    if method and path:
        return f"{str(method).upper()} {_normalize_path(str(path))}"
    return _normalize_operation(tool.name)


def _approval_fact(
    manifest: AgentsShipgateManifest,
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> ActionApprovalFact:
    fact = ActionApprovalFact(required="approval" in tool.resolved_controls)
    if declaration and declaration.approval:
        update = {
            key: value
            for key, value in declaration.approval.model_dump().items()
            if value is not None
        }
        fact = fact.model_copy(update=update)
    return fact


def _safeguards_fact(
    manifest: AgentsShipgateManifest,
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> ActionSafeguardsFact:
    annotations = tool.annotations
    fact = ActionSafeguardsFact(
        # Idempotency has several existing declarative sources; the other
        # safeguards stay nullable unless a source explicitly declares them.
        idempotency=(
            "idempotency" in tool.resolved_controls
            or annotations.get("idempotentHint") is True
            or any(parameter.name == "idempotency_key" for parameter in tool.parameters)
        ),
        audit_log=_annotation_bool(annotations, "audit_log", "auditLog"),
        rollback=_annotation_bool(annotations, "rollback"),
        dry_run=_annotation_bool(annotations, "dry_run", "dryRun"),
    )
    if declaration and declaration.safeguards:
        update = {
            key: value
            for key, value in declaration.safeguards.model_dump().items()
            if value is not None
        }
        fact = fact.model_copy(update=update)
    return fact


def _declaration_downgrade_findings(
    manifest: AgentsShipgateManifest,
    facts: ActionSurfaceFacts,
    tools: list[Tool],
    *,
    agent_id: str,
) -> list[Finding]:
    selector_index = ToolSelectorIndex.build(tools)
    declarations = _resolved_declarations_from_tools(
        manifest,
        tools,
        selector_index=selector_index,
    )
    controls_by_tool_id: dict[str, set[str]] = {}
    for control, entries in (
        ("approval", manifest.policies.require_approval_for_tools),
        ("idempotency", manifest.policies.require_idempotency_for_tools),
    ):
        for entry in entries:
            match = selector_index.resolve(entry)
            if match.resolved:
                controls_by_tool_id.setdefault(match.matches[0].id, set()).add(control)
    by_tool = {action.tool_id: action for action in facts.actions}
    findings: list[Finding] = []
    for original in sorted(tools, key=lambda item: item.id):
        resolved_controls = sorted(
            set(original.resolved_controls)
            | controls_by_tool_id.get(original.id, set())
        )
        tool = (
            original
            if resolved_controls == original.resolved_controls
            else original.model_copy(update={"resolved_controls": resolved_controls})
        )
        declaration = declarations.get(tool.id)
        action = by_tool.get(tool.id)
        if declaration is None or action is None:
            continue
        findings.extend(
            _control_downgrade_findings(
                manifest,
                tool,
                declaration,
                action,
                agent_id=agent_id,
            )
        )
        if declaration.effect is not None:
            action_assessment = action.semantic_assessment
            # The scan pipeline attached the declaration-aware central
            # assessment once after enrichment.  Re-running the resolver here
            # was both divergent and a measurable O(tools) latency regression.
            # Its claims retain source provenance, so exclude only claims
            # authored by this action declaration and recover the conservative
            # source bound without a second semantic evaluation.
            if action_assessment is None:
                action_assessment = assess_tool_semantics(tool, declaration)
            source_effects = [
                claim.value
                for claim in action_assessment.effect.claims
                if claim.source not in DECLARATION_CLAIM_SOURCES
                and claim.value in ACTION_EFFECT_RANK
            ]
            source_effect = max(
                source_effects or ["write"],
                key=ACTION_EFFECT_RANK.__getitem__,
            )
            if (
                action_assessment.effect.status == "conflicting"
                and ACTION_EFFECT_RANK[declaration.effect]
                < ACTION_EFFECT_RANK[source_effect]
            ):
                findings.append(
                    _finding(
                        check_id="SHIP-ACTION-EFFECT-DOWNGRADE-DECLARED",
                        title=(
                            f"{action.tool_name} declares a weaker action effect "
                            "than Shipgate inferred"
                        ),
                        severity="high",
                        action=action,
                        agent_id=agent_id,
                        evidence={
                            "action_id": action.action_id,
                            "inferred_effect": source_effect,
                            "declared_effect": declaration.effect,
                        },
                        recommendation=(
                            "Set action_surface.actions[].effect for "
                            f"{action.tool_name} to {source_effect}, or remove "
                            "the weaker declaration."
                        ),
                        blocks_release=True,
                    )
                )
    return findings


def _control_downgrade_findings(
    manifest: AgentsShipgateManifest,
    tool: Tool,
    declaration: ActionDeclarationConfig,
    action: ActionFact,
    *,
    agent_id: str,
) -> list[Finding]:
    findings: list[Finding] = []
    inherited_approval = ActionApprovalFact(required="approval" in tool.resolved_controls)
    if (
        inherited_approval.required is True
        and declaration.approval is not None
        and declaration.approval.required is False
    ):
        findings.append(
            _control_downgrade_finding(
                action,
                agent_id=agent_id,
                path="approval.required",
                inherited=True,
                declared=False,
            )
        )

    inherited_safeguards = ActionSafeguardsFact(
        idempotency=(
            "idempotency" in tool.resolved_controls
            or tool.annotations.get("idempotentHint") is True
            or any(parameter.name == "idempotency_key" for parameter in tool.parameters)
        ),
        audit_log=_annotation_bool(tool.annotations, "audit_log", "auditLog"),
        rollback=_annotation_bool(tool.annotations, "rollback"),
        dry_run=_annotation_bool(tool.annotations, "dry_run", "dryRun"),
    )
    if declaration.safeguards is None:
        return findings
    for field in _SAFEGUARD_FIELDS:
        if (
            getattr(inherited_safeguards, field) is True
            and getattr(declaration.safeguards, field) is False
        ):
            findings.append(
                _control_downgrade_finding(
                    action,
                    agent_id=agent_id,
                    path=f"safeguards.{field}",
                    inherited=True,
                    declared=False,
                )
            )
    return findings


def _control_downgrade_finding(
    action: ActionFact,
    *,
    agent_id: str,
    path: str,
    inherited: bool,
    declared: bool,
) -> Finding:
    return _finding(
        check_id="SHIP-ACTION-CONTROL-DOWNGRADE",
        title=f"{action.tool_name} declares a weaker action control at {path}",
        severity="high",
        action=action,
        agent_id=agent_id,
        evidence={
            "action_id": action.action_id,
            "path": path,
            "inherited": inherited,
            "declared": declared,
        },
        recommendation=(
            f"Keep action_surface.actions[].{path} enabled for {action.tool_name}, "
            "or remove the weakening action declaration."
        ),
        blocks_release=True,
    )


def _evidence_fact(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> ActionEvidenceFact:
    fact = ActionEvidenceFact(owner=tool.owner)
    if declaration and declaration.evidence:
        update = {
            key: value
            for key, value in declaration.evidence.model_dump().items()
            if value is not None
        }
        fact = fact.model_copy(update=update)
    return fact


def _normalized_risk_tags(tool: Tool) -> list[str]:
    tags = {_RISK_TAG_MAP.get(tag, tag) for tag in risk_tags(tool)}
    if is_effectively_read_only(tool):
        tags.add("read_only")
    return sorted(tags)


def _infer_effect(tool: Tool, tags: list[str]) -> str:
    # Compatibility wrapper for external/private callers. The resolver is the
    # only implementation of effect semantics; ``tags`` is retained in the
    # signature during the 0.x migration but no longer drives a second model.
    del tags
    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    return assessment.conservative_effect


def _risk_tag_for_effect(effect: str) -> str:
    return {
        "read": "read_only",
        "write": "writes_data",
        "destructive": "destructive",
        "external_communication": "external_communication",
        "financial_write": "financial_write",
        "production_operation": "production_ops",
        "privileged_data_access": "privileged_data",
        "code_execution": "code_execution",
        "identity_access": "identity_access",
    }[effect]


def _action_added_change(action: ActionFact) -> ActionSurfaceChange:
    severity = _severity_for_action(action)
    return ActionSurfaceChange(
        type="ACTION_ADDED",
        action_id=action.action_id,
        tool_id=action.tool_id,
        agent_id=action.agent_id,
        tool_name=action.tool_name,
        operation=action.operation,
        severity=severity,
        reason=f"Action added: {action.tool_name}",
        after=_action_summary(action),
    )


def _action_removed_change(action: ActionFact) -> ActionSurfaceChange:
    return ActionSurfaceChange(
        type="ACTION_REMOVED",
        action_id=action.action_id,
        tool_id=action.tool_id,
        agent_id=action.agent_id,
        tool_name=action.tool_name,
        operation=action.operation,
        severity="info",
        reason=f"Action removed: {action.tool_name}",
        before=_action_summary(action),
    )


def _modified_changes(current: ActionFact, base: ActionFact) -> list[ActionSurfaceChange]:
    changes: list[ActionSurfaceChange] = []
    added_scopes = sorted(set(current.required_scopes) - set(base.required_scopes))
    if added_scopes:
        severity: Severity = "critical" if any(is_broad_scope(s) for s in added_scopes) else "high"
        changes.append(
            _change(
                "SCOPE_EXPANDED",
                current,
                severity,
                "Action scope expanded.",
                before=base.required_scopes,
                after=current.required_scopes,
                added=added_scopes,
            )
        )
    if ACTION_EFFECT_RANK[current.effect] > ACTION_EFFECT_RANK[base.effect]:
        changes.append(
            _change(
                "EFFECT_ESCALATED",
                current,
                "critical",
                "Action effect escalated.",
                before=base.effect,
                after=current.effect,
            )
        )
    added_tags = sorted(set(current.risk_tags) - set(base.risk_tags))
    if added_tags:
        severity = "critical" if set(added_tags) & _CRITICAL_RISK_TAGS else "high"
        changes.append(
            _change(
                "RISK_TAG_ADDED",
                current,
                severity,
                "Action risk tag added.",
                before=base.risk_tags,
                after=current.risk_tags,
                added=added_tags,
            )
        )
    if base.approval_policy.required is True and current.approval_policy.required is not True:
        changes.append(
            _change(
                "APPROVAL_REMOVED",
                current,
                "critical",
                "Action approval policy was removed.",
                before=base.approval_policy.model_dump(mode="json"),
                after=current.approval_policy.model_dump(mode="json"),
            )
        )
    for field in _SAFEGUARD_FIELDS:
        if (
            getattr(base.safeguards, field) is True
            and getattr(current.safeguards, field) is not True
        ):
            changes.append(
                _change(
                    "SAFEGUARD_REMOVED",
                    current,
                    "critical"
                    if field == "rollback" and current.effect == "destructive"
                    else "high",
                    f"Action safeguard removed: {field}.",
                    before=base.safeguards.model_dump(mode="json"),
                    after=current.safeguards.model_dump(mode="json"),
                    removed=[field],
                )
            )
    added_fields = sorted(set(current.input_fields) - set(base.input_fields))
    added_required = sorted(set(current.required_input_fields) - set(base.required_input_fields))
    if added_fields or added_required:
        changes.append(
            _change(
                "INPUT_SCHEMA_EXPANDED",
                current,
                "medium",
                "Action input schema expanded.",
                before={
                    "input_fields": base.input_fields,
                    "required_input_fields": base.required_input_fields,
                },
                after={
                    "input_fields": current.input_fields,
                    "required_input_fields": current.required_input_fields,
                },
                added=sorted(set(added_fields) | set(added_required)),
            )
        )
    if not changes and current.hashes != base.hashes:
        changes.append(
            _change(
                "ACTION_MODIFIED",
                current,
                "medium",
                "Action metadata changed.",
                before=base.hashes.model_dump(mode="json"),
                after=current.hashes.model_dump(mode="json"),
            )
        )
    return changes


def _change(
    change_type: str,
    action: ActionFact,
    severity: Severity,
    reason: str,
    *,
    before: Any = None,
    after: Any = None,
    added: list[str] | None = None,
    removed: list[str] | None = None,
) -> ActionSurfaceChange:
    return ActionSurfaceChange(
        type=change_type,
        action_id=action.action_id,
        tool_id=action.tool_id,
        agent_id=action.agent_id,
        tool_name=action.tool_name,
        operation=action.operation,
        severity=severity,
        reason=reason,
        before=before,
        after=after,
        added=added or [],
        removed=removed or [],
        source_path=action.source_path,
        source_start_line=action.source_start_line,
    )


def _change_evidence(change: ActionSurfaceChange) -> dict[str, Any]:
    """Serialize a change row into the ``evidence.change`` slot used by
    action-surface policy findings.

    Excludes the v0.19 reviewer-grade ``source_path`` and
    ``source_start_line`` fields. ``evaluate_action_surface_policies``
    feeds this dump into ``Finding.evidence``, and
    ``finding_fingerprint`` hashes canonicalized ``evidence`` — so
    even when the structured source pointers are unset (``None``)
    their mere presence as keys would shift the hash relative to
    pre-v0.19 reports. Stripping them at the dump site keeps the
    fingerprint byte-equal to legacy and preserves baseline identity
    across the upgrade. The structured fields are still emitted on
    the diff row itself (and consumed by renderers); only the
    finding-evidence projection drops them.
    """
    return change.model_dump(
        mode="json",
        exclude={"source_path", "source_start_line"},
    )


def _summary(
    added: list[ActionSurfaceChange],
    removed: list[ActionSurfaceChange],
    modified: list[ActionSurfaceChange],
) -> ActionSurfaceDiffSummary:
    modified_action_ids = {change.action_id for change in modified}
    return ActionSurfaceDiffSummary(
        actions_added=len(added),
        actions_removed=len(removed),
        actions_modified=len(modified_action_ids),
        scope_expansions=sum(1 for change in modified if change.type == "SCOPE_EXPANDED"),
        effect_escalations=sum(1 for change in modified if change.type == "EFFECT_ESCALATED"),
        risk_tags_added=sum(1 for change in modified if change.type == "RISK_TAG_ADDED"),
        approvals_removed=sum(1 for change in modified if change.type == "APPROVAL_REMOVED"),
        safeguards_removed=sum(1 for change in modified if change.type == "SAFEGUARD_REMOVED"),
        input_schema_expansions=sum(
            1 for change in modified if change.type == "INPUT_SCHEMA_EXPANDED"
        ),
    )


def _builtin_policy_findings(
    manifest: AgentsShipgateManifest,
    facts: ActionSurfaceFacts,
    diff: ActionSurfaceDiff,
    by_action: dict[str, ActionFact],
    *,
    agent_id: str,
    tools: list[Tool] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    tools_by_id = {tool.id: tool for tool in tools or []}

    # Built-in controls protect the CURRENT capability surface.  They must
    # not disappear merely because the caller did not provide a diff base or
    # because a risky action predates that base.  The diff loop below adds
    # change-specific review context; it is not the source of truth for
    # whether the current surface satisfies its controls.
    for action in facts.actions:
        tool = tools_by_id.get(action.tool_id)
        if not _effect_can_drive_hard_controls(action, tool):
            # An inferred/unknown/conflicting effect is an evidence gap.  It
            # is routed through semantic coverage rather than laundered into
            # a high-confidence policy blocker.
            continue
        findings.extend(
            _current_action_policy_findings(
                manifest,
                action,
                agent_id=agent_id,
            )
        )

    if not diff.enabled:
        return findings
    for change in diff.modified:
        action = by_action.get(change.action_id)
        if action is None:
            continue
        if change.type == "SCOPE_EXPANDED" and any(is_broad_scope(scope) for scope in change.added):
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-WILDCARD-SCOPE",
                    title=f"{action.tool_name} expands to a broad action scope",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"change": _change_evidence(change)},
                    recommendation=(
                        "Replace action_surface.actions[].scopes for "
                        f"{action.tool_name} with operation-specific scopes; "
                        "remove wildcard/admin scopes."
                    ),
                    blocks_release=True,
                )
            )
        elif change.type == "EFFECT_ESCALATED":
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-EFFECT-ESCALATED",
                    title=f"{action.tool_name} escalates action effect",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"change": _change_evidence(change)},
                    recommendation=(
                        f"Review action_surface.actions[].effect for {action.tool_name}; "
                        f"restore {change.before} or document approval/evidence for "
                        f"{change.after}."
                    ),
                    blocks_release=True,
                )
            )
        elif change.type == "APPROVAL_REMOVED":
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-APPROVAL-REMOVED",
                    title=f"{action.tool_name} removes an action approval policy",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"change": _change_evidence(change)},
                    recommendation=(
                        "Restore action_surface.actions[].approval.required: true "
                        f"for {action.tool_name}, or document the reviewed exception "
                        "under action_surface.actions[].evidence.approval_ticket."
                    ),
                    blocks_release=True,
                )
            )
        elif change.type == "SAFEGUARD_REMOVED":
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-SAFEGUARD-REMOVED",
                    title=f"{action.tool_name} removes an action safeguard",
                    severity=change.severity,
                    action=action,
                    agent_id=agent_id,
                    evidence={"change": _change_evidence(change)},
                    recommendation=(
                        "Restore action_surface.actions[].safeguards."
                        f"{change.removed[0] if change.removed else '<removed>'}: true "
                        f"for {action.tool_name}, or document the reviewed exception "
                        "under action_surface.actions[].evidence."
                    ),
                    blocks_release=True,
                )
            )
    return findings


def _current_action_policy_findings(
    manifest: AgentsShipgateManifest,
    action: ActionFact,
    *,
    agent_id: str,
) -> list[Finding]:
    findings: list[Finding] = []
    control_effects = _control_effects(action)
    if any(is_broad_scope(scope) for scope in action.required_scopes):
        findings.append(
            _finding(
                check_id="SHIP-ACTION-WILDCARD-SCOPE",
                title=f"{action.tool_name} declares a broad action scope",
                severity="critical",
                action=action,
                agent_id=agent_id,
                evidence={
                    "action_id": action.action_id,
                    "scopes": action.required_scopes,
                },
                recommendation=(
                    "Replace action_surface.actions[].scopes for "
                    f"{action.tool_name} with operation-specific scopes; "
                    "remove wildcard/admin scopes."
                ),
                blocks_release=True,
            )
        )
    if "financial_write" in control_effects:
        missing = _missing_builtin_requirements(
            action,
            {
                "approval.required": True,
                "safeguards.audit_log": True,
                "safeguards.idempotency": True,
            },
        )
        if missing:
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
                    title=f"{action.tool_name} has financial write capability without required controls",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"action_id": action.action_id, "missing": missing},
                    recommendation=(
                        "Declare approval.required, safeguards.audit_log, and "
                        "safeguards.idempotency for this financial write action."
                    ),
                    blocks_release=True,
                    support=_builtin_control_support(
                        action,
                        effects={"financial_write"},
                        missing=missing,
                    ),
                )
            )
    if "external_communication" in control_effects:
        missing = _missing_builtin_requirements(
            action,
            {"safeguards.audit_log": True},
        )
        if not _action_has_policy_control(
            action,
            manifest.policies.require_confirmation_for_tools,
        ):
            missing.append("confirmation.required")
        if missing:
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
                    title=(
                        f"{action.tool_name} has external communication capability "
                        "without required controls"
                    ),
                    severity="high",
                    action=action,
                    agent_id=agent_id,
                    evidence={"action_id": action.action_id, "missing": missing},
                    recommendation=(
                        "Declare confirmation policy and safeguards.audit_log for "
                        "this external communication action."
                    ),
                    blocks_release=True,
                    support=_builtin_control_support(
                        action,
                        effects={"external_communication"},
                        missing=missing,
                    ),
                )
            )
    if "destructive" in control_effects:
        missing = _missing_builtin_requirements(
            action,
            {
                "approval.required": True,
                "safeguards.rollback": True,
            },
        )
        if not _action_has_policy_control(
            action,
            manifest.policies.require_confirmation_for_tools,
        ):
            missing.append("confirmation.required")
        if missing:
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
                    title=f"{action.tool_name} has destructive capability without required controls",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"action_id": action.action_id, "missing": missing},
                    recommendation=(
                        "Declare approval.required, confirmation policy, and "
                        "safeguards.rollback for this destructive action."
                    ),
                    blocks_release=True,
                    support=_builtin_control_support(
                        action,
                        effects={"destructive"},
                        missing=missing,
                    ),
                )
            )
    high_impact_effects = control_effects.intersection({"production_operation", "code_execution"})
    if high_impact_effects:
        missing = _missing_builtin_requirements(
            action,
            {"approval.required": True},
        )
        if missing:
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-POLICY-VIOLATION",
                    title=(
                        f"{action.tool_name} has "
                        f"{', '.join(sorted(high_impact_effects))} capability "
                        "without approval"
                    ),
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={
                        "policy_id": "builtin-high-impact-approval",
                        "action_id": action.action_id,
                        "missing": [
                            {
                                "path": path,
                                "expected": True,
                            }
                            for path in missing
                        ],
                    },
                    recommendation=(
                        "Declare approval.required for production-operation and "
                        "code-execution actions."
                    ),
                    blocks_release=True,
                    support=_builtin_control_support(
                        action,
                        effects=high_impact_effects,
                        missing=missing,
                    ),
                )
            )
    return findings


def _action_has_policy_control(action: ActionFact, entries: list[Any]) -> bool:
    """Return true only for an exact, non-ambiguous policy selector."""

    identity_eligible = (
        action.semantic_assessment is not None
        and action.semantic_assessment.identity is not None
        and action.semantic_assessment.identity.pass_eligible
    )
    # Alias-aware, not field-equal: binding rewrites both the canonical
    # ``tool_id`` and the row's ``source_type``/``source_id``, so a policy
    # selector written against the completed source stopped matching the moment
    # an inventory completed it — the scan then reported a missing
    # ``confirmation.required`` and moved to ``blocked`` on a manifest the user
    # had not touched (#386 review).
    aliases = action_identity_aliases(action)
    for entry in entries:
        if entry.tool_id:
            if not aliases.matches(tool_id=entry.tool_id):
                continue
        elif entry.tool != action.tool_name or not identity_eligible:
            continue
        if entry.provider and entry.provider != action.provider:
            continue
        if not aliases.matches(
            source_type=entry.source_type or None, source_id=entry.source_id or None
        ):
            continue
        return True
    return False


def _control_effects(action: ActionFact) -> set[str]:
    """Return the union of pass-eligible positive semantic effect claims."""

    assessment = action.semantic_assessment
    if assessment is None:
        return {action.effect}
    effects: set[str] = set()
    for claim in assessment.effect.claims:
        if claim.policy_eligible and claim.value in ACTION_EFFECT_RANK:
            effects.add(claim.value)
    return effects


def _non_authoritative_effect_escalation_support(
    action: ActionFact,
) -> FindingSupport | None:
    """Return unresolved support when weaker evidence outranks proven semantics.

    Hard controls intentionally consume only policy-eligible claims. Silently
    discarding a higher-risk non-authoritative claim would turn uncertainty
    into a clean pass, so this support record routes the action to insufficient
    evidence without laundering that claim into a blocker.
    """

    assessment = action.semantic_assessment
    if assessment is None:
        return None
    authoritative = [
        claim
        for claim in assessment.effect.claims
        if claim.policy_eligible and claim.value in ACTION_EFFECT_RANK
    ]
    if not authoritative:
        return None
    authoritative_rank = max(
        ACTION_EFFECT_RANK[claim.value] for claim in authoritative
    )
    # A reviewed override answers this exact question — "does the higher
    # heuristic effect apply here?" — so an acknowledged claim is resolved, not
    # unresolved. Without this the reviewer follows the row's own instruction,
    # writes the override, and trades `declaration_below_inferred_evidence` for
    # `mixed_policy_evidence`: the same verdict, a different name (review 1).
    # The run still cannot read `passed` — the acknowledgement is a semantic
    # review concern — and the exception is projected per action for a reviewer.
    acknowledged = acknowledged_effect_claim_ids(assessment.effect.claims)
    # The same comparison the declaration rule makes, so the two surfaces cannot
    # disagree about whether an observation is accounted for. Comparing
    # ``ACTION_EFFECT_RANK`` here while ``claims_above_declared_effect`` compared
    # ``_EFFECT_RANK`` left the two orders contradicting each other on the pair
    # they rank oppositely — a declaration read as covered there and raised
    # ``mixed_policy_evidence`` here, a verdict no override could reach.
    authoritative_effects = {claim.value for claim in authoritative}
    inferred_escalations = [
        claim
        for claim in assessment.effect.claims
        if not claim.policy_eligible
        and claim.value in ACTION_EFFECT_RANK
        and not any(
            declaration_covers(effect, claim.value) for effect in authoritative_effects
        )
        and claim.claim_id not in acknowledged
    ]
    if not inferred_escalations:
        return None
    strongest_rank = max(
        ACTION_EFFECT_RANK[claim.value] for claim in inferred_escalations
    )
    strongest_authoritative = [
        claim
        for claim in authoritative
        if ACTION_EFFECT_RANK[claim.value] == authoritative_rank
    ]
    strongest_inferred = [
        claim
        for claim in inferred_escalations
        if ACTION_EFFECT_RANK[claim.value] == strongest_rank
    ]
    return finding_support(
        [
            predicate_evidence(
                "authoritative_effect_bound",
                "matched",
                expected="reviewed or structural effect evidence",
                observed=sorted(
                    {claim.value for claim in strongest_authoritative}
                ),
                confidence=min(
                    (claim.confidence for claim in strongest_authoritative),
                    key=confidence_rank,
                ),
                claim_ids=[claim.claim_id for claim in strongest_authoritative],
                evidence_bases=[
                    claim.basis for claim in strongest_authoritative
                ],
                policy_eligible=True,
            ),
            predicate_evidence(
                "higher_effect_control_applicability",
                "indeterminate",
                expected="reviewed or structural evidence for the higher effect",
                observed=sorted({claim.value for claim in strongest_inferred}),
                confidence=min(
                    (claim.confidence for claim in strongest_inferred),
                    key=confidence_rank,
                ),
                claim_ids=[claim.claim_id for claim in strongest_inferred],
                evidence_bases=[claim.basis for claim in strongest_inferred],
                policy_eligible=False,
                why=(
                    "higher-risk effect evidence is heuristic and cannot be "
                    "silently excluded from control applicability"
                ),
            ),
        ],
        status="indeterminate",
    )


def _builtin_control_support(
    action: ActionFact,
    *,
    effects: set[str],
    missing: list[str],
) -> FindingSupport:
    assessment = action.semantic_assessment
    claims = (
        [
            claim
            for claim in assessment.effect.claims
            if claim.policy_eligible and claim.value in effects
        ]
        if assessment is not None
        else []
    )
    rows: list[PolicyPredicateEvidence] = []
    if claims:
        rows.append(
            predicate_evidence(
                "builtin_control_effect",
                "matched",
                expected=sorted(effects),
                observed=sorted({claim.value for claim in claims}),
                confidence=min(
                    (claim.confidence for claim in claims),
                    key=confidence_rank,
                ),
                claim_ids=[claim.claim_id for claim in claims],
                evidence_bases=[claim.basis for claim in claims],
                policy_eligible=True,
            )
        )
    elif assessment is None and action.effect in effects:
        rows.append(
            predicate_evidence(
                "builtin_control_effect",
                "matched",
                expected=sorted(effects),
                observed=action.effect,
                confidence="high",
                evidence_bases=["protocol_structure"],
                policy_eligible=True,
                why="compatibility projection from a normalized action fact",
            )
        )
    else:
        rows.append(
            predicate_evidence(
                "builtin_control_effect",
                "indeterminate",
                expected=sorted(effects),
                observed=action.effect,
                confidence="low",
                evidence_bases=["unknown"],
                why="no authoritative effect claim supports this built-in control",
            )
        )
    rows.append(
        predicate_evidence(
            "missing_builtin_controls",
            "matched",
            expected="all required controls present",
            observed=sorted(missing),
            confidence="high",
            evidence_bases=["protocol_structure"],
            policy_eligible=True,
        )
    )
    return finding_support(rows)


def _effect_can_drive_hard_controls(
    action: ActionFact,
    tool: Tool | None,
) -> bool:
    """Whether this tool's effect has non-heuristic static evidence.

    Semantic assessments are attached by the scan pipeline.  Keeping this
    helper defensive makes direct action-surface callers and old serialized
    facts work during the 0.x migration while preventing keyword-only
    classifications from being upgraded into high-confidence blockers.
    """

    assessment = action.semantic_assessment
    if assessment is None and tool is not None:
        assessment = getattr(tool, "semantic_assessment", None)
    if assessment is None:
        return True
    return bool(_control_effects(action))


def _missing_builtin_requirements(
    action: ActionFact,
    requirements: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    for path, expected in requirements.items():
        actual = _value_at_path(action, path)
        if actual is _MISSING_PATH or actual != expected:
            missing.append(path)
    return missing


def _assess_action_policy_match(
    policy: ActionPolicyConfig,
    action: ActionFact,
) -> tuple[str, FindingSupport]:
    match = policy.match
    rows = []
    for name, expected, observed in (
        ("action_ids", match.action_ids, action.action_id),
        ("tool_ids", match.tool_ids, action.tool_id),
        ("tools", match.tools, action.tool_name),
    ):
        if expected:
            rows.append(
                predicate_evidence(
                    name,
                    "matched" if observed in expected else "not_matched",
                    expected=expected,
                    observed=observed,
                    confidence="high",
                    evidence_bases=["protocol_structure"],
                    policy_eligible=True,
                )
            )

    assessment = action.semantic_assessment
    claims = list(assessment.effect.claims) if assessment is not None else []
    # An acknowledged claim is a decided question, not an open one. Leaving it
    # in the `possible` set kept every user policy that names the acknowledged
    # effect indeterminate forever (review 1).
    acknowledged = acknowledged_effect_claim_ids(claims)
    claims = [claim for claim in claims if claim.claim_id not in acknowledged]
    if match.effects:
        eligible = [
            claim
            for claim in claims
            if claim.policy_eligible and claim.value in match.effects
        ]
        possible = [claim for claim in claims if claim.value in match.effects]
        rows.append(
            _semantic_action_predicate(
                "effects",
                expected=list(match.effects),
                eligible=eligible,
                possible=possible,
                assessment_status=(assessment.effect.status if assessment else "unknown"),
            )
        )
    if match.risk_tags:
        requested = set(_normalize_risk_tag_values(match.risk_tags))
        eligible = [
            claim
            for claim in claims
            if claim.policy_eligible and _RISK_TAG_MAP.get(claim.value, claim.value) in requested
        ]
        possible = [
            claim
            for claim in claims
            if _RISK_TAG_MAP.get(claim.value, claim.value) in requested
        ]
        rows.append(
            _semantic_action_predicate(
                "risk_tags",
                expected=sorted(requested),
                eligible=eligible,
                possible=possible,
                assessment_status=(assessment.effect.status if assessment else "unknown"),
            )
        )
    if match.scopes:
        authority = assessment.authority if assessment is not None else None
        matched_scopes = sorted(set(match.scopes).intersection(action.required_scopes))
        if authority is None or authority.status in {"partial", "unknown", "conflicting"}:
            rows.append(
                predicate_evidence(
                    "scopes",
                    "conflicting"
                    if authority is not None and authority.status == "conflicting"
                    else "indeterminate",
                    expected=match.scopes,
                    observed=matched_scopes,
                    confidence="low",
                    claim_ids=[claim.claim_id for claim in authority.claims] if authority else [],
                    evidence_bases=[claim.basis for claim in authority.claims]
                    if authority
                    else ["unknown"],
                    why="authority scope evidence is incomplete or conflicting",
                )
            )
        else:
            rows.append(
                predicate_evidence(
                    "scopes",
                    "matched" if matched_scopes else "not_matched",
                    expected=match.scopes,
                    observed=matched_scopes,
                    confidence="high",
                    claim_ids=[claim.claim_id for claim in authority.claims],
                    evidence_bases=[claim.basis for claim in authority.claims],
                    policy_eligible=all(claim.policy_eligible for claim in authority.claims),
                )
            )
    support = finding_support(rows)
    return support.status, support


def _semantic_action_predicate(
    predicate: str,
    *,
    expected: list[str],
    eligible: list[Any],
    possible: list[Any],
    assessment_status: str,
) -> PolicyPredicateEvidence:
    if eligible:
        status = "matched"
        selected = eligible
    elif possible or assessment_status in {"inferred", "unknown", "protocol_default", "conflicting"}:
        status = "conflicting" if assessment_status == "conflicting" else "indeterminate"
        selected = possible
    else:
        status = "not_matched"
        selected = []
    return predicate_evidence(
        predicate,
        status,
        expected=expected,
        observed=sorted({claim.value for claim in selected}),
        confidence=(
            min(
                (claim.confidence for claim in selected),
                key=confidence_rank,
                default="low",
            )
        ),
        claim_ids=[claim.claim_id for claim in selected],
        evidence_bases=[claim.basis for claim in selected] or ["unknown"],
        policy_eligible=status == "matched" and all(claim.policy_eligible for claim in selected),
        why=(
            None
            if status in {"matched", "not_matched"}
            else "action semantics are heuristic, unknown, or conflicting"
        ),
    )


def _missing_requirements(
    policy: ActionPolicyConfig,
    action: ActionFact,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}
    for path, expected in sorted(policy.require.items()):
        actual = _value_at_path(action, path)
        if actual is _MISSING_PATH:
            missing.append(
                {
                    "path": path,
                    "expected": expected,
                    "reason": "unknown_path",
                }
            )
            observed[path] = None
            continue
        if actual != expected:
            missing.append({"path": path, "expected": expected})
            observed[path] = actual
    return missing, observed


def _value_at_path(action: ActionFact, path: str) -> Any:
    aliases = {
        "approval.required": "approval_policy.required",
        "approval.threshold": "approval_policy.threshold",
        "scopes": "required_scopes",
    }
    path = aliases.get(path, path)
    current: Any = action
    parts = path.split(".")
    for index, part in enumerate(parts):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING_PATH
            current = current[part]
        else:
            if not hasattr(current, part):
                return _MISSING_PATH
            current = getattr(current, part)
        if current is None and index < len(parts) - 1:
            return _MISSING_PATH
    return current


def _finding(
    *,
    check_id: str,
    title: str,
    severity: Severity,
    action: ActionFact,
    agent_id: str,
    evidence: dict[str, Any],
    recommendation: str,
    blocks_release: bool,
    support: FindingSupport | None = None,
) -> Finding:
    support = support or finding_support(
        [
            predicate_evidence(
                "deterministic_action_rule",
                "matched",
                observed=action.action_id,
                confidence="high",
                evidence_bases=["protocol_structure"],
                policy_eligible=True,
            )
        ]
    )
    return Finding(
        check_id=check_id,
        title=title,
        severity=severity,
        category="action_surface",
        tool_id=action.tool_id,
        tool_name=action.tool_name,
        agent_id=agent_id,
        evidence=evidence,
        confidence=support.confidence,
        provenance_kind="static_declaration",
        source=SourceReference(
            type=action.source_type or "action_surface",
            ref=action.source_ref or action.action_id,
            location=action.source_location,
            path=action.source_path,
            start_line=action.source_start_line,
            end_line=action.source_end_line,
            start_column=action.source_start_column,
            pointer=action.source_pointer,
        ),
        recommendation=recommendation,
        blocks_release=blocks_release and support.blocking_eligible,
        support=support,
    )


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    by_key: dict[tuple[str, str, str], Finding] = {}
    for finding in findings:
        evidence_key = json.dumps(finding.evidence, sort_keys=True, default=str)
        by_key.setdefault(
            (finding.check_id, finding.tool_name or "", evidence_key),
            finding,
        )
    return [by_key[key] for key in sorted(by_key)]


def _severity_for_action(action: ActionFact) -> Severity:
    if set(action.risk_tags) & _CRITICAL_RISK_TAGS:
        return "critical"
    if "external_communication" in action.risk_tags:
        return "high"
    if "writes_data" in action.risk_tags or action.effect != "read":
        return "medium"
    return "info"


def _action_summary(action: ActionFact) -> dict[str, Any]:
    return {
        "tool_name": action.tool_name,
        "operation": action.operation,
        "effect": action.effect,
        "risk_tags": action.risk_tags,
        "required_scopes": action.required_scopes,
        "approval_policy": action.approval_policy.model_dump(mode="json"),
        "safeguards": action.safeguards.model_dump(mode="json"),
    }


def _annotation_bool(annotations: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = annotations.get(key)
        if isinstance(value, bool):
            return value
    return None


def _normalize_risk_tag_values(values: list[str]) -> list[str]:
    return sorted(
        {_RISK_TAG_MAP.get(value, value) for value in normalize_declared_strings(values)}
    )


def _normalize_token(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip())


def _normalize_operation(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_path(value: str) -> str:
    path = unquote(value.strip()) or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    path = re.sub(r"/+", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    path = re.sub(r"/:([A-Za-z_][A-Za-z0-9_]*)", r"/{\1}", path)
    return path


def _diff_base(reference: ActionSurfaceDiffReference | None) -> ToolSurfaceDiffBase:
    if reference is None:
        return ToolSurfaceDiffBase()
    return ToolSurfaceDiffBase(
        kind=reference.kind,
        path=reference.path,
        report_schema_version=reference.report_schema_version,
        baseline_schema_version=reference.baseline_schema_version,
    )


def _action_notes(
    reference: ActionSurfaceDiffReference | None,
) -> list[str]:
    if reference is None:
        return []
    notes = list(getattr(reference, "notes", ()) or ())
    if reference.facts is None:
        if reference.kind == "report":
            notes.append(
                "Reference report lacks action_surface_facts; action-surface diff disabled."
            )
        elif reference.kind == "baseline":
            notes.append(
                "Reference baseline lacks action_surface_facts; action-surface diff disabled."
            )
    return notes
