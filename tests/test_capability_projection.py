"""v0.22 capability-change projection (core/findings/capability.py).

The projection answers "what changed about agent capability?" by rolling up
the action/tool surface diffs. Its load-bearing contract is one-decision-
engine: ``release_impact`` is a pure READ of ``release_decision`` /
``Finding.blocks_release`` — it never invents a blocker, and never mutates a
Finding or a diff row (both feed byte-stable fingerprints).
"""

from __future__ import annotations

from agents_shipgate.core.findings.capability import (
    build_capability_changes,
    top_capability_changes,
)
from agents_shipgate.core.findings.reviewer_summary import build_reviewer_summary
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReportSummary,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ActionSurfaceDiffSummary,
    ToolSurfaceDiff,
    ToolSurfaceHighRiskEffectChange,
    ToolSurfaceScopeChange,
    ToolSurfaceToolChange,
)


def _report(**kw) -> ReadinessReport:
    return ReadinessReport(
        run_id="r",
        project={"name": "demo"},
        agent={"name": "agent"},
        environment={"target": "local"},
        summary=ReportSummary(status="x"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        **kw,
    )


def _release_decision(decision, *, blockers=(), review_items=()):
    return ReleaseDecision(
        decision=decision,
        reason="",
        blockers=list(blockers),
        review_items=list(review_items),
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=decision != "passed",
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(ci_mode="advisory", would_fail_ci=False, exit_code=0),
    )


def _added_refund_change():
    return ActionSurfaceChange(
        type="ACTION_ADDED",
        action_id="a1",
        tool_name="stripe.create_refund",
        operation="create",
        severity="critical",
        reason="New money-moving action lacks approval and idempotency evidence.",
        after={
            "tool_name": "stripe.create_refund",
            "operation": "create",
            "effect": "financial_write",
            # historical synonym — must canonicalize to financial_write
            "risk_tags": ["financial_action"],
        },
    )


def test_action_added_projects_money_movement_blocked():
    change = _added_refund_change()
    finding = Finding(
        id="fp_a1",
        fingerprint="fpa1",
        check_id="SHIP-ACTION-APPROVAL-MISSING",
        title="Money-moving action without approval",
        severity="critical",
        category="action",
        tool_name="stripe.create_refund",
        evidence={"action_id": "a1"},
        recommendation="Add approval policy.",
        blocks_release=True,
        provenance_kind="static_declaration",
        confidence="high",
    )
    rd = _release_decision(
        "blocked",
        blockers=[
            ReleaseDecisionItem(
                check_id="SHIP-ACTION-APPROVAL-MISSING",
                severity="critical",
                title="Money-moving action without approval",
                fingerprint="fpa1",
                blocks_release=True,
            )
        ],
    )
    report = _report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            summary=ActionSurfaceDiffSummary(actions_added=1),
            added=[change],
        ),
        release_decision=rd,
        findings=[finding],
    )

    changes = build_capability_changes(report=report, findings=[finding])

    assert len(changes) == 1
    c = changes[0]
    assert c.change_type == "action_added"
    assert c.subject_kind == "action"
    assert c.subject == "stripe.create_refund"
    assert "financial_write" in c.risk_tags  # canonicalized from financial_action
    assert c.release_impact == "blocks_release"
    assert c.related_finding_ids == ["fp_a1"]
    assert c.rationale == change.reason


def test_release_impact_never_invents_a_blocker():
    """A critical ACTION_ADDED with NO gating finding must surface as
    informational — never blocks_release. release_impact is a read of the
    gate, not a second decision engine."""
    change = _added_refund_change()
    report = _report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            summary=ActionSurfaceDiffSummary(actions_added=1),
            added=[change],
        ),
        release_decision=_release_decision("passed"),
        findings=[],
    )
    changes = build_capability_changes(report=report, findings=[])
    assert changes[0].release_impact == "informational"


def test_approval_removed_maps_to_policy_change():
    change = ActionSurfaceChange(
        type="APPROVAL_REMOVED",
        action_id="a2",
        tool_name="stripe.create_refund",
        severity="critical",
        reason="Approval requirement removed.",
    )
    report = _report(
        action_surface_diff=ActionSurfaceDiff(enabled=True, modified=[change]),
        release_decision=_release_decision("review_required"),
        findings=[],
    )
    changes = build_capability_changes(report=report, findings=[])
    assert changes[0].change_type == "approval_policy_removed"
    assert changes[0].subject_kind == "policy"
    assert "approval_missing" in changes[0].risk_tags


def test_multiple_modifications_for_same_action_have_unique_ids():
    report = _report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            modified=[
                ActionSurfaceChange(
                    type="SCOPE_EXPANDED",
                    action_id="a2",
                    tool_name="stripe.create_refund",
                    severity="high",
                    reason="Action scope expanded.",
                    before=["stripe:refunds:read"],
                    after=["stripe:*"],
                    added=["stripe:*"],
                ),
                ActionSurfaceChange(
                    type="EFFECT_ESCALATED",
                    action_id="a2",
                    tool_name="stripe.create_refund",
                    severity="critical",
                    reason="Action effect escalated.",
                    before="read",
                    after="financial_write",
                ),
            ],
        ),
        release_decision=_release_decision("review_required"),
        findings=[],
    )

    changes = build_capability_changes(report=report, findings=[])

    assert len(changes) == 2
    assert len({change.id for change in changes}) == 2


def test_tool_and_scope_changes_project():
    tsd = ToolSurfaceDiff(
        enabled=True,
        tools=[ToolSurfaceToolChange(kind="added", name="gmail.send")],
        high_risk_effects=[
            ToolSurfaceHighRiskEffectChange(
                kind="added", tool="gmail.send", tag="external_communication"
            )
        ],
        scopes=[
            ToolSurfaceScopeChange(
                kind="added",
                scope="https://www.googleapis.com/auth/gmail.send",
                scope_kind="tool_required",
                tool_names=["gmail.send"],
                broad=True,
            )
        ],
    )
    report = _report(
        tool_surface_diff=tsd, release_decision=_release_decision("review_required")
    )
    changes = build_capability_changes(report=report, findings=[])
    by_type = {c.change_type: c for c in changes}
    assert by_type["tool_added"].subject == "gmail.send"
    assert "external_communication" in by_type["tool_added"].risk_tags
    assert by_type["scope_added"].subject.endswith("gmail.send")
    assert "broad_scope" in by_type["scope_added"].risk_tags


def test_disabled_diffs_yield_no_changes():
    # Plain scan with no base to diff against: surface diffs are disabled.
    assert build_capability_changes(report=_report(), findings=[]) == []


def test_projection_is_deterministic_and_non_mutating():
    change = _added_refund_change()
    report = _report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            summary=ActionSurfaceDiffSummary(actions_added=1),
            added=[change],
        ),
        release_decision=_release_decision("review_required"),
        findings=[],
    )
    before = report.action_surface_diff.model_dump(mode="json")
    first = [c.model_dump(mode="json") for c in build_capability_changes(report=report, findings=[])]
    second = [c.model_dump(mode="json") for c in build_capability_changes(report=report, findings=[])]
    assert first == second  # byte-stable for the same input
    assert report.action_surface_diff.model_dump(mode="json") == before  # no mutation


def test_top_capability_changes_orders_by_impact_and_caps():
    def _row(impact_kind, action_id, severity):
        return ActionSurfaceChange(
            type="ACTION_ADDED",
            action_id=action_id,
            tool_name=f"tool.{action_id}",
            severity=severity,
            reason="x",
            after={"risk_tags": []},
        )

    # Six added actions; only one is a blocker (via a gating finding).
    rows = [_row("x", f"a{i}", "medium") for i in range(6)]
    rows[3] = ActionSurfaceChange(
        type="ACTION_ADDED",
        action_id="a3",
        tool_name="tool.blocker",
        severity="critical",
        reason="blocks",
        after={"risk_tags": ["financial_action"]},
    )
    finding = Finding(
        id="fp_b",
        fingerprint="fpb",
        check_id="SHIP-X",
        title="t",
        severity="critical",
        category="action",
        tool_name="tool.blocker",
        evidence={"action_id": "a3"},
        recommendation="r",
        blocks_release=True,
    )
    report = _report(
        action_surface_diff=ActionSurfaceDiff(enabled=True, added=rows),
        release_decision=_release_decision("blocked"),
        findings=[finding],
    )
    changes = build_capability_changes(report=report, findings=[finding])
    top = top_capability_changes(changes, limit=5)
    assert len(top) == 5
    assert top[0].release_impact == "blocks_release"
    assert top[0].subject == "tool.blocker"


def test_reviewer_summary_capability_count_matches_projection():
    """§8 canonical ownership: reviewer_summary.capability_changes is a pure
    count of report.capability_changes."""
    change = _added_refund_change()
    report = _report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            summary=ActionSurfaceDiffSummary(actions_added=1),
            added=[change],
        ),
        release_decision=_release_decision("review_required"),
        findings=[],
    )
    report.capability_changes = build_capability_changes(report=report, findings=[])
    reviewer = build_reviewer_summary(findings=[], report=report)
    assert reviewer.capability_changes == len(report.capability_changes) == 1
