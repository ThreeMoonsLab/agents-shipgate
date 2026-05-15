from __future__ import annotations

import math

from agents_shipgate.ci.exit_policy import (
    baseline_filtered_active,
    effective_fail_on,
    exit_code_for_report,
)
from agents_shipgate.core.models import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReleaseDecisionStatus,
    Severity,
    Tool,
)

# Thresholds for the `insufficient_evidence` decision state. Private
# module-level constants so they're tunable in code without expanding
# the manifest or CLI surface.
_LOW_CONFIDENCE_TOOL_RATIO = 0.5
_MAX_TOLERATED_SOURCE_WARNINGS = 3


def _low_confidence_tool_threshold(tool_count: int) -> int:
    return max(1, math.ceil(tool_count * _LOW_CONFIDENCE_TOOL_RATIO))


def build_release_decision(
    *,
    report: ReadinessReport,
    tools: list[Tool],
    ci_mode: str,
    fail_on: list[Severity] | None,
    new_findings_only: bool,
) -> ReleaseDecision:
    fail_on_resolved = effective_fail_on(ci_mode, fail_on)

    # blockers/review_items consider the full active set, NOT
    # new_findings_only: baseline-matched criticals must remain visible
    # as accepted debt in review_items. The new_findings_only filter
    # only affects fail_policy.exit_code (via exit_code_for_report).
    active = baseline_filtered_active(report, new_findings_only=False)

    blockers: list[ReleaseDecisionItem] = []
    review_items: list[ReleaseDecisionItem] = []
    blocker_severities: set[Severity] = {"critical", *fail_on_resolved}

    for finding in active:
        if finding.blocks_release and finding.baseline_status != "matched":
            blockers.append(_to_item(finding))
            continue
        if (
            finding.baseline_status != "matched"
            and finding.severity in blocker_severities
        ):
            blockers.append(_to_item(finding))
            continue
        if (
            finding.severity in {"critical", "high", "medium"}
            or finding.requires_human_review is True
        ):
            review_items.append(_to_item(finding))

    low_confidence_tool_count = sum(
        1 for tool in tools if tool.extraction_confidence != "high"
    )
    evidence = EvidenceCoverageDecision(
        level=report.summary.evidence_coverage,
        human_review_recommended=report.summary.human_review_recommended,
        source_warning_count=len(report.source_warnings),
        low_confidence_tool_count=low_confidence_tool_count,
    )

    if report.baseline is None:
        baseline_delta = BaselineDelta(enabled=False)
    else:
        baseline_delta = BaselineDelta(
            enabled=True,
            path=report.baseline.path,
            matched_count=report.baseline.matched_count,
            new_count=report.baseline.new_count,
            resolved_count=report.baseline.resolved_count,
        )

    exit_code = exit_code_for_report(
        report,
        ci_mode,
        fail_on=fail_on,
        new_findings_only=new_findings_only,
    )
    fail_policy = FailPolicy(
        ci_mode=ci_mode,
        fail_on=fail_on_resolved,
        new_findings_only=new_findings_only,
        would_fail_ci=(exit_code != 0),
        exit_code=exit_code,
    )

    low_confidence_threshold = _low_confidence_tool_threshold(len(tools))

    decision: ReleaseDecisionStatus
    if blockers:
        decision = "blocked"
    elif (
        evidence.low_confidence_tool_count >= low_confidence_threshold
        or evidence.source_warning_count > _MAX_TOLERATED_SOURCE_WARNINGS
    ):
        decision = "insufficient_evidence"
    elif (
        review_items
        or evidence.human_review_recommended
        or evidence.source_warning_count > 0
    ):
        # Sub-threshold source warnings still warrant review.
        # summarize_findings() doesn't fold source_warning_count into
        # human_review_recommended (it tracks only tool confidence and
        # critical/high findings), so route any source warning here
        # explicitly. Otherwise 1-3 warnings with no findings would
        # silently pass.
        decision = "review_required"
    else:
        decision = "passed"

    reason = _decision_reason(decision, blockers, review_items, evidence)

    return ReleaseDecision(
        decision=decision,
        reason=reason,
        blockers=blockers,
        review_items=review_items,
        evidence_coverage=evidence,
        baseline_delta=baseline_delta,
        fail_policy=fail_policy,
    )


def _to_item(finding: Finding) -> ReleaseDecisionItem:
    return ReleaseDecisionItem(
        id=finding.id,
        fingerprint=finding.fingerprint,
        check_id=finding.check_id,
        severity=finding.severity,
        title=finding.title,
        baseline_status=finding.baseline_status,
        blocks_release=finding.blocks_release,
    )


def _decision_reason(
    decision: ReleaseDecisionStatus,
    blockers: list[ReleaseDecisionItem],
    review_items: list[ReleaseDecisionItem],
    evidence: EvidenceCoverageDecision,
) -> str:
    if decision == "blocked":
        n = len(blockers)
        noun = "finding" if n == 1 else "findings"
        verb = "blocks" if n == 1 else "block"
        return f"{n} active {noun} {verb} release."
    if decision == "insufficient_evidence":
        parts: list[str] = []
        if evidence.low_confidence_tool_count > 0:
            parts.append(
                f"{evidence.low_confidence_tool_count} low-confidence tool(s)"
            )
        if evidence.source_warning_count > 0:
            parts.append(
                f"{evidence.source_warning_count} source warning(s)"
            )
        detail = " and ".join(parts) if parts else "degraded evidence"
        return (
            f"Evidence coverage below threshold ({detail}); "
            "scan results are not trustworthy enough to gate release."
        )
    if decision == "review_required":
        matched_criticals = sum(
            1
            for item in review_items
            if item.severity == "critical" and item.baseline_status == "matched"
        )
        n_reviews = len(review_items)
        # Gate "evidence coverage is incomplete" wording on actual
        # measurable gaps. summary.human_review_recommended is also True
        # for any critical/high finding (see findings.summarize_findings),
        # so using it here would falsely claim evidence gaps for clean
        # static scans that simply have high-severity findings.
        has_evidence_gaps = (
            evidence.low_confidence_tool_count > 0
            or evidence.source_warning_count > 0
        )
        if (
            review_items
            and matched_criticals == n_reviews
            and matched_criticals > 0
        ):
            return (
                "All critical findings are baseline-matched; review "
                "accepted debt before shipping."
            )
        if review_items and has_evidence_gaps:
            noun = "finding" if n_reviews == 1 else "findings"
            return (
                f"{n_reviews} {noun} need review and evidence coverage "
                "is incomplete."
            )
        if review_items:
            noun = "finding" if n_reviews == 1 else "findings"
            verb = "requires" if n_reviews == 1 else "require"
            return f"{n_reviews} {noun} {verb} human review before shipping."
        if evidence.low_confidence_tool_count > 0:
            return (
                "Static-only scan with low-confidence evidence; human "
                "review recommended."
            )
        if evidence.source_warning_count > 0:
            # Reachable when no review_items and human_review_recommended
            # is False but source warnings tipped us into review_required
            # via the explicit source-warning branch in
            # build_release_decision. Checked after the low-confidence
            # branch so a scan with both gaps surfaces the more
            # actionable wording first.
            n = evidence.source_warning_count
            noun = "warning" if n == 1 else "warnings"
            return f"{n} source-loader {noun}; review evidence before shipping."
        # Defensive: review_required with no review_items and no
        # measurable evidence gaps. summarize_findings doesn't produce
        # this combination today, but cover the case explicitly.
        return "Human review recommended."
    return "No active blockers and evidence coverage is full."
