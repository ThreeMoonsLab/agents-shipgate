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


def test_logging_handler_survives_stream_swap_between_invocations(capsys):
    """Regression for the flaky CI self-check JSON failure (PR #192):
    a logging handler bound to a since-closed stderr (an earlier
    in-process CLI invocation's capture buffer) must not raise and must
    not print '--- Logging error ---' into a later invocation's output.
    The handler resolves sys.stderr at emit time."""
    import io
    import logging
    import sys

    from agents_shipgate.core.logging import configure_logging

    old_stderr = sys.stderr
    first = io.StringIO()
    try:
        # Invocation A configures logging while its capture is active...
        sys.stderr = first
        configure_logging(verbose=True, force=True)
        # ...then A's stream is closed, as CliRunner does on exit.
        first.close()

        # Invocation B runs with a fresh stderr; emitting must write to
        # B's live stream, not A's closed buffer.
        second = io.StringIO()
        sys.stderr = second
        logging.getLogger("agents_shipgate.test").debug("checks completed")

        assert "checks completed" in second.getvalue()
        assert "Logging error" not in second.getvalue()
    finally:
        sys.stderr = old_stderr
        configure_logging(verbose=False, force=True)
