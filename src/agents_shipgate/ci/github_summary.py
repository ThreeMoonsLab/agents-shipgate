from __future__ import annotations

import os
from pathlib import Path

from agents_shipgate.core.models import ReadinessReport
from agents_shipgate.report.markdown import _safe_markdown_text


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
    # v0.17 (M1): single-line surface of severity overrides applied.
    # Reviewers see this immediately in the GH step summary so a silently
    # downgraded critical can't hide behind aggregate counts.
    audit = report.policy_audit
    if audit and audit.severity_overrides_applied:
        rows = audit.severity_overrides_applied
        downgrades = sum(1 for r in rows if r.direction == "downgrade")
        upgrades = sum(1 for r in rows if r.direction == "upgrade")
        tier_crossed = sum(1 for r in rows if r.tier_crossed)
        parts = [f"{len(rows)} severity override{'s' if len(rows) != 1 else ''}"]
        if downgrades:
            parts.append(f"{downgrades} downgrade{'s' if downgrades != 1 else ''}")
        if upgrades:
            parts.append(f"{upgrades} upgrade{'s' if upgrades != 1 else ''}")
        if tier_crossed:
            parts.append(f"{tier_crossed} tier-crossed")
        lines.append(f"Policy audit: {' · '.join(parts)}.")
    diff = report.tool_surface_diff
    action_diff = report.action_surface_diff
    if action_diff.enabled:
        lines.extend(
            [
                "",
                "### Action Surface Diff",
                (
                    f"Actions: +{action_diff.summary.actions_added}, "
                    f"-{action_diff.summary.actions_removed}, "
                    f"{action_diff.summary.actions_modified} modified. "
                    f"Blocking findings: "
                    f"{action_diff.summary.blocking_findings}."
                ),
            ]
        )
        for item in _action_diff_highlights(report):
            lines.append(f"- {item}")
    elif action_diff.notes:
        lines.extend(["", f"Action-surface diff: {action_diff.notes[0]}"])
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
        if (
            diff.summary.new_findings
            or diff.summary.resolved_findings
            or diff.summary.accepted_debt
        ):
            lines.append(
                "Finding deltas: "
                f"{diff.summary.new_findings} new, "
                f"{diff.summary.resolved_findings} resolved, "
                f"{diff.summary.accepted_debt} accepted debt."
            )
    lines.extend(["", f"Generated reports: {formats}.", ""])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _action_diff_highlights(report: ReadinessReport) -> list[str]:
    diff = report.action_surface_diff
    highlights: list[str] = []
    for item in diff.modified:
        if item.type in {
            "SCOPE_EXPANDED",
            "EFFECT_ESCALATED",
            "APPROVAL_REMOVED",
            "SAFEGUARD_REMOVED",
        }:
            highlights.append(
                f"`{_safe_markdown_text(item.tool_name or item.action_id)}`: "
                f"{_safe_markdown_text(item.type)}"
            )
    for item in diff.added:
        highlights.append(
            f"Added action `{_safe_markdown_text(item.tool_name or item.action_id)}`"
        )
    for item in diff.removed:
        highlights.append(
            f"Removed action `{_safe_markdown_text(item.tool_name or item.action_id)}`"
        )
    return highlights[:5]


def _diff_highlights(report: ReadinessReport) -> list[str]:
    diff = report.tool_surface_diff
    risk_highlights: list[str] = []
    control_highlights: list[str] = []
    tool_highlights: list[str] = []
    for item in diff.high_risk_effects:
        if item.kind == "added":
            risk_highlights.append(
                "New high-risk tag "
                f"`{_safe_markdown_text(item.tag)}` on "
                f"`{_safe_markdown_text(item.tool)}`"
            )
    for item in diff.controls:
        if item.kind == "removed":
            control_highlights.append(
                f"Removed `{_safe_markdown_text(item.control)}` for "
                f"`{_safe_markdown_text(item.tool)}`"
            )
    for item in diff.tools:
        if item.kind == "added":
            tool_highlights.append(f"Added tool `{_safe_markdown_text(item.name)}`")
        elif item.kind == "removed":
            tool_highlights.append(f"Removed tool `{_safe_markdown_text(item.name)}`")
    return _interleaved_highlights(
        [control_highlights, risk_highlights, tool_highlights],
        limit=5,
    )


def _interleaved_highlights(groups: list[list[str]], *, limit: int) -> list[str]:
    highlights: list[str] = []
    while len(highlights) < limit and any(groups):
        for group in groups:
            if group and len(highlights) < limit:
                highlights.append(group.pop(0))
    return highlights
