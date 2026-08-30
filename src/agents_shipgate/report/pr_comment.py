from __future__ import annotations

import json
import re

from agents_shipgate.core.declaration_questions import progress_sentence
from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.findings.subject_rollup import (
    roll_up_findings,
    top_findings_block,
)
from agents_shipgate.report.capability_lock_diff_markdown import (
    render_capability_lock_diff_markdown,
)
from agents_shipgate.report.human_order import (
    HumanArtifactContext,
    capability_delta_by_subject,
    should_render_surface_first,
    surface_lead,
)
from agents_shipgate.schemas.capabilities import CapabilityLockDiffV1
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierCapabilityChange,
    VerifierCapabilityReview,
    VerifierFixTask,
)

STICKY_MARKER = "<!-- agents-shipgate-pr-comment -->"
TRUST_ROOT_CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
_ESCAPE_RE = re.compile(r"([\\\[\]\(\)`*_{}#+\-.!|>])")
_IMPACT_LABELS = {
    "blocks_release": "blocks release",
    "insufficient_evidence": "insufficient evidence",
    "review_required": "review required",
    "informational": "informational",
    "none": "none",
}
_COMMENT_MAX_CHARS = 6000

# Budget for the grouped summary inside a comment that is truncated at
# ``_COMMENT_MAX_CHARS``. Narrower than ``report.md`` on purpose: this block
# exists to tell a reviewer which subjects to open, and the report it links to
# carries the rest. One budget for both comment styles — a PR comment is one
# surface however it is laid out.
_COMMENT_SUBJECT_LIMIT = 4
_COMMENT_ROW_LIMIT = 3


def render_pr_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
    style: str = "capability-review",
    capability_lock_diff: CapabilityLockDiffV1 | None = None,
    human_context: HumanArtifactContext | None = None,
) -> str:
    if style == "findings":
        return _render_findings_comment(
            verifier,
            report=report,
            human_context=human_context,
        )
    return _render_capability_review_comment(
        verifier,
        report=report,
        capability_lock_diff=capability_lock_diff,
        human_context=human_context,
    )


def _render_capability_review_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
    capability_lock_diff: CapabilityLockDiffV1 | None,
    human_context: HumanArtifactContext | None,
) -> str:
    prose_lines = [
        STICKY_MARKER,
        "## Agents Shipgate",
        *_human_summary_lines(
            verifier,
            report=report,
            capability_lock_diff=capability_lock_diff,
            human_context=human_context,
        ),
    ]
    agent_block = _agent_instruction_block(verifier)
    comment = "\n".join([*prose_lines, *agent_block])
    if len(comment) <= _COMMENT_MAX_CHARS:
        return comment

    compact_agent_block = _agent_instruction_block(verifier, compact=True)
    return _join_with_preserved_agent_block(
        prose_lines,
        compact_agent_block,
        limit=_COMMENT_MAX_CHARS,
    )


def _human_summary_lines(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
    capability_lock_diff: CapabilityLockDiffV1 | None,
    human_context: HumanArtifactContext | None,
) -> list[str]:
    lines = ["", "### Human summary"]
    surface_first = bool(
        report is not None and should_render_surface_first(report, context=human_context)
    )
    if surface_first and report is not None:
        lines.extend(_cold_reader_lines(report))
    lines.append(f"- Merge verdict: `{verifier.merge_verdict}`")
    lines.append(f"- Can merge without human: `{str(verifier.can_merge_without_human).lower()}`")
    lines.append(f"- Agent control state: `{verifier.control.state}`")
    lines.append(f"- Agent must stop: `{str(verifier.control.must_stop).lower()}`")
    # Publishing this pull request and merging it are separate authorities.
    # Say so on the surface a reviewer actually reads, so "the agent updated
    # the PR" is never mistaken for "the gate let it through".
    permissions = verifier.control.permissions
    lines.append(f"- Agent may update this PR: `{str(permissions.update_pr).lower()}`")
    lines.append(f"- Agent may merge: `{str(permissions.merge).lower()}`")
    if verifier.control.stop_reason:
        lines.append(f"- Agent stop reason: `{verifier.control.stop_reason}`")

    headline = _headline(verifier, report)
    if headline:
        lines.append(f"- Summary: {_escape(headline)}")

    if report is None or report.release_decision is None:
        if verifier.head_status == "skipped":
            lines.append("- Release gate: `not_applicable`")
            lines.append("- Reason: No Shipgate scan was required for this diff.")
        else:
            lines.append("- Release gate: `unknown`")
            lines.append(
                f"- Reason: Head scan did not produce a report (exit {verifier.head_exit_code})."
            )
        lines.extend(_next_actor_lines(verifier))
        lines.extend(_trigger_and_base_summary(verifier))
        lines.extend(_artifact_summary_lines(verifier))
        return lines

    decision = report.release_decision
    review = _capability_review(verifier, report)
    lines.append(f"- Release gate: `{decision.decision}`")
    lines.append(f"- Reason: {_escape(decision.reason)}")
    lines.append(
        f"- Capability delta: +{review.added}, {review.modified} modified, -{review.removed}"
    )
    if capability_lock_diff is not None:
        summary = capability_lock_diff.summary
        lines.append(
            "- Capability lock diff: "
            f"+{summary.added}, -{summary.removed}, "
            f"{summary.changed} changed, "
            f"{summary.reidentified} reidentified, "
            f"{summary.evidence_changed} evidence-only"
        )
    # How much of the declaration work is left, on the surface a reviewer of an
    # adopting repository reads. A gap tally says what is wrong; this says what
    # remains, which is the only one of the two the author can finish (#410).
    # Omitted entirely when nothing was ever asked, so a repository that owes
    # no declarations gains no line.
    progress = progress_sentence(
        decision.evidence_coverage.semantic_coverage.declaration_questions
    )
    if progress:
        lines.append(f"- {_escape(progress)}")
    lines.append(
        "- Fail policy: "
        f"would_fail_ci=`{str(decision.fail_policy.would_fail_ci).lower()}` "
        f"(exit {decision.fail_policy.exit_code})"
    )
    lines.append(f"- Static-verdict boundary: {_escape(STATIC_VERDICT_DISCLAIMER)}")
    lines.extend(_next_actor_lines(verifier))
    if review.top_changes and not surface_first:
        lines.append("- Top capability changes:")
        for change in review.top_changes[:5]:
            source = _source_suffix(change.source_path, change.source_start_line)
            lines.append(
                "  - "
                f"`{change.subject}`: {_escape(_impact(change))}; "
                f"{_escape(change.rationale)}{source}"
            )
    elif review.notes and not surface_first:
        lines.append(f"- Capability delta note: {_escape(review.notes[0])}")
    if not surface_first:
        lines.extend(_subject_rollup_lines(report))
    if review.trust_root_touched or review.policy_weakened:
        if review.trust_root_touched:
            lines.append("- Trust root touched: `true`")
        if review.policy_weakened:
            lines.extend(_policy_change_lines(review))
    lines.extend(_trigger_and_base_summary(verifier))
    lines.extend(_artifact_summary_lines(verifier))
    return lines


def _cold_reader_lines(report: ReadinessReport) -> list[str]:
    lines = [f"- {_escape(line)}" for line in surface_lead(report).text_lines()]
    groups = capability_delta_by_subject(report)
    if groups:
        lines.append("- Capability changes by subject:")
        for group in groups:
            lines.append(f"  - `{_escape(group.subject)}`")
            for change in group.changes:
                lines.append(f"    - {_escape(change)}")
    lines.extend(_subject_rollup_lines(report))
    return lines


def _subject_rollup_lines(report: ReadinessReport) -> list[str]:
    """The grouped findings block, for the renderer a reviewer actually gets.

    ``capability-review`` is the default style and ``findings`` is the legacy
    one being retired, so putting the #364 rollup only in the latter would
    have shipped the change to nobody: what this comment told a reviewer was
    what *moved*, with no view of what is wrong per tool.  Both styles render
    the same projection at the same budget now — a PR comment is one surface
    however it is laid out.

    Nested one level, because everything in this comment is a bullet under
    "Human summary", and omitted entirely when nothing is selected: a
    repository with no critical or high finding and nothing named by the
    decision gains no line.
    """

    groups = roll_up_findings(report)
    if not groups:
        return []
    block = top_findings_block(
        groups,
        group_limit=_COMMENT_SUBJECT_LIMIT,
        row_limit=_COMMENT_ROW_LIMIT,
        escape=_escape,
        heading="Findings by subject",
        bullet="  - ",
        row_prefix="    - ",
    )
    return [f"- {block[0]}", *block[1:]]


def _next_actor_lines(verifier: VerifierArtifact) -> list[str]:
    action = verifier.first_next_action
    if action is None:
        return ["- Next actor: `human`"]
    lines = [f"- Next actor: `{action.actor}`"]
    if action.why:
        lines.append(f"- Next action: {_escape(action.why)}")
    if action.command:
        lines.append(f"- Next command: {_code(action.command)}")
    return lines


def _trigger_and_base_summary(verifier: VerifierArtifact) -> list[str]:
    lines = [f"- Trigger: {_escape(verifier.trigger.get('rationale') or 'not evaluated')}"]
    if verifier.base_status != "not_requested":
        base = verifier.base_ref or "(none)"
        lines.append(f"- Base diff: `{base}` -> `{verifier.base_status}`")
        for note in verifier.base_notes[:2]:
            lines.append(f"  - {_escape(note)}")
    return lines


def _artifact_summary_lines(verifier: VerifierArtifact) -> list[str]:
    if not verifier.artifacts:
        return []
    lines = ["- Artifacts:"]
    for key in (
        "verifier_json",
        "verify_run_json",
        "agent_handoff_json",
        "report_json",
        "report_sarif",
        "packet_json",
        "capability_lock_json",
        "base_capability_lock_json",
        "capability_lock_diff_json",
        "capability_lock_diff_markdown",
    ):
        value = verifier.artifacts.get(key)
        if value:
            label = key.replace("_", ".")
            lines.append(f"  - [{label}]({_escape_link(value)})")
    return lines


def _agent_instruction_block(
    verifier: VerifierArtifact,
    *,
    compact: bool = False,
) -> list[str]:
    payload = _agent_instruction_payload(verifier, compact=compact)
    return [
        "",
        "### Agent instruction block",
        "```json",
        json.dumps(payload, indent=2, sort_keys=True),
        "```",
    ]


def _agent_instruction_payload(
    verifier: VerifierArtifact,
    *,
    compact: bool,
) -> dict[str, object]:
    verifier_json = verifier.artifacts.get("verifier_json")
    handoff_json = verifier.artifacts.get("agent_handoff_json")
    fix_task = verifier.fix_task.model_dump(mode="json") if verifier.fix_task is not None else None
    control = verifier.control.model_dump(mode="json")
    if compact:
        if fix_task is not None:
            fix_task = _artifact_pointer(
                handoff_json or verifier_json,
                "fix_task omitted from PR comment size budget; read agent-handoff.json or verifier.json.fix_task.",
            )
    payload = {
        "agent_handoff": handoff_json,
        "merge_verdict": verifier.merge_verdict,
        "can_merge_without_human": verifier.can_merge_without_human,
        "control": control,
        "fix_task": fix_task,
        "verification_command": (
            verifier.fix_task.verification_command if verifier.fix_task is not None else None
        ),
    }
    return payload


def _artifact_pointer(artifact: str | None, reason: str) -> dict[str, object]:
    return {
        "omitted": True,
        "reason": reason,
        "artifact": artifact,
    }


def _join_with_preserved_agent_block(
    prose_lines: list[str],
    agent_block: list[str],
    *,
    limit: int,
) -> str:
    block = "\n".join(agent_block)
    prose = "\n".join(prose_lines)
    budget = limit - len(block) - 1
    if budget > 0:
        prose = _truncate(prose, budget).rstrip()
    else:
        prose = "\n".join(
            [
                STICKY_MARKER,
                "## Agents Shipgate",
                "",
                "### Human summary",
                "- Summary: PR comment prose omitted; read verifier.json for the full review.",
            ]
        )
    return f"{prose}\n{block}"


def _source_suffix(path: str | None, line: int | None) -> str:
    if not path:
        return ""
    if line is not None:
        return f" ({path}:{line})"
    return f" ({path})"


def _render_findings_comment(
    verifier: VerifierArtifact,
    *,
    report: ReadinessReport | None,
    human_context: HumanArtifactContext | None,
) -> str:
    surface_first = bool(
        report is not None and should_render_surface_first(report, context=human_context)
    )
    title = (
        "## Agents Shipgate"
        if surface_first
        else f"## Agents Shipgate result: {verifier.merge_verdict}"
    )
    lines = [STICKY_MARKER, title]
    if surface_first and report is not None:
        lines.extend(["", *_cold_reader_lines(report)])
    lines.extend(_verifier_lead(verifier))
    lines.append("")
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
            f"Release gate: `{decision.decision}`",
            f"Reason: {_escape(decision.reason)}",
            (f"Blockers: {len(decision.blockers)} · Review items: {len(decision.review_items)}"),
            (
                "Fail policy: "
                f"would_fail_ci=`{str(decision.fail_policy.would_fail_ci).lower()}` "
                f"(exit {decision.fail_policy.exit_code})"
            ),
            f"Static-verdict boundary: {_escape(STATIC_VERDICT_DISCLAIMER)}",
        ]
    )
    if report.agent_summary and report.agent_summary.headline:
        lines.append(f"Summary: {_escape(report.agent_summary.headline)}")
    if report.reviewer_summary and report.reviewer_summary.first_recommended_surface:
        surface = report.reviewer_summary.first_recommended_surface
        lines.append(f"Reviewer start: `{surface.name}` - {_escape(surface.why)}")

    if not surface_first:
        lines.extend(_diff_lines(report))
    groups = roll_up_findings(report)
    lines.append("")
    if groups and not surface_first:
        # Grouped by subject (#364). A reviewer reads this comment to decide
        # what to look at, and three rows of one check family on three sibling
        # tools names one thing to look at while the other four go unmentioned.
        lines.extend(
            top_findings_block(
                groups,
                group_limit=_COMMENT_SUBJECT_LIMIT,
                row_limit=_COMMENT_ROW_LIMIT,
                escape=_escape,
                bullet="- ",
                row_prefix="  - ",
            )
        )
    elif not surface_first:
        lines.append("No critical or high findings.")
    lines.extend(_artifact_lines(verifier, links=False))
    return _truncate("\n".join(lines), 6000)


def _verifier_lead(verifier: VerifierArtifact) -> list[str]:
    lines = [
        "",
        f"Merge verdict: `{verifier.merge_verdict}`",
        f"Can merge without human: `{str(verifier.can_merge_without_human).lower()}`",
    ]
    if verifier.decision:
        lines.append(f"Release gate: `{verifier.decision}`")
    if verifier.human_review is not None and verifier.human_review.required:
        why = verifier.human_review.why or "Human review required before merge."
        lines.append(f"Human review: `{_escape(why)}`")
    return lines


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


def _capability_lock_diff_lines(diff: CapabilityLockDiffV1) -> list[str]:
    rendered = render_capability_lock_diff_markdown(
        diff,
        heading_level=3,
        max_rows=5,
    ).rstrip()
    if not rendered:
        return []
    return ["", *rendered.splitlines()]


def _policy_change_lines(review: VerifierCapabilityReview) -> list[str]:
    """Say what the run established about the policy, not how it routed it.

    ``policy_weakened`` is the fail-closed routing flag: it is raised whenever
    the direction may have moved, including when there was no base policy to
    compare against at all. Printing it as ``Policy weakened: true`` reported a
    proven weakening on every first adoption — the one audience least able to
    tell the difference. The route is unchanged; only the claim is now taken
    from ``policy_weakening_proven``, the narrower fact that a comparison
    actually ran.
    """

    if review.policy_weakening_proven:
        return ["- Policy weakened: `true`"]
    return [
        "- Policy changed, weakening unproven: `true` "
        "(no base policy was available to compare against; "
        "routed to human review as if weakened)"
    ]


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
        lines.append(f"- {_code(row.path)} ({_escape(row.kind)}): human review is required.")
    for finding in fallback_warnings[: max(0, 5 - len(protected_rows))]:
        evidence = finding.evidence or {}
        path = evidence.get("changed_file") or finding.title
        trust_root_class = evidence.get("trust_root_class") or "trust root"
        lines.append(f"- {_code(path)} ({_escape(trust_root_class)}): human review is required.")
    if review.policy_weakened:
        lines.append(
            "- Release policy weakening was detected; a human must approve the change."
            if review.policy_weakening_proven
            else (
                "- The release policy changed and no base policy was available to prove "
                "the change does not weaken the gate; a human must approve it."
            )
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
            "capability_lock_json",
            "base_capability_lock_json",
            "capability_lock_diff_json",
            "capability_lock_diff_markdown",
            "verifier_json",
            "verify_run_json",
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
        "capability_lock_json",
        "base_capability_lock_json",
        "capability_lock_diff_json",
        "capability_lock_diff_markdown",
        "verifier_json",
        "verify_run_json",
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
    if len(value) <= limit:
        return value
    if limit <= 3:
        return "." * max(limit, 0)
    return value[: limit - 3] + "..."


__all__ = ["STICKY_MARKER", "render_pr_comment"]
