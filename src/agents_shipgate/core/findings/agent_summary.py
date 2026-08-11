from __future__ import annotations

import shlex

from agents_shipgate.ci.release_decision import evidence_below_ie_threshold
from agents_shipgate.invocation import retarget_command
from agents_shipgate.schemas.report import (
    AgentSummary,
    AgentSummaryAction,
    Finding,
    ReleaseDecision,
)

from .constants import SEVERITY_ORDER


def build_agent_summary(
    *,
    findings: list[Finding],
    release_decision: ReleaseDecision | None,
    json_report_path: str | None = None,
    tool_count: int = 0,
) -> AgentSummary:
    """Construct the top-level ``agent_summary`` block.

    Deterministic projection of ``release_decision`` plus the
    per-finding ``agent_action`` values. Surfaces the same numbers a
    coding agent would otherwise compute by traversing arrays — same
    inputs, same output, no agent-side aggregation needed.

    ``json_report_path`` is the actual on-disk path of the emitted JSON
    report (from ``ReadinessReport.generated_reports['json']``). It is
    threaded in so ``first_recommended_action.command`` can name the
    real path the user just wrote — not the default. When the scan ran
    without JSON output (no path available), the action falls back to
    ``kind: "info"`` with a parameterised hint instead of a command,
    so we never emit an apply-patches invocation pointing at a file
    that doesn't exist or — worse — at a stale default-path report
    from a previous run.
    """
    if release_decision is None:
        verdict: str = "passed"
        blocker_count = 0
        review_item_count = 0
        reason = "No release decision computed."
        evidence_recommended = False
        evidence_below_threshold = False
    else:
        verdict = release_decision.decision
        blocker_count = len(release_decision.blockers)
        review_item_count = len(release_decision.review_items)
        reason = (release_decision.reason or "").strip()
        # `evidence_coverage.human_review_recommended` is the
        # release-decision signal that says "this is review_required
        # because the scan saw only low-confidence/static evidence,
        # not because any specific finding needs fixing." In that
        # case we want to surface the evidence-coverage reason
        # (rather than the unhelpful "0 review items flagged" text)
        # and route the agent toward gathering better evidence
        # (#57 review P2: evidence-only review_required).
        #
        # v0.14 also routes source_warning_count > 0 to review_required
        # via an explicit branch in build_release_decision()
        # (summarize_findings() doesn't fold source warnings into
        # human_review_recommended, so without including them here a
        # source-warning-only scan would render as "0 review item(s)
        # flagged" with no first_recommended_action — losing the
        # release_decision.reason that has the only useful context).
        evidence_recommended = bool(
            release_decision.evidence_coverage
            and (
                release_decision.evidence_coverage.human_review_recommended
                or release_decision.evidence_coverage.source_warning_count > 0
            )
        )
        # `evidence_recommended` is the BROAD signal (any review-worthy
        # evidence gap, including 1-3 sub-threshold source warnings).
        # `evidence_below_threshold` is the NARROW one: evidence weak
        # enough that, absent an active high/critical finding, the verdict
        # would have been `insufficient_evidence`. Since that finding now
        # *elevates* such a case to `review_required` (Phase 2c), the
        # narrow signal is what tells the action picker to put evidence
        # remediation ahead of auto-apply — applying patches never makes a
        # below-threshold scan trustworthy. Uses the same predicate
        # `build_release_decision` does, so the two never disagree.
        evidence_below_threshold = bool(
            release_decision.evidence_coverage
            and evidence_below_ie_threshold(
                release_decision.evidence_coverage, tool_count=tool_count
            )
        )

    active_findings = [f for f in findings if not f.suppressed]
    auto_appliable = sum(
        1 for f in active_findings if f.agent_action == "auto_apply"
    )
    # `needs_human_review` covers every active finding the user has to
    # weigh in on before release: full escalations (no machine path)
    # PLUS proposed patches that ship at medium/low confidence and
    # require an explicit `--apply` after the user reviews the diff.
    # Earlier this counted only `escalate_to_human`, which silently
    # under-counted propose_patch_for_review findings — release_decision
    # already routes both into review_items, so the agent_summary
    # number must agree (#57 review P1).
    needs_review = sum(
        1
        for f in active_findings
        if f.agent_action in {"escalate_to_human", "propose_patch_for_review"}
    )

    # Headline: short, one-sentence statement that names the verdict
    # and the action-driven counts. The two populations differ:
    # `review_item_count` mirrors `release_decision.review_items`
    # (severity-driven; can include medium-severity auto_apply
    # findings), while `needs_human_review` counts only findings whose
    # `agent_action` requires human input. The headline uses
    # `needs_human_review` for the "require human review" wording so a
    # review_required verdict with only auto-applicable findings reads
    # honestly as "auto-applicable; none require human input" instead
    # of falsely claiming N findings need review.
    # `release_decision.reason` is severity-driven and can contradict
    # an action-driven headline (e.g. when only-auto-applicable
    # findings are flagged for release review, the reason often reads
    # "1 finding requires human review" — the opposite of what
    # agent_summary needs to say). We therefore skip the reason append
    # in branches where the headline already explains the agent-level
    # situation in agent-driven terms; we keep the append in branches
    # where the reason adds non-overlapping context (like blocker
    # counts).
    append_reason = True
    if verdict == "blocked":
        headline = (
            f"{blocker_count} active finding(s) block release"
            + (
                f"; {review_item_count} review item(s) accepted as debt."
                if review_item_count
                else "."
            )
        )
        # The blocked release_decision.reason is always "{n} active findings
        # block release." — exactly the blocker count this headline already
        # leads with. Appending it just restates the count (and the headline's
        # review-item clause is strictly more informative), so skip it.
        append_reason = False
    elif verdict == "review_required":
        if needs_review > 0:
            head = f"{needs_review} finding(s) require human review"
            if auto_appliable > 0:
                head += f"; {auto_appliable} also auto-applicable"
            headline = head + "."
        elif auto_appliable > 0 and evidence_recommended:
            # Mixed case: every flagged finding is auto-applicable
            # *but* evidence coverage is incomplete (low-confidence
            # tools or source warnings tipped review_required). Saying
            # "none require human input beyond apply-patches" would
            # silently drop the evidence-review requirement that the
            # release_decision.reason explicitly calls out. Surface
            # both so the agent applies the patches AND asks the
            # human to review the evidence gap.
            evidence_clause = reason or (
                "evidence coverage is incomplete and should be reviewed "
                "before shipping"
            )
            headline = (
                f"{auto_appliable} auto-applicable finding(s) flagged for "
                f"release review; {evidence_clause}"
            )
            if not headline.endswith("."):
                headline += "."
            append_reason = False  # already in headline
        elif auto_appliable > 0:
            headline = (
                f"{auto_appliable} auto-applicable finding(s) flagged for "
                "release review; none require human input beyond apply-patches."
            )
            # Suppress the severity-driven reason here. release_decision
            # likely says something like "N finding(s) require human
            # review" — appending it would directly contradict the
            # action-driven headline (#57 review P1).
            append_reason = False
        elif evidence_recommended:
            # Evidence-coverage-driven review: no actionable findings,
            # but the scan saw only low-confidence/static evidence and
            # the release_decision wants a human to weigh in. Surface
            # the reason directly — it carries the only useful
            # explanation. Falling back to "0 review items flagged"
            # would lose the most important context (#57 review P2).
            headline = (
                reason
                if reason
                else "Human review recommended: low-confidence evidence."
            )
            append_reason = False  # already in headline
        else:
            # Even rarer fallback: review_required without any of the
            # above signals. Surface review_item_count so the
            # headline isn't a self-contradiction.
            headline = (
                f"{review_item_count} review item(s) flagged for release review."
            )
            append_reason = False
        if blocker_count:
            headline += f" ({blocker_count} blocker(s) detected.)"
    elif verdict == "insufficient_evidence":
        # No specific finding to surface — by definition the issue is
        # evidence quality, not findings. Surface the release_decision
        # reason verbatim; it already names the counts and explains why
        # the scan can't gate release. Falling through to the "Release
        # ready" branch would lie about a degraded scan.
        headline = (
            reason
            if reason
            else "Evidence coverage below threshold; scan results not trustworthy enough to gate release."
        )
        append_reason = False
        if blocker_count:
            headline += f" ({blocker_count} blocker(s) detected.)"
    else:
        headline = (
            "Release ready"
            + (
                f" ({review_item_count} review item(s) accepted as debt)."
                if review_item_count
                else "."
            )
        )
    if append_reason and reason and len(headline) + len(reason) + 4 < 240:
        headline = f"{headline} {reason}" if reason.endswith(".") else f"{headline} {reason}."

    first_action = _build_first_recommended_action(
        verdict=verdict,
        auto_appliable=auto_appliable,
        needs_review=needs_review,
        review_item_count=review_item_count,
        active_findings=active_findings,
        json_report_path=json_report_path,
        evidence_recommended=evidence_recommended,
        evidence_below_threshold=evidence_below_threshold,
        evidence_reason=(
            reason
            if (evidence_recommended or verdict == "insufficient_evidence")
            else ""
        ),
    )

    return AgentSummary(
        verdict=verdict,  # type: ignore[arg-type]
        headline=headline,
        blocker_count=blocker_count,
        review_item_count=review_item_count,
        auto_appliable_patches=auto_appliable,
        needs_human_review=needs_review,
        first_recommended_action=first_action,
    )


def _build_first_recommended_action(
    *,
    verdict: str,
    auto_appliable: int,
    needs_review: int,
    review_item_count: int,
    active_findings: list[Finding],
    json_report_path: str | None,
    evidence_recommended: bool = False,
    evidence_below_threshold: bool = False,
    evidence_reason: str = "",
) -> AgentSummaryAction | None:
    """Deterministic next-step picker for ``agent_summary``.

    Order (highest impact first):
    1. Verdict is insufficient_evidence → emit an info action that
       surfaces the evidence reason and recommends gathering deeper
       sources (MCP, OpenAPI inputs, eval traces). Checked before
       auto-apply because applying patches does NOT clear an evidence
       verdict — the scan results are not trustworthy enough to gate
       release, and running apply-patches first would contradict the
       headline. Tell the agent to fix the trust problem before
       cleaning up findings.
    1b. Verdict is review_required BUT evidence is below the IE
       threshold (an active high/critical finding elevated it out of
       insufficient_evidence — Phase 2c) → same as (1): evidence
       remediation + human review of the named concern outrank
       auto-apply. Without this, a report-only consumer following
       agent_summary would be told to run ``apply-patches --apply`` on a
       scan the gate has already said it can't trust.
    2. Auto-applicable patches available → propose ``apply-patches``,
       but only as a ``command`` action when we know the actual JSON
       report path (so the command never points at the wrong file).
       Otherwise emit ``kind: "info"`` with a parameterised hint.
    3. Verdict is blocked → surface the top blocker for review.
    4. Verdict is review_required → walk the top review item.
    5. Verdict is passed → no action (None).
    """
    if verdict == "insufficient_evidence":
        base = (
            evidence_reason
            or "Evidence coverage below threshold; scan results are not "
            "trustworthy enough to gate release."
        )
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"{base} Surface this to the user and gather deeper "
                "evidence (e.g. MCP/OpenAPI inputs, eval traces, "
                "additional source files) before re-running the scan; "
                "applying patches does not clear an evidence verdict, "
                "so no machine-applicable fix is available."
            ),
        )

    if verdict == "review_required" and evidence_below_threshold:
        # An active high/critical finding elevated a below-IE-threshold
        # scan to review_required (Phase 2c). Evidence is still too weak to
        # gate, so — exactly like insufficient_evidence — gathering better
        # evidence and routing the named concern to a human outrank any
        # auto-apply patch. Emitting a runnable apply-patches command here
        # would tell a report-only consumer to "fix" a scan the gate has
        # already said it cannot trust.
        base = (
            evidence_reason
            or "Evidence coverage is below threshold; scan results are not "
            "trustworthy enough to gate release."
        )
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"{base} A human must review the active high/critical "
                "finding(s), and you should gather deeper evidence (e.g. "
                "MCP/OpenAPI inputs, eval traces, additional source files) "
                "before re-running the scan; applying patches does not clear "
                "the evidence gap."
            ),
        )

    if auto_appliable > 0:
        why = (
            f"{auto_appliable} finding(s) carry high-confidence patches "
            "safe to apply without human review."
        )
        if verdict == "review_required" and evidence_recommended:
            # Reaching here means evidence is recommended but only
            # *sub-threshold* (1-3 source warnings, or human-review-
            # recommended without enough low-confidence tools) — the
            # below-threshold case was already intercepted above and
            # routed to evidence remediation. So the patches ARE worth
            # applying here, but the why must still call out the
            # sub-threshold gap so the agent doesn't treat apply-patches
            # as the *only* next step — the human still needs to
            # review the source warnings / low-confidence tools.
            evidence_note = evidence_reason or (
                "Evidence coverage is incomplete (source warnings or "
                "low-confidence tools); review before shipping."
            )
            why = (
                f"{why} Note: {evidence_note} Applying patches does not "
                "address the evidence gap."
            )
        if json_report_path:
            # shlex.quote so paths with spaces (e.g. macOS
            # "/Users/.../My Project/agents-shipgate-reports/report.json")
            # round-trip through shlex.split unchanged. Without the
            # quote, the advertised command splits at the spaces and
            # apply-patches receives garbage --from arguments
            # (#57 review P2).
            quoted_path = shlex.quote(json_report_path)
            return AgentSummaryAction(
                kind="command",
                command=retarget_command(
                    f"agents-shipgate apply-patches --from "
                    f"{quoted_path} --confidence high --apply"
                ),
                why=why,
            )
        # No JSON output on this scan: emit an info action that names
        # the canonical pattern so the agent runs apply-patches against
        # *their* report, not the default path. The user-facing reports
        # path is stable enough (`agents-shipgate-reports/report.json`
        # is the default) that we mention it in the why-text, but as
        # documentation, not a literal command the agent might dispatch.
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"{why} Re-run the scan with --format json (default path "
                "is agents-shipgate-reports/report.json), then: "
                "agents-shipgate apply-patches --from <report.json> "
                "--confidence high --apply."
            ),
        )

    if verdict == "blocked":
        top = _top_active_finding(active_findings)
        if top is None:
            return None
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"Surface {top.check_id} on {top.tool_name or 'agent'} to "
                "the user; release is blocked and no auto-applicable patch "
                "is available."
            ),
        )

    if verdict == "review_required":
        # Evidence-coverage-driven review: no specific finding to walk;
        # the release_decision is asking for human attention because
        # the scan saw only low-confidence/static evidence. Return an
        # info action that names the situation so first_recommended_action
        # is non-null and useful in this case (#57 review P2).
        if (
            evidence_recommended
            and needs_review == 0
            and auto_appliable == 0
        ):
            base = (
                evidence_reason
                or "Static-only scan with low-confidence evidence; "
                "human review recommended."
            )
            return AgentSummaryAction(
                kind="info",
                command=None,
                why=(
                    f"{base} Surface this to the user and discuss whether "
                    "to gather better evidence (e.g. add MCP/OpenAPI "
                    "inputs, eval traces) or accept the static-only "
                    "review posture; no machine-applicable fix is "
                    "available."
                ),
            )

        top = _top_active_finding(active_findings)
        if top is None:
            return None
        # Prefer the action-driven count when there are findings that
        # need human input. Fall back to the severity-driven
        # review_item_count when needs_review is 0 — otherwise the
        # text would read "Walk the 0 review item(s)" even though the
        # release decision has flagged something for review.
        visible = needs_review if needs_review > 0 else review_item_count
        return AgentSummaryAction(
            kind="info",
            command=None,
            why=(
                f"Walk the {visible} review item(s) starting with "
                f"{top.check_id}; release is allowed but the human "
                "reviewer should weigh in."
            ),
        )

    return None


def _top_active_finding(findings: list[Finding]) -> Finding | None:
    """Pick the highest-severity active finding (ties broken by check_id)."""
    if not findings:
        return None
    return min(
        findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.check_id)
    )
