from __future__ import annotations

import os
from pathlib import Path

from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.findings.subject_rollup import (
    roll_up_findings,
    top_findings_block,
)
from agents_shipgate.core.privacy import sanitize_report
from agents_shipgate.report.human_order import (
    HumanArtifactContext,
    capability_delta_by_subject,
    should_render_surface_first,
    surface_lead,
)
from agents_shipgate.report.markdown import _safe_markdown_text
from agents_shipgate.report.summary_text import (
    evidence_coverage_text,
    primary_evidence_remediation_text,
)
from agents_shipgate.schemas.report import ReadinessReport


def write_github_step_summary(
    report: ReadinessReport,
    *,
    human_context: HumanArtifactContext | None = None,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    report = sanitize_report(report)
    path = Path(summary_path)
    summary = report.summary
    reviewer_summary = report.reviewer_summary
    formats = ", ".join(sorted(report.generated_reports)) or "configured"
    lines = ["## Agents Shipgate", ""]
    surface_first = should_render_surface_first(report, context=human_context)
    decision_lines = _decision_lines(report)
    if surface_first:
        _append_cold_reader_lead(lines, report)
    lines.extend(decision_lines)
    if reviewer_summary and reviewer_summary.first_recommended_surface:
        surface = reviewer_summary.first_recommended_surface
        lines.append(
            "Reviewer start: "
            f"`{_safe_markdown_text(surface.name)}` — "
            f"{_safe_markdown_text(surface.why)}"
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
    if action_diff.enabled and not surface_first:
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
    elif action_diff.notes and not surface_first:
        lines.extend(["", f"Action-surface diff: {action_diff.notes[0]}"])
    if diff.enabled and not surface_first:
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
    elif diff.notes and not surface_first:
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


def _decision_lines(report: ReadinessReport) -> list[str]:
    summary = report.summary
    decision = report.release_decision
    agent_summary = report.agent_summary
    if decision is None:
        # Defensive fallback for older reports loaded without
        # release_decision (e.g., baselines from <v0.8).
        return [
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
    lines = [
        f"Decision: `{decision.decision}`",
        *([f"Summary: {_safe_markdown_text(agent_summary.headline)}"] if agent_summary else []),
        # The reason carries repository-derived text on an evidence verdict
        # (a gap subject is a tool name or agent id), so it gets the same
        # Markdown escaping report.md has always applied to it.
        f"Reason: {_safe_markdown_text(decision.reason)}",
        (f"Blockers: {len(decision.blockers)} · Review items: {len(decision.review_items)}"),
        f"Evidence coverage: {evidence_coverage_text(decision.evidence_coverage)}",
    ]
    if decision.decision == "insufficient_evidence":
        lines.append(
            "Improve evidence: "
            f"{_safe_markdown_text(primary_evidence_remediation_text(decision.evidence_coverage))}"
        )
    if agent_summary and agent_summary.first_recommended_action:
        lines.append(_agent_next_action_line(agent_summary.first_recommended_action))
    fp = decision.fail_policy
    lines.append(
        f"Fail policy: ci_mode=`{fp.ci_mode}`, "
        f"would_fail_ci=`{str(fp.would_fail_ci).lower()}` "
        f"(exit `{fp.exit_code}`)"
    )
    lines.append(f"Static-verdict boundary: {STATIC_VERDICT_DISCLAIMER}")
    return lines


def _append_cold_reader_lead(lines: list[str], report: ReadinessReport) -> None:
    lines.append("### Capability surface")
    for line in surface_lead(report).text_lines():
        lines.append(_safe_markdown_text(line))
    groups = capability_delta_by_subject(report)
    if groups:
        lines.extend(["", "### Capability delta by subject"])
        for group in groups:
            lines.append(f"- `{_safe_markdown_text(group.subject)}`")
            for change in group.changes:
                lines.append(f"  - {_safe_markdown_text(change)}")
    finding_lines = top_findings_block(
        roll_up_findings(report),
        group_limit=4,
        row_limit=3,
        escape=_safe_markdown_text,
        heading="Findings by subject",
        bullet="- ",
        row_prefix="  - ",
    )
    lines.extend(["", "### Findings", *finding_lines, ""])


def _agent_next_action_line(action) -> str:
    if action.command:
        return f"Next action: `{_safe_markdown_text(action.command)}`"
    return f"Next action: {_safe_markdown_text(action.why)}"


def _action_diff_highlights(report: ReadinessReport) -> list[str]:
    diff = report.action_surface_diff
    inv = _tool_inventory_by_name(report)
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
                f"{_inventory_citation(inv.get(item.tool_name))}"
            )
    for item in diff.added:
        highlights.append(
            f"Added action `{_safe_markdown_text(item.tool_name or item.action_id)}`"
            f"{_inventory_citation(inv.get(item.tool_name))}"
        )
    for item in diff.removed:
        highlights.append(
            f"Removed action `{_safe_markdown_text(item.tool_name or item.action_id)}`"
            f"{_inventory_citation(inv.get(item.tool_name))}"
        )
    return highlights[:5]


def _diff_highlights(report: ReadinessReport) -> list[str]:
    diff = report.tool_surface_diff
    inv = _tool_inventory_by_name(report)
    risk_highlights: list[str] = []
    control_highlights: list[str] = []
    tool_highlights: list[str] = []
    for item in diff.high_risk_effects:
        if item.kind == "added":
            risk_highlights.append(
                "New high-risk tag "
                f"`{_safe_markdown_text(item.tag)}` on "
                f"`{_safe_markdown_text(item.tool)}`"
                f"{_inventory_citation(inv.get(item.tool))}"
            )
    for item in diff.controls:
        if item.kind == "removed":
            control_highlights.append(
                f"Removed `{_safe_markdown_text(item.control)}` for "
                f"`{_safe_markdown_text(item.tool)}`"
                f"{_inventory_citation(inv.get(item.tool))}"
            )
    for item in diff.tools:
        if item.kind == "added":
            tool_highlights.append(
                f"Added tool `{_safe_markdown_text(item.name)}`"
                f"{_inventory_citation(inv.get(item.name))}"
            )
        elif item.kind == "removed":
            tool_highlights.append(
                f"Removed tool `{_safe_markdown_text(item.name)}`"
                f"{_inventory_citation(inv.get(item.name))}"
            )
    return _interleaved_highlights(
        [control_highlights, risk_highlights, tool_highlights],
        limit=5,
    )


def _tool_inventory_by_name(
    report: ReadinessReport,
) -> dict[str, dict[str, object]]:
    """Build a tool-name → inventory-row index for citation lookups.

    The v0.19 ``tool_inventory`` rows carry ``source_path``,
    ``source_start_line``, and ``source_pointer`` per tool. The
    GitHub Step Summary uses this lookup to append a ``(path:line)``
    suffix to each diff highlight so a reviewer can jump straight to
    the tool definition without scrolling through the inventory.
    """
    index: dict[str, dict[str, object]] = {}
    for entry in report.tool_inventory:
        name = entry.get("name")
        if isinstance(name, str):
            index[name] = entry
    return index


def _inventory_citation(entry: dict[str, object] | None) -> str:
    if entry is None:
        return ""
    path = entry.get("source_path")
    line = entry.get("source_start_line")
    if isinstance(path, str) and isinstance(line, int):
        return f" (`{_safe_markdown_text(f'{path}:{line}')}`)"
    if isinstance(path, str):
        return f" (`{_safe_markdown_text(path)}`)"
    return ""


def _interleaved_highlights(groups: list[list[str]], *, limit: int) -> list[str]:
    highlights: list[str] = []
    while len(highlights) < limit and any(groups):
        for group in groups:
            if group and len(highlights) < limit:
                highlights.append(group.pop(0))
    return highlights
