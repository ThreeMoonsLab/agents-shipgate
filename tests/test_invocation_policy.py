"""#322: emitted next-action commands must run in the environment that emitted them.

Running from a source checkout with ``python -m agents_shipgate`` used to emit
``agents-shipgate ...`` recovery commands, which need a console script the
checkout may not have installed, and to report the running command as
``__main__.py`` — not a program, and a string that corrupts to ``**main**.py``
in any consumer that renders it as Markdown.

The end-to-end tests at the bottom of this module are the ones that would have
caught the original bug: they run a real ``python -m agents_shipgate``
subprocess and read what the process actually printed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents_shipgate.invocation import (
    CANONICAL_CONSOLE_SCRIPT,
    CLI_OVERRIDE_ENV_VAR,
    invocation_prefix,
    is_console_script_invocation,
    render_command,
    retarget_command,
    split_invocation,
)
from agents_shipgate.schemas.diagnostics import NextAction

REPO_ROOT = Path(__file__).resolve().parents[1]

_CONSOLE = SimpleNamespace(__spec__=None)
_MODULE = SimpleNamespace(
    __spec__=SimpleNamespace(name="agents_shipgate.__main__", parent="agents_shipgate")
)


def _module_prefix() -> tuple[str, ...]:
    return (sys.executable, "-m", "agents_shipgate")


# --------------------------------------------------------------------------
# invocation_prefix
# --------------------------------------------------------------------------


@pytest.mark.parametrize("script", ["agents-shipgate", "shipgate"])
def test_console_script_invocation_reports_that_script(script: str) -> None:
    prefix = invocation_prefix(
        argv=[f"/opt/venv/bin/{script}", "scan"], env={}, main_module=_CONSOLE
    )
    assert prefix == (script,)
    assert is_console_script_invocation(
        argv=[f"/opt/venv/bin/{script}", "scan"], env={}, main_module=_CONSOLE
    )


def test_windows_console_script_wrapper_is_recognised() -> None:
    prefix = invocation_prefix(
        argv=[r"C:\venv\Scripts\agents-shipgate.exe", "scan"],
        env={},
        main_module=_CONSOLE,
    )
    assert prefix == ("agents-shipgate",)


def test_module_invocation_reports_the_running_interpreter() -> None:
    argv = [str(REPO_ROOT / "src/agents_shipgate/__main__.py"), "scan"]
    assert invocation_prefix(argv=argv, env={}, main_module=_MODULE) == _module_prefix()


def test_module_invocation_is_detected_from_argv_when_the_spec_is_gone() -> None:
    """Some launchers rewrite ``__main__.__spec__``; argv still names the file."""

    argv = [str(REPO_ROOT / "src/agents_shipgate/__main__.py"), "scan"]
    assert invocation_prefix(argv=argv, env={}, main_module=_CONSOLE) == _module_prefix()


def test_another_packages_module_run_is_not_mistaken_for_ours() -> None:
    """``python -m pytest`` also puts a ``__main__.py`` in argv[0].

    Reading that as "entered through ``-m agents_shipgate``" would make every
    command emitted from an in-process test claim an entry point the caller
    never used.
    """

    foreign = SimpleNamespace(__spec__=SimpleNamespace(name="pytest.__main__", parent="pytest"))
    prefix = invocation_prefix(
        argv=["/opt/venv/lib/python3.13/site-packages/pytest/__main__.py", "-q"],
        env={},
        main_module=foreign,
    )
    assert prefix == (CANONICAL_CONSOLE_SCRIPT,)


def test_unrecognised_argv_falls_back_to_the_canonical_console_script() -> None:
    """A test runner, an embedding host, or a rewritten argv.

    Guessing from an unrecognised argv would emit a differently-broken command;
    the documented spelling is the safe answer.
    """

    prefix = invocation_prefix(argv=["/usr/bin/pytest", "-q"], env={}, main_module=_CONSOLE)
    assert prefix == (CANONICAL_CONSOLE_SCRIPT,)


def test_empty_argv_does_not_raise() -> None:
    assert invocation_prefix(argv=[], env={}, main_module=_CONSOLE) == (CANONICAL_CONSOLE_SCRIPT,)


def test_cli_override_env_var_wins_over_detection() -> None:
    prefix = invocation_prefix(
        argv=["/opt/venv/bin/agents-shipgate"],
        env={CLI_OVERRIDE_ENV_VAR: "uv run agents-shipgate"},
        main_module=_CONSOLE,
    )
    assert prefix == ("uv", "run", "agents-shipgate")


def test_unparseable_cli_override_is_ignored_rather_than_fatal() -> None:
    prefix = invocation_prefix(
        argv=["/opt/venv/bin/shipgate"],
        env={CLI_OVERRIDE_ENV_VAR: "'unbalanced"},
        main_module=_CONSOLE,
    )
    assert prefix == ("shipgate",)


def test_no_prefix_ever_contains_the_main_module_filename() -> None:
    """The third acceptance criterion, stated directly."""

    for main_module in (_CONSOLE, _MODULE):
        for argv in (
            [str(REPO_ROOT / "src/agents_shipgate/__main__.py"), "scan"],
            ["__main__.py"],
            [],
        ):
            prefix = invocation_prefix(argv=argv, env={}, main_module=main_module)
            assert not any("__main__" in token for token in prefix), prefix


# --------------------------------------------------------------------------
# retarget_command / render_command
# --------------------------------------------------------------------------


def test_console_script_runs_emit_the_written_command_unchanged() -> None:
    """The shipped path must not churn: byte-identical to pre-#322 output."""

    for command in (
        "agents-shipgate verify --json",
        "shipgate detect --workspace . --json",
        "agents-shipgate apply-patches --from <report.json> --confidence high --apply",
    ):
        assert retarget_command(command, prefix=("agents-shipgate",)) == command
        assert retarget_command(command, prefix=("shipgate",)) == command


def test_module_runs_replace_only_the_program_token() -> None:
    rendered = retarget_command(
        "agents-shipgate verify --workspace '/a b/c' --json", prefix=_module_prefix()
    )
    assert rendered.startswith(f"{sys.executable} -m agents_shipgate verify")
    # Everything after the program token is spliced through verbatim, so
    # deliberately unquoted placeholders and pre-quoted paths both survive.
    assert rendered.endswith("verify --workspace '/a b/c' --json")


def test_placeholders_are_not_requoted_by_the_rewrite() -> None:
    rendered = retarget_command(
        "agents-shipgate apply-patches --from <report.json> --apply",
        prefix=_module_prefix(),
    )
    assert rendered.endswith("apply-patches --from <report.json> --apply")


def test_env_assignment_prefixes_keep_their_assignments() -> None:
    rendered = retarget_command(
        "AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan -c shipgate.yaml",
        prefix=_module_prefix(),
    )
    assert rendered.startswith("AGENTS_SHIPGATE_ENABLE_PLUGINS=1 ")
    assert f"{sys.executable} -m agents_shipgate scan" in rendered


def test_commands_belonging_to_other_programs_are_left_alone() -> None:
    for command in ("pip install <adapter-package>", "git fetch origin main"):
        assert retarget_command(command, prefix=_module_prefix()) == command


def test_retarget_is_idempotent() -> None:
    once = retarget_command("agents-shipgate verify --json", prefix=_module_prefix())
    assert retarget_command(once, prefix=_module_prefix()) == once


def test_render_command_quotes_arguments() -> None:
    rendered = render_command(["verify", "--workspace", "/a b/c"], prefix=("shipgate",))
    # ``program`` names the canonical spelling; a console-script run keeps it.
    assert rendered == "agents-shipgate verify --workspace '/a b/c'"
    assert render_command(["detect"], program="shipgate", prefix=("shipgate",)) == (
        "shipgate detect"
    )
    assert render_command(["detect"], prefix=_module_prefix()) == (
        f"{sys.executable} -m agents_shipgate detect"
    )


# --------------------------------------------------------------------------
# split_invocation
# --------------------------------------------------------------------------


def test_split_invocation_separates_the_entry_point_from_its_arguments() -> None:
    assert split_invocation("agents-shipgate verify --json", prefix=("agents-shipgate",)) == (
        ["agents-shipgate"],
        ["verify", "--json"],
    )


def test_split_invocation_carries_a_multi_token_entry_point() -> None:
    command = retarget_command("agents-shipgate verify --json", prefix=_module_prefix())
    assert split_invocation(command, prefix=_module_prefix()) == (
        [sys.executable, "-m", "agents_shipgate"],
        ["verify", "--json"],
    )


def test_split_invocation_agrees_with_the_string_it_was_derived_from() -> None:
    """Under a ``shipgate`` run the string keeps saying ``shipgate``."""

    assert split_invocation(
        "shipgate detect --workspace . --json", prefix=("agents-shipgate",)
    ) == (["shipgate"], ["detect", "--workspace", ".", "--json"])


def test_split_invocation_refuses_shell_only_commands() -> None:
    assert split_invocation("VAR=1 agents-shipgate scan", prefix=("shipgate",)) is None
    assert split_invocation("agents-shipgate scan --config 'oops", prefix=()) is None


# --------------------------------------------------------------------------
# NextAction projection
# --------------------------------------------------------------------------


def test_next_action_publishes_a_structured_sibling(monkeypatch) -> None:
    monkeypatch.setenv(CLI_OVERRIDE_ENV_VAR, "uv run agents-shipgate")
    action = NextAction(
        kind="command",
        command="agents-shipgate verify --workspace . --json",
        why="because",
    )
    assert action.command == "uv run agents-shipgate verify --workspace . --json"
    assert action.executable == ["uv", "run", "agents-shipgate"]
    assert action.args == ["verify", "--workspace", ".", "--json"]
    # The legacy single-string projection is the retargeted command, so the two
    # forms of the same action can never route a caller to different programs.
    assert action.to_legacy_string() == action.command


def test_non_command_actions_carry_no_structured_pair() -> None:
    action = NextAction(kind="edit", path="shipgate.yaml:4", why="fix it")
    assert action.executable is None
    assert action.args is None


def test_structured_pair_is_derived_not_accepted(monkeypatch) -> None:
    """A caller cannot publish an argv that disagrees with the string."""

    monkeypatch.delenv(CLI_OVERRIDE_ENV_VAR, raising=False)
    action = NextAction(
        kind="command",
        command="agents-shipgate verify --json",
        why="because",
        executable=["rm"],
        args=["-rf", "/"],
    )
    assert action.executable == ["agents-shipgate"]
    assert action.args == ["verify", "--json"]


def test_structured_pair_is_omitted_when_the_string_needs_a_shell(monkeypatch) -> None:
    monkeypatch.delenv(CLI_OVERRIDE_ENV_VAR, raising=False)
    action = NextAction(
        kind="command",
        command="AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan -c shipgate.yaml",
        why="because",
    )
    assert action.executable is None
    assert action.args is None
    assert action.command.startswith("AGENTS_SHIPGATE_ENABLE_PLUGINS=1 ")


# --------------------------------------------------------------------------
# End-to-end: a real ``python -m agents_shipgate`` process
# --------------------------------------------------------------------------


def _run_module(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "1"
    env.pop("AGENTS_SHIPGATE_CLI", None)
    return subprocess.run(
        [sys.executable, "-m", "agents_shipgate", *args],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(cwd),
    )


def _agent_mode_line(stderr: str) -> dict:
    for line in reversed(stderr.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no agent-mode JSON line in stderr:\n{stderr}")


def test_module_run_reports_a_runnable_command_not_dunder_main(tmp_path: Path) -> None:
    result = _run_module("scan", "-c", "missing.yaml", "--format", "json", cwd=tmp_path)
    payload = _agent_mode_line(result.stderr)

    assert "__main__" not in result.stderr
    assert payload["command"].startswith(f"{sys.executable} -m agents_shipgate scan")


def test_module_run_recovery_commands_stay_on_python_m(tmp_path: Path) -> None:
    result = _run_module("scan", "-c", "missing.yaml", "--format", "json", cwd=tmp_path)
    payload = _agent_mode_line(result.stderr)
    prefix = f"{sys.executable} -m agents_shipgate "

    assert payload["next_action"].startswith(prefix)
    for action in payload["next_actions"]:
        if action["kind"] != "command":
            continue
        assert action["command"].startswith(prefix)
        assert action["executable"] == [sys.executable, "-m", "agents_shipgate"]


def test_the_recommended_command_actually_runs(tmp_path: Path) -> None:
    """The whole point: follow the emitted route and it must not fail to launch."""

    result = _run_module("scan", "-c", "missing.yaml", "--format", "json", cwd=tmp_path)
    action = next(
        item
        for item in _agent_mode_line(result.stderr)["next_actions"]
        if item["kind"] == "command"
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    followed = subprocess.run(
        [*action["executable"], *action["args"]],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(tmp_path),
    )
    # It may well decline to do anything useful in an empty directory; what it
    # must not do is fail to start, which is what a missing console script
    # would have produced (exit 127 / "command not found").
    assert followed.returncode != 127, followed.stderr
    assert "No such file or directory" not in followed.stderr


# Fields whose value is a command the caller is meant to execute. Anything
# reachable from a surface's JSON under one of these keys is in scope for the
# invocation policy; prose (``why``, ``recommendation``) deliberately is not.
_COMMAND_KEYS = frozenset(
    {
        "command",
        "next_action",
        "verify_command",
        "detect_command",
        "preview_command",
        "related_command",
        "verification_command",
        "rerun_command",
        "default_command",
        "allowed_next_commands",
    }
)
_CONSOLE_SCRIPT_COMMAND = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:agents-shipgate|shipgate)\b"
)


def _command_values(node: object, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _COMMAND_KEYS:
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item.strip():
                        found.append((f"{path}.{key}", item))
            found.extend(_command_values(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_command_values(item, f"{path}[{index}]"))
    return found


def _json_documents(text: str) -> list[object]:
    documents: list[object] = []
    for candidate in [text, *text.splitlines()]:
        stripped = candidate.strip()
        if not stripped.startswith(("{", "[")):
            continue
        try:
            documents.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return documents


@pytest.mark.parametrize(
    ("label", "args"),
    [
        ("scan", ["scan", "-c", "missing.yaml", "--format", "json"]),
        (
            "check",
            ["check", "--base", "HEAD~1", "--head", "HEAD", "--format", "agent-boundary-json"],
        ),
        (
            "verify --preview",
            ["verify", "--preview", "--base", "HEAD~1", "--head", "HEAD", "--json"],
        ),
    ],
)
def test_no_surface_emits_a_console_script_command_under_python_m(
    label: str, args: list[str]
) -> None:
    """The whole policy, swept rather than spot-checked.

    Every fix in #322 was found by reading one surface at a time, and three of
    them (the boundary's matched trigger rules, detect's legacy single-string
    ``next_action``, and verify's own rerun command) were only found by sweeping
    a *configured* repository. This walks every command-valued key in the
    output instead, so a newly added emitter that forgets the policy fails here
    rather than in an agent's terminal.
    """

    result = _run_module(*args, cwd=REPO_ROOT)
    assert "__main__" not in result.stdout + result.stderr

    offenders = [
        (where, value)
        for document in _json_documents(result.stdout + "\n" + result.stderr)
        for where, value in _command_values(document, label)
        if _CONSOLE_SCRIPT_COMMAND.match(value)
    ]
    assert not offenders, "commands not spelled for this invocation:\n" + "\n".join(
        f"  {where} = {value}" for where, value in offenders
    )


def test_the_sweep_would_notice_a_missed_emitter() -> None:
    """Negative control: the sweep above is only meaningful if it can fail."""

    payload = {"control": {"next_action": {"command": "agents-shipgate verify --json"}}}
    offenders = [
        value for _where, value in _command_values(payload) if _CONSOLE_SCRIPT_COMMAND.match(value)
    ]
    assert offenders == ["agents-shipgate verify --json"]


def test_console_script_invocation_still_emits_the_console_script(
    tmp_path: Path,
) -> None:
    """Second acceptance criterion, exercised through a real wrapper."""

    # The wrapper that belongs to the interpreter running the suite, so it
    # imports the same package the PYTHONPATH below selects.
    script = Path(sys.executable).parent / "agents-shipgate"
    if not script.exists():  # pragma: no cover - depends on the local install
        pytest.skip("no console script installed alongside this interpreter")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "1"
    env.pop("AGENTS_SHIPGATE_CLI", None)
    result = subprocess.run(
        [str(script), "scan", "-c", "missing.yaml", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(tmp_path),
    )
    payload = _agent_mode_line(result.stderr)
    assert payload["command"].startswith("agents-shipgate scan")
    assert payload["next_action"].startswith("agents-shipgate ")
