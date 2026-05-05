from __future__ import annotations

import pytest

from agents_shipgate.ci.exit_policy import (
    GATE_FAILURE_EXIT_CODE,
    exit_code_for_report,
)
from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.core.models import (
    AuthInfo,
    BaselineSummary,
    Finding,
    ReadinessReport,
    ReportSummary,
    Tool,
    ToolSurfaceSummary,
)


def _finding(
    *,
    check_id: str = "check.x",
    severity: str = "critical",
    baseline_status: str | None = None,
    requires_human_review: bool | None = None,
    suppressed: bool = False,
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
    )


def _tool(*, name: str = "t1", confidence: str = "high") -> Tool:
    return Tool(
        id=f"tool-{name}",
        name=name,
        source_type="manual",
        auth=AuthInfo(),
        extraction_confidence=confidence,
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
        tool_surface=ToolSurfaceSummary(
            total_tools=len(tools), high_risk_tools=0
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


def test_clean_scan_with_low_confidence_tool_is_review_required():
    report = _report(
        tools=[_tool(confidence="low")],
        summary_status="human_review_recommended",
        human_review_recommended=True,
    )
    decision = _build(
        report, ci_mode="strict", tools=[_tool(confidence="low")]
    )
    assert decision.decision == "review_required"
    assert decision.review_items == []  # no findings, just evidence gate
    assert decision.evidence_coverage.human_review_recommended is True
    assert decision.evidence_coverage.low_confidence_tool_count == 1
    assert "low-confidence" in decision.reason


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
        decision = _build(
            report, ci_mode=ci_mode, fail_on=fail_on, new_findings_only=new_only
        )
        expected = exit_code_for_report(
            report, ci_mode, fail_on=fail_on, new_findings_only=new_only
        )
        assert decision.fail_policy.exit_code == expected, (
            f"mismatch for ci_mode={ci_mode}, fail_on={fail_on}, "
            f"new_findings_only={new_only}"
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
        ("blocked", {"severity": "critical", "baseline_status": "new"}, {"ci_mode": "strict"}, "block"),
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
