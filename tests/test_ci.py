from pathlib import Path

from agents_shipgate.cli.scan import run_scan


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
