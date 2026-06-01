from __future__ import annotations

import re

from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierCapabilityChange,
    VerifierCapabilityReview,
    VerifierFixTask,
)

from .capability_review import TRUST_ROOT_CHECK_ID

STICKY_MARKER = "<!-- agents-shipgate-pr-comment -->"
_ESCAPE_RE = re.compile(r"([\\\[\]\(\)`*_{}#+\-.!|>])")
_IMPACT_LABELS = {
    "blocks_release": "blocks release",
    "insufficient_evidence": "insufficient evidence",
    "review_required": "review required",
    "informational": "informational",
    "none": "none",
}


def render_pr_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
    style: str = "capability-review",
) -> str:
    if style == "findings":
        return _render_findings_comment(verifier, report=report)
    return _render_capability_review_comment(verifier, report=report)


def _render_capability_review_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
) -> str:
    visible_verdict = _visible_verdict(verifier)
    lines = [STICKY_MARKER, f"## Agents Shipgate: {visible_verdict}"]
    headline = _headline(verifier, report)
    if headline:
        lines.extend(["", f"Headline: {_escape(headline)}"])

    if report is None or report.release_decision is None:
        lines.append("")
        if verifier.head_status == "skipped":
            lines.append("No Shipgate scan was required for this diff.")
        else:
            lines.append(f"Head scan did not produce a report (exit {verifier.head_exit_code}).")
        lines.extend(_trigger_and_base_lines(verifier))
        lines.extend(_artifact_lines(verifier))
        return _truncate("\n".join(lines), 6000)

    decision = report.release_decision
    review = _capability_review(verifier, report)
    lines.extend(
        [
            "",
            f"Decision: `{decision.decision}`",
            f"Reason: {_escape(decision.reason)}",
            (
                "Capability changes: "
                f"+{review.added}, {review.modified} modified, "
                f"-{review.removed}"
            ),
            (
                "Fail policy: "
                f"would_fail_ci=`{str(decision.fail_policy.would_fail_ci).lower()}` "
                f"(exit {decision.fail_policy.exit_code})"
            ),
        ]
    )

    lines.extend(_capability_change_table(review))
    lines.extend(_required_before_merge_lines(report, review, verifier.fix_task))
    lines.extend(_trust_root_warning_lines(review, report))
    lines.extend(_trigger_and_base_lines(verifier))
    lines.extend(_artifact_lines(verifier))
    return _truncate("\n".join(lines), 6000)


def _render_findings_comment(
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
        lines.extend(_artifact_lines(verifier, links=False))
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
    lines.extend(_artifact_lines(verifier, links=False))
    return _truncate("\n".join(lines), 6000)


def _visible_verdict(verifier: VerifierArtifact) -> str:
    return verifier.merge_verdict


def _headline(
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
) -> str | None:
    if verifier.headline:
        return verifier.headline
    if report is not None and report.release_decision is not None:
        return report.release_decision.reason
    return None


def _trigger_and_base_lines(verifier: VerifierArtifact) -> list[str]:
    lines = [
        "",
        f"Trigger: {_escape(verifier.trigger.get('rationale') or 'not evaluated')}",
    ]
    if verifier.base_status != "not_requested":
        base = verifier.base_ref or "(none)"
        lines.append(f"Base diff: `{base}` -> `{verifier.base_status}`")
        for note in verifier.base_notes[:2]:
            lines.append(f"- {_escape(note)}")
    return lines


def _capability_review(
    verifier: VerifierArtifact,
    report: ReadinessReport,
) -> VerifierCapabilityReview:
    return verifier.capability_review


def _capability_change_table(review: VerifierCapabilityReview) -> list[str]:
    lines = ["", "### Capability changes"]
    if not review.top_changes:
        lines.append("No capability changes were detected by the enabled diff surfaces.")
        for note in review.notes[:2]:
            lines.append(f"- {_escape(note)}")
        return lines

    lines.extend(
        [
            "| Impact | Change | Subject | Why |",
            "|---|---|---|---|",
        ]
    )
    for change in review.top_changes[:5]:
        lines.append(
            "| "
            f"{_table_cell(_impact(change))} | "
            f"{_table_cell(_humanize_change(change.change_type))} | "
            f"{_table_cell(_code(change.subject))} | "
            f"{_table_cell(change.rationale)} |"
        )
    return lines


def _trust_root_warning_lines(
    review: VerifierCapabilityReview,
    report: ReadinessReport,
) -> list[str]:
    protected_rows = list(report.protected_surface_changes)
    fallback_warnings = []
    if not protected_rows and review.trust_root_touched:
        fallback_warnings = [
            finding
            for finding in report.findings
            if not finding.suppressed and finding.check_id == TRUST_ROOT_CHECK_ID
        ]
    if not protected_rows and not fallback_warnings and not review.policy_weakened:
        return []
    lines = ["", "### Trust-root warnings"]
    for row in protected_rows[:5]:
        lines.append(
            "- "
            f"{_code(row.path)} ({_escape(row.kind)}): human review is required."
        )
    for finding in fallback_warnings[: max(0, 5 - len(protected_rows))]:
        evidence = finding.evidence or {}
        path = evidence.get("changed_file") or finding.title
        trust_root_class = evidence.get("trust_root_class") or "trust root"
        lines.append(
            "- "
            f"{_code(path)} ({_escape(trust_root_class)}): human review is required."
        )
    if review.policy_weakened:
        lines.append(
            "- Release policy weakening was detected; a human must approve the change."
        )
    if protected_rows or fallback_warnings or review.policy_weakened:
        lines.append(
            "- Do not suppress findings, lower severity, or edit evidence just to make CI pass."
        )
    return lines


def _required_before_merge_lines(
    report: ReadinessReport,
    review: VerifierCapabilityReview,
    fix_task: VerifierFixTask | None = None,
) -> list[str]:
    lines = ["", "### Required before merge"]
    # When verify produced a fix_task it is the authoritative repair contract;
    # render it verbatim so the human surface and verifier.json never tell
    # different stories about who acts next and whether it is safe.
    if fix_task is not None:
        who = "Coding agent" if fix_task.actor == "coding_agent" else "Human"
        safety = (
            "safe for the coding agent to attempt"
            if fix_task.safe_to_attempt
            else "human authority required — a coding agent must not self-resolve"
        )
        lines.append(f"Actor: {who} ({safety}).")
        for index, instruction in enumerate(fix_task.instructions[:6], start=1):
            lines.append(f"{index}. {_escape(instruction)}")
        if fix_task.verification_command:
            lines.append(f"Then re-verify: {_code(fix_task.verification_command)}")
        return lines
    items: list[str] = []
    if review.trust_root_touched or review.policy_weakened:
        items.append("Human: review the release-gate or policy change before merge.")
    if report.agent_summary and report.agent_summary.first_recommended_action:
        action = report.agent_summary.first_recommended_action
        detail = action.why
        if action.command:
            detail = f"{detail} Command: `{action.command}`"
        actor = "Coding agent" if action.kind == "command" else "Human"
        items.append(f"{actor}: {detail}")
    if not items:
        if report.release_decision.decision == "passed":
            items.append("No Shipgate release action is required.")
        else:
            items.append(report.release_decision.reason)
    for index, item in enumerate(items[:5], start=1):
        lines.append(f"{index}. {_escape(item)}")
    return lines


def _impact(change: VerifierCapabilityChange) -> str:
    return _IMPACT_LABELS.get(change.impact, change.impact)


def _humanize_change(value: str) -> str:
    return value.replace("_", " ")


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


def _artifact_lines(verifier: VerifierArtifact, *, links: bool = True) -> list[str]:
    artifacts = verifier.artifacts
    if not artifacts:
        return []
    if not links:
        lines = [
            "",
            "Artifacts:",
            "Available in the `agents-shipgate-reports` workflow artifact.",
        ]
        for key in (
            "report_markdown",
            "report_json",
            "report_sarif",
            "packet_json",
            "verifier_json",
        ):
            if key in artifacts:
                lines.append(f"- `{artifacts[key]}`")
        return lines

    lines = ["", "### Artifacts"]
    lines.append("Available in the `agents-shipgate-reports` workflow artifact.")
    for key in (
        "report_markdown",
        "report_json",
        "report_sarif",
        "packet_json",
        "verifier_json",
        "pr_comment",
    ):
        if key in artifacts:
            label = key.replace("_", ".")
            lines.append(f"- [{label}]({_escape_link(artifacts[key])})")
    return lines


def _escape(value: object) -> str:
    return _ESCAPE_RE.sub(r"\\\1", str(value or ""))


def _table_cell(value: object) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _code(value: object) -> str:
    text = str(value or "").replace("`", "")
    return f"`{text}`"


def _escape_link(value: object) -> str:
    text = str(value or "")
    return text.replace(")", "%29").replace(" ", "%20")


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "..."


__all__ = ["STICKY_MARKER", "render_pr_comment"]
