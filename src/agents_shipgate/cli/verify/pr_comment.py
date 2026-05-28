from __future__ import annotations

import re

from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.verifier import VerifierArtifact

STICKY_MARKER = "<!-- agents-shipgate-pr-comment -->"
_ESCAPE_RE = re.compile(r"([\\\[\]\(\)`*_{}#+\-.!|>])")


def render_pr_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
) -> str:
    lines = [STICKY_MARKER, "## Agents Shipgate", ""]
    lines.append(f"Trigger: {_escape(verifier.trigger.get('rationale') or 'not evaluated')}")
    if verifier.base_status != "not_requested":
        base = verifier.base_ref or "(none)"
        lines.append(f"Base diff: `{base}` -> `{verifier.base_status}`")
        for note in verifier.base_notes[:2]:
            lines.append(f"- {_escape(note)}")

    if report is None or report.release_decision is None:
        if verifier.head_status == "skipped":
            lines.append("")
            lines.append("No Shipgate scan was required for this diff.")
        else:
            lines.append("")
            lines.append(f"Head scan did not produce a report (exit {verifier.head_exit_code}).")
        lines.extend(_artifact_lines(verifier))
        return _truncate("\n".join(lines), 6000)

    decision = report.release_decision
    lines.extend(
        [
            "",
            f"Decision: `{decision.decision}`",
            f"Reason: {_escape(decision.reason)}",
            (
                f"Blockers: {len(decision.blockers)} · "
                f"Review items: {len(decision.review_items)}"
            ),
            (
                "Fail policy: "
                f"would_fail_ci=`{str(decision.fail_policy.would_fail_ci).lower()}` "
                f"(exit {decision.fail_policy.exit_code})"
            ),
        ]
    )
    if report.agent_summary and report.agent_summary.headline:
        lines.append(f"Summary: {_escape(report.agent_summary.headline)}")
    if report.reviewer_summary and report.reviewer_summary.first_recommended_surface:
        surface = report.reviewer_summary.first_recommended_surface
        lines.append(
            f"Reviewer start: `{surface.name}` - {_escape(surface.why)}"
        )

    lines.extend(_diff_lines(report))
    top = _top_findings(report.findings)
    lines.append("")
    if top:
        lines.append("Top findings:")
        for index, finding in enumerate(top, start=1):
            lines.append(f"{index}. {_escape(finding.title or finding.check_id)}")
    else:
        lines.append("No critical or high findings.")
    lines.extend(_artifact_lines(verifier))
    return _truncate("\n".join(lines), 6000)


def _diff_lines(report: ReadinessReport) -> list[str]:
    lines: list[str] = []
    action_diff = report.action_surface_diff
    if action_diff.enabled:
        summary = action_diff.summary
        lines.append("")
        lines.append("### Action Surface Diff")
        lines.append(
            f"Actions: +{summary.actions_added}, "
            f"-{summary.actions_removed}, {summary.actions_modified} modified"
        )
    elif action_diff.notes:
        lines.append(f"Action-surface diff: {_escape(action_diff.notes[0])}")

    tool_diff = report.tool_surface_diff
    if tool_diff.enabled:
        summary = tool_diff.summary
        lines.append("")
        lines.append("### What changed")
        lines.append(
            f"Tools: +{summary.tools_added}, "
            f"-{summary.tools_removed}, {summary.tools_changed} changed"
        )
        lines.append(
            f"Findings: {summary.new_findings} new, "
            f"{summary.resolved_findings} resolved, {summary.accepted_debt} accepted debt"
        )
    elif tool_diff.notes:
        lines.append(f"Tool-surface diff: {_escape(tool_diff.notes[0])}")
    return lines


def _top_findings(findings: list[Finding]) -> list[Finding]:
    severities = {"critical": 0, "high": 1}
    active = [
        finding
        for finding in findings
        if not finding.suppressed and finding.severity in severities
    ]
    return sorted(active, key=lambda item: (severities[item.severity], item.check_id))[:3]


def _artifact_lines(verifier: VerifierArtifact) -> list[str]:
    artifacts = verifier.artifacts
    if not artifacts:
        return []
    lines = [
        "",
        "Artifacts:",
        "Available in the `agents-shipgate-reports` workflow artifact.",
    ]
    for key in ("report_markdown", "report_json", "report_sarif", "packet_json", "verifier_json"):
        if key in artifacts:
            lines.append(f"- `{artifacts[key]}`")
    return lines


def _escape(value: object) -> str:
    return _ESCAPE_RE.sub(r"\\\1", str(value or ""))


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "..."


__all__ = ["STICKY_MARKER", "render_pr_comment"]
