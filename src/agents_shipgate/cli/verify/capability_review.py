from __future__ import annotations

from collections.abc import Iterable

from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceChange,
    ToolSurfaceControlChange,
    ToolSurfaceHighRiskEffectChange,
    ToolSurfaceMetadataChange,
    ToolSurfacePolicyDrift,
    ToolSurfaceScopeChange,
    ToolSurfaceToolChange,
)
from agents_shipgate.schemas.verifier import (
    CapabilityChangeBucket,
    CapabilityReleaseImpact,
    VerifierCapabilityChange,
    VerifierCapabilityReview,
)

TRUST_ROOT_CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
POLICY_WEAKENING_CHECK_IDS = frozenset(
    {
        "SHIP-VERIFY-POLICY-WEAKENED",
        "SHIP-VERIFY-CI-GATE-REMOVED",
        "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED",
        "SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED",
        "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT",
    }
)

_IMPACT_ORDER = {
    "blocks_release": 0,
    "insufficient_evidence": 1,
    "review_required": 2,
    "informational": 3,
    "none": 4,
}
_BUCKET_ORDER = {"added": 0, "modified": 1, "removed": 2}


def build_capability_review(
    report: ReadinessReport,
    *,
    top_limit: int = 5,
) -> VerifierCapabilityReview:
    """Project report diffs/findings into a compact PR-review rollup.

    The projection is intentionally derived from existing report surfaces:
    no release decision is recomputed here.
    """

    active_findings = [finding for finding in report.findings if not finding.suppressed]
    blockers, review_items = _release_membership(report)
    changes: list[VerifierCapabilityChange] = []

    for finding in active_findings:
        if finding.check_id == TRUST_ROOT_CHECK_ID:
            changes.append(_trust_root_change(finding))
        elif finding.check_id in POLICY_WEAKENING_CHECK_IDS:
            changes.append(_policy_weakening_change(finding))

    if report.action_surface_diff.enabled:
        changes.extend(
            _action_change(
                change,
                bucket="added",
                active_findings=active_findings,
                blockers=blockers,
                review_items=review_items,
            )
            for change in report.action_surface_diff.added
        )
        changes.extend(
            _action_change(
                change,
                bucket="removed",
                active_findings=active_findings,
                blockers=blockers,
                review_items=review_items,
            )
            for change in report.action_surface_diff.removed
        )
        changes.extend(
            _action_change(
                change,
                bucket="modified",
                active_findings=active_findings,
                blockers=blockers,
                review_items=review_items,
            )
            for change in report.action_surface_diff.modified
        )

    if report.tool_surface_diff.enabled:
        changes.extend(_tool_change(change) for change in report.tool_surface_diff.tools)
        changes.extend(
            _high_risk_effect_change(change)
            for change in report.tool_surface_diff.high_risk_effects
        )
        changes.extend(_scope_change(change) for change in report.tool_surface_diff.scopes)
        changes.extend(
            _control_change(change) for change in report.tool_surface_diff.controls
        )
        changes.extend(
            _metadata_change(change)
            for change in report.tool_surface_diff.metadata_changes
        )
        changes.extend(
            _policy_drift_change(change)
            for change in report.tool_surface_diff.policy_drift
        )

    counts = {
        "added": sum(1 for change in changes if change.change_bucket == "added"),
        "modified": sum(1 for change in changes if change.change_bucket == "modified"),
        "removed": sum(1 for change in changes if change.change_bucket == "removed"),
    }
    notes: list[str] = []
    if not report.action_surface_diff.enabled and report.action_surface_diff.notes:
        notes.append(report.action_surface_diff.notes[0])
    if not report.tool_surface_diff.enabled and report.tool_surface_diff.notes:
        notes.append(report.tool_surface_diff.notes[0])

    top_changes = sorted(
        changes,
        key=lambda change: (
            _IMPACT_ORDER.get(change.impact, 99),
            _BUCKET_ORDER.get(change.change_bucket, 99),
            change.change_type,
            change.subject,
            change.id,
        ),
    )[: max(0, top_limit)]
    return VerifierCapabilityReview(
        added=counts["added"],
        modified=counts["modified"],
        removed=counts["removed"],
        trust_root_touched=any(f.check_id == TRUST_ROOT_CHECK_ID for f in active_findings),
        policy_weakened=any(
            f.check_id in POLICY_WEAKENING_CHECK_IDS for f in active_findings
        ),
        top_changes=top_changes,
        notes=notes,
    )


def _release_membership(
    report: ReadinessReport,
) -> tuple[set[str], set[str]]:
    decision = report.release_decision
    if decision is None:
        return set(), set()
    blockers = _identity_set(decision.blockers)
    review_items = _identity_set(decision.review_items)
    return blockers, review_items


def _identity_set(items: Iterable[object]) -> set[str]:
    values: set[str] = set()
    for item in items:
        for attr in ("id", "fingerprint"):
            value = getattr(item, attr, None)
            if value:
                values.add(str(value))
    return values


def _finding_identity(finding: Finding) -> set[str]:
    values = {value for value in (finding.id, finding.fingerprint) if value}
    return {str(value) for value in values}


def _related_for_action(
    change: ActionSurfaceChange,
    findings: list[Finding],
) -> list[Finding]:
    related: list[Finding] = []
    for finding in findings:
        evidence = finding.evidence or {}
        evidence_change = evidence.get("change")
        evidence_action_id = evidence.get("action_id")
        if isinstance(evidence_change, dict):
            evidence_action_id = evidence_action_id or evidence_change.get("action_id")
        source_ref = finding.source.ref if finding.source is not None else None
        if change.action_id and change.action_id in {
            evidence_action_id,
            source_ref,
        }:
            related.append(finding)
            continue
        if change.tool_name and finding.tool_name == change.tool_name:
            related.append(finding)
    return related


def _impact(
    *,
    related: list[Finding],
    blockers: set[str],
    review_items: set[str],
    fallback: CapabilityReleaseImpact,
) -> CapabilityReleaseImpact:
    for finding in related:
        identities = _finding_identity(finding)
        if finding.blocks_release or identities & blockers:
            return "blocks_release"
    for finding in related:
        if _finding_identity(finding) & review_items:
            return "review_required"
    return fallback


def _related_ids(findings: list[Finding]) -> list[str]:
    values: list[str] = []
    for finding in findings:
        value = finding.id or finding.fingerprint
        if value:
            values.append(value)
    return sorted(dict.fromkeys(values))


def _action_change(
    change: ActionSurfaceChange,
    *,
    bucket: CapabilityChangeBucket,
    active_findings: list[Finding],
    blockers: set[str],
    review_items: set[str],
) -> VerifierCapabilityChange:
    related = _related_for_action(change, active_findings)
    fallback: CapabilityReleaseImpact = (
        "review_required"
        if change.severity in {"critical", "high", "medium"}
        and bucket != "removed"
        else "informational"
    )
    return VerifierCapabilityChange(
        id=f"action:{bucket}:{change.action_id}:{change.type}",
        change_type=_action_change_type(change, bucket),
        change_bucket=bucket,
        subject_kind="action",
        subject=change.tool_name or change.action_id,
        impact=_impact(
            related=related,
            blockers=blockers,
            review_items=review_items,
            fallback=fallback,
        ),
        rationale=change.reason,
        source_path=change.source_path,
        source_start_line=change.source_start_line,
        related_finding_ids=_related_ids(related),
    )


def _action_change_type(
    change: ActionSurfaceChange,
    bucket: CapabilityChangeBucket,
) -> str:
    if change.type == "ACTION_ADDED":
        return "action_added"
    if change.type == "ACTION_REMOVED":
        return "action_removed"
    if change.type == "ACTION_MODIFIED":
        return "action_modified"
    return change.type.lower()


def _trust_root_change(finding: Finding) -> VerifierCapabilityChange:
    evidence = finding.evidence or {}
    subject = str(evidence.get("changed_file") or finding.title)
    trust_root_class = str(evidence.get("trust_root_class") or "trust_root")
    return VerifierCapabilityChange(
        id=f"trust-root:{subject}",
        change_type="trust_root_touched",
        change_bucket="modified",
        subject_kind=trust_root_class,
        subject=subject,
        impact="review_required",
        rationale="Release trust root changed; human review is required.",
        source_path=subject,
        related_finding_ids=_related_ids([finding]),
    )


def _policy_weakening_change(finding: Finding) -> VerifierCapabilityChange:
    evidence = finding.evidence or {}
    subject = str(
        evidence.get("changed_file")
        or evidence.get("policy_surface")
        or finding.tool_name
        or finding.check_id
    )
    return VerifierCapabilityChange(
        id=f"policy-weakened:{finding.check_id}:{subject}",
        change_type="policy_weakened",
        change_bucket="modified",
        subject_kind="policy",
        subject=subject,
        impact="review_required",
        rationale=finding.title or "Release policy was weakened.",
        source_path=subject if "/" in subject or subject.endswith(".yaml") else None,
        related_finding_ids=_related_ids([finding]),
    )


def _tool_change(change: ToolSurfaceToolChange) -> VerifierCapabilityChange:
    bucket = _bucket(change.kind)
    return VerifierCapabilityChange(
        id=f"tool:{change.kind}:{change.name}",
        change_type=f"tool_{_type_suffix(change.kind)}",
        change_bucket=bucket,
        subject_kind="tool",
        subject=change.name,
        impact="informational",
        rationale=_tool_change_reason(change),
        source_path=change.source_path,
        source_start_line=change.source_start_line,
    )


def _high_risk_effect_change(
    change: ToolSurfaceHighRiskEffectChange,
) -> VerifierCapabilityChange:
    bucket = _bucket(change.kind)
    return VerifierCapabilityChange(
        id=f"risk:{change.kind}:{change.tool}:{change.tag}",
        change_type=f"risk_tag_{_type_suffix(change.kind)}",
        change_bucket=bucket,
        subject_kind="tool",
        subject=change.tool,
        impact="review_required" if change.kind == "added" else "informational",
        rationale=f"High-risk effect {change.kind}: {change.tag}.",
        source_path=change.source_path,
        source_start_line=change.source_start_line,
    )


def _scope_change(change: ToolSurfaceScopeChange) -> VerifierCapabilityChange:
    bucket = _bucket(change.kind)
    impact: CapabilityReleaseImpact = (
        "review_required" if change.kind == "added" and change.broad else "informational"
    )
    return VerifierCapabilityChange(
        id=f"scope:{change.kind}:{change.scope}:{','.join(change.tool_names)}",
        change_type=f"scope_{_type_suffix(change.kind)}",
        change_bucket=bucket,
        subject_kind="scope",
        subject=change.scope,
        impact=impact,
        rationale=(
            f"Scope {change.kind} for "
            f"{', '.join(change.tool_names) or 'the tool surface'}."
        ),
    )


def _control_change(change: ToolSurfaceControlChange) -> VerifierCapabilityChange:
    bucket = _bucket(change.kind)
    impact: CapabilityReleaseImpact = (
        "review_required" if change.kind == "removed" else "informational"
    )
    return VerifierCapabilityChange(
        id=f"control:{change.kind}:{change.control}:{change.tool}",
        change_type=(
            f"{change.control}_removed"
            if change.kind == "removed"
            else f"{change.control}_added"
        ),
        change_bucket=bucket,
        subject_kind="policy",
        subject=change.tool,
        impact=impact,
        rationale=change.reason or f"{change.control} {change.kind}.",
        source_path=change.source_path,
        source_start_line=change.source_start_line,
    )


def _metadata_change(change: ToolSurfaceMetadataChange) -> VerifierCapabilityChange:
    return VerifierCapabilityChange(
        id=f"metadata:{change.kind}:{change.tool}:{change.metadata}",
        change_type="tool_metadata_modified",
        change_bucket="modified",
        subject_kind="tool",
        subject=change.tool,
        impact="informational",
        rationale=f"Tool metadata changed: {change.metadata}.",
        source_path=change.source_path,
        source_start_line=change.source_start_line,
    )


def _policy_drift_change(change: ToolSurfacePolicyDrift) -> VerifierCapabilityChange:
    bucket = _bucket(change.kind)
    summary = change.after_summary if change.kind != "removed" else change.before_summary
    return VerifierCapabilityChange(
        id=f"policy:{change.kind}:{change.policy_kind}:{change.key}",
        change_type="shipgate_policy_modified",
        change_bucket=bucket,
        subject_kind="policy",
        subject=f"{change.policy_kind}:{change.key}",
        impact="review_required",
        rationale=summary or f"Policy {change.kind}: {change.policy_kind}.",
    )


def _tool_change_reason(change: ToolSurfaceToolChange) -> str:
    if change.kind == "changed" and change.changes:
        fields = ", ".join(item.field for item in change.changes[:3])
        return f"Tool metadata changed: {fields}."
    return f"Tool {change.kind}: {change.name}."


def _bucket(kind: str) -> CapabilityChangeBucket:
    if kind == "added":
        return "added"
    if kind == "removed":
        return "removed"
    return "modified"


def _type_suffix(kind: str) -> str:
    if kind == "added":
        return "added"
    if kind == "removed":
        return "removed"
    return "modified"


__all__ = [
    "POLICY_WEAKENING_CHECK_IDS",
    "TRUST_ROOT_CHECK_ID",
    "build_capability_review",
]
