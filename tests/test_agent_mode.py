"""Agent-mode environment detection and the verify JSON stdout surface.

Claude Code exports ``CLAUDECODE=1`` (and Cursor ``CURSOR_TRACE_ID``) in
every shell it spawns. ``is_agent_mode`` auto-enables agent mode on those
hints so coding agents get structured errors and JSON verifier stdout without
remembering ``AGENTS_SHIPGATE_AGENT_MODE=1``. An explicit
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

from agents_shipgate.cli.agent_mode import (
    AGENT_ENV_HINTS,
    DEFAULT_ACTOR,
    detect_actor,
    emit_agent_mode_error,
    is_agent_mode,
)
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
    assert is_agent_mode({"AGENTS_SHIPGATE_AGENT_MODE": "maybe", "CLAUDECODE": "1"}) is True


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
    assert payload["error"] == "config_error"
    assert payload["message"] == "boom"
    assert "command" in payload


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
    assert _resolve_verify_format("text", json_output=True, preview=False) == "text"


def test_resolve_format_json_shortcut_is_verifier_surface() -> None:
    assert _resolve_verify_format(None, json_output=True, preview=False) == "json"


def test_resolve_format_json_shortcut_keeps_full_json_for_preview() -> None:
    assert _resolve_verify_format(None, json_output=True, preview=True) == "json"


def test_resolve_format_defaults_to_text_without_agent_environment() -> None:
    assert _resolve_verify_format(None, json_output=False, preview=False) == "text"


def test_resolve_format_agent_environment_defaults_to_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _resolve_verify_format(None, json_output=False, preview=False) == "json"
    assert _resolve_verify_format(None, json_output=False, preview=True) == "json"


def test_resolve_format_explicit_text_wins_in_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _resolve_verify_format("text", json_output=False, preview=False) == "text"


def test_resolve_format_rejects_removed_agent_value() -> None:
    with pytest.raises(ConfigError):
        _resolve_verify_format("agent", json_output=False, preview=False)


def test_resolve_format_rejects_unknown_value() -> None:
    with pytest.raises(ConfigError):
        _resolve_verify_format("yaml", json_output=False, preview=False)


# --- verify --json CLI surface ------------------------------------------------


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
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
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
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


def test_verify_json_shortcut_prints_verifier_artifact(tmp_path: Path) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(app, _verify_args(repo, "--json"))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verifier_schema_version"] == "0.12"
    assert payload["merge_verdict"] == "insufficient_evidence"
    assert payload["can_merge_without_human"] is False
    # The run completed and produced a release decision; the outstanding
    # obligation is human judgement, so the agent may still publish the change.
    assert payload["control"]["state"] == "review_publishable"
    assert payload["control"]["permissions"]["merge"] is False
    # Full artifacts still land on disk for the documented file contract.
    assert (repo / "agents-shipgate-reports" / "verifier.json").is_file()
    assert (repo / "agents-shipgate-reports" / "verify-run.json").is_file()
    handoff_path = repo / "agents-shipgate-reports" / "agent-handoff.json"
    assert handoff_path.is_file()
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff["schema_version"] == "shipgate.agent_handoff/v7"
    assert handoff["operation"] == "verify_pr"
    assert not (repo / "agents-shipgate-reports" / "agent-result.json").exists()


def test_verify_preview_writes_agent_handoff(tmp_path: Path) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    handoff = json.loads(
        (repo / "agents-shipgate-reports" / "agent-handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["schema_version"] == "shipgate.agent_handoff/v7"
    assert handoff["operation"] == "verify_preview"
    assert handoff["gate"]["decision"] is None
    assert handoff["control"]["state"] == "agent_action_required"
    assert handoff["control"]["completion_allowed"] is False


def test_verify_format_json_still_prints_full_verifier_artifact(
    tmp_path: Path,
) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(app, _verify_args(repo, "--format", "json"))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verifier_schema_version"] == "0.12"
    assert payload["execution"] == "succeeded"
    assert payload["head_status"] == "succeeded"
    assert payload["trigger"]["run_shipgate"] is True
    assert payload["trigger"]["force_run"] is True


def test_verify_agent_environment_defaults_to_verifier_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _docs_only_repo(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")

    result = runner.invoke(app, _verify_args(repo))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verifier_schema_version"] == "0.12"
    assert payload["merge_verdict"] == "insufficient_evidence"


def test_verify_without_agent_environment_defaults_to_text(
    tmp_path: Path,
) -> None:
    repo = _docs_only_repo(tmp_path)

    result = runner.invoke(app, _verify_args(repo))

    assert result.exit_code == 0, result.output
    # Text mode leads with the control state (contract v22); the verdict line
    # follows it. What this test pins is that the output is text at all, not
    # the verifier JSON an agent environment would have selected.
    assert result.output.startswith("Control: ")
    assert "Agents Shipgate verify:" in result.output
    assert "verifier_schema_version" not in result.output


# --- detect_actor -----------------------------------------------------------


def test_detect_actor_defaults_to_codex() -> None:
    assert detect_actor({}) == "codex"


def test_detect_actor_recognizes_claude_code() -> None:
    assert detect_actor({"CLAUDECODE": "1"}) == "claude-code"


def test_detect_actor_recognizes_cursor() -> None:
    assert detect_actor({"CURSOR_TRACE_ID": "abc123"}) == "cursor"


def test_detect_actor_ignores_empty_hint_values() -> None:
    assert detect_actor({"CLAUDECODE": ""}) == "codex"


def test_agent_env_hints_stay_in_sync_with_actor_detection() -> None:
    """One table drives both, so agent mode and the actor cannot disagree."""

    assert AGENT_ENV_HINTS == ("CLAUDECODE", "CURSOR_TRACE_ID")
    for hint in AGENT_ENV_HINTS:
        assert is_agent_mode({hint: "x"}) is True
        assert detect_actor({hint: "x"}) != DEFAULT_ACTOR


def test_check_labels_its_result_with_the_detected_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Claude Code run used to record `codex` in the result and audit id."""

    monkeypatch.setenv("CLAUDECODE", "1")
    result = runner.invoke(app, ["check", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["agent"] == "claude-code"


def test_check_explicit_agent_flag_beats_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    result = runner.invoke(
        app, ["check", "--workspace", str(tmp_path), "--agent", "codex"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["agent"] == "codex"


# --- structured errors on the commands that had none ------------------------


_DOCUMENTED_ERROR_IDS = {
    entry["id"]
    for entry in json.loads(
        (Path(__file__).resolve().parent.parent / "docs" / "errors.json").read_text(
            encoding="utf-8"
        )
    )["errors"]
}


def test_config_error_catalog_covers_non_manifest_request_configuration() -> None:
    catalog = json.loads(
        (Path(__file__).resolve().parent.parent / "docs" / "errors.json").read_text(
            encoding="utf-8"
        )
    )
    entry = next(item for item in catalog["errors"] if item["id"] == "config_error")

    assert "incompatible CLI flags" in entry["description"]
    assert "preflight" in entry["typical_cause"]
    assert "host-grants baseline" in entry["typical_cause"]
    assert "do not assume every config_error is a manifest problem" in entry[
        "recovery_hint"
    ]


def _assert_documented_envelope(payload: dict) -> None:
    """`docs/errors.json` is the contract, not a description of what we emit.

    It states that an agent-mode error line always carries `next_action` *and*
    the ranked `next_actions` array, and that the kind is one of the published
    ids. An agent that routes on the documented array got nothing from an
    emitter that shipped only the legacy string.
    """

    assert payload["error"] in _DOCUMENTED_ERROR_IDS, payload["error"]
    assert isinstance(payload.get("next_actions"), list)
    assert payload["next_actions"], payload
    for action in payload["next_actions"]:
        assert action["kind"] in {"command", "edit", "review", "stop"}
        assert action["why"]
        if action["kind"] == "command":
            assert action["command"]
    assert payload["next_action"]


def _agent_mode_error(result) -> dict:
    """The last stderr line of an agent-mode run, parsed."""

    lines = [line for line in result.output.splitlines() if line.startswith("{")]
    assert lines, result.output
    return json.loads(lines[-1])


@pytest.mark.parametrize("command", ["detect", "init"])
def test_discovery_inventory_failure_is_a_structured_agent_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    monkeypatch.setattr(
        "agents_shipgate.cli.discovery.artifacts._run_git_inventory_bounded",
        lambda *_args, **_kwargs: None,
    )
    args = [command, "--workspace", str(tmp_path), "--json"]

    result = runner.invoke(app, args)

    assert result.exit_code == 4
    assert "Traceback" not in result.output
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert payload["error"] == "other_error"
    assert payload["next_actions"][0]["kind"] == "review"
    assert "bounded coverage" in payload["message"]
    assert not (tmp_path / "shipgate.yaml").exists()


def test_check_rejects_an_unknown_format_on_the_agent_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app, ["check", "--workspace", str(tmp_path), "--format", "nope"]
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    assert payload["error"] == "config_error"
    assert payload["exit_code"] == 2
    assert "agent-boundary-json" in payload["next_action"]
    _assert_documented_envelope(payload)


def test_check_rejects_an_unknown_agent_on_the_agent_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app, ["check", "--workspace", str(tmp_path), "--agent", "nope"]
    )

    assert result.exit_code == 2
    _assert_documented_envelope(_agent_mode_error(result))


def test_audit_without_host_reports_a_next_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(app, ["audit"])

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    assert payload["error"] == "config_error"
    assert "--host" in payload["next_action"]
    _assert_documented_envelope(payload)


def test_preflight_config_error_reports_a_next_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "shipgate.yaml").write_text(
        'version: "1"\nproject:\n  name: [broken\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["preflight", "--workspace", str(tmp_path)])

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    assert payload["error"] == "config_error"
    assert payload["exit_code"] == 2
    _assert_documented_envelope(payload)


def test_preflight_silent_without_agent_mode(tmp_path: Path) -> None:
    (tmp_path / "shipgate.yaml").write_text(
        'version: "1"\nproject:\n  name: [broken\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["preflight", "--workspace", str(tmp_path)])

    assert result.exit_code == 2
    assert not [line for line in result.output.splitlines() if line.startswith("{")]


def test_audit_reports_an_unwritable_out_path_as_other_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unwritable --out was a Rich traceback and exit 1, not a contract.

    The catalog gives filesystem failures `other_error`/4; reporting them as
    `config_error`/2 sends an agent back to re-read flags that were fine.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--json",
            "--out",
            str(blocker / "nested" / "audit.json"),
        ],
    )

    assert result.exit_code == 4
    payload = _agent_mode_error(result)
    assert payload["error"] == "other_error"
    assert payload["exit_code"] == 4
    _assert_documented_envelope(payload)


def test_preflight_reports_only_documented_error_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shipgate_error` was not an id an agent could look up."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "shipgate.yaml").write_text(
        'version: "1"\nproject:\n  name: demo\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["preflight", "--workspace", str(tmp_path)])

    assert result.exit_code != 0
    _assert_documented_envelope(_agent_mode_error(result))


def test_preflight_recovery_edits_then_reruns_the_same_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair comes first; the later rerun must still preserve the request.

    Replaying a malformed manifest cannot repair it. After the edit, a bare
    `preflight --json` would still answer a question nobody asked because it
    discards workspace, config, plan, diff and capability request.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "shipgate.yaml").write_text(
        'version: "1"\nproject:\n  name: [broken\n', encoding="utf-8"
    )
    result = runner.invoke(
        app, ["preflight", "--workspace", str(tmp_path), "--config", "shipgate.yaml"]
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    actions = payload["next_actions"]
    assert actions[0]["kind"] == "edit"
    assert actions[0]["path"] == str(tmp_path / "shipgate.yaml")
    assert payload["next_action"] == f"Edit {tmp_path / 'shipgate.yaml'}"
    assert actions[1]["kind"] == "command"
    assert str(tmp_path) in actions[1]["command"]
    assert "--config shipgate.yaml" in actions[1]["command"]


def test_preflight_config_identity_error_is_review_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlinked trust root is not an editable or replayable repair target."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    target = tmp_path / "actual-gate.yml"
    target.write_text(
        'version: "0.1"\n'
        "project:\n  name: demo\n"
        "agent:\n  name: demo\n  declared_purpose:\n    - Test.\n"
        "environment:\n  target: development\n"
        "tool_sources: []\n",
        encoding="utf-8",
    )
    alias = tmp_path / "gate.yml"
    alias.symlink_to(target.name)

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(tmp_path),
            "--config",
            alias.name,
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert payload["error"] == "config_error"
    assert len(payload["next_actions"]) == 1
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert action["path"] is None
    assert "symlink" in payload["message"].lower()


def test_preflight_external_request_file_is_not_an_edit_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing a file for inspection does not grant authority to rewrite it."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_plan = tmp_path / "outside-plan.json"
    external_plan.write_text("{", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(workspace),
            "--plan",
            str(external_plan),
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert len(payload["next_actions"]) == 1
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert action["path"] is None
    assert str(external_plan) in payload["message"]


@pytest.mark.parametrize(
    ("flag", "payload_text", "exit_code"),
    [
        ("--plan", "[]\n", 3),
        ("--plan", '{"changed_files": "not-a-list"}\n', 2),
        ("--capability-request", "[]\n", 3),
        ("--capability-request", "{}\n", 2),
        ("--base-preflight", "[]\n", 3),
        ("--base-preflight", "{}\n", 2),
    ],
)
def test_preflight_external_json_errors_name_source_and_stay_review_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    payload_text: str,
    exit_code: int,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / f"outside-{flag.removeprefix('--')}.json"
    external.write_text(payload_text, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(workspace),
            flag,
            str(external),
            "--json",
        ],
    )

    assert result.exit_code == exit_code, result.output
    envelope = _agent_mode_error(result)
    _assert_documented_envelope(envelope)
    assert str(external) in envelope["message"]
    assert len(envelope["next_actions"]) == 1
    action = envelope["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert action["path"] is None


def test_preflight_hardlinked_request_is_not_an_edit_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external-request.json"
    external.write_text("{", encoding="utf-8")
    request = workspace / "request.json"
    request.hardlink_to(external)

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(workspace),
            "--capability-request",
            str(request),
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    envelope = _agent_mode_error(result)
    _assert_documented_envelope(envelope)
    assert request.stat().st_nlink == 2
    assert len(envelope["next_actions"]) == 1
    action = envelope["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert action["path"] is None


def test_preflight_malformed_request_edits_then_replays_exact_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    request = tmp_path / "capability-request.json"
    request.write_text("{", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(tmp_path),
            "--capability-request",
            str(request),
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    actions = payload["next_actions"]
    assert actions[0]["kind"] == "edit"
    assert actions[0]["path"] == str(request)
    assert actions[1]["kind"] == "command"
    assert actions[1]["command"] == (
        "agents-shipgate preflight "
        f"--workspace {tmp_path} "
        "--config shipgate.yaml "
        f"--capability-request {request} "
        "--json"
    )


def test_preflight_offers_no_command_it_cannot_reproduce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request read from stdin cannot be rerun; a review action is honest."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "shipgate.yaml").write_text(
        'version: "1"\nproject:\n  name: [broken\n', encoding="utf-8"
    )
    result = runner.invoke(
        app,
        ["preflight", "--workspace", str(tmp_path), "--plan", "-"],
        input="{}\n",
    )

    assert result.exit_code != 0
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert payload["next_actions"][0]["kind"] == "review"
    assert len(payload["next_actions"]) == 1


def test_audit_baseline_directory_is_an_other_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IsADirectoryError reached the user as a traceback and exit 1."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "as-a-dir").mkdir()
    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--save-baseline",
            "--baseline-file",
            str(tmp_path / "as-a-dir"),
        ],
    )

    assert result.exit_code == 4, result.output
    payload = _agent_mode_error(result)
    assert payload["error"] == "other_error"
    assert payload["exit_code"] == 4
    _assert_documented_envelope(payload)


def test_check_recovery_keeps_the_rest_of_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed command discards every valid argument around the bad one.

    Following it switched actor, workspace, config, and policy — answering a
    different boundary question than the one that failed.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app,
        [
            "check",
            "--agent",
            "cursor",
            "--workspace",
            str(tmp_path),
            "--config",
            "new-gate.yml",
            "--format",
            "nope",
        ],
    )

    assert result.exit_code == 2
    command = _agent_mode_error(result)["next_actions"][0]["command"]
    assert "--agent cursor" in command
    assert str(tmp_path) in command
    assert "--config new-gate.yml" in command
    assert "--format agent-boundary-json" in command


def test_check_offers_no_command_for_a_stdin_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app, ["check", "--diff", "-", "--format", "nope"], input=""
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert payload["next_actions"][0]["kind"] == "review"
    assert payload["next_actions"][0]["command"] is None


def test_check_offers_no_command_for_a_one_sided_range_flag_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correcting --format must not silently switch a range to the worktree."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app,
        ["check", "--base", "origin/main", "--format", "nope"],
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert payload["next_actions"][0]["kind"] == "review"
    assert payload["next_actions"][0]["command"] is None


@pytest.mark.parametrize(
    "args",
    [
        ["--diff", "changes.diff", "--base", "origin/main", "--head", "HEAD"],
        ["--diff", "changes.diff", "--head", "HEAD"],
        ["--diff", ""],
        ["--base", "", "--head", "HEAD"],
        ["--base", "", "--head", ""],
    ],
)
def test_check_rejects_ambiguous_diff_shapes_without_a_command(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(app, ["check", *args])

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None


def test_check_missing_diff_file_requires_review_instead_of_repeating_request(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing change.diff"

    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(missing),
            "--format",
            "agent-boundary-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["audit_id"].startswith("agent_boundary_error_")
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["next_action"]["kind"] == "review"
    assert payload["control"]["allowed_next_commands"] == []
    assert str(missing) in payload["control"]["next_action"]["why"]
    assert payload["repair"]["safe_to_attempt"] is False
    assert "command" not in payload["repair"]


def test_check_missing_refs_requests_inputs_without_repeating_failed_command(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _commit_all(repo, "initial")

    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(repo),
            "--base",
            "origin/missing",
            "--head",
            "HEAD",
            "--format",
            "agent-boundary-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["audit_id"].startswith("agent_boundary_error_")
    assert payload["control"]["state"] == "agent_action_required"
    action = payload["control"]["next_action"]
    assert action["kind"] == "fetch_base"
    assert action["command"] is None
    assert action["expects"] == "origin/missing and HEAD"
    assert payload["control"]["allowed_next_commands"] == []
    assert payload["repair"]["safe_to_attempt"] is False
    assert "command" not in payload["repair"]


def test_preflight_offers_no_command_for_conflicting_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replay of a request-shape conflict can never satisfy its own expects."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    (tmp_path / "changed.txt").write_text("README.md\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(tmp_path / "plan.json"),
            "--changed-files",
            str(tmp_path / "changed.txt"),
        ],
    )

    assert result.exit_code != 0
    payload = _agent_mode_error(result)
    _assert_documented_envelope(payload)
    assert payload["next_actions"][0]["kind"] == "review"
    assert len(payload["next_actions"]) == 1


def test_audit_missing_baseline_requires_human_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drift request never authorizes acceptance of the current grants."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    baseline = tmp_path / "missing baseline; grants.json"
    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--scope",
            "local-static",
            "--drift",
            "--baseline-file",
            str(baseline),
        ],
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    assert payload["error"] == "config_error"
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert "Human review is required" in action["why"]
    assert str(tmp_path) in action["why"]
    assert "local-static" in action["why"]
    assert "--save-baseline" not in result.output
    assert payload["message"].endswith(
        f"No baseline was written to {baseline}."
    )
    assert "agents-shipgate audit" not in payload["message"]
    assert "agents-shipgate audit" not in action["why"]


@pytest.mark.parametrize(
    "failure",
    ["malformed_json", "unsupported_schema", "integrity_failure"],
)
def test_audit_never_overwrites_an_invalid_existing_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """An error action must preserve the evidence instead of accepting drift."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    baseline = tmp_path / "important baseline.json"
    if failure == "malformed_json":
        baseline.write_text("{not json", encoding="utf-8")
    elif failure == "unsupported_schema":
        baseline.write_text(
            json.dumps({"host_grants_schema_version": "99.0"}),
            encoding="utf-8",
        )
    else:
        saved = runner.invoke(
            app,
            [
                "audit",
                "--host",
                "--workspace",
                str(tmp_path),
                "--save-baseline",
                "--baseline-file",
                str(baseline),
                "--json",
            ],
        )
        assert saved.exit_code == 0, saved.output
        data = json.loads(baseline.read_text(encoding="utf-8"))
        data["inventory_sha256"] = "0" * 64
        baseline.write_text(json.dumps(data), encoding="utf-8")
    before = baseline.read_bytes()

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--drift",
            "--baseline-file",
            str(baseline),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result)
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert "--save-baseline" not in result.output
    assert baseline.read_bytes() == before

    save = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--save-baseline",
            "--baseline-file",
            str(baseline),
            "--json",
        ],
    )
    assert save.exit_code == 2
    save_action = _agent_mode_error(save)["next_actions"][0]
    assert save_action["kind"] == "review"
    assert save_action["command"] is None
    assert "--save-baseline" not in save.output
    assert baseline.read_bytes() == before


def test_audit_does_not_treat_a_broken_baseline_symlink_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving through a broken link could create a target outside the request."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    target = tmp_path / "missing-target.json"
    baseline = tmp_path / "baseline.json"
    baseline.symlink_to(target)

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--drift",
            "--baseline-file",
            str(baseline),
            "--json",
        ],
    )

    assert result.exit_code == 2
    action = _agent_mode_error(result)["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert not target.exists()

    save = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--save-baseline",
            "--baseline-file",
            str(baseline),
            "--json",
        ],
    )
    assert save.exit_code == 2
    save_action = _agent_mode_error(save)["next_actions"][0]
    assert save_action["kind"] == "review"
    assert save_action["command"] is None
    assert not target.exists()


def test_audit_unreadable_baseline_is_a_filesystem_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader turns every read OSError into ValueError; the cause matters."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / "as-a-dir").mkdir()
    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--drift",
            "--baseline-file",
            str(tmp_path / "as-a-dir"),
        ],
    )

    assert result.exit_code == 4
    payload = _agent_mode_error(result)
    assert payload["error"] == "other_error"
    _assert_documented_envelope(payload)
