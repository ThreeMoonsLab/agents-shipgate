from __future__ import annotations

import os
from pathlib import Path

from agents_shipgate.core.models import ReadinessReport


def write_github_step_summary(report: ReadinessReport) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    path = Path(summary_path)
    summary = report.summary
    decision = report.release_decision
    formats = ", ".join(sorted(report.generated_reports)) or "configured"
    lines = ["## Agents Shipgate", ""]
    if decision is not None:
        lines.extend(
            [
                f"Decision: `{decision.decision}`",
                f"Reason: {decision.reason}",
                (
                    f"Blockers: {len(decision.blockers)} · "
                    f"Review items: {len(decision.review_items)}"
                ),
            ]
        )
        fp = decision.fail_policy
        lines.append(
            f"Fail policy: ci_mode=`{fp.ci_mode}`, "
            f"would_fail_ci=`{str(fp.would_fail_ci).lower()}` "
            f"(exit `{fp.exit_code}`)"
        )
    else:
        # Defensive fallback for older reports loaded without
        # release_decision (e.g., baselines from <v0.8).
        lines.extend(
            [
                f"Status: `{summary.status}`",
                (
                    f"Critical: {summary.critical_count} · "
                    f"High: {summary.high_count} · "
                    f"Medium: {summary.medium_count}"
                ),
                (
                    "Human review: "
                    f"{'recommended' if summary.human_review_recommended else 'not required'}"
                ),
            ]
        )
    lines.extend(
        [
            (
                f"Counts: critical={summary.critical_count}, "
                f"high={summary.high_count}, medium={summary.medium_count}"
            ),
            "",
            f"Generated reports: {formats}.",
            "",
        ]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
