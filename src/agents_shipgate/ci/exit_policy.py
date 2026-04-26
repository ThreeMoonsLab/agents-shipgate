from __future__ import annotations

from agents_shipgate.core.models import ReadinessReport, Severity

GATE_FAILURE_EXIT_CODE = 20


def exit_code_for_report(
    report: ReadinessReport,
    ci_mode: str,
    *,
    fail_on: list[Severity] | None = None,
    new_findings_only: bool = False,
) -> int:
    # Summary counts exclude suppressed findings, so strict mode intentionally
    # passes when every critical finding has an explicit suppression reason.
    effective_fail_on = fail_on if fail_on is not None else _default_fail_on(ci_mode)
    if not effective_fail_on:
        return 0
    active = [finding for finding in report.findings if not finding.suppressed]
    if new_findings_only:
        active = [
            finding
            for finding in active
            if finding.baseline_status in {None, "new"}
        ]
    if any(finding.severity in effective_fail_on for finding in active):
        return GATE_FAILURE_EXIT_CODE
    return 0


def _default_fail_on(ci_mode: str) -> list[Severity]:
    if ci_mode == "strict":
        return ["critical"]
    return []
