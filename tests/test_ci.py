from pathlib import Path

from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.models import (
    ReadinessReport,
    ReportSummary,
    ToolSurfaceDiff,
    ToolSurfaceDiffSummary,
    ToolSurfaceHighRiskEffectChange,
    ToolSurfaceSummary,
)


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
    assert "Reason:" in summary
    assert "Blockers:" in summary
    assert "Fail policy:" in summary


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
