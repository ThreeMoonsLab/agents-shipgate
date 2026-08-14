from __future__ import annotations

from agents_shipgate.core.findings.verifier_blocks import build_capability_change
from agents_shipgate.core.policy_reason_codes import (
    POLICY_BASE_ABSENT_CHECK_ID,
    POLICY_WEAKENED_CHECK_ID,
    counts_as_weakened,
    weakening_is_proven,
)
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeDirection,
    CapabilityChangeMember,
)
from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.verifier import (
    CapabilityChangeBucket,
    VerifierCapabilityChange,
    VerifierCapabilityReview,
)

TRUST_ROOT_CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
POLICY_WEAKENING_CHECK_ID = POLICY_WEAKENED_CHECK_ID

_IMPACT_ORDER = {
    "blocks_release": 0,
    "insufficient_evidence": 1,
    "review_required": 2,
    "informational": 3,
    "none": 4,
}
_DIRECTION_ORDER = {"added": 0, "broadened": 1, "narrowed": 2, "removed": 3}
_DIRECTION_TO_BUCKET: dict[CapabilityChangeDirection, CapabilityChangeBucket] = {
    "added": "added",
    "removed": "removed",
    "broadened": "modified",
    "narrowed": "modified",
}


def build_capability_review(
    report: ReadinessReport,
    *,
    top_limit: int = 5,
) -> VerifierCapabilityReview:
    """Project canonical verifier report blocks into the PR-review rollup.

    ``report.capability_change`` owns capability counts and top-change
    members. ``report.verifier_summary`` owns trust-root and policy flags.
    This function is a formatter-facing projection only; it never derives
    a separate release decision or independent capability delta.
    """

    capability_change = _capability_change_block(report)
    members = _capability_members(capability_change)
    source_by_finding_id = {
        finding.id: finding.source
        for finding in report.findings
        if finding.id and finding.source is not None
    }
    changes = [
        _member_change(member, source_by_finding_id=source_by_finding_id)
        for member in members
    ]
    top_changes = sorted(
        changes,
        key=lambda change: (
            _IMPACT_ORDER.get(change.impact, 99),
            _DIRECTION_ORDER.get(_direction_from_change_type(change.change_type), 99),
            change.subject_kind,
            change.subject,
            change.id,
        ),
    )[: max(0, top_limit)]

    notes: list[str] = []
    if not capability_change.enabled:
        notes.extend(_disabled_diff_notes(report))

    return VerifierCapabilityReview(
        added=len(capability_change.added),
        modified=len(capability_change.broadened) + len(capability_change.narrowed),
        removed=len(capability_change.removed),
        trust_root_touched=_trust_root_touched(report),
        policy_weakened=_policy_weakened(report),
        policy_weakening_proven=_policy_weakening_proven(report),
        top_changes=top_changes,
        notes=notes,
    )


def _capability_change_block(report: ReadinessReport) -> CapabilityChangeBlock:
    if report.capability_change is not None:
        return report.capability_change
    # Compatibility for tests or older callers that construct a report object
    # before final_report has attached the canonical v0.22 block.
    return build_capability_change(report)


def _capability_members(
    capability_change: CapabilityChangeBlock,
) -> list[CapabilityChangeMember]:
    return [
        *capability_change.added,
        *capability_change.broadened,
        *capability_change.narrowed,
        *capability_change.removed,
    ]


def _member_change(
    member: CapabilityChangeMember,
    *,
    source_by_finding_id: dict[str, object],
) -> VerifierCapabilityChange:
    direction = member.direction
    source = _member_source(member, source_by_finding_id)
    return VerifierCapabilityChange(
        id=member.id,
        change_type=f"{member.subject_kind}_{direction}",
        change_bucket=_DIRECTION_TO_BUCKET[direction],
        subject_kind=member.subject_kind,
        subject=_member_subject(member),
        impact=member.release_impact,
        rationale=member.rationale or _default_rationale(member),
        source_path=getattr(source, "path", None) if source is not None else None,
        source_start_line=(
            getattr(source, "start_line", None) if source is not None else None
        ),
        related_finding_ids=list(member.related_finding_ids),
    )


def _member_subject(member: CapabilityChangeMember) -> str:
    if member.tool:
        if member.subject_kind == "scope" and member.scope:
            return f"{member.tool}:{member.scope}"
        if member.subject_kind == "policy" and member.action:
            return f"{member.tool}:{member.action}"
        return member.tool
    return member.scope or member.action or member.id


def _default_rationale(member: CapabilityChangeMember) -> str:
    return f"{member.subject_kind} {member.direction}"


def _member_source(
    member: CapabilityChangeMember,
    source_by_finding_id: dict[str, object],
) -> object | None:
    for finding_id in member.related_finding_ids:
        source = source_by_finding_id.get(finding_id)
        if source is not None and getattr(source, "path", None):
            return source
    return None


def _direction_from_change_type(change_type: str) -> str:
    return change_type.rsplit("_", maxsplit=1)[-1]


def _trust_root_touched(report: ReadinessReport) -> bool:
    if report.verifier_summary is not None:
        return report.verifier_summary.protected_surface_touched
    if report.protected_surface_changes:
        return True
    return any(f.check_id == TRUST_ROOT_CHECK_ID for f in _active_findings(report))


def _policy_weakened(report: ReadinessReport) -> bool:
    if report.verifier_summary is not None:
        return report.verifier_summary.policy_weakened
    # Same rule as ``verifier_blocks``: the no-base fail-safe keeps this flag
    # raised (an unprovable direction is treated as weakened for routing), but
    # a proven first adoption weakens nothing, and this flag is read as a fact
    # downstream. Both reason codes are accepted so a pre-split report
    # reprojects to what it meant when it was written.
    return any(
        counts_as_weakened(f.check_id, f.evidence) for f in _active_findings(report)
    )


def _policy_weakening_proven(report: ReadinessReport) -> bool:
    """Whether a base-vs-head comparison actually established a weakening.

    Derived from findings on both paths — unlike ``policy_weakened`` there is
    no summary shortcut, because this is a strictly narrower question than the
    fail-closed flag and the two must not be conflated by a stale rollup.
    """

    return any(
        weakening_is_proven(f.check_id, f.evidence) for f in _active_findings(report)
    )


def _active_findings(report: ReadinessReport) -> list[Finding]:
    return [finding for finding in report.findings if not finding.suppressed]


def _disabled_diff_notes(report: ReadinessReport) -> list[str]:
    notes: list[str] = []
    if not report.action_surface_diff.enabled and report.action_surface_diff.notes:
        notes.append(report.action_surface_diff.notes[0])
    if not report.tool_surface_diff.enabled and report.tool_surface_diff.notes:
        notes.append(report.tool_surface_diff.notes[0])
    return notes


# ``POLICY_BASE_ABSENT_CHECK_ID`` is re-exported so callers that already import
# the weakening id from here keep one import site for the pair.
__all__ = [
    "POLICY_BASE_ABSENT_CHECK_ID",
    "POLICY_WEAKENING_CHECK_ID",
    "TRUST_ROOT_CHECK_ID",
    "build_capability_review",
]
