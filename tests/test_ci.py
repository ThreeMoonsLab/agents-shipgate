from pathlib import Path

from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.cli._helpers import _print_cli_summary
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.domain import (
    AuthInfo,
    AuthoritySemanticAssessment,
    EffectSemanticAssessment,
    Tool,
    ToolSemanticAssessment,
)
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment
from agents_shipgate.schemas.report import (
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.surfaces import (
    ToolSurfaceDiff,
    ToolSurfaceDiffSummary,
    ToolSurfaceHighRiskEffectChange,
)


def _google_adk_insufficient_evidence_report() -> ReadinessReport:
    tool = Tool(
        id="tool-lookup-case",
        name="lookup_case",
        source_type="google_adk_function",
        source_location="agent.py:14",
        auth=AuthInfo(mode="none", explicit=True),
        extraction_confidence="medium",
        semantic_assessment=ToolSemanticAssessment(
            conservative_effect="read",
            effect=EffectSemanticAssessment(status="declared", confidence="high"),
            authority=AuthoritySemanticAssessment(status="declared", mode="none"),
            pass_eligible=True,
        ),
    )
    report = ReadinessReport(
        run_id="test",
        project={"name": "project"},
        agent={"name": "agent"},
        environment={"target": "local"},
        summary=ReportSummary(
            status="warnings_detected",
            human_review_recommended=True,
        ),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="agent",
            status="structural",
            reachable_tool_ids=[tool.id],
            pass_eligible=True,
        ),
    )
    report.release_decision = build_release_decision(
        report=report,
        tools=[tool],
        ci_mode="advisory",
        fail_on=None,
        new_findings_only=False,
    )
    return report


def test_github_step_summary_is_written(monkeypatch, tmp_path):
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))

    run_scan(
        config_path=Path("samples/support_refund_agent/shipgate.yaml"),
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    summary = summary_path.read_text(encoding="utf-8")
    assert "## Agents Shipgate" in summary
    # v0.8: lead with release_decision instead of summary.status. The
    # support_refund_agent sample has new criticals → decision=blocked.
    assert "Decision: `blocked`" in summary
    assert "Summary:" in summary
    assert "Reason:" in summary
    assert "Blockers:" in summary
    assert "Evidence coverage:" in summary
    assert "Next action:" in summary
    assert "Fail policy:" in summary
    assert "Static-verdict boundary:" in summary
    assert "did not execute the agent or prove runtime behavior" in summary


def test_short_summaries_project_framework_specific_evidence_action(
    monkeypatch, tmp_path, capsys
):
    report = _google_adk_insufficient_evidence_report()

    _print_cli_summary(report, "advisory", 0)
    console = capsys.readouterr().out

    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    write_github_step_summary(report)
    github = summary_path.read_text(encoding="utf-8")

    assert "google_adk.tool_inventories" in console
    assert "google\\_adk.tool\\_inventories" in github
    for output in (console, github):
        assert "skeleton written next to report.json" in output
        assert "suggested-inventory.json" in output
        assert "broader OpenAI SDK source path" not in output


def test_github_step_summary_escapes_diff_highlights(monkeypatch, tmp_path):
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    report = ReadinessReport(
        run_id="test",
        project={"name": "project"},
        agent={"name": "agent"},
        environment={"target": "local"},
        summary=ReportSummary(status="warnings_detected"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        tool_surface_diff=ToolSurfaceDiff(
            enabled=True,
            summary=ToolSurfaceDiffSummary(new_high_risk_effects=1),
            high_risk_effects=[
                ToolSurfaceHighRiskEffectChange(
                    kind="added",
                    tool="tool`with|chars",
                    tag="external`write",
                )
            ],
        ),
    )

    write_github_step_summary(report)

    summary = summary_path.read_text(encoding="utf-8")
    assert "tool\\`with\\|chars" in summary
    assert "external\\`write" in summary
