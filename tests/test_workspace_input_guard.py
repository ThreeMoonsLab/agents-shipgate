"""An absent ``--workspace`` is an invocation error on every command (#389).

The reported defect was ``verify --preview``: given a ``--workspace`` that did
not exist it created the whole four-level path, wrote a complete artifact set
into it, and exited 0 — so a typo produced a confident-looking result about a
workspace that was never there, and in CI it looked healthy on both signals a
caller can gate on. The same class was live elsewhere: ``init --write``,
``audit --host``, and the verification workers raised bare
``FileNotFoundError`` tracebacks; ``install-hooks --write`` wrote hooks into
the mistyped tree; ``mcp audit`` answered ``decision: allow``.

The sweep below is the part that keeps the class closed. It enumerates every
command that exposes ``--workspace`` from the live Typer app and fails if one
is not covered here, so a new command cannot quietly reintroduce the hole.
"""

from __future__ import annotations

import json

import pytest
import typer.main
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import ensure_git_workspace
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.workspace_input import (
    classify_workspace,
    require_workspace_directory,
    workspace_input_error,
)

runner = CliRunner()

# One invocation per command that takes ``--workspace``. Extra flags exist
# only to reach the command body; the guard must fire before any of them
# matter, and before any of them writes.
COMMAND_INVOCATIONS: dict[str, list[str]] = {
    "detect": ["detect"],
    "check": ["check", "--agent", "codex"],
    "preflight": ["preflight", "--json"],
    "bootstrap": ["bootstrap", "--json"],
    "trigger": ["trigger", "--json"],
    "verify": ["verify", "--json"],
    "install-hooks": ["install-hooks", "--target", "claude-code", "--write"],
    "audit": ["audit", "--host", "--json"],
    "scan": ["scan", "--format", "json"],
    "init": ["init", "--write"],
    "doctor": ["doctor", "--json"],
    "agent control": ["agent", "control"],
    "mcp audit": ["mcp", "audit"],
    "org status": ["org", "status"],
    "org policy-packs": ["org", "policy-packs"],
    "org bundle": ["org", "bundle"],
    "verification prepare": ["verification", "prepare"],
    "verification worker": ["verification", "worker", "--plan", "plan.json"],
    "authorization execute": ["authorization", "execute"],
}

# ``verify --preview`` is not a separate command, but it is the invocation the
# issue was filed against and the one with the documented "always exits 0"
# promise, so it is swept alongside the commands.
EXTRA_INVOCATIONS: dict[str, list[str]] = {
    "verify --preview": ["verify", "--preview", "--json"],
}


def _commands_with_workspace_option() -> set[str]:
    """Every command in the live app that exposes ``--workspace``."""

    root = typer.main.get_command(app)
    found: set[str] = set()

    def walk(command: object, prefix: tuple[str, ...]) -> None:
        subcommands = getattr(command, "commands", None)
        if subcommands:
            for name, sub in subcommands.items():
                walk(sub, (*prefix, name))
            return
        options = [
            opt
            for param in getattr(command, "params", ())
            for opt in getattr(param, "opts", ())
        ]
        if "--workspace" in options:
            found.add(" ".join(prefix))

    walk(root, ())
    return found


def test_every_workspace_command_is_swept() -> None:
    """A new ``--workspace`` command must be added to the sweep.

    Without this the sweep silently stops covering the surface it is meant
    to protect: the next command to grow the option would reintroduce the
    "creates the path it was told to inspect" defect unnoticed.
    """

    assert _commands_with_workspace_option() == set(COMMAND_INVOCATIONS)


@pytest.mark.parametrize(
    "argv",
    list(COMMAND_INVOCATIONS.values()) + list(EXTRA_INVOCATIONS.values()),
    ids=list(COMMAND_INVOCATIONS) + list(EXTRA_INVOCATIONS),
)
def test_absent_workspace_is_refused_and_creates_nothing(
    argv: list[str], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    absent = root / "does-not-exist" / "nested" / "workspace"
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(app, [*argv, "--workspace", str(absent)])

    assert result.exit_code == 2, result.output
    assert "does not exist" in result.output
    assert str(absent) in result.output
    # The filesystem is the acceptance criterion the issue actually asks
    # for: refusing after writing the artifacts is not refusing.
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    list(COMMAND_INVOCATIONS.values()) + list(EXTRA_INVOCATIONS.values()),
    ids=list(COMMAND_INVOCATIONS) + list(EXTRA_INVOCATIONS),
)
def test_absent_workspace_emits_one_config_error_line(
    argv: list[str], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agents route on the structured line, not on the prose."""

    absent = tmp_path / "nope"
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(app, [*argv, "--workspace", str(absent)])

    payloads = [
        json.loads(line)
        for line in result.output.splitlines()
        if line.startswith('{"error"')
    ]
    assert len(payloads) == 1, result.output
    payload = payloads[0]
    assert payload["error"] == "config_error"
    assert payload["exit_code"] == 2
    assert "does not exist" in payload["message"]
    assert payload["next_actions"][0]["kind"] == "review"


def test_preview_no_longer_creates_the_workspace_it_was_given(tmp_path) -> None:
    """The reported reproduction, asserted on the filesystem.

    The original run created four directory levels plus an
    ``agents-shipgate-reports`` tree with four artifacts in it, and the
    leftover directory then blocked the ``git clone`` the user had skipped.
    """

    absent = tmp_path / "does-not-exist" / "nested" / "workspace"

    result = runner.invoke(
        app,
        [
            "verify",
            "--preview",
            "--json",
            "--workspace",
            str(absent),
            "--base",
            "main",
            "--head",
            "HEAD",
        ],
    )

    assert result.exit_code == 2
    assert not (tmp_path / "does-not-exist").exists()
    assert list(tmp_path.iterdir()) == []


def test_workspace_pointing_at_a_file_is_named_as_such(tmp_path) -> None:
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text("version: '0.1'\n", encoding="utf-8")

    result = runner.invoke(app, ["detect", "--workspace", str(manifest)])

    assert result.exit_code == 2
    assert "is not a directory" in result.output
    # Distinct from the absent message: a file in the way and a path that
    # was never created are different repairs.
    assert "does not exist" not in result.output


def test_relative_workspace_message_names_the_directory_it_resolved_against(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Ran the preview before the clone" is a cwd mistake as often as a typo."""

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["detect", "--workspace", "adk-samples"])

    assert result.exit_code == 2
    assert "adk-samples" in result.output
    assert str(tmp_path.resolve()) in result.output


# --- the classifier itself --------------------------------------------------


def test_classify_workspace_states(tmp_path) -> None:
    directory = tmp_path / "repo"
    directory.mkdir()
    regular_file = tmp_path / "file.txt"
    regular_file.write_text("x", encoding="utf-8")

    assert classify_workspace(directory) == "present"
    assert classify_workspace(tmp_path / "absent") == "absent"
    assert classify_workspace(regular_file) == "not_a_directory"
    # A path whose *parent* component is a file, not a directory.
    assert classify_workspace(regular_file / "child") == "not_a_directory"


def test_workspace_input_error_is_none_for_a_real_directory(tmp_path) -> None:
    assert workspace_input_error(tmp_path) is None
    assert require_workspace_directory(tmp_path) == tmp_path.resolve()


def test_workspace_input_error_option_label_is_carried(tmp_path) -> None:
    error = workspace_input_error(tmp_path / "absent", option="--out")
    assert error is not None
    assert str(error).startswith("--out does not exist:")


# --- the git reader, which reported absent as "not a git checkout" ----------


def test_ensure_git_workspace_distinguishes_absent_from_not_a_repository(
    tmp_path,
) -> None:
    """Two states, two messages (#384's second instance, filed via #389).

    "Workspace is not inside a git checkout" asserts the directory exists
    and lacks git, which sends the reader to diagnose git when the real
    state is a directory they never created.
    """

    not_a_repository = tmp_path / "plain"
    not_a_repository.mkdir()

    with pytest.raises(ConfigError) as absent:
        ensure_git_workspace(tmp_path / "absent")
    with pytest.raises(ConfigError) as no_git:
        ensure_git_workspace(not_a_repository)

    assert "does not exist" in str(absent.value)
    assert "not inside a git checkout" not in str(absent.value)
    assert "not inside a git checkout" in str(no_git.value)
