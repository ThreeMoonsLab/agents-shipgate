from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from agents_shipgate.config.schema import AgentsShipgateManifest, PolicyToolEntry
from agents_shipgate.core.baseline import BaselineFile
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.findings import _canonicalize_for_fingerprint
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.models import (
    AnthropicArtifacts,
    Finding,
    OpenAIApiArtifacts,
    ReadinessReport,
    Tool,
    ToolSurfaceControlChange,
    ToolSurfaceControlFact,
    ToolSurfaceDiff,
    ToolSurfaceDiffBase,
    ToolSurfaceDiffBaseKind,
    ToolSurfaceDiffSummary,
    ToolSurfaceFacts,
    ToolSurfaceFactScopeKind,
    ToolSurfaceFieldChange,
    ToolSurfaceFindingDeltaItem,
    ToolSurfaceFindingDeltas,
    ToolSurfaceHashes,
    ToolSurfaceHighRiskEffectChange,
    ToolSurfaceMetadataChange,
    ToolSurfacePolicyDrift,
    ToolSurfacePolicyFact,
    ToolSurfaceScopeChange,
    ToolSurfaceScopeFact,
    ToolSurfaceToolChange,
    ToolSurfaceToolFact,
)
from agents_shipgate.core.risk_hints import HIGH_RISK_TAGS, risk_tags

_METADATA_FIELDS = {"owner", "description", "auth_scopes", "extraction_confidence"}


@dataclass(frozen=True)
class ToolSurfaceDiffReference:
    kind: ToolSurfaceDiffBaseKind
    facts: ToolSurfaceFacts | None
    path: str | None = None
    report_schema_version: str | None = None
    baseline_schema_version: str | None = None
    findings: list[ToolSurfaceFindingDeltaItem] | None = None
    notes: tuple[str, ...] = ()


def build_tool_surface_facts(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    findings: list[Finding],
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
) -> ToolSurfaceFacts:
    del findings  # Reserved for future evidence projections.
    return ToolSurfaceFacts(
        tools=_tool_facts(tools),
        scopes=_scope_facts(manifest, tools),
        controls=_control_facts(manifest, api_artifacts, anthropic_artifacts),
        policies=_policy_facts(manifest, api_artifacts, anthropic_artifacts),
    )


def compute_tool_surface_diff(
    current: ToolSurfaceFacts,
    base: ToolSurfaceFacts | None,
    findings: list[Finding],
    *,
    reference: ToolSurfaceDiffReference | None = None,
) -> ToolSurfaceDiff:
    if base is None:
        finding_deltas = (
            _finding_deltas(findings, reference.findings)
            if reference
            else ToolSurfaceFindingDeltas()
        )
        diff_base = _diff_base(reference)
        notes = list(reference.notes) if reference else []
        if not notes:
            notes.append("No --diff-from report or v0.3 baseline snapshot was provided.")
        elif reference and reference.kind == "report":
            notes.append(
                "Finding deltas were computed from reference findings. Re-run "
                "the base scan with report_schema_version 0.10, or use a v0.3 "
                "baseline, to enable the full tool-surface diff."
            )
        elif reference and reference.kind == "baseline":
            notes.append(
                "Run `agents-shipgate baseline save` with v0.10 or newer to "
                "write a v0.3 baseline snapshot and enable the full "
                "tool-surface diff."
            )
        return ToolSurfaceDiff(
            enabled=False,
            base=diff_base,
            summary=_summary_from_diff_parts(finding_deltas=finding_deltas),
            finding_deltas=finding_deltas,
            notes=notes,
        )

    tool_changes = _diff_tools(current.tools, base.tools)
    high_risk_effects = _diff_high_risk_effects(current.tools, base.tools)
    scopes = _diff_scopes(current.scopes, base.scopes)
    controls = _diff_controls(current.controls, base.controls)
    metadata_changes = _metadata_changes(tool_changes)
    policy_drift = _diff_policies(current.policies, base.policies)
    finding_deltas = _finding_deltas(findings, reference.findings if reference else None)
    summary = _summary_from_diff_parts(
        tool_changes=tool_changes,
        high_risk_effects=high_risk_effects,
        scopes=scopes,
        controls=controls,
        metadata_changes=metadata_changes,
        policy_drift=policy_drift,
        finding_deltas=finding_deltas,
    )
    notes = ["Tool renames are reported as one removed tool plus one added tool."]
    if reference:
        notes.extend(reference.notes)
    return ToolSurfaceDiff(
        enabled=True,
        base=_diff_base(reference),
        summary=summary,
        tools=tool_changes,
        high_risk_effects=high_risk_effects,
        scopes=scopes,
        controls=controls,
        metadata_changes=metadata_changes,
        policy_drift=policy_drift,
        finding_deltas=finding_deltas,
        notes=notes,
    )


def _summary_from_diff_parts(
    *,
    tool_changes: list[ToolSurfaceToolChange] | None = None,
    high_risk_effects: list[ToolSurfaceHighRiskEffectChange] | None = None,
    scopes: list[ToolSurfaceScopeChange] | None = None,
    controls: list[ToolSurfaceControlChange] | None = None,
    metadata_changes: list[ToolSurfaceMetadataChange] | None = None,
    policy_drift: list[ToolSurfacePolicyDrift] | None = None,
    finding_deltas: ToolSurfaceFindingDeltas,
) -> ToolSurfaceDiffSummary:
    tool_changes = tool_changes or []
    high_risk_effects = high_risk_effects or []
    scopes = scopes or []
    controls = controls or []
    metadata_changes = metadata_changes or []
    policy_drift = policy_drift or []
    return ToolSurfaceDiffSummary(
        tools_added=sum(1 for item in tool_changes if item.kind == "added"),
        tools_removed=sum(1 for item in tool_changes if item.kind == "removed"),
        tools_changed=sum(1 for item in tool_changes if item.kind == "changed"),
        new_scopes=sum(1 for item in scopes if item.kind == "added"),
        removed_scopes=sum(1 for item in scopes if item.kind == "removed"),
        new_high_risk_effects=sum(
            1 for item in high_risk_effects if item.kind == "added"
        ),
        removed_high_risk_effects=sum(
            1 for item in high_risk_effects if item.kind == "removed"
        ),
        controls_added=sum(1 for item in controls if item.kind == "added"),
        controls_removed=sum(1 for item in controls if item.kind == "removed"),
        metadata_changes=len(metadata_changes),
        policy_drift_items=len(policy_drift),
        new_findings=len(finding_deltas.new_findings),
        resolved_findings=len(finding_deltas.resolved_findings),
        unchanged_findings=len(finding_deltas.unchanged_findings),
        accepted_debt=len(finding_deltas.accepted_debt),
    )


def load_tool_surface_diff_reference(
    path: Path,
    *,
    display_path: str | None = None,
) -> ToolSurfaceDiffReference:
    if not path.exists():
        raise InputParseError(f"Diff reference file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputParseError(f"Invalid diff reference file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InputParseError(f"Invalid diff reference file {path}: expected object")
    shown = display_path or str(path)
    if "report_schema_version" in payload:
        return _reference_from_report_payload(payload, shown)
    if "schema_version" in payload and "source_report_run_id" in payload:
        return _reference_from_baseline_payload(payload, shown)
    raise InputParseError(
        f"Invalid diff reference file {path}: expected report.json or baseline JSON"
    )


def reference_from_baseline(
    baseline: BaselineFile,
    *,
    display_path: str | None = None,
) -> ToolSurfaceDiffReference:
    return _reference_from_baseline(baseline, display_path)


def disabled_tool_surface_diff(
    note: str,
    *,
    base: ToolSurfaceDiffBase | None = None,
) -> ToolSurfaceDiff:
    return ToolSurfaceDiff(enabled=False, base=base or ToolSurfaceDiffBase(), notes=[note])


def _tool_facts(tools: list[Tool]) -> list[ToolSurfaceToolFact]:
    return [
        ToolSurfaceToolFact(
            name=tool.name,
            source_type=tool.source_type,
            source_id=tool.source_id,
            source_ref=tool.source_ref,
            risk_tags=risk_tags(tool),
            auth_scopes=sorted(set(tool.auth.scopes)),
            owner=tool.owner,
            extraction_confidence=tool.extraction_confidence,
            has_description=bool((tool.description or "").strip()),
            hashes=ToolSurfaceHashes(
                source_ref=_stable_hash(tool.source_ref),
                description=_stable_hash((tool.description or "").strip()),
                input_schema=_stable_hash(tool.input_schema),
                output_schema=_stable_hash(tool.output_schema),
                parameters=_stable_hash(_parameter_facts(tool)),
                annotations=_stable_hash(tool.annotations),
            ),
        )
        for tool in sorted(tools, key=lambda item: item.name)
    ]


def _parameter_facts(tool: Tool) -> list[dict[str, Any]]:
    return [
        parameter.model_dump(mode="json", exclude_none=True)
        for parameter in sorted(tool.parameters, key=lambda item: item.name)
    ]


def _scope_facts(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
) -> list[ToolSurfaceScopeFact]:
    by_scope: dict[tuple[ToolSurfaceFactScopeKind, str], set[str]] = {}
    for tool in tools:
        for scope in tool.auth.scopes:
            if not scope:
                continue
            key: tuple[ToolSurfaceFactScopeKind, str] = ("tool_required", scope)
            by_scope.setdefault(key, set()).add(tool.name)
    for scope in manifest.permissions.scopes:
        if not scope:
            continue
        key = ("manifest_declared", scope)
        by_scope.setdefault(key, set())
    return [
        ToolSurfaceScopeFact(
            kind=kind,
            scope=scope,
            tool_names=sorted(tool_names),
            broad=is_broad_scope(scope),
        )
        for (kind, scope), tool_names in sorted(by_scope.items())
    ]


def _control_facts(
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
) -> list[ToolSurfaceControlFact]:
    facts: list[ToolSurfaceControlFact] = []
    facts.extend(
        _manifest_control_facts(
            "approval_policy",
            "manifest",
            manifest.policies.require_approval_for_tools,
        )
    )
    facts.extend(
        _manifest_control_facts(
            "confirmation_policy",
            "manifest",
            manifest.policies.require_confirmation_for_tools,
        )
    )
    facts.extend(
        _manifest_control_facts(
            "idempotency_evidence",
            "manifest",
            manifest.policies.require_idempotency_for_tools,
        )
    )
    if api_artifacts:
        facts.extend(
            _artifact_control_facts(
                "approval_policy", "openai_api", api_artifacts.approval_tools()
            )
        )
        facts.extend(
            _artifact_control_facts(
                "confirmation_policy",
                "openai_api",
                api_artifacts.confirmation_tools(),
            )
        )
        facts.extend(
            _artifact_control_facts(
                "idempotency_evidence",
                "openai_api",
                api_artifacts.idempotency_tools(),
            )
        )
    if anthropic_artifacts:
        facts.extend(
            _artifact_control_facts(
                "approval_policy",
                "anthropic_api",
                anthropic_artifacts.approval_tools(),
            )
        )
        facts.extend(
            _artifact_control_facts(
                "confirmation_policy",
                "anthropic_api",
                anthropic_artifacts.confirmation_tools(),
            )
        )
        facts.extend(
            _artifact_control_facts(
                "idempotency_evidence",
                "anthropic_api",
                anthropic_artifacts.idempotency_tools(),
            )
        )
    return sorted(facts, key=_control_key)


def _manifest_control_facts(
    kind: Literal["approval_policy", "confirmation_policy", "idempotency_evidence"],
    source: str,
    entries: list[PolicyToolEntry],
) -> list[ToolSurfaceControlFact]:
    return [
        ToolSurfaceControlFact(
            kind=kind,
            tool=entry.tool,
            source=source,
            reason=entry.reason,
        )
        for entry in entries
    ]


def _artifact_control_facts(
    kind: Literal["approval_policy", "confirmation_policy", "idempotency_evidence"],
    source: str,
    tools: set[str],
) -> list[ToolSurfaceControlFact]:
    return [
        ToolSurfaceControlFact(kind=kind, tool=tool, source=source)
        for tool in sorted(tools)
    ]


def _policy_facts(
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
) -> list[ToolSurfacePolicyFact]:
    facts: list[ToolSurfacePolicyFact] = []
    facts.extend(
        _policy_entry_facts(
            "policy.approval", manifest.policies.require_approval_for_tools
        )
    )
    facts.extend(
        _policy_entry_facts(
            "policy.confirmation",
            manifest.policies.require_confirmation_for_tools,
        )
    )
    facts.extend(
        _policy_entry_facts(
            "policy.idempotency",
            manifest.policies.require_idempotency_for_tools,
        )
    )
    for suppression in manifest.checks.ignore:
        key = f"{suppression.check_id}:{suppression.tool or '*'}"
        facts.append(
            ToolSurfacePolicyFact(
                kind="suppression",
                key=key,
                value_hash=_stable_hash(suppression.model_dump(mode="json")),
                summary=suppression.reason,
            )
        )
    for check_id, severity in sorted(manifest.checks.severity_overrides.items()):
        facts.append(
            ToolSurfacePolicyFact(
                kind="severity_override",
                key=check_id,
                value_hash=_stable_hash(severity),
                summary=severity,
            )
        )
    for tool_name, override in sorted(manifest.risk_overrides.tools.items()):
        facts.append(
            ToolSurfacePolicyFact(
                kind="risk_override",
                key=tool_name,
                value_hash=_stable_hash(override.model_dump(mode="json")),
                summary=", ".join(override.tags + override.remove_tags) or override.reason,
            )
        )
    if api_artifacts:
        facts.extend(_artifact_policy_facts("openai_api.policy", api_artifacts.policy_rules))
    if anthropic_artifacts:
        facts.extend(
            _artifact_policy_facts(
                "anthropic_api.policy", anthropic_artifacts.policy_rules
            )
        )
    return sorted(facts, key=lambda item: (item.kind, item.key))


def _policy_entry_facts(
    kind: str,
    entries: list[PolicyToolEntry],
) -> list[ToolSurfacePolicyFact]:
    return [
        ToolSurfacePolicyFact(
            kind=kind,
            key=entry.tool,
            value_hash=_stable_hash(entry.model_dump(mode="json")),
            summary=entry.reason,
        )
        for entry in entries
    ]


def _artifact_policy_facts(kind: str, rules: dict[str, Any]) -> list[ToolSurfacePolicyFact]:
    facts: list[ToolSurfacePolicyFact] = []
    for key, value in sorted(rules.items()):
        facts.append(
            ToolSurfacePolicyFact(
                kind=kind,
                key=str(key),
                value_hash=_stable_hash(value),
                summary=_summarize_value(value),
            )
        )
    return facts


def _diff_tools(
    current: list[ToolSurfaceToolFact],
    base: list[ToolSurfaceToolFact],
) -> list[ToolSurfaceToolChange]:
    current_by_name = {tool.name: tool for tool in current}
    base_by_name = {tool.name: tool for tool in base}
    changes: list[ToolSurfaceToolChange] = []
    for name in sorted(current_by_name.keys() - base_by_name.keys()):
        tool = current_by_name[name]
        changes.append(
            ToolSurfaceToolChange(
                kind="added",
                name=name,
                source_type=tool.source_type,
                source_id=tool.source_id,
            )
        )
    for name in sorted(base_by_name.keys() - current_by_name.keys()):
        tool = base_by_name[name]
        changes.append(
            ToolSurfaceToolChange(
                kind="removed",
                name=name,
                source_type=tool.source_type,
                source_id=tool.source_id,
            )
        )
    for name in sorted(current_by_name.keys() & base_by_name.keys()):
        field_changes = _tool_field_changes(current_by_name[name], base_by_name[name])
        if field_changes:
            tool = current_by_name[name]
            changes.append(
                ToolSurfaceToolChange(
                    kind="changed",
                    name=name,
                    source_type=tool.source_type,
                    source_id=tool.source_id,
                    changes=field_changes,
                )
            )
    return changes


def _tool_field_changes(
    current: ToolSurfaceToolFact,
    base: ToolSurfaceToolFact,
) -> list[ToolSurfaceFieldChange]:
    pairs: list[tuple[str, Any, Any]] = [
        (
            "source",
            {
                "source_type": base.source_type,
                "source_id": base.source_id,
                "source_ref": base.source_ref,
            },
            {
                "source_type": current.source_type,
                "source_id": current.source_id,
                "source_ref": current.source_ref,
            },
        ),
        ("risk_tags", base.risk_tags, current.risk_tags),
        ("auth_scopes", base.auth_scopes, current.auth_scopes),
        ("owner", base.owner, current.owner),
        ("extraction_confidence", base.extraction_confidence, current.extraction_confidence),
        ("description", base.hashes.description, current.hashes.description),
        ("input_schema", base.hashes.input_schema, current.hashes.input_schema),
        ("output_schema", base.hashes.output_schema, current.hashes.output_schema),
        ("parameters", base.hashes.parameters, current.hashes.parameters),
        ("annotations", base.hashes.annotations, current.hashes.annotations),
    ]
    return [
        ToolSurfaceFieldChange(field=field, before=before, after=after)
        for field, before, after in pairs
        if before != after
    ]


def _diff_high_risk_effects(
    current: list[ToolSurfaceToolFact],
    base: list[ToolSurfaceToolFact],
) -> list[ToolSurfaceHighRiskEffectChange]:
    current_items = _high_risk_items(current)
    base_items = _high_risk_items(base)
    changes: list[ToolSurfaceHighRiskEffectChange] = []
    for tool, tag in sorted(current_items - base_items):
        changes.append(ToolSurfaceHighRiskEffectChange(kind="added", tool=tool, tag=tag))
    for tool, tag in sorted(base_items - current_items):
        changes.append(
            ToolSurfaceHighRiskEffectChange(kind="removed", tool=tool, tag=tag)
        )
    return changes


def _high_risk_items(tools: list[ToolSurfaceToolFact]) -> set[tuple[str, str]]:
    return {
        (tool.name, tag)
        for tool in tools
        for tag in tool.risk_tags
        if tag in HIGH_RISK_TAGS
    }


def _diff_scopes(
    current: list[ToolSurfaceScopeFact],
    base: list[ToolSurfaceScopeFact],
) -> list[ToolSurfaceScopeChange]:
    current_by_key = {_scope_key(item): item for item in current}
    base_by_key = {_scope_key(item): item for item in base}
    changes: list[ToolSurfaceScopeChange] = []
    for key in sorted(current_by_key.keys() - base_by_key.keys()):
        changes.append(_scope_change("added", current_by_key[key]))
    for key in sorted(base_by_key.keys() - current_by_key.keys()):
        changes.append(_scope_change("removed", base_by_key[key]))
    for key in sorted(current_by_key.keys() & base_by_key.keys()):
        current_item = current_by_key[key]
        base_item = base_by_key[key]
        if (
            current_item.tool_names != base_item.tool_names
            or current_item.broad != base_item.broad
        ):
            changes.append(_scope_change("changed", current_item))
    return changes


def _scope_change(
    kind: Literal["added", "removed", "changed"],
    item: ToolSurfaceScopeFact,
) -> ToolSurfaceScopeChange:
    return ToolSurfaceScopeChange(
        kind=kind,
        scope=item.scope,
        scope_kind=item.kind,
        tool_names=item.tool_names,
        broad=item.broad,
    )


def _diff_controls(
    current: list[ToolSurfaceControlFact],
    base: list[ToolSurfaceControlFact],
) -> list[ToolSurfaceControlChange]:
    current_by_key = {_control_key(item): item for item in current}
    base_by_key = {_control_key(item): item for item in base}
    changes: list[ToolSurfaceControlChange] = []
    for key in sorted(current_by_key.keys() - base_by_key.keys()):
        changes.append(_control_change("added", current_by_key[key]))
    for key in sorted(base_by_key.keys() - current_by_key.keys()):
        changes.append(_control_change("removed", base_by_key[key]))
    for key in sorted(current_by_key.keys() & base_by_key.keys()):
        if current_by_key[key].reason != base_by_key[key].reason:
            changes.append(_control_change("changed", current_by_key[key]))
    return changes


def _control_change(
    kind: Literal["added", "removed", "changed"],
    item: ToolSurfaceControlFact,
) -> ToolSurfaceControlChange:
    return ToolSurfaceControlChange(
        kind=kind,
        control=item.kind,
        tool=item.tool,
        source=item.source,
        reason=item.reason,
    )


def _metadata_changes(
    tool_changes: list[ToolSurfaceToolChange],
) -> list[ToolSurfaceMetadataChange]:
    items: list[ToolSurfaceMetadataChange] = []
    for tool_change in tool_changes:
        if tool_change.kind != "changed":
            continue
        for change in tool_change.changes:
            if change.field not in _METADATA_FIELDS:
                continue
            items.append(
                ToolSurfaceMetadataChange(
                    kind=_change_kind(change.before, change.after),
                    tool=tool_change.name,
                    metadata=change.field,
                    before=change.before,
                    after=change.after,
                )
            )
    return items


def _diff_policies(
    current: list[ToolSurfacePolicyFact],
    base: list[ToolSurfacePolicyFact],
) -> list[ToolSurfacePolicyDrift]:
    current_by_key = {_policy_key(item): item for item in current}
    base_by_key = {_policy_key(item): item for item in base}
    changes: list[ToolSurfacePolicyDrift] = []
    for key in sorted(current_by_key.keys() - base_by_key.keys()):
        item = current_by_key[key]
        changes.append(
            ToolSurfacePolicyDrift(
                kind="added",
                policy_kind=item.kind,
                key=item.key,
                after_hash=item.value_hash,
                after_summary=item.summary,
            )
        )
    for key in sorted(base_by_key.keys() - current_by_key.keys()):
        item = base_by_key[key]
        changes.append(
            ToolSurfacePolicyDrift(
                kind="removed",
                policy_kind=item.kind,
                key=item.key,
                before_hash=item.value_hash,
                before_summary=item.summary,
            )
        )
    for key in sorted(current_by_key.keys() & base_by_key.keys()):
        current_item = current_by_key[key]
        base_item = base_by_key[key]
        if current_item.value_hash == base_item.value_hash:
            continue
        changes.append(
            ToolSurfacePolicyDrift(
                kind="changed",
                policy_kind=current_item.kind,
                key=current_item.key,
                before_hash=base_item.value_hash,
                after_hash=current_item.value_hash,
                before_summary=base_item.summary,
                after_summary=current_item.summary,
            )
        )
    return changes


def _finding_deltas(
    findings: list[Finding],
    base_findings: list[ToolSurfaceFindingDeltaItem] | None,
) -> ToolSurfaceFindingDeltas:
    current_items = {
        item.fingerprint: item
        for item in (_finding_item(finding) for finding in findings)
        if item is not None
    }
    base_items = {item.fingerprint: item for item in base_findings or []}
    accepted = {
        fingerprint: item
        for fingerprint, item in current_items.items()
        if item.baseline_status == "matched"
    }
    new_keys = sorted((current_items.keys() - base_items.keys()) - accepted.keys())
    resolved_keys = sorted(base_items.keys() - current_items.keys())
    unchanged_keys = sorted((current_items.keys() & base_items.keys()) - accepted.keys())
    return ToolSurfaceFindingDeltas(
        new_findings=[current_items[key] for key in new_keys],
        resolved_findings=[base_items[key] for key in resolved_keys],
        unchanged_findings=[current_items[key] for key in unchanged_keys],
        accepted_debt=[accepted[key] for key in sorted(accepted)],
    )


def _finding_item(finding: Finding) -> ToolSurfaceFindingDeltaItem | None:
    if finding.suppressed:
        return None
    fingerprint = finding.fingerprint or finding.id
    if not fingerprint:
        return None
    return ToolSurfaceFindingDeltaItem(
        fingerprint=fingerprint,
        check_id=finding.check_id,
        severity=finding.severity,
        title=finding.title,
        tool_name=finding.tool_name,
        baseline_status=finding.baseline_status,
    )


def _reference_from_report_payload(
    payload: dict[str, Any],
    display_path: str,
) -> ToolSurfaceDiffReference:
    try:
        report = ReadinessReport.model_validate(payload)
    except ValidationError as exc:
        raise InputParseError(f"Invalid diff report {display_path}: {exc}") from exc
    facts = report.tool_surface_facts if "tool_surface_facts" in payload else None
    notes: list[str] = []
    if facts is None:
        notes.append(
            "Reference report is pre-v0.10 or otherwise lacks "
            "tool_surface_facts; surface diff disabled."
        )
    return ToolSurfaceDiffReference(
        kind="report",
        path=display_path,
        facts=facts,
        report_schema_version=report.report_schema_version,
        findings=[
            item
            for item in (_finding_item(finding) for finding in report.findings)
            if item is not None
        ],
        notes=tuple(notes),
    )


def _reference_from_baseline_payload(
    payload: dict[str, Any],
    display_path: str,
) -> ToolSurfaceDiffReference:
    try:
        baseline = BaselineFile.model_validate(payload)
    except ValidationError as exc:
        raise InputParseError(f"Invalid diff baseline {display_path}: {exc}") from exc
    return _reference_from_baseline(baseline, display_path)


def _reference_from_baseline(
    baseline: BaselineFile,
    display_path: str | None,
) -> ToolSurfaceDiffReference:
    notes: list[str] = list(baseline.notes)
    if baseline.schema_version == "0.2":
        notes.append("Baseline schema 0.2 has no tool_surface_facts; surface diff disabled.")
    elif baseline.tool_surface_facts is None:
        notes.append("Baseline has no tool_surface_facts; surface diff disabled.")
    return ToolSurfaceDiffReference(
        kind="baseline",
        path=display_path,
        facts=baseline.tool_surface_facts,
        baseline_schema_version=baseline.schema_version,
        findings=[
            ToolSurfaceFindingDeltaItem(
                fingerprint=finding.fingerprint,
                check_id=finding.check_id,
                severity=finding.severity,
                title=finding.title,
                tool_name=finding.tool_name,
            )
            for finding in baseline.findings
        ],
        notes=tuple(notes),
    )


def _diff_base(reference: ToolSurfaceDiffReference | None) -> ToolSurfaceDiffBase:
    if reference is None:
        return ToolSurfaceDiffBase()
    return ToolSurfaceDiffBase(
        kind=reference.kind,
        path=reference.path,
        report_schema_version=reference.report_schema_version,
        baseline_schema_version=reference.baseline_schema_version,
    )


def _stable_hash(value: Any) -> str:
    canonical = _canonicalize_for_fingerprint(value)
    payload = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _summarize_value(value: Any) -> str:
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return f"{len(value)} key(s)"
    if value is None:
        return "none"
    return str(value)


def _scope_key(item: ToolSurfaceScopeFact) -> tuple[str, str]:
    return item.kind, item.scope


def _control_key(item: ToolSurfaceControlFact) -> tuple[str, str, str]:
    return item.kind, item.tool, item.source


def _policy_key(item: ToolSurfacePolicyFact) -> tuple[str, str]:
    return item.kind, item.key


def _change_kind(before: Any, after: Any) -> Literal["added", "removed", "changed"]:
    if _empty(before) and not _empty(after):
        return "added"
    if not _empty(before) and _empty(after):
        return "removed"
    return "changed"


def _empty(value: Any) -> bool:
    return value in (None, "", [], {}, ())
