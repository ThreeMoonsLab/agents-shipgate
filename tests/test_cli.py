import json
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin

import click
import pytest
from pydantic import BaseModel
from typer.main import get_command
from typer.testing import CliRunner

from agents_shipgate import __version__
from agents_shipgate.checks import registry
from agents_shipgate.cli.main import _safe_output_name, app
from agents_shipgate.contract import (
    CONTRACT_VERSION,
    GATING_SIGNAL,
    MANUAL_REVIEW_SIGNALS,
)
from agents_shipgate.core.models import ReadinessReport, ToolSurfaceDiffSummary
from agents_shipgate.packet.models import EvidencePacket

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
    assert "Agents Shipgate 0.10.0" in result.output
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
    assert result.output.strip() == "Agents Shipgate 0.10.0"


def test_cli_contract_json_outputs_runtime_contract():
    result = runner.invoke(app, ["contract", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Key order is part of the agent-facing contract payload.
    assert list(payload) == [
        "contract_version",
        "cli_version",
        "report_schema_version",
        "packet_schema_version",
        "gating_signal",
        "manual_review_signals",
    ]
    assert payload == {
        "contract_version": CONTRACT_VERSION,
        "cli_version": __version__,
        "report_schema_version": str(
            ReadinessReport.model_fields["report_schema_version"].default
        ),
        "packet_schema_version": str(
            EvidencePacket.model_fields["packet_schema_version"].default
        ),
        "gating_signal": GATING_SIGNAL,
        "manual_review_signals": list(MANUAL_REVIEW_SIGNALS),
    }


def test_contract_manual_review_signals_resolve_to_model_fields():
    for signal in MANUAL_REVIEW_SIGNALS:
        if signal.startswith("packet."):
            root_model = EvidencePacket
            segments = signal.split(".")[1:]
        else:
            root_model = ReadinessReport
            segments = signal.split(".")
        _assert_field_path_resolves(root_model, segments, signal)


def test_cli_contract_text_outputs_key_values():
    result = runner.invoke(app, ["contract"])

    assert result.exit_code == 0, result.output
    assert CONTRACT_VERSION in result.output
    assert __version__ in result.output
    assert (
        str(ReadinessReport.model_fields["report_schema_version"].default)
        in result.output
    )
    assert (
        str(EvidencePacket.model_fields["packet_schema_version"].default)
        in result.output
    )
    assert GATING_SIGNAL in result.output


def _assert_field_path_resolves(
    root_model: type[BaseModel],
    segments: list[str],
    signal: str,
) -> None:
    model = root_model
    for index, raw_segment in enumerate(segments):
        is_array = raw_segment.endswith("[]")
        field_name = raw_segment.removesuffix("[]")
        assert field_name in model.model_fields, (
            f"{signal!r} references missing field {field_name!r} on "
            f"{model.__name__}"
        )
        if index == len(segments) - 1:
            return
        annotation = model.model_fields[field_name].annotation
        model = (
            _list_item_model(annotation, signal, field_name)
            if is_array
            else _model_annotation(annotation, signal, field_name)
        )


def _list_item_model(
    annotation: object,
    signal: str,
    field_name: str,
) -> type[BaseModel]:
    annotation = _unwrap_optional(annotation)
    assert get_origin(annotation) is list, (
        f"{signal!r} marks {field_name!r} as an array field, but it is "
        f"{annotation!r}"
    )
    args = get_args(annotation)
    assert args, f"{signal!r} array field {field_name!r} has no item type"
    return _model_annotation(args[0], signal, field_name)


def _model_annotation(
    annotation: object,
    signal: str,
    field_name: str,
) -> type[BaseModel]:
    annotation = _unwrap_optional(annotation)
    assert isinstance(annotation, type) and issubclass(annotation, BaseModel), (
        f"{signal!r} traverses through {field_name!r}, but it resolves to "
        f"{annotation!r}, not a Pydantic model"
    )
    return annotation


def _unwrap_optional(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


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


# --- Ranked next-action diagnostics integration ----------------------------


def _stderr_json_lines(output: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (output or "").splitlines()
        if line.strip().startswith("{") and '"error"' in line
    ]


def test_agent_mode_scan_missing_config_emits_next_actions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app, ["scan", "--config", str(tmp_path / "missing.yaml")]
    )

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    assert payloads, f"no agent-mode JSON in output: {result.output!r}"
    payload = payloads[-1]
    assert payload["error"] == "config_error"
    assert isinstance(payload["next_action"], str)
    assert payload["next_action"]
    assert isinstance(payload["next_actions"], list)
    assert payload["next_actions"]
    rank_one = payload["next_actions"][0]
    assert payload["next_action"] == rank_one["command"]


def test_agent_mode_doctor_missing_config_matches_scan(tmp_path, monkeypatch):
    """Cross-command consistency: scan and doctor surface the same diagnostic
    for missing manifest."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    scan_result = runner.invoke(
        app, ["scan", "--config", str(tmp_path / "missing.yaml")]
    )
    doctor_result = runner.invoke(
        app, ["doctor", "--config", str(tmp_path / "missing.yaml")]
    )

    scan_payload = _stderr_json_lines(scan_result.output)[-1]
    doctor_payload = _stderr_json_lines(doctor_result.output)[-1]
    # Same rank-1 next_action whether the agent reached for scan or doctor.
    assert (
        scan_payload["next_actions"][0]["command"]
        == doctor_payload["next_actions"][0]["command"]
    )


def test_detect_emits_negative_control_diagnostic_for_empty_workspace(
    tmp_path,
):
    # Empty workspace — no python, no pyproject, no prompts/tools.
    result = runner.invoke(
        app, ["detect", "--workspace", str(tmp_path), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    diagnostics = payload.get("diagnostics", [])
    assert any(
        d["id"] == "SHIP-DIAG-NO-AGENT-SURFACE" for d in diagnostics
    )
    assert payload["next_actions"]
    rank_one = payload["next_actions"][0]
    assert rank_one["kind"] == "stop"
    # next_action stays string-typed even when rank-1 is "stop".
    assert isinstance(payload["next_action"], str)
    assert payload["next_action"].startswith("Stop:")


def test_detect_plain_json_carries_diagnostics_without_agent_mode(tmp_path):
    """Diagnostics surface in --json output even when AGENTS_SHIPGATE_AGENT_MODE
    is unset. The env var only gates structured-error stderr emission."""
    result = runner.invoke(
        app, ["detect", "--workspace", str(tmp_path), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "diagnostics" in payload
    assert "next_actions" in payload


def test_doctor_emits_unresolved_source_diagnostic_without_failing(tmp_path):
    """Deliberate behavior change: a required tool_sources path that doesn't
    resolve causes ``doctor --json`` to exit 0 with a diagnostic — not the
    legacy InputParseError(3)."""
    config = tmp_path / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: missing-source
agent:
  name: missing-source-agent
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

    result = runner.invoke(app, ["doctor", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    payloads = json.loads(result.output)
    assert len(payloads) == 1
    payload = payloads[0]
    unresolved = payload["unresolved_sources"]
    assert len(unresolved) == 1
    assert unresolved[0]["id"] == "missing"
    assert unresolved[0]["declared_path"] == "missing.json"
    diag_ids = [d["id"] for d in payload["diagnostics"]]
    assert "SHIP-DIAG-MISSING-SOURCE-FILE" in diag_ids


def test_scan_still_raises_on_missing_required_source(tmp_path):
    """Regression guard: doctor's behavior change must not leak into scan.
    scan should still fail with InputParseError(3) when a required
    tool_sources path doesn't resolve."""
    config = tmp_path / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: missing-source
agent:
  name: missing-source-agent
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


# Regression coverage for PR #47 reviewer findings -----------------------------


_MISSING_SOURCE_MANIFEST = """
version: "0.1"
project:
  name: missing-source
agent:
  name: missing-source-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: missing
    type: mcp
    path: missing.json
"""


def test_doctor_human_output_fails_loudly_on_missing_required_source(tmp_path):
    """P1-1 regression: the human (non-JSON) doctor output must not silently
    pass when a required tool_sources path doesn't resolve. It used to raise
    InputParseError(3); now it surfaces the diagnostic and exits 3."""
    config = tmp_path / "shipgate.yaml"
    config.write_text(_MISSING_SOURCE_MANIFEST, encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--config", str(config)])

    assert result.exit_code == 3, result.output
    assert "Unresolved required sources:" in result.output
    assert "missing.json" in result.output
    assert "SHIP-DIAG-MISSING-SOURCE-FILE" in result.output


def test_missing_manifest_recovery_uses_config_workspace(tmp_path, monkeypatch):
    """P2-1 regression: agent-mode rank-1 command must point at the config's
    parent dir, not whichever cwd the CLI was invoked from."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    config = tmp_path / "repo" / "shipgate.yaml"
    config.parent.mkdir()

    result = runner.invoke(app, ["scan", "--config", str(config)])

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    assert payloads, result.output
    rank_one = payloads[-1]["next_actions"][0]
    assert "agents-shipgate detect --workspace" in rank_one["command"]
    # Routes recovery to the config's parent directory, not the foreign cwd.
    assert str(tmp_path / "repo") in rank_one["command"]
    assert str(foreign_cwd) not in rank_one["command"]


def test_doctor_flags_outside_manifest_dir_source_as_diagnostic(tmp_path):
    """P2-2 regression: a required tool_sources path that resolves outside
    the manifest directory must surface as SHIP-DIAG-MISSING-SOURCE-FILE
    with reason="outside_manifest_dir" — not crash the loader."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # The "outside" file genuinely exists, so the existence check alone
    # would pass it through. Containment must catch it.
    outside = tmp_path / "outside.json"
    outside.write_text('{"tools": []}', encoding="utf-8")

    config = repo / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: outside-source
agent:
  name: outside-source-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: escaped
    type: mcp
    path: ../outside.json
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)[0]
    unresolved = payload["unresolved_sources"]
    assert len(unresolved) == 1
    assert unresolved[0]["id"] == "escaped"
    assert unresolved[0]["reason"] == "outside_manifest_dir"
    diag = next(
        d
        for d in payload["diagnostics"]
        if d["id"] == "SHIP-DIAG-MISSING-SOURCE-FILE"
    )
    assert "outside" in diag["next_actions"][0]["why"].lower()


def test_invalid_manifest_dispatches_to_invalid_diagnostic(
    tmp_path, monkeypatch
):
    """P1 regression: ConfigError where the file exists but is invalid must
    surface SHIP-DIAG-INVALID-MANIFEST with an `edit` rank-1 action — NOT
    SHIP-DIAG-MISSING-MANIFEST with a detect/init command. Otherwise the
    coding agent runs `init`, which refuses to overwrite, and loops."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    config = tmp_path / "shipgate.yaml"
    # Valid YAML structure but schema-invalid (missing required project, etc.)
    config.write_text("not: a valid manifest\n", encoding="utf-8")

    for command in (["scan", "--config", str(config)],
                     ["doctor", "--config", str(config)]):
        result = runner.invoke(app, command)
        assert result.exit_code == 2, result.output
        payloads = _stderr_json_lines(result.output)
        assert payloads, result.output
        rank_one = payloads[-1]["next_actions"][0]
        assert rank_one["kind"] == "edit", (
            f"{command[0]} dispatched to {rank_one!r}, expected kind=edit"
        )
        assert str(config) in rank_one["path"]
        # And the next_action string must NOT advertise a detect command —
        # that would route the agent away from the actual fix.
        assert not payloads[-1]["next_action"].startswith(
            "agents-shipgate detect"
        )


def test_invalid_yaml_manifest_dispatches_to_invalid_diagnostic(
    tmp_path, monkeypatch
):
    """Companion to the schema-invalid case: an unparseable YAML file is
    also "exists but invalid" — same dispatch."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    config = tmp_path / "shipgate.yaml"
    config.write_text("version: '0.1\nproject:\n  name: \"x", encoding="utf-8")

    result = runner.invoke(app, ["scan", "--config", str(config)])

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    assert rank_one["kind"] == "edit"
    assert str(config) in rank_one["path"]


def test_missing_manifest_command_quotes_workspace_with_spaces(
    tmp_path, monkeypatch
):
    """P2 regression: dynamic `command` strings must POSIX-shell-quote
    paths so a coding-agent shell runner doesn't word-split a workspace
    containing spaces."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    spaced = tmp_path / "space path" / "repo dir"
    spaced.mkdir(parents=True)
    config = spaced / "shipgate.yaml"
    # File does not exist → MISSING-MANIFEST path → command embeds workspace.

    result = runner.invoke(app, ["scan", "--config", str(config)])

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    command = rank_one["command"]
    # Path must round-trip through shlex; the quoted form survives split.
    import shlex

    parts = shlex.split(command)
    assert parts[0] == "agents-shipgate"
    assert parts[1] == "detect"
    assert "--workspace" in parts
    workspace_arg = parts[parts.index("--workspace") + 1]
    assert workspace_arg == str(spaced)


def test_doctor_workspace_dispatches_invalid_manifest(tmp_path, monkeypatch):
    """P1 regression (round 3): doctor --workspace with an existing-but-invalid
    shipgate.yaml must surface SHIP-DIAG-INVALID-MANIFEST pointing at the
    actual file, not SHIP-DIAG-MISSING-MANIFEST with a detect command."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "shipgate.yaml"
    # Schema-invalid (missing required project block) but valid YAML.
    config.write_text("not: a manifest\n", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--workspace", str(repo)])

    assert result.exit_code == 2, result.output
    payloads = _stderr_json_lines(result.output)
    assert payloads, result.output
    rank_one = payloads[-1]["next_actions"][0]
    assert rank_one["kind"] == "edit", (
        f"--workspace mode dispatched to {rank_one!r}, "
        "expected SHIP-DIAG-INVALID-MANIFEST edit action"
    )
    assert str(config) in rank_one["path"]
    assert not payloads[-1]["next_action"].startswith(
        "agents-shipgate detect"
    )


def test_scan_glob_dispatches_invalid_manifest(tmp_path, monkeypatch):
    """P1 regression (round 3): same dispatch must work for glob configs."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = tmp_path / "subdir"
    repo.mkdir()
    config = repo / "shipgate.yaml"
    config.write_text("not: a manifest\n", encoding="utf-8")
    glob_pattern = str(tmp_path / "*" / "shipgate.yaml")

    result = runner.invoke(app, ["scan", "--config", glob_pattern])

    assert result.exit_code == 2, result.output
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    assert rank_one["kind"] == "edit"
    assert str(config) in rank_one["path"]


def test_glob_with_no_matches_yields_workspace_cwd_not_glob_chars(
    tmp_path, monkeypatch
):
    """P1 regression (round 3): a glob with no matches must produce a
    missing-manifest hint targeting cwd, not a workspace argument
    containing literal `*` characters."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["scan", "--config", "no_such_dir/*/shipgate.yaml"]
    )

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    assert rank_one["kind"] == "command"
    # No literal glob metacharacter ends up in the workspace argument.
    import shlex as _shlex

    parts = _shlex.split(rank_one["command"])
    workspace_arg = parts[parts.index("--workspace") + 1]
    assert "*" not in workspace_arg
    assert "?" not in workspace_arg


def test_artifact_only_command_quotes_workspace_with_spaces(
    tmp_path, monkeypatch
):
    """P2 regression (round 3): SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY's rank-1
    command must shell-quote the workspace path."""
    spaced = tmp_path / "space dir" / "artifact only"
    spaced.mkdir(parents=True)
    # Plant an MCP file matching MCP_PATTERNS so the artifact-only
    # diagnostic fires.
    (spaced / "mcp-tools.json").write_text(
        '{"tools": [{"name": "x"}]}', encoding="utf-8"
    )

    result = runner.invoke(
        app, ["detect", "--workspace", str(spaced), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    diag_ids = [d["id"] for d in payload["diagnostics"]]
    assert "SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY" in diag_ids
    artifact_only = next(
        d
        for d in payload["diagnostics"]
        if d["id"] == "SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY"
    )
    command = artifact_only["next_actions"][0]["command"]
    import shlex as _shlex

    parts = _shlex.split(command)
    assert parts[0] == "agents-shipgate"
    workspace_arg = parts[parts.index("--workspace") + 1]
    assert workspace_arg == str(spaced)


def test_scan_bad_flag_value_does_not_dispatch_to_invalid_manifest(
    tmp_path, monkeypatch
):
    """P1 regression (round 4): a bad CLI flag value (e.g. --fail-on banana)
    raises ConfigError before the manifest is touched. The error must NOT
    surface SHIP-DIAG-INVALID-MANIFEST claiming the loader rejected the
    file — the manifest is fine; the fix is the flag value."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/clean_read_only_agent/shipgate.yaml",
            "--fail-on",
            "banana",
        ],
    )

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    assert payloads, result.output
    rank_one = payloads[-1]["next_actions"][0]
    # Must not advertise an edit action against the manifest, and must
    # not advertise a detect/init command (this isn't a missing manifest
    # either — it's a flag-value problem).
    assert rank_one["kind"] != "edit", (
        f"flag-value error dispatched to {rank_one!r}; the manifest is fine"
    )
    assert "samples/clean_read_only_agent" not in str(
        rank_one.get("path") or ""
    )


@pytest.mark.parametrize(
    ("bad_flag", "bad_value"),
    [
        ("--format", "txt"),
        ("--ci-mode", "yolo"),
        ("--packet-format", "docx"),
    ],
)
def test_scan_other_bad_flag_values_skip_manifest_diagnostic(
    tmp_path, monkeypatch, bad_flag, bad_value
):
    """P1 regression coverage for the rest of the option parsers."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            "samples/clean_read_only_agent/shipgate.yaml",
            bad_flag,
            bad_value,
        ],
    )

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    assert rank_one["kind"] != "edit"


def test_absolute_glob_no_match_targets_glob_prefix(tmp_path, monkeypatch):
    """P2 regression (round 4): an absolute glob with no matches must route
    recovery to the longest non-glob prefix (the area the user explicitly
    pointed at), not the caller's cwd."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(foreign_cwd)

    glob_pattern = str(repo / "*" / "shipgate.yaml")
    result = runner.invoke(app, ["scan", "--config", glob_pattern])

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    assert rank_one["kind"] == "command"
    import shlex as _shlex

    parts = _shlex.split(rank_one["command"])
    workspace_arg = parts[parts.index("--workspace") + 1]
    # Routes to the explicit glob prefix, not the foreign cwd.
    assert workspace_arg == str(repo)
    assert workspace_arg != str(foreign_cwd)


def test_relative_glob_no_match_still_falls_back_to_cwd(tmp_path, monkeypatch):
    """A purely relative glob (no leading non-glob component) keeps the
    existing cwd-fallback behavior — there's no useful prefix to route to."""
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["scan", "--config", "*/shipgate.yaml"])

    assert result.exit_code == 2
    payloads = _stderr_json_lines(result.output)
    rank_one = payloads[-1]["next_actions"][0]
    import shlex as _shlex

    parts = _shlex.split(rank_one["command"])
    workspace_arg = parts[parts.index("--workspace") + 1]
    assert workspace_arg == str(tmp_path)


def test_doctor_edit_action_paths_include_manifest_directory(tmp_path):
    """P2-3 regression: edit-action paths must include the manifest's
    directory so workspace and nested-manifest runs route the agent to
    the correct file, not just `shipgate.yaml:<line>`."""
    repo = tmp_path / "subdir"
    repo.mkdir()
    config = repo / "shipgate.yaml"
    config.write_text(_MISSING_SOURCE_MANIFEST, encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)[0]
    diag = next(
        d
        for d in payload["diagnostics"]
        if d["id"] == "SHIP-DIAG-MISSING-SOURCE-FILE"
    )
    edit_path = diag["next_actions"][0]["path"]
    # Edit target carries the directory the user pointed doctor at.
    assert str(config) in edit_path
    assert edit_path != "shipgate.yaml" and not edit_path.startswith(
        "shipgate.yaml:"
    )
