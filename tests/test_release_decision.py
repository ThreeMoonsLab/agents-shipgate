from __future__ import annotations

import pytest

from agents_shipgate.ci.exit_policy import (
    GATE_FAILURE_EXIT_CODE,
    exit_code_for_report,
)
from agents_shipgate.ci.release_decision import (
    _LOW_CONFIDENCE_TOOL_RATIO,
    _MAX_TOLERATED_SOURCE_WARNINGS,
    build_release_decision,
    evidence_below_ie_threshold,
)
from agents_shipgate.core.domain import (
    AuthInfo,
    AuthoritySemanticAssessment,
    EffectSemanticAssessment,
    SemanticIssue,
    Tool,
    ToolSemanticAssessment,
)
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment
from agents_shipgate.schemas.report import (
    BaselineSummary,
    Finding,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)


def _finding(
    *,
    check_id: str = "check.x",
    severity: str = "critical",
    baseline_status: str | None = None,
    requires_human_review: bool | None = None,
    suppressed: bool = False,
    tool_id: str | None = None,
) -> Finding:
    return Finding(
        id=f"id-{check_id}-{severity}-{baseline_status or 'new'}",
        fingerprint=f"fp-{check_id}",
        check_id=check_id,
        title=f"{check_id} title",
        severity=severity,
        category="test",
        recommendation="do the thing",
        suppressed=suppressed,
        baseline_status=baseline_status,
        requires_human_review=requires_human_review,
        tool_id=tool_id,
    )


def _tool(
    *,
    name: str = "t1",
    confidence: str = "high",
    semantic_assessment: ToolSemanticAssessment | None | bool = True,
) -> Tool:
    assessment = (
        ToolSemanticAssessment(
            conservative_effect="read",
            effect=EffectSemanticAssessment(status="declared", confidence="high"),
            authority=AuthoritySemanticAssessment(status="declared", mode="none"),
            pass_eligible=True,
        )
        if semantic_assessment is True
        else semantic_assessment
    )
    return Tool(
        id=f"tool-{name}",
        name=name,
        source_type="manual",
        auth=AuthInfo(mode="none", explicit=True),
        extraction_confidence=confidence,
        semantic_assessment=assessment,
    )


def _report(
    *,
    findings: list[Finding] | None = None,
    tools: list[Tool] | None = None,
    summary_status: str = "warnings_detected",
    human_review_recommended: bool = False,
    evidence_coverage: str = "static",
    baseline: BaselineSummary | None = None,
    source_warnings: list[str] | None = None,
) -> ReadinessReport:
    findings = findings or []
    tools = tools or []
    return ReadinessReport(
        run_id="r",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(
            status=summary_status,
            critical_count=sum(1 for f in findings if f.severity == "critical"),
            high_count=sum(1 for f in findings if f.severity == "high"),
            medium_count=sum(1 for f in findings if f.severity == "medium"),
            human_review_recommended=human_review_recommended,
            evidence_coverage=evidence_coverage,
        ),
        tool_surface=ToolSurfaceSummary(total_tools=len(tools), high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="unit-test-agent",
            status="structural",
            reachable_tool_ids=[tool.id for tool in tools],
            pass_eligible=True,
        ),
        baseline=baseline,
        findings=findings,
        source_warnings=source_warnings or [],
    )


def _build(
    report: ReadinessReport,
    *,
    tools: list[Tool] | None = None,
    ci_mode: str = "advisory",
    fail_on: list[str] | None = None,
    new_findings_only: bool = False,
):
    return build_release_decision(
        report=report,
        tools=tools or [],
        ci_mode=ci_mode,
        fail_on=fail_on,
        new_findings_only=new_findings_only,
    )


def test_advisory_with_new_critical_blocks_but_does_not_fail_ci():
    report = _report(findings=[_finding(severity="critical", baseline_status="new")])
    decision = _build(report, ci_mode="advisory")
    assert decision.decision == "blocked"
    assert len(decision.blockers) == 1
    assert decision.fail_policy.would_fail_ci is False
    assert decision.fail_policy.exit_code == 0


def test_strict_with_new_critical_blocks_and_fails_ci():
    report = _report(findings=[_finding(severity="critical", baseline_status="new")])
    decision = _build(report, ci_mode="strict")
    assert decision.decision == "blocked"
    assert decision.fail_policy.would_fail_ci is True
    assert decision.fail_policy.exit_code == GATE_FAILURE_EXIT_CODE
    assert decision.fail_policy.fail_on == ["critical"]


def test_baseline_matched_critical_only_is_review_required():
    report = _report(
        findings=[_finding(severity="critical", baseline_status="matched")],
        baseline=BaselineSummary(path=".agents-shipgate/baseline.json", matched_count=1),
    )
    decision = _build(report, ci_mode="strict", new_findings_only=True)
    assert decision.decision == "review_required"
    assert decision.blockers == []
    assert len(decision.review_items) == 1
    assert decision.review_items[0].baseline_status == "matched"
    assert "baseline-matched" in decision.reason


def test_explicit_fail_on_high_with_high_finding_blocks():
    report = _report(findings=[_finding(severity="high", baseline_status="new")])
    decision = _build(report, ci_mode="strict", fail_on=["high"])
    assert decision.decision == "blocked"
    assert len(decision.blockers) == 1
    assert decision.blockers[0].severity == "high"
    assert decision.fail_policy.fail_on == ["high"]
    assert decision.fail_policy.would_fail_ci is True


def test_clean_scan_with_high_confidence_tools_passes():
    report = _report(
        tools=[_tool(confidence="high")],
        summary_status="no_release_blockers_detected",
        human_review_recommended=False,
    )
    decision = _build(report, ci_mode="strict", tools=[_tool(confidence="high")])
    assert decision.decision == "passed"
    assert decision.blockers == []
    assert decision.review_items == []
    assert decision.fail_policy.would_fail_ci is False


def test_missing_semantic_assessment_is_zero_tolerance_ie_and_strict_failure():
    tool = _tool(semantic_assessment=None)
    report = _report(tools=[tool], summary_status="no_release_blockers_detected")

    decision = _build(report, ci_mode="strict", tools=[tool])

    assert decision.decision == "insufficient_evidence"
    assert decision.evidence_coverage.semantic_coverage.model_dump() == {
        "total_actions": 1,
        "pass_eligible_actions": 0,
        "gap_count": 1,
        "review_concern_count": 0,
        "reason_counts": {"incomplete_surface": 1},
    }
    gap = decision.evidence_coverage.evidence_gaps[0]
    assert gap.kind == "incomplete_surface"
    assert gap.subject.startswith("t1 [")
    assert gap.next_action.kind == "provide_complete_inventory"
    assert decision.fail_policy.exit_code == GATE_FAILURE_EXIT_CODE
    assert decision.fail_policy.would_fail_ci is True


@pytest.mark.parametrize("safe_count", [1, 2, 10, 100])
def test_one_semantic_gap_cannot_be_diluted_by_safe_tools(safe_count: int):
    tools = [
        *[_tool(name=f"safe-{index}") for index in range(safe_count)],
        _tool(name="unresolved", semantic_assessment=None),
    ]
    decision = _build(_report(tools=tools), tools=tools)

    assert decision.decision == "insufficient_evidence"
    assert decision.evidence_coverage.semantic_coverage.gap_count == 1
    assert decision.evidence_coverage.semantic_coverage.pass_eligible_actions == safe_count


def test_semantic_gap_has_human_declaration_remediation():
    issue = SemanticIssue(
        kind="missing_authority_evidence",
        dimension="authority",
        message="No explicit authority evidence was found.",
        source="mcp",
        source_pointer="/tools/0",
    )
    assessment = ToolSemanticAssessment(
        conservative_effect="write",
        effect=EffectSemanticAssessment(status="structural", confidence="high"),
        authority=AuthoritySemanticAssessment(status="unknown", mode="unknown", issues=[issue]),
        pass_eligible=False,
    )
    tool = _tool(name="process_order", semantic_assessment=assessment)

    decision = _build(_report(tools=[tool]), tools=[tool])

    assert decision.decision == "insufficient_evidence"
    gap = decision.evidence_coverage.evidence_gaps[0]
    assert gap.kind == "missing_authority_evidence"
    assert gap.source_ref == "/tools/0"
    assert gap.next_action.kind == "declare_action_authority"
    assert gap.next_action.suggested_patch_kind == "manual"
    assert gap.next_action.path == ("shipgate.yaml#action_surface.actions[tool='process_order']")
    assert gap.next_action.accepted_values == [
        "none",
        "scoped",
        "unscoped",
        "ambient",
    ]
    # The template must name every co-required field, not just `mode`: the
    # manifest rejects `scoped`/`unscoped`/`ambient` without `auth_type`, and
    # `scoped` without non-empty `scopes`, so a mode-only template is
    # unfillable for the answers people actually give.
    assert gap.next_action.declaration_template == {
        "tool": "process_order",
        "scopes": ["<REVIEW_REQUIRED>"],
        "authority": {
            "mode": "<REVIEW_REQUIRED>",
            "auth_type": "<REVIEW_REQUIRED>",
            "reason": "<REVIEW_REQUIRED>",
        },
    }
    assert gap.next_action.auto_apply is False
    assert gap.next_action.requires_human_review is True


def test_known_unscoped_authority_routes_to_review_not_ie():
    assessment = ToolSemanticAssessment(
        conservative_effect="write",
        effect=EffectSemanticAssessment(status="declared", confidence="high"),
        authority=AuthoritySemanticAssessment(
            status="declared", mode="unscoped", auth_type="api_key"
        ),
        pass_eligible=False,
    )
    tool = _tool(name="process_order", semantic_assessment=assessment)

    decision = _build(_report(tools=[tool]), tools=[tool])

    assert decision.decision == "review_required"
    assert decision.evidence_coverage.semantic_coverage.gap_count == 0
    assert decision.evidence_coverage.semantic_coverage.review_concern_count == 1


def test_all_tools_low_confidence_is_insufficient_evidence():
    """Under the v0.14 ratio rule (ceil(N * 0.5) with min 1), a 1-of-1
    low-confidence scan trips the gate — 0-of-1 confidence rate is
    degraded by definition. The previous behavior routed this to
    review_required; v0.14 introduces insufficient_evidence to
    distinguish "scan is shaky" from "review the findings."""
    report = _report(
        tools=[_tool(confidence="low")],
        summary_status="human_review_recommended",
        human_review_recommended=True,
    )
    decision = _build(report, ci_mode="strict", tools=[_tool(confidence="low")])
    assert decision.decision == "insufficient_evidence"
    assert decision.review_items == []
    assert decision.evidence_coverage.low_confidence_tool_count == 1
    assert "low-confidence" in decision.reason


def test_majority_low_confidence_tools_is_insufficient_evidence():
    """2 of 3 tools low-confidence (>= ceil(3 * 0.5) = 2) trips the
    insufficient_evidence gate."""
    tools = [
        _tool(name="a", confidence="low"),
        _tool(name="b", confidence="low"),
        _tool(name="c", confidence="high"),
    ]
    report = _report(
        tools=tools,
        summary_status="human_review_recommended",
        human_review_recommended=True,
    )
    decision = _build(report, ci_mode="strict", tools=tools)
    assert decision.decision == "insufficient_evidence"
    assert decision.evidence_coverage.low_confidence_tool_count == 2


def test_sub_threshold_low_confidence_is_review_required():
    """1 of 3 low-confidence tools (count 1 < threshold 2) stays at
    review_required — via human_review_recommended."""
    tools = [
        _tool(name="a", confidence="low"),
        _tool(name="b", confidence="high"),
        _tool(name="c", confidence="high"),
    ]
    report = _report(
        tools=tools,
        summary_status="human_review_recommended",
        human_review_recommended=True,
    )
    decision = _build(report, ci_mode="strict", tools=tools)
    assert decision.decision == "review_required"
    assert decision.evidence_coverage.low_confidence_tool_count == 1


def test_four_source_warnings_is_insufficient_evidence():
    """Source-warning threshold is > 3 (strict). 4 warnings trip."""
    report = _report(
        tools=[_tool(confidence="high")],
        source_warnings=["w1", "w2", "w3", "w4"],
    )
    decision = _build(report, ci_mode="strict", tools=[_tool(confidence="high")])
    assert decision.decision == "insufficient_evidence"
    assert decision.evidence_coverage.source_warning_count == 4
    assert "4 source warning(s)" in decision.reason


def test_three_source_warnings_is_review_required():
    """Boundary: exactly 3 source warnings is at the limit; > 3 trips.
    3 should stay at review_required via the explicit source-warning
    routing branch."""
    report = _report(
        tools=[_tool(confidence="high")],
        source_warnings=["w1", "w2", "w3"],
    )
    decision = _build(report, ci_mode="strict", tools=[_tool(confidence="high")])
    assert decision.decision == "review_required"
    assert decision.evidence_coverage.source_warning_count == 3


def test_one_source_warning_is_review_required():
    """The new explicit source_warning_count > 0 routing must fire even
    when human_review_recommended is False — summarize_findings doesn't
    feed source warnings into that signal, so without this branch a
    single warning would silently pass."""
    report = _report(
        tools=[_tool(confidence="high")],
        summary_status="no_release_blockers_detected",
        human_review_recommended=False,
        source_warnings=["w1"],
    )
    decision = _build(report, ci_mode="strict", tools=[_tool(confidence="high")])
    assert decision.decision == "review_required"
    assert decision.evidence_coverage.source_warning_count == 1
    assert "source-loader" in decision.reason


def test_diff_reference_degradation_uses_typed_action_kind_for_ie():
    warning = (
        "Base report predates report schema semantic evidence and is not "
        "comparable with --diff-from. Regenerate it."
    )
    tool = _tool(confidence="high")
    decision = _build(
        _report(tools=[tool], source_warnings=[warning]),
        ci_mode="strict",
        tools=[tool],
    )

    assert decision.decision == "insufficient_evidence"
    [gap] = decision.evidence_coverage.evidence_gaps
    assert gap.kind == "source_warning"
    assert gap.next_action.kind == "provide_source"

    # Routing remains typed even if presentation strings change.
    gap.next_action.command = None
    gap.next_action.path = None
    assert evidence_below_ie_threshold(decision.evidence_coverage, tool_count=1) is True


def test_zero_source_warnings_clean_scan_passes():
    """Regression guard: 0 warnings + 0 findings + all-high-confidence
    tools must still produce passed under the new logic."""
    report = _report(
        tools=[_tool(confidence="high")],
        summary_status="no_release_blockers_detected",
        human_review_recommended=False,
    )
    decision = _build(report, ci_mode="strict", tools=[_tool(confidence="high")])
    assert decision.decision == "passed"


def test_blockers_outrank_insufficient_evidence():
    """Priority order: blockers always win over evidence-degradation."""
    tools = [_tool(name="a", confidence="low"), _tool(name="b", confidence="low")]
    report = _report(
        findings=[_finding(severity="critical", baseline_status="new")],
        tools=tools,
    )
    decision = _build(report, ci_mode="strict", tools=tools)
    assert decision.decision == "blocked"


def test_insufficient_evidence_outranks_review_required():
    """Priority order: insufficient_evidence wins over review_required.
    A medium-severity finding (would normally trigger review_required)
    plus 2-of-2 low-confidence tools (insufficient_evidence) → the
    evidence-gate verdict takes precedence."""
    tools = [_tool(name="a", confidence="low"), _tool(name="b", confidence="low")]
    report = _report(
        findings=[_finding(severity="medium", baseline_status="new")],
        tools=tools,
        summary_status="human_review_recommended",
        human_review_recommended=True,
    )
    decision = _build(report, ci_mode="strict", tools=tools)
    assert decision.decision == "insufficient_evidence"
    assert len(decision.review_items) == 1


def test_active_high_review_finding_outranks_insufficient_evidence():
    """Phase 2c: an active (non-accepted) HIGH finding is a *named* concern —
    review_required, not the vaguer insufficient_evidence — even when the
    low-confidence-tool gate would otherwise trip. Contrast with the medium
    case above, which stays insufficient_evidence. Both verdicts are equally
    non-auto-mergeable, so this loses no safety; the evidence detail is kept."""
    tools = [_tool(name="a", confidence="low"), _tool(name="b", confidence="low")]
    report = _report(
        findings=[
            _finding(
                check_id="SHIP-SCOPE-TOOLKIT-UNBOUNDED",
                severity="high",
                baseline_status="new",
                tool_id=tools[0].id,
            )
        ],
        tools=tools,
        human_review_recommended=True,
    )
    decision = _build(report, ci_mode="advisory", tools=tools)
    assert decision.decision == "review_required"
    assert len(decision.review_items) == 1
    # The insufficient-evidence detail is preserved for the reviewer.
    assert decision.evidence_coverage.low_confidence_tool_count == 2
    assert decision.evidence_coverage.evidence_gaps


def test_accepted_high_finding_does_not_outrank_insufficient_evidence():
    """A baseline-MATCHED (accepted) high finding is acknowledged debt — it
    must NOT elevate out of insufficient_evidence; only active high concerns do."""
    tools = [_tool(name="a", confidence="low"), _tool(name="b", confidence="low")]
    report = _report(
        findings=[_finding(severity="high", baseline_status="matched")],
        tools=tools,
        baseline=BaselineSummary(path=".agents-shipgate/baseline.json", matched_count=1),
    )
    decision = _build(report, ci_mode="advisory", tools=tools, new_findings_only=True)
    assert decision.decision == "insufficient_evidence"


def test_ie_threshold_constants_are_frozen():
    """Freeze the IE-threshold constants at their examined-and-held values.

    benchmark/miner/CALIBRATION.md decided to HOLD these because no available
    data justifies a change. The labeled constructed fixture
    (test_miner_constructed) only guards extraction for that case — it sits at
    ratio 1.0 and so cannot detect a threshold edit. This is the guard that
    makes a threshold edit surface in CI: changing 0.5 / 3 here is a deliberate
    recalibration that must update CALIBRATION.md (and the revisit
    prerequisites: the human labeling pass + a re-mine) alongside this test.
    """
    assert _LOW_CONFIDENCE_TOOL_RATIO == 0.5, (
        "IE low-confidence ratio changed — recalibrate per "
        "benchmark/miner/CALIBRATION.md and update this guard."
    )
    assert _MAX_TOLERATED_SOURCE_WARNINGS == 3, (
        "IE source-warning tolerance changed — recalibrate per "
        "benchmark/miner/CALIBRATION.md and update this guard."
    )


def test_insufficient_evidence_reason_lists_both_counts():
    """When both gates trip, the reason names both counts."""
    tools = [_tool(name="a", confidence="low"), _tool(name="b", confidence="low")]
    report = _report(
        tools=tools,
        source_warnings=["w1", "w2", "w3", "w4"],
    )
    decision = _build(report, ci_mode="strict", tools=tools)
    assert decision.decision == "insufficient_evidence"
    assert "2 low-confidence tool(s)" in decision.reason
    assert "4 source warning(s)" in decision.reason


def test_fail_policy_exit_code_matches_exit_code_for_report():
    """The shared-helper refactor must keep release_decision.fail_policy.exit_code
    in lockstep with the standalone exit_code_for_report() across the matrix."""
    matrix = [
        ("advisory", None, False, [_finding(severity="critical")]),
        ("strict", None, False, [_finding(severity="critical")]),
        ("strict", ["high"], False, [_finding(severity="high")]),
        ("strict", ["critical"], True, [_finding(severity="critical", baseline_status="matched")]),
        ("advisory", None, False, []),
    ]
    for ci_mode, fail_on, new_only, findings in matrix:
        report = _report(findings=findings)
        decision = _build(report, ci_mode=ci_mode, fail_on=fail_on, new_findings_only=new_only)
        expected = exit_code_for_report(
            report, ci_mode, fail_on=fail_on, new_findings_only=new_only
        )
        assert decision.fail_policy.exit_code == expected, (
            f"mismatch for ci_mode={ci_mode}, fail_on={fail_on}, new_findings_only={new_only}"
        )
        assert decision.fail_policy.would_fail_ci == (expected != 0)


def test_summary_status_remains_baseline_blind():
    """Regression: summary.status MUST stay baseline-blind for v0.7 compat
    even though release_decision.decision is baseline-aware. A baseline-matched
    critical produces summary.status='release_blockers_detected' AND
    release_decision.decision='review_required' — that intentional divergence
    is documented in STABILITY.md."""
    from agents_shipgate.core.findings import summarize_findings

    findings = [_finding(severity="critical", baseline_status="matched")]
    summary = summarize_findings(findings, [])
    assert summary.status == "release_blockers_detected"

    report = _report(
        findings=findings,
        summary_status=summary.status,
        baseline=BaselineSummary(path=".agents-shipgate/baseline.json", matched_count=1),
    )
    decision = _build(report, ci_mode="strict", new_findings_only=True)
    assert decision.decision == "review_required"


def test_blockers_and_review_items_use_reference_only_shape():
    finding = _finding(severity="critical", baseline_status="new")
    report = _report(findings=[finding])
    decision = _build(report, ci_mode="strict")
    assert len(decision.blockers) == 1
    item = decision.blockers[0]
    # Reference-only shape: id/fingerprint/check_id/severity/title/baseline_status only.
    assert item.id == finding.id
    assert item.fingerprint == finding.fingerprint
    assert item.check_id == finding.check_id
    assert item.severity == finding.severity
    assert item.title == finding.title
    assert item.baseline_status == finding.baseline_status
    # Must NOT carry full Finding fields like recommendation or evidence.
    assert not hasattr(item, "recommendation")
    assert not hasattr(item, "evidence")


@pytest.mark.parametrize(
    "decision_branch,findings_kwargs,build_kwargs,expected_keyword",
    [
        (
            "blocked",
            {"severity": "critical", "baseline_status": "new"},
            {"ci_mode": "strict"},
            "block",
        ),
        (
            "review_required_matched",
            {"severity": "critical", "baseline_status": "matched"},
            {"ci_mode": "strict", "new_findings_only": True},
            "baseline-matched",
        ),
    ],
)
def test_decision_reason_strings_are_deterministic(
    decision_branch, findings_kwargs, build_kwargs, expected_keyword
):
    report = _report(findings=[_finding(**findings_kwargs)])
    decision = _build(report, **build_kwargs)
    assert expected_keyword in decision.reason


# ---------------------------------------------------------------------------
# v0.17 contribution_rules truth table.
#
# The truth-table contract documented in STABILITY.md
# "Release decision truth table" describes which (rule, category) pair
# fires for every (blocks_release, severity, baseline_status, fail_on)
# combination. Each parametrized case below is one row of that table;
# the row is named so a failure points at the exact contract it
# violates.
#
# Inputs are kept minimal — one finding per case — so a regression in
# build_release_decision picks exactly one named test, and the
# contribution rule under test is the only audit row produced.
# ---------------------------------------------------------------------------


def _policy_finding(
    *,
    severity: str = "high",
    baseline_status: str | None = None,
    suppressed: bool = False,
) -> Finding:
    f = _finding(
        severity=severity,
        baseline_status=baseline_status,
        suppressed=suppressed,
        check_id="check.policy",
    )
    f.blocks_release = True
    return f


@pytest.mark.parametrize(
    "case_name,finding_factory,build_kwargs,expected_category,expected_rule,expected_in_blockers,expected_in_review",
    [
        # ---- blocks_release=true paths -----------------------------------
        (
            "policy_block_new_unbaselined",
            lambda: _policy_finding(severity="high", baseline_status="new"),
            {"ci_mode": "advisory"},
            "blocker",
            "policy_block_new",
            True,
            False,
        ),
        (
            "policy_block_new_no_baseline",
            lambda: _policy_finding(severity="high", baseline_status=None),
            {"ci_mode": "advisory"},
            "blocker",
            "policy_block_new",
            True,
            False,
        ),
        (
            "policy_baseline_accepted_review_tier",
            # severity in {C,H,M} → falls into review tier when matched
            lambda: _policy_finding(severity="high", baseline_status="matched"),
            {"ci_mode": "advisory"},
            "review_item",
            "policy_baseline_accepted",
            False,
            True,
        ),
        (
            "policy_baseline_accepted_below_review_tier",
            # severity below review tier → silently dropped in v0.16; v0.17
            # records the audit row but the finding stays excluded.
            lambda: _policy_finding(severity="low", baseline_status="matched"),
            {"ci_mode": "advisory"},
            "excluded",
            "policy_baseline_accepted",
            False,
            False,
        ),
        # ---- severity-driven paths --------------------------------------
        (
            "severity_block_new_critical_default",
            lambda: _finding(severity="critical", baseline_status="new"),
            {"ci_mode": "advisory"},  # critical always in blocker_severities floor
            "blocker",
            "severity_block_new",
            True,
            False,
        ),
        (
            "severity_block_new_high_via_fail_on",
            lambda: _finding(severity="high", baseline_status="new"),
            {"ci_mode": "advisory", "fail_on": ["high"]},
            "blocker",
            "severity_block_new",
            True,
            False,
        ),
        (
            "severity_baseline_accepted_critical",
            lambda: _finding(severity="critical", baseline_status="matched"),
            {"ci_mode": "advisory"},
            "review_item",
            "severity_baseline_accepted",
            False,
            True,
        ),
        # ---- review-tier paths ------------------------------------------
        (
            "review_required_high_no_fail_on",
            # severity=high but advisory-default fail_on=[] → not a blocker;
            # severity in {C,H,M} → review_required.
            lambda: _finding(severity="high", baseline_status="new"),
            {"ci_mode": "advisory"},
            "review_item",
            "review_required",
            False,
            True,
        ),
        (
            "review_required_medium_no_fail_on",
            lambda: _finding(severity="medium", baseline_status="new"),
            {"ci_mode": "advisory"},
            "review_item",
            "review_required",
            False,
            True,
        ),
        (
            "review_required_low_with_human_review_flag",
            # severity below review tier but requires_human_review=True
            # explicitly routes to review_items.
            lambda: _finding(severity="low", baseline_status="new", requires_human_review=True),
            {"ci_mode": "advisory"},
            "review_item",
            "review_required",
            False,
            True,
        ),
        # ---- sub-threshold (excluded) ----------------------------------
        (
            "sub_threshold_low",
            lambda: _finding(severity="low", baseline_status="new"),
            {"ci_mode": "advisory"},
            "excluded",
            "sub_threshold",
            False,
            False,
        ),
        (
            "sub_threshold_info",
            lambda: _finding(severity="info", baseline_status="new"),
            {"ci_mode": "advisory"},
            "excluded",
            "sub_threshold",
            False,
            False,
        ),
        # ---- suppressed ------------------------------------------------
        (
            "suppressed_critical_excluded",
            lambda: _finding(severity="critical", baseline_status="new", suppressed=True),
            {"ci_mode": "strict"},
            "excluded",
            "suppressed",
            False,
            False,
        ),
    ],
)
def test_contribution_rules_truth_table(
    case_name,
    finding_factory,
    build_kwargs,
    expected_category,
    expected_rule,
    expected_in_blockers,
    expected_in_review,
):
    """Every row of STABILITY.md "Release decision truth table" is
    exercised here. The audit's (category, rule) pair must match the
    documented contract, and the underlying blockers[]/review_items[]
    membership must agree with the audit (no contradictions allowed).
    """
    finding = finding_factory()
    report = _report(findings=[finding])
    decision = _build(report, **build_kwargs)

    # Audit row must exist for this finding.
    rules_for = [r for r in decision.contribution_rules if r.finding_id == finding.id]
    assert len(rules_for) == 1, (
        f"{case_name}: expected exactly one contribution rule for the "
        f"finding, got {len(rules_for)}: {rules_for}"
    )
    rule = rules_for[0]
    assert rule.category == expected_category, (
        f"{case_name}: expected category={expected_category!r}, "
        f"got {rule.category!r}; rationale: {rule.rationale}"
    )
    assert rule.rule == expected_rule, (
        f"{case_name}: expected rule={expected_rule!r}, "
        f"got {rule.rule!r}; rationale: {rule.rationale}"
    )

    # Audit must agree with the underlying lists.
    in_blockers = any(b.id == finding.id for b in decision.blockers)
    in_review = any(r.id == finding.id for r in decision.review_items)
    assert in_blockers is expected_in_blockers, (
        f"{case_name}: in_blockers mismatch (rule said {expected_category!r})"
    )
    assert in_review is expected_in_review, (
        f"{case_name}: in_review_items mismatch (rule said {expected_category!r})"
    )


def test_contribution_rules_audit_row_per_finding():
    """The audit must be exhaustive: one row per report.findings entry,
    including suppressed findings. No finding can be silently absent.
    """
    findings = [
        _finding(check_id="c1", severity="critical", baseline_status="new"),
        _finding(check_id="c2", severity="high", baseline_status="matched"),
        _finding(check_id="c3", severity="low", baseline_status="new"),
        _finding(check_id="c4", severity="critical", baseline_status="new", suppressed=True),
    ]
    report = _report(findings=findings)
    decision = _build(report, ci_mode="strict")

    audit_finding_ids = {r.finding_id for r in decision.contribution_rules}
    expected = {f.id for f in findings}
    assert audit_finding_ids == expected, (
        "contribution_rules must contain exactly one row per finding "
        f"(missing: {expected - audit_finding_ids}, "
        f"extra: {audit_finding_ids - expected})"
    )

    # Each audit row's category is one of the documented values.
    for rule in decision.contribution_rules:
        assert rule.category in {"blocker", "review_item", "excluded"}
        assert rule.rule in {
            "policy_block_new",
            "severity_block_new",
            "policy_baseline_accepted",
            "severity_baseline_accepted",
            "review_required",
            "sub_threshold",
            "suppressed",
        }
        assert rule.rationale, "rationale must be non-empty"


def test_contribution_rules_audit_works_without_finding_id():
    """`Finding.id` is Python-Optional. Direct callers — internal tests,
    plugin checks that emit Findings before `assign_finding_ids` runs,
    `explain-finding` rebuilding from a stripped report — may invoke
    `build_release_decision` with `finding.id is None`. The audit row's
    `finding_id` is required-as-string on the wire, so the gate must
    fall back through `fingerprint` to `check_id` rather than raising
    a Pydantic ValidationError. Regression for P2 review feedback on
    PR #81."""
    finding_with_fingerprint = Finding(
        id=None,
        fingerprint="fp_unit_test_fingerprint",
        check_id="SHIP-UNIT-TEST-CHECK",
        title="title",
        severity="critical",
        category="test",
        recommendation="do the thing",
    )
    finding_without_anything = Finding(
        id=None,
        fingerprint=None,
        check_id="SHIP-UNIT-TEST-NOID",
        title="title",
        severity="high",
        category="test",
        recommendation="do the thing",
    )
    report = _report(findings=[finding_with_fingerprint, finding_without_anything])
    # Should not raise.
    decision = _build(report, ci_mode="strict", fail_on=["high"])

    # Both audit rows present; fallback chain produced a usable id.
    assert len(decision.contribution_rules) == 2
    by_check = {r.check_id: r for r in decision.contribution_rules}
    # Fingerprint fallback when id is None but fingerprint is set.
    assert by_check["SHIP-UNIT-TEST-CHECK"].finding_id == "fp_unit_test_fingerprint"
    # check_id fallback when both id and fingerprint are None.
    assert by_check["SHIP-UNIT-TEST-NOID"].finding_id == "SHIP-UNIT-TEST-NOID"
    # finding_id is never the empty string.
    for rule in decision.contribution_rules:
        assert rule.finding_id, "finding_id must be non-empty"


def test_contribution_rules_default_to_empty_for_legacy_report():
    """A `ReleaseDecision` constructed without `contribution_rules`
    (e.g., loaded from a v0.16 report via explain-finding, or built by
    minimal test helpers) must accept the missing field and default to
    an empty list. Forward-compat for old reports."""
    from agents_shipgate.schemas.report import (
        BaselineDelta,
        EvidenceCoverageDecision,
        FailPolicy,
        ReleaseDecision,
    )

    decision = ReleaseDecision(
        decision="passed",
        reason="ok",
        evidence_coverage=EvidenceCoverageDecision(
            level="full",
            human_review_recommended=False,
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory",
            fail_on=[],
            new_findings_only=False,
            would_fail_ci=False,
            exit_code=0,
        ),
    )
    assert decision.contribution_rules == []


# --- v0.26 evidence gaps ----------------------------------------------------


def _langchain_tool(name: str, confidence: str = "medium") -> Tool:
    return Tool(
        id=f"tool-{name}",
        name=name,
        source_type="langchain_function",
        source_location="agent.py:14",
        auth=AuthInfo(),
        extraction_confidence=confidence,
    )


def test_evidence_gaps_empty_for_clean_high_confidence_scan():
    tools = [_tool(name="a"), _tool(name="b")]
    decision = _build(_report(tools=tools), tools=tools)
    assert decision.evidence_coverage.evidence_gaps == []


def test_evidence_gaps_low_confidence_tool_points_at_inventory():
    tools = [_langchain_tool("lookup_case")]
    decision = _build(_report(tools=tools), tools=tools)

    gaps = decision.evidence_coverage.evidence_gaps
    assert [gap.kind for gap in gaps] == [
        "incomplete_surface",
        "low_confidence_tool",
    ]
    gap = gaps[1]
    assert gap.kind == "low_confidence_tool"
    assert gap.subject.startswith("lookup_case [")
    assert gap.source_type == "langchain_function"
    assert gap.source_ref == "agent.py:14"
    assert gap.next_action.kind == "declare_tool_inventory"
    assert gap.next_action.path == "suggested-inventory.json"
    assert "langchain.tool_inventories" in gap.next_action.expects


def test_evidence_gaps_non_inventory_source_gets_provide_source():
    tools = [_tool(name="mystery", confidence="low")]
    decision = _build(_report(tools=tools), tools=tools)

    gap = decision.evidence_coverage.evidence_gaps[0]
    assert gap.next_action.kind == "provide_source"
    assert gap.next_action.path is None


def test_evidence_gaps_include_source_warnings_in_report_order():
    tools = [_tool(name="a")]
    warnings = ["warning B about source", "warning A about source"]
    decision = _build(_report(tools=tools, source_warnings=warnings), tools=tools)

    gaps = decision.evidence_coverage.evidence_gaps
    assert [gap.kind for gap in gaps] == ["source_warning", "source_warning"]
    assert [gap.subject for gap in gaps] == warnings
    assert all(gap.next_action.kind == "review_warning" for gap in gaps)


def test_semantic_gaps_gate_independently_of_extraction_ratio():
    """Semantic gaps are zero-tolerance; extraction rows remain explanatory."""
    tools = [_langchain_tool("a"), _langchain_tool("b")]
    decision = _build(_report(tools=tools), tools=tools)
    assert decision.decision == "insufficient_evidence"
    assert len(decision.evidence_coverage.evidence_gaps) == 4

    # One unresolved action cannot be diluted by two healthy actions even
    # though the legacy extraction-confidence ratio is sub-threshold.
    tools = [_langchain_tool("a"), _tool(name="b"), _tool(name="c")]
    decision = _build(_report(tools=tools), tools=tools)
    assert decision.decision == "insufficient_evidence"
    assert len(decision.evidence_coverage.evidence_gaps) == 2


def test_evidence_gaps_deterministic_ordering():
    tools = [_langchain_tool("zeta"), _langchain_tool("alpha")]
    decision = _build(_report(tools=tools), tools=tools)
    subjects = [gap.subject for gap in decision.evidence_coverage.evidence_gaps]
    assert subjects == [
        "alpha [langchain_function]",
        "zeta [langchain_function]",
        "alpha [langchain_function]",
        "zeta [langchain_function]",
    ]
