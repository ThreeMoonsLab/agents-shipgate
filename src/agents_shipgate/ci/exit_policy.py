from __future__ import annotations

from agents_shipgate.core.models import Finding, ReadinessReport, Severity

GATE_FAILURE_EXIT_CODE = 20


def effective_fail_on(
    ci_mode: str, fail_on: list[Severity] | None
) -> list[Severity]:
    """Resolve the active fail-on severity list for a given CI mode.

    Shared with build_release_decision so the FailPolicy block in
    release_decision and the process exit code can never disagree.
    """
    return fail_on if fail_on is not None else _default_fail_on(ci_mode)


def baseline_filtered_active(
    report: ReadinessReport, *, new_findings_only: bool
) -> list[Finding]:
    """Active (non-suppressed) findings, filtered by baseline new-only."""
    active = [finding for finding in report.findings if not finding.suppressed]
    if new_findings_only:
        active = [
            finding
            for finding in active
            if finding.baseline_status in {None, "new"}
        ]
    return active


def exit_code_for_report(
    report: ReadinessReport,
    ci_mode: str,
    *,
    fail_on: list[Severity] | None = None,
    new_findings_only: bool = False,
) -> int:
    # Summary counts exclude suppressed findings, so strict mode intentionally
    # passes when every critical finding has an explicit suppression reason.
    fail_on_resolved = effective_fail_on(ci_mode, fail_on)
    if not fail_on_resolved:
        return 0
    active = baseline_filtered_active(report, new_findings_only=new_findings_only)
    if any(finding.severity in fail_on_resolved for finding in active):
        return GATE_FAILURE_EXIT_CODE
    return 0


def _default_fail_on(ci_mode: str) -> list[Severity]:
    if ci_mode == "strict":
        return ["critical"]
    return []
