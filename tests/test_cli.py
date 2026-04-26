import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.checks import registry
from agents_shipgate.cli.main import _safe_output_name, app

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
    assert "Agents Shipgate 0.2.0" in result.output
    assert "release_blockers_detected" in result.output


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


def test_cli_scan_help_hides_deferred_flags():
    result = runner.invoke(app, ["scan", "--help"])

    assert result.exit_code == 0
    assert "--deep-import" not in result.output
    assert "--baseline-mode" not in result.output


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
    assert "valid/shipgate.yaml: no_release_blockers_detected" in result.output
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
    assert "Baseline: matched=" in scan.output


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
