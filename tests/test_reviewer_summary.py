"""Pin v0.20's `report.reviewer_summary` projection contract.

Parallels ``test_agent_action_summary.py`` but for the reviewer-side
projection (audit/lens dimensions, NOT action-driven).

Covers:
- The two enum surfaces (verdict, surface_name, surface_kind) are exactly
  what STABILITY.md + the contract doc promise (no silent additions).
- ``build_reviewer_summary`` is a deterministic projection of the
  reviewer lens surfaces + audit envelopes — counts match cheaply, and
  ``first_recommended_surface`` follows the documented priority order.
- Determinism: same inputs → byte-identical output.

Schema-level checks (every emitted report carries `reviewer_summary`)
live in ``tests/test_reports.py``; this file pins the *semantics* of the
projection.
"""

from __future__ import annotations

from typing import get_args

from agents_shipgate.core.findings import build_reviewer_summary
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    Misalignment,
    PolicyAudit,
    PrivacyAudit,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReportSummary,
    ReviewerSummary,
    ReviewerSurfacePointer,
    SeverityOverrideAuditEntry,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceDiff,
    ActionSurfaceDiffSummary,
    ToolSurfaceDiff,
    ToolSurfaceDiffSummary,
)

# --- Enum surface contracts --------------------------------------------------

EXPECTED_REVIEWER_VERDICTS = {
    "blocked",
    "review_required",
    "insufficient_evidence",
    "passed",
}


def test_reviewer_summary_verdict_enum_values():
    """ReviewerSummary.verdict mirrors release_decision.decision and
    AgentSummary.verdict. Pin the exact set so additions trip a test
    in the same PR, and so the three places never silently diverge."""
    verdict_field = ReviewerSummary.model_fields["verdict"]
    verdict_values = set(get_args(verdict_field.annotation))
    assert verdict_values == EXPECTED_REVIEWER_VERDICTS, (
        "ReviewerSummary.verdict diverged from the public contract. "
        "If you're adding or removing a value, update STABILITY.md, "
        "docs/agent-contract-current.md, AgentSummary.verdict, and "
        "ReleaseDecisionStatus in the same PR — all four MUST mirror."
    )


EXPECTED_SURFACE_KINDS = {
    "release_decision",
    "lens",
    "audit",
    "evidence_matrix",
}


EXPECTED_SURFACE_NAMES = {
    "tool_surface_diff",
    "capability_intent_diff",
    "action_surface_diff",
    "evidence_matrix",
    "policy_audit",
    "privacy_audit",
    "baseline_integrity",
    "release_decision",
}


def test_reviewer_surface_kind_enum_values():
    """The closed set of surface families a pointer can target."""
    kind_field = ReviewerSurfacePointer.model_fields["kind"]
    assert set(get_args(kind_field.annotation)) == EXPECTED_SURFACE_KINDS


def test_reviewer_surface_name_enum_values():
    """The closed set of surface names. Reviewer-side public contract
    surface; STABILITY.md enumerates the same set."""
    name_field = ReviewerSurfacePointer.model_fields["name"]
    assert set(get_args(name_field.annotation)) == EXPECTED_SURFACE_NAMES, (
        "ReviewerSurfaceName diverged from the public contract. Update "
        "STABILITY.md + docs/agent-contract-current.md in the same PR."
    )


# --- Test fixtures -----------------------------------------------------------


def _release_decision(
    *,
    decision: str = "passed",
    blockers: int = 0,
    review_items: int = 0,
    reason: str = "",
) -> ReleaseDecision:
    """Minimal ReleaseDecision the projection reads from."""
    def item(i: int) -> ReleaseDecisionItem:
        return ReleaseDecisionItem(
            id=f"f_{i}",
            check_id=f"SHIP-X-{i}",
            severity="high",
            title=f"Finding {i}",
        )

    return ReleaseDecision(
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
        blockers=[item(i) for i in range(blockers)],
        review_items=[item(i) for i in range(review_items)],
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=False,
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory",
            fail_on=[],
            would_fail_ci=False,
            exit_code=0,
        ),
    )


def _empty_report(release_decision: ReleaseDecision | None = None) -> ReadinessReport:
    """Minimal ReadinessReport with all lens/audit surfaces at zero
    activity. Fields the projection reads are populated; the rest get
    defaults."""
    return ReadinessReport(
        run_id="run_test",
        project={"name": "test"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(status="clean"),
        release_decision=release_decision or _release_decision(decision="passed"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        policy_audit=PolicyAudit(),
        privacy_audit=PrivacyAudit(
            enabled=False,
            rules_version="0.1",
            sensitive_field_inventory_version="0.1",
        ),
    )


def _override(
    *,
    check_id: str = "SHIP-X",
    tier_crossed: bool = False,
    direction: str = "downgrade",
) -> SeverityOverrideAuditEntry:
    return SeverityOverrideAuditEntry(
        check_id=check_id,
        default_severity="critical",
        applied_severity="high",
        manifest_path=f"#/checks/severity_overrides/{check_id}",
        reason=None,
        tier_crossed=tier_crossed,
        direction=direction,  # type: ignore[arg-type]
        expires=None,
    )


# --- All-clean scan: pointer is None, headline says clean --------------------


def test_clean_scan_pointer_is_none():
    """When every lens/audit count is zero AND release is passed,
    ``first_recommended_surface`` is None and the headline declares
    no signals."""
    report = _empty_report()
    summary = build_reviewer_summary(findings=[], report=report)

    assert summary.verdict == "passed"
    assert summary.first_recommended_surface is None
    assert summary.tool_surface_changes == 0
    assert summary.capability_misalignments == 0
    assert summary.action_surface_changes == 0
    assert summary.evidence_matrix_gaps == 0
    assert summary.severity_overrides_applied == 0
    assert summary.severity_overrides_tier_crossed == 0
    assert summary.privacy_redactions == 0
    assert summary.baseline_integrity_issues == 0
    assert "no reviewer signals" in summary.headline.lower()


# --- Each per-surface count projects correctly -------------------------------


def test_tool_surface_changes_counts_diff_summary():
    """``tool_surface_changes`` sums every structural-change counter in
    tool_surface_diff.summary."""
    report = _empty_report()
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        summary=ToolSurfaceDiffSummary(
            tools_added=2,
            tools_changed=1,
            new_scopes=1,
            new_high_risk_effects=0,
            controls_added=0,
            metadata_changes=0,
        ),
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.tool_surface_changes == 4


def test_tool_surface_changes_returns_zero_when_diff_disabled():
    """A disabled diff (no baseline) projects to zero regardless of any
    summary content."""
    report = _empty_report()
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=False,
        summary=ToolSurfaceDiffSummary(tools_added=5),
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.tool_surface_changes == 0


def test_action_surface_changes_counts_diff_summary():
    """``action_surface_changes`` sums structural-change counters in
    action_surface_diff.summary (NOT blocking_findings — that flows
    into the agent_summary blocker count instead)."""
    report = _empty_report()
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(
            actions_added=1,
            actions_modified=2,
            scope_expansions=1,
            blocking_findings=3,  # excluded from reviewer count
        ),
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.action_surface_changes == 4  # 1+2+1, not +3


def test_capability_misalignments_counts_list():
    report = _empty_report()
    report.misalignments = [
        Misalignment(
            id=f"m{i}",
            kind="policy_gap",
            severity="high",
            policy_requirement="x",
            gap="y",
            release_implication="z",
        )
        for i in range(3)
    ]
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.capability_misalignments == 3


def test_severity_overrides_counts_total_and_tier_crossed():
    """``severity_overrides_applied`` is the total; the tier-crossed
    subset is broken out so reviewers can spot the highest-attention
    rows immediately."""
    report = _empty_report()
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[
            _override(check_id="SHIP-X-1", tier_crossed=False),
            _override(check_id="SHIP-X-2", tier_crossed=True),
            _override(check_id="SHIP-X-3", tier_crossed=True),
        ]
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.severity_overrides_applied == 3
    assert summary.severity_overrides_tier_crossed == 2


def test_privacy_redactions_reads_occurrence_count():
    report = _empty_report()
    report.privacy_audit = PrivacyAudit(
        enabled=True,
        rules_version="0.1",
        sensitive_field_inventory_version="0.1",
        redacted_occurrence_count=7,
        )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.privacy_redactions == 7


def test_privacy_redactions_returns_zero_when_audit_disabled():
    report = _empty_report()
    report.privacy_audit = PrivacyAudit(
        enabled=False,
        rules_version="0.1",
        sensitive_field_inventory_version="0.1",
        redacted_occurrence_count=7,  # ignored when enabled=False
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.privacy_redactions == 0


def _baseline_finding(check_id: str, suppressed: bool = False) -> Finding:
    return Finding(
        check_id=check_id,
        title=check_id,
        severity="medium",
        category="baseline",
        recommendation="x",
        suppressed=suppressed,
    )


def test_baseline_integrity_counts_three_check_ids():
    """All three SHIP-BASELINE-* check IDs count; a non-baseline check
    ID with `BASELINE` in its name does not."""
    findings = [
        _baseline_finding("SHIP-BASELINE-INTEGRITY-MISMATCH"),
        _baseline_finding("SHIP-BASELINE-ENTRY-EXPIRED"),
        _baseline_finding("SHIP-BASELINE-ENTRY-STALE"),
        # Negative control: should NOT count
        _baseline_finding("SHIP-MANIFEST-STALE-SUPPRESSION"),
    ]
    summary = build_reviewer_summary(findings=findings, report=_empty_report())
    assert summary.baseline_integrity_issues == 3


def test_baseline_integrity_excludes_suppressed_findings():
    """Suppressed findings do not count — same convention as
    `agent_summary.needs_human_review`."""
    findings = [
        _baseline_finding("SHIP-BASELINE-INTEGRITY-MISMATCH", suppressed=True),
        _baseline_finding("SHIP-BASELINE-ENTRY-EXPIRED", suppressed=False),
    ]
    summary = build_reviewer_summary(findings=findings, report=_empty_report())
    assert summary.baseline_integrity_issues == 1


# --- first_recommended_surface priority order --------------------------------


def test_pointer_priority_blocked_verdict_wins():
    """Blocked verdict outranks every other signal — the reviewer
    needs the gating findings first, not a lens delta."""
    report = _empty_report(
        release_decision=_release_decision(decision="blocked", blockers=1)
    )
    # Even with a noisy action-surface diff, the pointer routes to
    # release_decision because the gate is blocked.
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=5),
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.kind == "release_decision"
    assert summary.first_recommended_surface.name == "release_decision"


def test_pointer_priority_insufficient_evidence_verdict_wins():
    report = _empty_report(
        release_decision=_release_decision(decision="insufficient_evidence")
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=5),
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "release_decision"


def test_pointer_priority_action_surface_outranks_others():
    """Among non-gate signals: action surface > baseline > tier-crossed
    overrides > misalignments > tool surface > privacy > evidence matrix."""
    report = _empty_report()
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=1),
    )
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True, summary=ToolSurfaceDiffSummary(tools_added=10)
    )
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[_override(tier_crossed=True)]
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "action_surface_diff"


def test_pointer_priority_baseline_integrity_outranks_tier_crossed_override():
    """Baseline tampering ranks above ack'd tier-crossed overrides."""
    report = _empty_report()
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[_override(tier_crossed=True)]
    )
    findings = [_baseline_finding("SHIP-BASELINE-INTEGRITY-MISMATCH")]
    summary = build_reviewer_summary(findings=findings, report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "baseline_integrity"


def test_pointer_priority_tier_crossed_outranks_misalignments():
    report = _empty_report()
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[_override(tier_crossed=True)]
    )
    report.misalignments = [
        Misalignment(
            id="m1",
            kind="policy_gap",
            severity="medium",
            policy_requirement="x",
            gap="y",
            release_implication="z",
        )
    ]
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "policy_audit"


def test_pointer_priority_non_tier_crossed_overrides_do_not_outrank_misalignments():
    """A same-tier override (tier_crossed=False) is informational; the
    pointer routes to misalignments instead."""
    report = _empty_report()
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[_override(tier_crossed=False)]
    )
    report.misalignments = [
        Misalignment(
            id="m1",
            kind="policy_gap",
            severity="medium",
            policy_requirement="x",
            gap="y",
            release_implication="z",
        )
    ]
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "capability_intent_diff"


def test_pointer_priority_misalignments_outrank_tool_surface_changes():
    report = _empty_report()
    report.misalignments = [
        Misalignment(
            id="m1",
            kind="policy_gap",
            severity="medium",
            policy_requirement="x",
            gap="y",
            release_implication="z",
        )
    ]
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True, summary=ToolSurfaceDiffSummary(tools_added=10)
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "capability_intent_diff"


def test_pointer_priority_tool_surface_outranks_privacy():
    report = _empty_report()
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True, summary=ToolSurfaceDiffSummary(tools_added=1)
    )
    report.privacy_audit = PrivacyAudit(
        enabled=True,
        rules_version="0.1",
        sensitive_field_inventory_version="0.1",
        redacted_occurrence_count=5,
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "tool_surface_diff"


def test_pointer_priority_privacy_outranks_evidence_matrix():
    """A privacy redaction signal outranks an evidence-matrix gap (the
    last two priority steps). Asserted with redactions present + a
    minimal report whose evidence matrix produces no ``missing`` rows."""
    report = _empty_report()
    report.privacy_audit = PrivacyAudit(
        enabled=True,
        rules_version="0.1",
        sensitive_field_inventory_version="0.1",
        redacted_occurrence_count=3,
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "privacy_audit"


def test_pointer_falls_through_to_policy_audit_for_non_tier_crossed_overrides():
    """PR #107 P2 regression: a same-tier severity override (the only
    reviewer signal in the scan) must NOT produce a null
    ``first_recommended_surface``. Without the low-priority fallthrough
    in ``_pick_first_recommended_surface``, the headline would say
    "1 audit event" but the pointer would be ``null`` — contradicting
    the contract that ``null`` means a fully clean scan.

    Routes to ``policy_audit`` with a ``why`` text that signals lower
    priority than the tier-crossed case (no acknowledgement was
    required; the override is the user's stated intent).
    """
    report = _empty_report()
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[_override(tier_crossed=False)]
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.severity_overrides_applied == 1
    assert summary.severity_overrides_tier_crossed == 0
    assert summary.first_recommended_surface is not None, (
        "Non-tier-crossed override produced a null pointer despite a "
        "non-zero audit_total — contradicts the schema contract that "
        "null only means a fully clean scan."
    )
    assert summary.first_recommended_surface.name == "policy_audit"
    # The "why" should distinguish this from the tier-crossed case so a
    # reviewer reading the pointer knows the override is lower-attention.
    assert "same-tier" in summary.first_recommended_surface.why.lower() or (
        "upgrade" in summary.first_recommended_surface.why.lower()
    )


def test_pointer_falls_through_for_upgrades_too():
    """An upgrade (direction=upgrade) is also a same-tier-or-cross
    override that the new fallthrough should cover. Asserts the
    pointer is non-null and routes to policy_audit."""
    report = _empty_report()
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[
            _override(tier_crossed=False, direction="upgrade"),
        ]
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.first_recommended_surface is not None
    assert summary.first_recommended_surface.name == "policy_audit"


def test_pointer_is_null_only_when_every_signal_is_zero():
    """The schema contract says ``first_recommended_surface`` is null
    only on a fully clean scan (verdict=passed AND every count zero).
    Regression test for PR #107 P2 and P1(r2): with ANY non-zero signal
    (including a non-passed verdict) the pointer MUST be non-null."""
    # Empty report = all-zero counters + passed verdict → null
    summary = build_reviewer_summary(findings=[], report=_empty_report())
    assert summary.first_recommended_surface is None
    # review_required verdict alone (no other signals) is a non-zero
    # signal that must produce a non-null pointer (PR #107 P1 round 2)
    rr_report = _empty_report(
        release_decision=_release_decision(decision="review_required")
    )
    summary_rr = build_reviewer_summary(findings=[], report=rr_report)
    assert summary_rr.first_recommended_surface is not None, (
        "review_required with all-zero counters produced a null pointer"
    )
    # Any single non-zero signal flips the pointer to non-null
    for setup in [
        # same-tier override (the PR #107 P2 case)
        lambda r: setattr(
            r,
            "policy_audit",
            PolicyAudit(severity_overrides_applied=[_override(tier_crossed=False)]),
        ),
        # privacy redaction
        lambda r: setattr(
            r,
            "privacy_audit",
            PrivacyAudit(
                enabled=True,
                rules_version="0.1",
                sensitive_field_inventory_version="0.1",
                redacted_occurrence_count=1,
            ),
        ),
    ]:
        report = _empty_report()
        setup(report)
        summary = build_reviewer_summary(findings=[], report=report)
        assert summary.first_recommended_surface is not None, (
            f"Single non-zero signal produced a null pointer: "
            f"reviewer_summary={summary.model_dump()}"
        )


# --- PR #107 P1 (round 2) regression: review_required with source warnings ----


def test_pointer_non_null_for_review_required_with_source_warnings():
    """PR #107 P1 (second-round) regression: a scan driven to
    ``review_required`` purely by source warnings (e.g., duplicate
    MCP tool names) has zero findings and all reviewer counters at zero.
    Without the final fallback branch in ``_pick_first_recommended_surface``,
    such a scan emits ``decision=review_required`` but
    ``first_recommended_surface=null`` — contradicting the contract that
    null means ``passed + all-zero``.

    Reproduce: build a report with decision=review_required and every
    counter at zero; assert the pointer is non-null and points at
    release_decision.
    """
    report = _empty_report(
        release_decision=_release_decision(decision="review_required")
    )
    # Zero findings, zero tool_surface_diff, zero action_surface_diff,
    # zero policy_audit, zero privacy_audit, zero misalignments —
    # only the verdict itself signals "not passed".
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.verdict == "review_required"
    assert summary.first_recommended_surface is not None, (
        "review_required verdict with all-zero counters produced a null "
        "pointer — contradicts the contract that null means passed+all-zero."
    )
    assert summary.first_recommended_surface.kind == "release_decision"
    assert summary.first_recommended_surface.name == "release_decision"
    assert summary.first_recommended_surface.path == "report.release_decision"
    assert "release_decision.reason" in summary.first_recommended_surface.why


# --- PR #107 P1 regression: build order vs apply_capability_diff -----------


def test_reviewer_summary_reflects_misalignments_added_after_construction():
    """PR #107 P1 regression: ``misalignments`` are populated by
    ``apply_capability_diff(report, tools)`` AFTER ``build_report``
    returns. The scan pipeline must call ``build_reviewer_summary``
    AFTER ``apply_capability_diff`` so the projection sees the final
    state.

    If a future refactor moves the projection back into
    ``build_report`` it would project from an empty
    ``report.misalignments`` and emit ``capability_misalignments: 0``
    even on reports with dozens of misalignments — the exact bug
    PR #107 P1 caught (``simple_openai_api_agent`` emitted
    ``misalignments: 23`` but ``reviewer_summary.capability_misalignments: 0``).
    This test mimics the scan-pipeline order: build a report with empty
    misalignments, then APPEND misalignments (simulating
    ``apply_capability_diff``), then call ``build_reviewer_summary`` and
    assert the count reflects the post-construction state.
    """
    report = _empty_report()
    # Sanity: empty report has zero misalignments and a null pointer
    summary_pre = build_reviewer_summary(findings=[], report=report)
    assert summary_pre.capability_misalignments == 0
    assert summary_pre.first_recommended_surface is None

    # Simulate apply_capability_diff(report, tools) populating misalignments
    report.misalignments = [
        Misalignment(
            id=f"m{i}",
            kind="policy_gap",
            severity="high",
            policy_requirement="x",
            gap="y",
            release_implication="z",
        )
        for i in range(23)
    ]

    summary_post = build_reviewer_summary(findings=[], report=report)
    assert summary_post.capability_misalignments == 23, (
        "build_reviewer_summary did not pick up misalignments added "
        "after construction — the projection is staleness-prone."
    )
    assert summary_post.first_recommended_surface is not None
    assert summary_post.first_recommended_surface.name == "capability_intent_diff"


# --- Determinism -------------------------------------------------------------


def test_build_reviewer_summary_is_deterministic():
    """Same inputs → byte-identical JSON. Mirrors the
    ``build_agent_summary`` determinism contract."""
    report = _empty_report(
        release_decision=_release_decision(decision="review_required", review_items=1)
    )
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[
            _override(check_id="SHIP-X-1", tier_crossed=True),
            _override(check_id="SHIP-X-2", tier_crossed=False),
        ]
    )
    report.privacy_audit = PrivacyAudit(enabled=True, rules_version="0.1", sensitive_field_inventory_version="0.1", redacted_occurrence_count=2)
    summary_a = build_reviewer_summary(findings=[], report=report)
    summary_b = build_reviewer_summary(findings=[], report=report)
    assert summary_a.model_dump(mode="json") == summary_b.model_dump(mode="json")


# --- Headline templates ------------------------------------------------------


def test_headline_blocked_with_signals():
    report = _empty_report(
        release_decision=_release_decision(decision="blocked", blockers=2)
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True, summary=ActionSurfaceDiffSummary(actions_added=1)
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.headline.startswith("Release blocked")
    assert "lens change" in summary.headline


def test_headline_review_required_with_audits():
    report = _empty_report(
        release_decision=_release_decision(decision="review_required")
    )
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[_override(tier_crossed=True)]
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.headline.startswith("Review required")
    assert "audit event" in summary.headline


def test_headline_passed_clean():
    report = _empty_report(release_decision=_release_decision(decision="passed"))
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.headline.startswith("Release ready")
    assert "no reviewer signals" in summary.headline.lower()


def test_headline_stays_under_200_chars():
    """Hard cap to keep the headline PR-comment-friendly. Built by
    truncating the optional pointer suffix if it would push us over."""
    report = _empty_report(
        release_decision=_release_decision(decision="blocked", blockers=99)
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(
            actions_added=99,
            actions_modified=99,
            scope_expansions=99,
        ),
    )
    report.policy_audit = PolicyAudit(
        severity_overrides_applied=[
            _override(check_id=f"SHIP-X-{i}", tier_crossed=True) for i in range(20)
        ]
    )
    summary = build_reviewer_summary(findings=[], report=report)
    assert len(summary.headline) <= 200


# --- Mirror invariant: cannot disagree with underlying data -----------------


def test_reviewer_summary_verdict_mirrors_release_decision():
    """``reviewer_summary.verdict`` MUST equal
    ``release_decision.decision`` on every emitted scan. This block is
    a deterministic projection — it cannot disagree."""
    for decision in EXPECTED_REVIEWER_VERDICTS:
        report = _empty_report(release_decision=_release_decision(decision=decision))
        summary = build_reviewer_summary(findings=[], report=report)
        assert summary.verdict == decision, (
            f"reviewer_summary.verdict={summary.verdict!r} != "
            f"release_decision.decision={decision!r}"
        )


def test_reviewer_summary_verdict_defaults_to_passed_when_no_release_decision():
    """A minimal report without a release_decision (older test fixtures,
    SARIF-only callers) projects to ``passed``. Matches the
    ``build_agent_summary`` convention."""
    report = _empty_report()
    report.release_decision = None
    summary = build_reviewer_summary(findings=[], report=report)
    assert summary.verdict == "passed"


# --- Schema-required surface check -----------------------------------------


def test_reviewer_summary_block_is_required_on_emitted_reports():
    """``reviewer_summary`` is schema-required + non-nullable on emitted
    scans. The Pydantic model declares it Optional only so older test
    fixtures can construct minimal ReadinessReports — but the JSON
    schema (``docs/report-schema.v0.20.json``) marks it required and
    pins ``$ref`` so a payload missing the field fails validation."""
    import json
    from pathlib import Path

    schema_path = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "report-schema.v0.20.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "reviewer_summary" in schema["required"]
    # Pinned to the $ref, not anyOf-null
    assert schema["properties"]["reviewer_summary"] == {
        "$ref": "#/$defs/ReviewerSummary"
    }
    # Each of the 11 ReviewerSummary fields is required on the wire
    rs_required = set(schema["$defs"]["ReviewerSummary"]["required"])
    assert rs_required == {
        "verdict",
        "headline",
        "tool_surface_changes",
        "capability_misalignments",
        "action_surface_changes",
        "evidence_matrix_gaps",
        "severity_overrides_applied",
        "severity_overrides_tier_crossed",
        "privacy_redactions",
        "baseline_integrity_issues",
        "first_recommended_surface",
    }
