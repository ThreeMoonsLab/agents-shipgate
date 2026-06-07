from __future__ import annotations

import math

from agents_shipgate.ci.exit_policy import (
    effective_fail_on,
    exit_code_for_report,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.report import (
    BaselineDelta,
    ContributionRule,
    ContributionRuleName,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReleaseDecisionStatus,
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

    # blockers/review_items consider the full findings set, NOT
    # new_findings_only: baseline-matched criticals must remain visible
    # as accepted debt in review_items. The new_findings_only filter
    # only affects fail_policy.exit_code (via exit_code_for_report).
    # v0.17: iterate report.findings directly so the contribution_rules
    # audit row set is exhaustive (suppressed findings get an audit row
    # too, classified as excluded/suppressed).
    blockers: list[ReleaseDecisionItem] = []
    review_items: list[ReleaseDecisionItem] = []
    contribution_rules: list[ContributionRule] = []
    blocker_severities: set[Severity] = {"critical", *fail_on_resolved}

    # v0.17: iterate the FULL findings list (not just `active`) so the
    # audit row set is exhaustive over report.findings. The branching
    # below mirrors the original active classification exactly — same
    # `if/elif/elif` shape, same fall-through to silent-drop — so the
    # blockers[]/review_items[] lists are byte-identical to v0.16. The
    # only addition is one ContributionRule per finding documenting
    # which branch fired (or, for the silent-drop tail, which baseline
    # acceptance silently consumed it).
    for finding in report.findings:
        if finding.suppressed:
            contribution_rules.append(
                _rule(
                    finding,
                    category="excluded",
                    rule="suppressed",
                    rationale="Finding suppressed via checks.ignore in the manifest.",
                )
            )
            continue
        # Branch 1: explicit policy blocker, not baseline-matched.
        if finding.blocks_release and finding.baseline_status != "matched":
            blockers.append(_to_item(finding))
            contribution_rules.append(
                _rule(
                    finding,
                    category="blocker",
                    rule="policy_block_new",
                    rationale=(
                        f"blocks_release=true and baseline_status="
                        f"{finding.baseline_status or 'null'}; "
                        "explicit policy blocker."
                    ),
                )
            )
            continue
        # Branch 2: severity in active blocker tier, not baseline-matched.
        if (
            finding.baseline_status != "matched"
            and finding.severity in blocker_severities
        ):
            blockers.append(_to_item(finding))
            contribution_rules.append(
                _rule(
                    finding,
                    category="blocker",
                    rule="severity_block_new",
                    rationale=(
                        f"severity={finding.severity} is in blocker tier "
                        f"({sorted(blocker_severities)}); "
                        f"baseline_status={finding.baseline_status or 'null'}."
                    ),
                )
            )
            continue
        # Branch 3: review tier (severity C/H/M or requires_human_review).
        # The rule name distinguishes WHY the finding landed here:
        # - matched policy → policy_baseline_accepted
        # - matched severity-tier → severity_baseline_accepted
        # - otherwise → review_required (severity in C/H/M without
        #   matching blocker tier, or requires_human_review=True)
        if (
            finding.severity in {"critical", "high", "medium"}
            or finding.requires_human_review is True
        ):
            review_items.append(_to_item(finding))
            contribution_rules.append(
                _rule(
                    finding,
                    category="review_item",
                    rule=_review_rule_for(finding, blocker_severities),
                    rationale=_review_rationale_for(finding, blocker_severities),
                )
            )
            continue
        # Branch 4 (fall-through): sub-threshold or silently-accepted
        # baseline debt below the review tier. Original code dropped
        # these silently; v0.17 records why.
        contribution_rules.append(
            _rule(
                finding,
                category="excluded",
                rule=_excluded_rule_for(finding, blocker_severities),
                rationale=_excluded_rationale_for(finding, blocker_severities),
            )
        )

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
        contribution_rules=contribution_rules,
    )


def _rule(
    finding: Finding,
    *,
    category: str,
    rule: ContributionRuleName,
    rationale: str,
) -> ContributionRule:
    # `Finding.id` and `Finding.fingerprint` are Python-Optional —
    # `assign_finding_ids()` populates them on the normal scan path,
    # but direct/internal callers (tests constructing minimal Findings,
    # `explain-finding` rebuilding from a stripped report, plugin
    # checks that emit Findings before id assignment) may pass
    # findings with both unset. ContributionRule.finding_id is
    # required-as-string on the wire, so fall back through fingerprint
    # to check_id (which is always a non-empty string per the model
    # contract). The audit row stays useful in every case: even
    # without an id, a reviewer can match the row back to the finding
    # via fingerprint or, last resort, the check_id.
    return ContributionRule(
        finding_id=finding.id or finding.fingerprint or finding.check_id,
        fingerprint=finding.fingerprint,
        check_id=finding.check_id,
        category=category,  # type: ignore[arg-type]
        rule=rule,
        rationale=rationale,
    )


def _review_rule_for(
    finding: Finding, blocker_severities: set[Severity]
) -> ContributionRuleName:
    """Disambiguate the rule name when a finding lands in review_items.

    Three cases reach the review-tier branch in build_release_decision:
    - Policy finding (`blocks_release=True`) + baseline_status="matched":
      would have been a `policy_block_new` blocker if not matched →
      `policy_baseline_accepted`.
    - Severity in active blocker tier + baseline_status="matched":
      would have been `severity_block_new` if not matched →
      `severity_baseline_accepted`.
    - Otherwise (severity in {C,H,M} but not in blocker tier, or
      requires_human_review=True): plain `review_required`.
    """
    if finding.blocks_release and finding.baseline_status == "matched":
        return "policy_baseline_accepted"
    if (
        finding.baseline_status == "matched"
        and finding.severity in blocker_severities
    ):
        return "severity_baseline_accepted"
    return "review_required"


def _review_rationale_for(
    finding: Finding, blocker_severities: set[Severity]
) -> str:
    if finding.blocks_release and finding.baseline_status == "matched":
        return (
            "blocks_release=true and baseline_status=matched; "
            "accepted as policy debt and routed to review_items."
        )
    if (
        finding.baseline_status == "matched"
        and finding.severity in blocker_severities
    ):
        return (
            f"severity={finding.severity} is in blocker tier "
            f"({sorted(blocker_severities)}) but baseline_status=matched; "
            "accepted as debt."
        )
    if finding.requires_human_review is True:
        return (
            f"requires_human_review=true (severity={finding.severity}); "
            "routed to review_items."
        )
    return (
        f"severity={finding.severity}; below active blocker tier "
        f"({sorted(blocker_severities)}) but in review tier "
        "{critical, high, medium}."
    )


def _excluded_rule_for(
    finding: Finding, blocker_severities: set[Severity]
) -> ContributionRuleName:
    """Disambiguate the rule name when a finding falls through to excluded.

    Two reachable cases:
    - blocks_release=True + matched + severity below review tier:
      original code drops silently → `policy_baseline_accepted` (with
      excluded category, since severity didn't reach the review fall-
      through above).
    - severity in blocker tier + matched + severity below review tier:
      same shape → `severity_baseline_accepted`.
    - Otherwise: plain `sub_threshold`.
    """
    if finding.blocks_release and finding.baseline_status == "matched":
        return "policy_baseline_accepted"
    if (
        finding.baseline_status == "matched"
        and finding.severity in blocker_severities
    ):
        return "severity_baseline_accepted"
    return "sub_threshold"


def _excluded_rationale_for(
    finding: Finding, blocker_severities: set[Severity]
) -> str:
    if finding.blocks_release and finding.baseline_status == "matched":
        return (
            "blocks_release=true and baseline_status=matched, but "
            f"severity={finding.severity} is below review tier; "
            "excluded from blockers and review_items."
        )
    if (
        finding.baseline_status == "matched"
        and finding.severity in blocker_severities
    ):
        return (
            f"severity={finding.severity} in blocker tier with "
            "baseline_status=matched, but below review tier; excluded."
        )
    return (
        f"severity={finding.severity}; below active blocker tier and "
        "below review tier."
    )


def _to_item(finding: Finding) -> ReleaseDecisionItem:
    # v0.19 reviewer-grade provenance: mirror the dual-source pointers
    # so packet §1 and re-renderers (which consume ReleaseDecisionItem,
    # not the full Finding) can cite both the tool location and the
    # manifest evidence pointer without a side lookup.
    return ReleaseDecisionItem(
        id=finding.id,
        fingerprint=finding.fingerprint,
        check_id=finding.check_id,
        severity=finding.severity,
        title=finding.title,
        baseline_status=finding.baseline_status,
        blocks_release=finding.blocks_release,
        source=finding.source,
        policy_evidence_source=finding.policy_evidence_source,
        capability_refs=list(finding.capability_refs),
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
