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
    formats = ", ".join(sorted(report.generated_reports)) or "configured"
    lines = [
        "## Agents Shipgate",
        "",
        f"Status: `{summary.status}`",
        f"Critical: {summary.critical_count} · High: {summary.high_count} · Medium: {summary.medium_count}",
        f"Human review: {'recommended' if summary.human_review_recommended else 'not required'}",
        "",
        f"Generated reports: {formats}.",
        "",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
