from __future__ import annotations

from agents_shipgate.schemas.common import ReleaseDecisionStatus, Severity
from agents_shipgate.schemas.report import (
    Finding,
    ReadinessReport,
)

GATE_FAILURE_EXIT_CODE = 20


def effective_fail_on(ci_mode: str, fail_on: list[Severity] | None) -> list[Severity]:
    """Resolve the active fail-on severity list for a given CI mode.

    Shared with build_release_decision so the FailPolicy block in
    release_decision and the process exit code can never disagree.
    """
    return fail_on if fail_on is not None else _default_fail_on(ci_mode)


def baseline_filtered_active(report: ReadinessReport, *, new_findings_only: bool) -> list[Finding]:
    """Gate-eligible findings, filtered by suppression and baseline mode.

    A rule's requested severity or block bit cannot upgrade predicate evidence.
    Findings with typed support therefore contribute to process policy only
    when that support is policy-eligible. Legacy findings without support keep
    their established behavior for frozen-report compatibility.
    """
    active = [
        finding
        for finding in report.findings
        if not finding.suppressed
        and (finding.support is None or finding.support.policy_eligible)
    ]
    if new_findings_only:
        active = [finding for finding in active if finding.baseline_status in {None, "new"}]
    return active


def exit_code_for_report(
    report: ReadinessReport,
    ci_mode: str,
    *,
    fail_on: list[Severity] | None = None,
    new_findings_only: bool = False,
    release_decision: ReleaseDecisionStatus | None = None,
    has_semantic_gaps: bool = False,
) -> int:
    # Summary counts exclude suppressed findings, so strict mode intentionally
    # passes when every critical finding has an explicit suppression reason.
    fail_on_resolved = effective_fail_on(ci_mode, fail_on)
    active = baseline_filtered_active(report, new_findings_only=new_findings_only)
    # v0.29: the semantic gate is not represented as a Finding on purpose —
    # baselines, suppressions, severity overrides, and --no-heuristics must
    # not be able to hide missing/conflicting capability evidence. Once the
    # canonical release decision has been computed, strict CI treats
    # insufficient evidence as a gate failure independently of findings.
    if ci_mode == "strict" and (release_decision == "insufficient_evidence" or has_semantic_gaps):
        return GATE_FAILURE_EXIT_CODE
    if ci_mode == "strict" and release_decision == "blocked":
        return GATE_FAILURE_EXIT_CODE
    if ci_mode == "strict" and any(finding.blocks_release for finding in active):
        return GATE_FAILURE_EXIT_CODE
    if not fail_on_resolved:
        return 0
    if any(finding.severity in fail_on_resolved for finding in active):
        return GATE_FAILURE_EXIT_CODE
    return 0


def _default_fail_on(ci_mode: str) -> list[Severity]:
    if ci_mode == "strict":
        return ["critical"]
    return []
