"""Tests for ``agents-shipgate self-check``."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()


def test_self_check_text_output_indicates_ready():
    result = runner.invoke(app, ["self-check"])
    assert result.exit_code == 0, result.output
    assert "Ready: yes" in result.output


def test_self_check_json_output_is_well_formed():
    result = runner.invoke(app, ["self-check", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    for key in ("version", "python", "platform", "fixtures_run", "cli_surface", "ready"):
        assert key in payload, f"self-check JSON missing key: {key}"
    assert payload["ready"] is True
    assert payload["cli_surface"]["contract"] == "ok"
    # Every CLI command should resolve cleanly in a healthy environment.
    for status in payload["cli_surface"].values():
        assert status == "ok", f"unhealthy CLI surface: {payload['cli_surface']}"
    # Bundled fixtures should run successfully.
    for name, status in payload["fixtures_run"].items():
        assert status == "ok", f"fixture {name} not ok: {status}"
