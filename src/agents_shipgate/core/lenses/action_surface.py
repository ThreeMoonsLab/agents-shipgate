from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from agents_shipgate.core.action_semantics import ACTION_EFFECT_RANK
from agents_shipgate.core.domain import Action, Scope, Tool
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference, _stable_hash
from agents_shipgate.core.risk_hints import (
    derive_side_effect,
    is_effectively_read_only,
    risk_tags,
)
from agents_shipgate.schemas.common import (
    Severity,
    SourceReference,
)
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    ActionPolicyConfig,
    AgentsShipgateManifest,
)
from agents_shipgate.schemas.report import Finding
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
    declarations = {entry.tool: entry for entry in manifest.action_surface.actions}
    actions = [
        _action_from_tool(
            manifest,
            agent_id=agent_id,
            tool=tool,
            declaration=declarations.get(tool.name),
        )
        for tool in sorted(tools, key=lambda item: item.name)
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
            entry.tool
            for entry in manifest.action_surface.actions
            if entry.id or entry.provider or entry.operation
        }
        warnings.extend(
            _disambiguate_duplicate_action_ids(
                actions, explicit_tool_names=explicit_tool_names
            )
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
) -> ActionSurfaceDiff:
    if base is None:
        notes = _action_notes(reference)
        if not notes:
            notes.append("No action-surface comparison source was provided.")
        return ActionSurfaceDiff(
            enabled=False,
            base=_diff_base(reference),
            notes=notes,
        )

    current_by_id = _actions_by_id(current.actions)
    base_by_id = _actions_by_id(base.actions)
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


def evaluate_action_surface_policies(
    manifest: AgentsShipgateManifest,
    facts: ActionSurfaceFacts,
    diff: ActionSurfaceDiff,
    *,
    agent_id: str,
    tools: list[Tool] | None = None,
) -> list[Finding]:
    """Evaluate action-surface policies for a current action snapshot.

    ``tools`` is optional for callers that only have serialized action facts.
    When omitted, declaration-downgrade checks that compare declarations with
    inferred tool metadata are skipped; the scan pipeline always passes tools.
    """
    findings: list[Finding] = []
    by_action = {action.action_id: action for action in facts.actions}
    if manifest.action_surface.require_explicit_actions:
        declared_tools = {entry.tool for entry in manifest.action_surface.actions}
        for action in facts.actions:
            if action.tool_name in declared_tools:
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

    findings.extend(_builtin_policy_findings(diff, by_action, agent_id=agent_id))
    for policy in manifest.action_surface.policies:
        for action in facts.actions:
            if not _policy_matches(policy, action):
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
                        or (
                            f"Action surface policy {policy.id} failed for "
                            f"{action.tool_name}"
                        ),
                        severity=policy.severity,
                        action=action,
                        agent_id=agent_id,
                        evidence=evidence,
                        recommendation=policy.recommendation
                        or (
                            f"Satisfy action surface policy {policy.id} for "
                            f"{action.tool_name}."
                        ),
                        blocks_release=policy.block,
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
    action_id = (
        declaration.id
        if declaration and declaration.id
        else f"{agent_id}:{tool.source_type}:{provider}:{operation}"
    )
    inferred_tags = _normalized_risk_tags(tool)
    risk_tag_values = (
        _normalize_risk_tag_values(declaration.risk_tags)
        if declaration and declaration.risk_tags
        else inferred_tags
    )
    scope_strings = (
        _normalize_strings(declaration.scopes)
        if declaration and declaration.scopes
        else _normalize_strings(tool.auth.scopes)
    )
    # Wire-format effect (string) follows the declaration override, then
    # falls back to the legacy ``_infer_effect`` so byte-identical hashes
    # are preserved during the migration window. The typed ``SideEffect``
    # built below carries the same effect plus structural fields.
    effect = (
        declaration.effect
        if declaration and declaration.effect
        else _infer_effect(tool, risk_tag_values)
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
        parameters_for_hash=[
            parameter.model_dump(mode="json") for parameter in tool.parameters
        ],
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
    schema_hash = public_action_schema_hash(
        action.input_fields, action.required_input_fields
    )
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
        }
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
    colliding = {
        action_id: group for action_id, group in by_id.items() if len(group) > 1
    }

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
            action.hashes = action.hashes.model_copy(
                update={"identity_hash": _stable_hash(new_id)}
            )
    return warnings


def _provider(tool: Tool, declaration: ActionDeclarationConfig | None) -> str:
    if declaration and declaration.provider:
        return _normalize_token(declaration.provider)
    if tool.source_id:
        return _normalize_token(tool.source_id)
    return _normalize_token(tool.source_type)


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
    fact = ActionApprovalFact(required=tool.name in manifest.policies.approval_tools())
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
            tool.name in manifest.policies.idempotency_tools()
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
    declarations = {entry.tool: entry for entry in manifest.action_surface.actions}
    by_tool = {action.tool_name: action for action in facts.actions}
    findings: list[Finding] = []
    for tool in sorted(tools, key=lambda item: item.name):
        declaration = declarations.get(tool.name)
        action = by_tool.get(tool.name)
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
            inferred_effect = _strongest_effect(
                [
                    _infer_effect(tool, _normalized_risk_tags(tool)),
                    _infer_effect(
                        tool,
                        _normalize_risk_tag_values(declaration.risk_tags)
                        if declaration.risk_tags
                        else _normalized_risk_tags(tool),
                    ),
                ]
            )
            if ACTION_EFFECT_RANK[declaration.effect] < ACTION_EFFECT_RANK[inferred_effect]:
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
                            "inferred_effect": inferred_effect,
                            "declared_effect": declaration.effect,
                        },
                        recommendation=(
                            "Set action_surface.actions[].effect for "
                            f"{action.tool_name} to {inferred_effect}, or remove "
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
    inherited_approval = ActionApprovalFact(
        required=tool.name in manifest.policies.approval_tools()
    )
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
            tool.name in manifest.policies.idempotency_tools()
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


def _strongest_effect(effects: list[str]) -> str:
    return max(effects, key=lambda item: ACTION_EFFECT_RANK[item])


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
    tag_set = set(tags)
    if "destructive" in tag_set:
        return "destructive"
    if "financial_write" in tag_set:
        return "financial_write"
    if "external_communication" in tag_set:
        return "external_communication"
    if "production_ops" in tag_set:
        return "production_operation"
    if "code_execution" in tag_set:
        return "code_execution"
    if "identity_access" in tag_set:
        return "identity_access"
    if "privileged_data" in tag_set:
        return "privileged_data_access"
    if "unknown_side_effect" in tag_set:
        return "write"
    if "writes_data" in tag_set:
        return "write"
    method = str(tool.annotations.get("httpMethod") or "").upper()
    if method in {"POST", "PUT", "PATCH"}:
        return "write"
    if method == "DELETE":
        return "destructive"
    return "read"


def _action_added_change(action: ActionFact) -> ActionSurfaceChange:
    severity = _severity_for_action(action)
    return ActionSurfaceChange(
        type="ACTION_ADDED",
        action_id=action.action_id,
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
        if getattr(base.safeguards, field) is True and getattr(current.safeguards, field) is not True:
            changes.append(
                _change(
                    "SAFEGUARD_REMOVED",
                    current,
                    "critical" if field == "rollback" and current.effect == "destructive" else "high",
                    f"Action safeguard removed: {field}.",
                    before=base.safeguards.model_dump(mode="json"),
                    after=current.safeguards.model_dump(mode="json"),
                    removed=[field],
                )
            )
    added_fields = sorted(set(current.input_fields) - set(base.input_fields))
    added_required = sorted(
        set(current.required_input_fields) - set(base.required_input_fields)
    )
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
        effect_escalations=sum(
            1 for change in modified if change.type == "EFFECT_ESCALATED"
        ),
        risk_tags_added=sum(1 for change in modified if change.type == "RISK_TAG_ADDED"),
        approvals_removed=sum(
            1 for change in modified if change.type == "APPROVAL_REMOVED"
        ),
        safeguards_removed=sum(
            1 for change in modified if change.type == "SAFEGUARD_REMOVED"
        ),
        input_schema_expansions=sum(
            1 for change in modified if change.type == "INPUT_SCHEMA_EXPANDED"
        ),
    )


def _builtin_policy_findings(
    diff: ActionSurfaceDiff,
    by_action: dict[str, ActionFact],
    *,
    agent_id: str,
) -> list[Finding]:
    if not diff.enabled:
        return []
    findings: list[Finding] = []
    for change in diff.added:
        action = by_action.get(change.action_id)
        if action is None:
            continue
        findings.extend(_added_action_policy_findings(action, agent_id=agent_id))
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


def _added_action_policy_findings(action: ActionFact, *, agent_id: str) -> list[Finding]:
    findings: list[Finding] = []
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
    if "financial_write" in action.risk_tags or action.effect == "financial_write":
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
                    title=f"{action.tool_name} adds financial write capability without required controls",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"action_id": action.action_id, "missing": missing},
                    recommendation=(
                        "Declare approval.required, safeguards.audit_log, and "
                        "safeguards.idempotency for this financial write action."
                    ),
                    blocks_release=True,
                )
            )
    if (
        "external_communication" in action.risk_tags
        or action.effect == "external_communication"
    ) and action.safeguards.audit_log is not True:
        findings.append(
            _finding(
                check_id="SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
                title=f"{action.tool_name} adds external communication without audit evidence",
                severity="high",
                action=action,
                agent_id=agent_id,
                evidence={"action_id": action.action_id, "missing": ["safeguards.audit_log"]},
                recommendation="Declare safeguards.audit_log for this external communication action.",
                blocks_release=True,
            )
        )
    if "destructive" in action.risk_tags or action.effect == "destructive":
        missing = _missing_builtin_requirements(
            action,
            {
                "approval.required": True,
                "safeguards.rollback": True,
            },
        )
        if missing:
            findings.append(
                _finding(
                    check_id="SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
                    title=f"{action.tool_name} adds destructive capability without rollback controls",
                    severity="critical",
                    action=action,
                    agent_id=agent_id,
                    evidence={"action_id": action.action_id, "missing": missing},
                    recommendation="Declare approval.required and safeguards.rollback for this destructive action.",
                    blocks_release=True,
                )
            )
    return findings


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


def _policy_matches(policy: ActionPolicyConfig, action: ActionFact) -> bool:
    match = policy.match
    if match.action_ids and action.action_id not in match.action_ids:
        return False
    if match.tools and action.tool_name not in match.tools:
        return False
    if match.effects and action.effect not in match.effects:
        return False
    if match.risk_tags and not set(_normalize_risk_tag_values(match.risk_tags)).intersection(
        action.risk_tags
    ):
        return False
    if match.scopes and not set(match.scopes).intersection(action.required_scopes):
        return False
    return True


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
) -> Finding:
    return Finding(
        check_id=check_id,
        title=title,
        severity=severity,
        category="action_surface",
        tool_id=action.tool_id,
        tool_name=action.tool_name,
        agent_id=agent_id,
        evidence=evidence,
        confidence="high",
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
        blocks_release=blocks_release,
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


def _normalize_strings(values: list[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _normalize_risk_tag_values(values: list[str]) -> list[str]:
    return sorted(
        {
            _RISK_TAG_MAP.get(value, value)
            for value in _normalize_strings(values)
        }
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
            notes.append("Reference report lacks action_surface_facts; action-surface diff disabled.")
        elif reference.kind == "baseline":
            notes.append("Reference baseline lacks action_surface_facts; action-surface diff disabled.")
    return notes
