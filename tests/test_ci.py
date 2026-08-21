from pathlib import Path

from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.cli._helpers import _print_cli_summary
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.report.summary_text import primary_evidence_remediation_text
from agents_shipgate.schemas.report import (
    EvidenceCoverageDecision,
    EvidenceGap,
    EvidenceGapAction,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.surfaces import (
    ToolSurfaceDiff,
    ToolSurfaceDiffSummary,
    ToolSurfaceHighRiskEffectChange,
)


def _scan_google_adk_insufficient_evidence_project(tmp_path) -> ReadinessReport:
    project = tmp_path / "google-adk-project"
    project.mkdir()
    # ``case_id`` is unannotated on purpose: since #393 the ADK AST path reports
    # a proven surface for a module it fully resolved, so a project that is
    # meant to reach ``insufficient_evidence`` needs a surface that genuinely is
    # not proven. Here the parameter type is the part static extraction cannot
    # read, which is exactly what the inventory remediation under test supplies.
    (project / "agent.py").write_text(
        '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def lookup_case(case_id) -> dict:
    """Look up read-only support case metadata."""
    return {"case_id": case_id}


lookup_tool = FunctionTool(func=lookup_case)
root_agent = LlmAgent(name="support_reader", tools=[lookup_tool])
'''.lstrip(),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        '''
version: "0.1"
project:
  name: google-adk-remediation
agent:
  name: support-reader
  declared_purpose: [read support case metadata]
environment:
  target: local
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
'''.lstrip(),
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
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
    report = _scan_google_adk_insufficient_evidence_project(tmp_path)
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    first_gap = report.release_decision.evidence_coverage.evidence_gaps[0]
    assert first_gap.kind == "incomplete_surface"
    assert first_gap.next_action.kind == "declare_tool_inventory"
    assert first_gap.next_action.path == "suggested-inventory.json"
    assert "google_adk.tool_inventories" in first_gap.next_action.expects
    assert (tmp_path / "reports" / "suggested-inventory.json").is_file()

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
        assert "verification. Target: suggested-inventory.json." in output
        assert output.index("Review the skeleton") < output.index("Run:")
        assert "broader OpenAI SDK source path" not in output


def test_primary_evidence_remediation_preserves_terminal_ellipsis():
    evidence = EvidenceCoverageDecision(
        level="partial",
        human_review_recommended=True,
        source_warning_count=1,
        low_confidence_tool_count=0,
        evidence_gaps=[
            EvidenceGap(
                kind="source_warning",
                subject="legacy source",
                why="The source requires review.",
                next_action=EvidenceGapAction(
                    kind="review_warning",
                    path="source-notes.json",
                    why="The source warning must be resolved.",
                    expects="See source notes...",
                ),
            )
        ],
    )

    assert primary_evidence_remediation_text(evidence) == (
        "See source notes... Target: source-notes.json."
    )


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
