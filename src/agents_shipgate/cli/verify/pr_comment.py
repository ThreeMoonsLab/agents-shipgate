from __future__ import annotations

import re

from agents_shipgate.core.findings.capability import top_capability_changes
from agents_shipgate.schemas.report import ReadinessReport, ReleaseDecision
from agents_shipgate.schemas.verifier import VerifierArtifact

STICKY_MARKER = "<!-- agents-shipgate-pr-comment -->"
_ESCAPE_RE = re.compile(r"([\\\[\]\(\)`*_{}#+\-.!|>])")

_IMPACT_LABELS = {
    "blocks_release": "blocks release",
    "insufficient_evidence": "insufficient evidence",
    "review_required": "review required",
    "informational": "informational",
    "none": "no gate",
}


def render_pr_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
) -> str:
    """Render the capability-review PR comment.

    Leads with the merge verdict and the capability delta — what the PR
    changes about what the agent can do — not a generic findings dump.
    The release gate stays ``release_decision.decision``; this comment is
    a readability layer over it.
    """
    lines = [STICKY_MARKER, f"## Agents Shipgate: {verifier.merge_verdict}", ""]
    lines.append(
        f"Trigger: {_escape(verifier.trigger.get('rationale') or 'not evaluated')}"
    )
    if verifier.base_status != "not_requested":
        base = verifier.base_ref or "(none)"
        lines.append(f"Base diff: `{base}` -> `{verifier.base_status}`")
        for note in verifier.base_notes[:2]:
            lines.append(f"- {_escape(note)}")

    # Preview: a tiny relevance note + the install/verify command.
    if verifier.mode == "preview":
        lines.append("")
        lines.append(_escape(verifier.headline or "Shipgate preview."))
        lines.extend(_next_action_lines(verifier))
        lines.extend(_artifact_lines(verifier))
        return _truncate("\n".join(lines), 6000)

    # Skip / no-report: a tiny comment explaining why Shipgate did not gate.
    if report is None or report.release_decision is None:
        lines.append("")
        if verifier.head_status == "skipped":
            lines.append(
                "No Shipgate scan was required for this diff "
                "(no agent-capability changes detected)."
            )
        else:
            lines.append(
                f"Head scan did not produce a report (exit "
                f"{verifier.head_exit_code}); human review required."
            )
        lines.extend(_artifact_lines(verifier))
        return _truncate("\n".join(lines), 6000)

    decision = report.release_decision
    changes = verifier.capability_changes
    lines.append("")
    lines.append(
        "This PR changes what the agent can do."
        if changes
        else "No agent-capability changes detected in this diff."
    )

    lines.extend(_capability_table(verifier))

    lines.append("")
    lines.append(f"Decision: `{decision.decision}`")
    lines.append(
        f"Can merge without human: `{str(verifier.can_merge_without_human).lower()}`"
    )
    lines.append(
        "Fail policy: "
        f"ci_mode=`{decision.fail_policy.ci_mode}`, "
        f"would_fail_ci=`{str(decision.fail_policy.would_fail_ci).lower()}` "
        f"(exit {decision.fail_policy.exit_code})"
    )
    if verifier.trust_root_touched:
        lines.append(
            "Trust root touched: `true` — this PR changes the rules that "
            "evaluate it; human review required."
        )

    required = _required_before_merge(verifier, decision)
    if required:
        lines.append("")
        lines.append("### Required before merge")
        for index, item in enumerate(required, start=1):
            lines.append(f"{index}. {item}")

    lines.extend(_next_action_lines(verifier))
    lines.extend(_artifact_lines(verifier))
    return _truncate("\n".join(lines), 6000)


def _capability_table(verifier: VerifierArtifact) -> list[str]:
    top = top_capability_changes(verifier.capability_changes, limit=5)
    if not top:
        return []
    lines = ["", "### Capability changes", "", "| Impact | Change | Subject | Why |", "|---|---|---|---|"]
    for change in top:
        impact = _IMPACT_LABELS.get(change.release_impact, change.release_impact)
        change_label = change.change_type.replace("_", " ")
        subject = _code_cell(change.subject)
        why = _cell(change.rationale)
        lines.append(f"| {impact} | {change_label} | {subject} | {why} |")
    return lines


def _required_before_merge(
    verifier: VerifierArtifact, decision: ReleaseDecision
) -> list[str]:
    items: list[str] = []
    for blocker in decision.blockers[:5]:
        items.append(
            f"Resolve blocker: {_escape(blocker.title)} (`{blocker.check_id}`)."
        )
    for review in decision.review_items[: max(0, 5 - len(items))]:
        items.append(f"Review: {_escape(review.title)} (`{review.check_id}`).")
    if (
        not items
        and verifier.human_review is not None
        and verifier.human_review.required
    ):
        items.append(
            _escape(
                verifier.human_review.why
                or "Human review required before merge."
            )
        )
    return items


def _next_action_lines(verifier: VerifierArtifact) -> list[str]:
    action = verifier.first_next_action
    if action is None or (not action.command and not action.why):
        return []
    lines = [""]
    if action.command:
        lines.append(
            f"Next ({action.actor}): `{action.command}` — {_escape(action.why)}"
        )
    else:
        lines.append(f"Next ({action.actor}): {_escape(action.why)}")
    return lines


def _artifact_lines(verifier: VerifierArtifact) -> list[str]:
    artifacts = verifier.artifacts
    if not artifacts:
        return []
    lines = [
        "",
        "Artifacts:",
        "Available in the `agents-shipgate-reports` workflow artifact.",
    ]
    for key in (
        "verifier_json",
        "report_markdown",
        "report_json",
        "report_sarif",
        "packet_json",
    ):
        if key in artifacts:
            lines.append(f"- `{artifacts[key]}`")
    return lines


def _code_cell(value: object) -> str:
    """A code-span table cell: strip backticks/pipes that would break the
    table or the span, then wrap. No markdown escaping inside code spans."""
    text = str(value or "").replace("`", "").replace("|", "/")
    return f"`{text}`"


def _cell(value: object) -> str:
    """A plain table cell: escape markdown AND neutralize the cell delimiter."""
    return _escape(str(value or "")).replace("|", r"\|")


def _escape(value: object) -> str:
    return _ESCAPE_RE.sub(r"\\\1", str(value or ""))


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "..."


__all__ = ["STICKY_MARKER", "render_pr_comment"]
