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
        ]
    )
    diff = report.tool_surface_diff
    if diff.enabled:
        lines.extend(
            [
                "",
                "### What changed",
                (
                    f"Tools: +{diff.summary.tools_added}, "
                    f"-{diff.summary.tools_removed}, "
                    f"{diff.summary.tools_changed} changed. "
                    f"New high-risk effects: "
                    f"{diff.summary.new_high_risk_effects}. "
                    f"Removed controls: {diff.summary.controls_removed}. "
                    f"New findings: {diff.summary.new_findings}."
                ),
            ]
        )
        for item in _diff_highlights(report):
            lines.append(f"- {item}")
    elif diff.notes:
        lines.extend(["", f"Tool-surface diff: {diff.notes[0]}"])
    lines.extend(["", f"Generated reports: {formats}.", ""])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _diff_highlights(report: ReadinessReport) -> list[str]:
    diff = report.tool_surface_diff
    highlights: list[str] = []
    for item in diff.high_risk_effects:
        if item.kind == "added":
            highlights.append(f"New high-risk tag `{item.tag}` on `{item.tool}`")
    for item in diff.controls:
        if item.kind == "removed":
            highlights.append(f"Removed `{item.control}` for `{item.tool}`")
    for item in diff.tools:
        if item.kind == "added":
            highlights.append(f"Added tool `{item.name}`")
        elif item.kind == "removed":
            highlights.append(f"Removed tool `{item.name}`")
    return highlights[:5]
