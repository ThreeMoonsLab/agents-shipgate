import json
from pathlib import Path

import click
from typer.main import get_command
from typer.testing import CliRunner

from agents_shipgate.checks import registry
from agents_shipgate.cli.main import _safe_output_name, app
from agents_shipgate.core.models import ToolSurfaceDiffSummary

runner = CliRunner()


def test_cli_advisory_exits_zero(tmp_path):
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--out",
            str(tmp_path),
            "--ci-mode",
            "advisory",
        ],
    )

    assert result.exit_code == 0
    assert "Agents Shipgate 0.8.0" in result.output
    # v0.8: CLI summary leads with the release decision; the support_refund
    # sample has new criticals → decision=blocked. (Advisory exit is still 0.)
    assert "Decision: blocked" in result.output
    assert "Reason:" in result.output
    assert "Fail policy:" in result.output


def test_cli_strict_exits_gate_failure_code(tmp_path):
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--out",
            str(tmp_path),
            "--ci-mode",
            "strict",
        ],
    )

    assert result.exit_code == 20
    assert "Exit code: 20" in result.output


def test_cli_invalid_config_exits_two(tmp_path):
    bad_config = tmp_path / "shipgate.yaml"
    bad_config.write_text("version: '0.1'\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", "--config", str(bad_config)])

    assert result.exit_code == 2
    assert "Config error:" in result.output


def test_cli_input_parse_error_exits_three(tmp_path):
    config = tmp_path / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: parse-error
agent:
  name: parse-error-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: missing
    type: mcp
    path: missing.json
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", "--config", str(config)])

    assert result.exit_code == 3
    assert "Input parsing error:" in result.output


def test_cli_list_checks_outputs_catalog():
    result = runner.invoke(app, ["list-checks"])

    assert result.exit_code == 0
    assert "SHIP-POLICY-APPROVAL-MISSING" in result.output
    assert "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY" in result.output
    assert "SHIP-API-OPERATIONAL-READINESS" in result.output
    assert "Deprecated compatibility alias" in result.output


def test_cli_version_outputs_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "Agents Shipgate 0.8.0"


def test_cli_scan_help_hides_deferred_flags():
    result = runner.invoke(app, ["scan", "--help"])
    scan_command = get_command(app).commands["scan"]
    public_options = {
        option
        for parameter in scan_command.params
        if isinstance(parameter, click.Option) and not parameter.hidden
        for option in parameter.opts
    }
    hidden_options = {
        option
        for parameter in scan_command.params
        if isinstance(parameter, click.Option) and parameter.hidden
        for option in parameter.opts
    }

    assert result.exit_code == 0
    assert "--deep-import" not in result.output
    assert "--deep-import" in hidden_options
    assert "--baseline-mode" in public_options
    assert "--policy-pack" in public_options


def test_cli_tool_surface_summary_detects_no_changes():
    from agents_shipgate.cli.main import _tool_surface_diff_has_changes

    assert _tool_surface_diff_has_changes(ToolSurfaceDiffSummary()) is False
    assert (
        _tool_surface_diff_has_changes(ToolSurfaceDiffSummary(tools_added=1))
        is True
    )


def test_cli_scan_no_plugins_forces_plugins_off(monkeypatch, tmp_path):
    class FakeEntryPoint:
        value = "acme_shipgate_checks:run"

        def load(self):
            raise AssertionError("plugin should not be loaded")

    monkeypatch.setattr(registry, "entry_points", lambda group: [FakeEntryPoint()])

    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/clean_read_only_agent/shipgate.yaml",
            "--out",
            str(tmp_path),
            "--format",
            "json",
            "--no-plugins",
        ],
        env={"AGENTS_SHIPGATE_ENABLE_PLUGINS": "1"},
    )

    assert result.exit_code == 0


def test_cli_explain_outputs_check_details():
    result = runner.invoke(app, ["explain", "SHIP-POLICY-APPROVAL-MISSING"])

    assert result.exit_code == 0
    assert "Default severity: critical" in result.output
    assert "Rationale:" in result.output


def test_cli_explain_outputs_atomic_api_check_details():
    result = runner.invoke(app, ["explain", "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY"])

    assert result.exit_code == 0
    assert "Default severity: high" in result.output
    assert "OpenAI API write tool may be retried without idempotency evidence." in result.output


def test_cli_explain_outputs_legacy_compatibility_alias():
    result = runner.invoke(app, ["explain", "SHIP-API-OPERATIONAL-READINESS"])

    assert result.exit_code == 0
    assert "Deprecated compatibility alias" in result.output


def test_cli_explain_unknown_check_suggests_close_match():
    result = runner.invoke(app, ["explain", "SHIP-POLICY-APPROVAL-MISSNG"])

    assert result.exit_code == 2
    assert "Did you mean SHIP-POLICY-APPROVAL-MISSING?" in result.output


def test_cli_init_prints_manifest_template(tmp_path):
    (tmp_path / "api.openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["init", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "tool_sources:" in result.output
    assert "api.openapi.yaml" in result.output
    assert "yaml-language-server" in result.output


def test_cli_init_write_json_reports_placeholders(tmp_path):
    result = runner.invoke(
        app,
        ["init", "--workspace", str(tmp_path), "--write", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["created"] is True
    assert payload["path"].endswith("shipgate.yaml")
    placeholder_paths = {entry["path"] for entry in payload["placeholders"]}
    # Every starter manifest puts CHANGE_ME under at least agent.name and
    # agent.declared_purpose; the exact path strings can vary slightly with
    # rendering, but both keys must appear.
    assert any("name" in path for path in placeholder_paths)
    assert any("declared_purpose" in path for path in placeholder_paths)
    assert "next_action" in payload


def test_cli_explain_json_returns_full_metadata():
    result = runner.invoke(
        app, ["explain", "SHIP-POLICY-APPROVAL-MISSING", "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["id"] == "SHIP-POLICY-APPROVAL-MISSING"
    for key in ("category", "default_severity", "description"):
        assert key in payload


def test_cli_agent_mode_emits_structured_error_on_missing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app,
        ["scan", "--config", str(tmp_path / "missing.yaml")],
    )

    assert result.exit_code == 2
    # Find the JSON line on stderr/stdout (typer.testing combines them).
    json_lines = [
        line for line in (result.output or "").splitlines() if line.startswith("{")
    ]
    assert json_lines, f"no structured-error JSON line in output: {result.output!r}"
    payload = json.loads(json_lines[-1])
    assert payload["error"] == "config_error"
    assert "next_action" in payload


def test_cli_doctor_enumerates_sources():
    result = runner.invoke(
        app,
        [
            "doctor",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
        ],
    )

    assert result.exit_code == 0
    assert "Total tools:" in result.output
    assert "support_openapi" in result.output


def test_cli_scan_accepts_workspace(tmp_path):
    result = runner.invoke(
        app,
        [
            "scan",
            "--workspace",
            "samples/support_refund_agent",
            "--out",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert "Project: support-refund-agent" in result.output


def test_cli_scan_workspace_writes_separate_report_dirs(tmp_path):
    result = runner.invoke(
        app,
        [
            "scan",
            "--workspace",
            "samples/multi_agent_workspace",
            "--out",
            str(tmp_path),
            "--format",
            "json",
            "--ci-mode",
            "advisory",
        ],
    )

    assert result.exit_code == 0
    assert "Scanning 2 manifests" in result.output
    assert len(list(tmp_path.glob("*/report.json"))) == 2
    # v0.8: per-manifest workspace summary leads with the baseline-aware
    # release_decision.decision, NOT the legacy baseline-blind summary.status.
    # Regression for PR #38 reviewer feedback.
    decision_lines = [
        line for line in result.output.splitlines() if "shipgate.yaml:" in line
    ]
    assert decision_lines, "expected per-manifest summary lines in workspace output"
    for line in decision_lines:
        assert any(
            f": {decision} " in line
            for decision in ("blocked", "review_required", "passed")
        ), f"workspace summary line should lead with decision: {line!r}"
        assert "blockers=" in line


def test_cli_scan_workspace_continues_after_config_error(tmp_path):
    workspace = tmp_path / "workspace"
    valid = workspace / "valid"
    invalid = workspace / "invalid"
    valid.mkdir(parents=True)
    invalid.mkdir()
    (valid / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up internal documentation metadata.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (valid / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: valid-workspace
agent:
  name: valid-agent
  declared_purpose:
    - read documentation
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )
    (invalid / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "--workspace",
            str(workspace),
            "--out",
            str(tmp_path / "reports"),
            "--format",
            "json",
            "--ci-mode",
            "advisory",
        ],
    )

    assert result.exit_code == 2
    # v0.8: workspace per-manifest summaries lead with release_decision.
    # The clean valid manifest produces decision=passed.
    assert "valid/shipgate.yaml: passed" in result.output
    assert "invalid/shipgate.yaml: config_error" in result.output
    assert len(list((tmp_path / "reports").glob("*/report.json"))) == 1


def test_safe_output_name_normalizes_unsafe_segments():
    output_name = _safe_output_name(Path("../../etc/passwd/shipgate.yaml"))

    assert output_name
    assert "/" not in output_name
    assert "\\" not in output_name
    assert ":" not in output_name
    assert ".." not in output_name


def test_cli_verbose_json_logs(tmp_path):
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--out",
            str(tmp_path),
            "--format",
            "json",
            "--verbose",
        ],
        env={"AGENTS_SHIPGATE_LOG_FORMAT": "json"},
    )

    assert result.exit_code == 0
    assert '"message": "loaded sources"' in result.output
    assert '"source_count": 4' in result.output


def test_cli_doctor_json_includes_baseline_status():
    result = runner.invoke(
        app,
        [
            "doctor",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "baseline" in payload[0]
    assert payload[0]["baseline"]["default_path"] == ".agents-shipgate/baseline.json"


def test_cli_baseline_save_and_scan(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    save = runner.invoke(
        app,
        [
            "baseline",
            "save",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--out",
            str(baseline_path),
        ],
    )

    assert save.exit_code == 0
    assert baseline_path.exists()
    assert "Findings saved:" in save.output

    scan = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--out",
            str(tmp_path / "reports"),
            "--format",
            "json",
            "--ci-mode",
            "strict",
            "--baseline",
            str(baseline_path),
        ],
    )

    assert scan.exit_code == 0
    assert "Baseline delta: matched=" in scan.output


def test_cli_scan_missing_baseline_exits_three(tmp_path):
    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/support_refund_agent/shipgate.yaml",
            "--baseline",
            str(tmp_path / "missing-baseline.json"),
        ],
    )

    assert result.exit_code == 3
    assert "Baseline file not found" in result.output
