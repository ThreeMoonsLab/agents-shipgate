"""Agent-mode environment detection and the compact verify stdout surface.

Claude Code exports ``CLAUDECODE=1`` (and Cursor ``CURSOR_TRACE_ID``) in
every shell it spawns. ``is_agent_mode`` auto-enables agent mode on those
hints so coding agents get structured errors and the compact agent-result
stdout without remembering ``AGENTS_SHIPGATE_AGENT_MODE=1``. An explicit
``AGENTS_SHIPGATE_AGENT_MODE`` value wins in both directions.

The suite-wide autouse fixture in the root ``conftest.py`` scrubs these
variables, so each test sets exactly the environment it asserts on.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.agent_mode import emit_agent_mode_error, is_agent_mode
from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.command import _resolve_verify_format
from agents_shipgate.core.errors import ConfigError

runner = CliRunner()


# --- is_agent_mode ----------------------------------------------------------


def test_is_agent_mode_off_by_default() -> None:
    assert is_agent_mode({}) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " 1 "])
def test_is_agent_mode_explicit_truthy(value: str) -> None:
    assert is_agent_mode({"AGENTS_SHIPGATE_AGENT_MODE": value}) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_is_agent_mode_explicit_falsy_overrides_harness_hint(value: str) -> None:
    env = {"AGENTS_SHIPGATE_AGENT_MODE": value, "CLAUDECODE": "1"}
    assert is_agent_mode(env) is False


def test_is_agent_mode_auto_enables_for_claude_code() -> None:
    assert is_agent_mode({"CLAUDECODE": "1"}) is True


def test_is_agent_mode_auto_enables_for_cursor() -> None:
    assert is_agent_mode({"CURSOR_TRACE_ID": "abc123"}) is True


def test_is_agent_mode_ignores_empty_hint_values() -> None:
    assert is_agent_mode({"CLAUDECODE": ""}) is False


def test_is_agent_mode_unrecognized_explicit_value_falls_back_to_hints() -> None:
    assert is_agent_mode({"AGENTS_SHIPGATE_AGENT_MODE": "maybe"}) is False
    assert (
        is_agent_mode({"AGENTS_SHIPGATE_AGENT_MODE": "maybe", "CLAUDECODE": "1"})
        is True
    )


def test_is_agent_mode_reads_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert is_agent_mode() is False
    monkeypatch.setenv("CLAUDECODE", "1")
    assert is_agent_mode() is True


# --- emit_agent_mode_error ---------------------------------------------------


def test_emit_agent_mode_error_auto_detects_claude_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    emit_agent_mode_error("config_error", message="boom")
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload == {"error": "config_error", "message": "boom"}


def test_emit_agent_mode_error_silent_without_agent_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_agent_mode_error("config_error", message="boom")
    assert capsys.readouterr().err == ""


def test_emit_agent_mode_error_respects_explicit_opt_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "0")
    emit_agent_mode_error("config_error", message="boom")
    assert capsys.readouterr().err == ""


# --- verify stdout format resolution -----------------------------------------


def test_resolve_format_explicit_flag_wins_over_json_shortcut() -> None:
    assert (
        _resolve_verify_format("text", json_output=True, preview=False) == "text"
    )


def test_resolve_format_json_shortcut_is_compact_agent_surface() -> None:
    assert _resolve_verify_format(None, json_output=True, preview=False) == "agent"


def test_resolve_format_json_shortcut_keeps_full_json_for_preview() -> None:
    assert _resolve_verify_format(None, json_output=True, preview=True) == "json"


def test_resolve_format_defaults_to_text_without_agent_environment() -> None:
    assert _resolve_verify_format(None, json_output=False, preview=False) == "text"


def test_resolve_format_agent_environment_defaults_to_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _resolve_verify_format(None, json_output=False, preview=False) == "agent"
    assert _resolve_verify_format(None, json_output=False, preview=True) == "json"


def test_resolve_format_explicit_text_wins_in_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _resolve_verify_format("text", json_output=False, preview=False) == "text"


def test_resolve_format_accepts_agent_value() -> None:
    assert _resolve_verify_format("agent", json_output=False, preview=False) == "agent"


def test_resolve_format_rejects_unknown_value() -> None:
    with pytest.raises(ConfigError):
        _resolve_verify_format("yaml", json_output=False, preview=False)


# --- verify --json CLI surface ------------------------------------------------


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _set_origin_main(repo: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )


def _docs_only_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")
    return repo


def _verify_args(repo: Path, *extra: str) -> list[str]:
    return [
        "verify",
        "--workspace",
        str(repo),
        "--config",
        "shipgate.yaml",
        "--base",
        "origin/main",
        "--head",
        "HEAD",
        *extra,
    ]


def test_verify_json_shortcut_prints_compact_agent_result(tmp_path: Path) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(app, _verify_args(repo, "--json"))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "shipgate.agent_result/v1"
    assert payload["decision"] == "allow"
    assert payload["merge_verdict"] == "mergeable"
    assert payload["can_merge_without_human"] is True
    assert "agent_repair_instructions" in payload
    # The compact surface stays small enough to land in an agent
    # transcript without a second file read.
    assert len(result.output) < 4096
    # Full artifacts still land on disk for the documented file contract.
    assert (repo / "agents-shipgate-reports" / "verifier.json").is_file()


def test_verify_format_json_still_prints_full_verifier_artifact(
    tmp_path: Path,
) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(app, _verify_args(repo, "--format", "json"))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verifier_schema_version"] == "0.1"
    assert payload["head_status"] == "skipped"
    assert payload["trigger"]["run_shipgate"] is False


def test_verify_agent_environment_defaults_to_compact_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _docs_only_repo(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")

    result = runner.invoke(app, _verify_args(repo))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "shipgate.agent_result/v1"


def test_verify_without_agent_environment_defaults_to_text(
    tmp_path: Path,
) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(app, _verify_args(repo))

    assert result.exit_code == 0, result.output
    assert result.output.startswith("Agents Shipgate verify:")
