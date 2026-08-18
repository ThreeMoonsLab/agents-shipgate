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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents_shipgate.invocation import (
    CANONICAL_CONSOLE_SCRIPT,
    CLI_OVERRIDE_ENV_VAR,
    Invocation,
    invocation_prefix,
    join_argv,
    render_command,
    resolve_invocation,
    retarget_command,
    split_invocation,
    split_windows_command_line,
)
from agents_shipgate.schemas.diagnostics import Diagnostic, NextAction

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
    assert resolve_invocation(
        argv=[f"/opt/venv/bin/{script}", "scan"], env={}, main_module=_CONSOLE
    ).keeps_written_spelling


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
    assert rendered.startswith(f"{join_argv(_module_prefix())} verify")
    # Everything after the program token is spliced through verbatim, so
    # deliberately unquoted placeholders and pre-quoted paths both survive.
    assert rendered.endswith("verify --workspace '/a b/c' --json")


def test_placeholders_are_not_requoted_by_the_rewrite() -> None:
    rendered = retarget_command(
        "agents-shipgate apply-patches --from <report.json> --apply",
        prefix=_module_prefix(),
    )
    assert rendered.endswith("apply-patches --from <report.json> --apply")


def test_a_quoted_path_is_read_before_it_is_judged_to_be_ours() -> None:
    """Locating the program token by scanning to raw whitespace was unsafe.

    Its own defence was that our console-script names contain no whitespace and
    no quotes — true of the *names*, and irrelevant to the strings they appear
    in. A quoted interpreter path whose directory happens to be named after
    this project, which is what cloning it into `~/agents-shipgate worktree/`
    produces, was cut at the space; the remaining `'/tmp/agents-shipgate` has
    `agents-shipgate` as its basename, so a `python -m pip install` recovery was
    rewritten to name the launcher instead — leaving a runnable command that
    runs the wrong program, and a dangling quote that cost the action its argv.
    """

    command = join_argv(
        ["/tmp/agents-shipgate worktree/.venv/bin/python", "-m", "pip", "install", "-e", "/repo"]
    )
    prefix = Invocation(("/repo/shipgate",), "override")

    assert retarget_command(command, prefix=prefix) == command
    assert split_invocation(command, prefix=prefix) == (
        ["/tmp/agents-shipgate worktree/.venv/bin/python"],
        ["-m", "pip", "install", "-e", "/repo"],
    )


def test_a_quoted_shipgate_path_is_still_retargeted() -> None:
    """Reading the token properly is not the same as leaving quoted tokens alone.

    `'/opt/my tools/agents-shipgate'` really is our console script, and the
    replacement has to cover the quotes rather than land inside them.
    """

    command = join_argv(["/opt/my tools/agents-shipgate", "scan", "-c", "shipgate.yaml"])
    prefix = Invocation(("/repo/shipgate",), "override")

    assert retarget_command(command, prefix=prefix) == "/repo/shipgate scan -c shipgate.yaml"


@pytest.mark.parametrize(
    "command",
    [
        "agents-shipgate scan",
        "'/opt/my tools/agents-shipgate' scan --json",
        r"/opt/my\ tool --flag",
        '"/opt/quoted path/python" -m agents_shipgate doctor',
        "AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan",
        "pip install agents-shipgate",
    ],
)
def test_the_program_token_span_agrees_with_the_grammar(command: str) -> None:
    """The scan finds a boundary; `shlex` reads the value. They must agree.

    Two readings of one string is the bug class this module keeps hitting, so
    the span is cross-checked against the grammar the string was rendered with:
    the bytes the scan would replace must parse as exactly the token `shlex`
    reports there, and everything after them must be the rest of the argv.
    """

    from agents_shipgate.invocation import _ENV_ASSIGNMENT, _program_token

    located = _program_token(command)
    assert located is not None
    start, end, program = located

    tokens = shlex.split(command)
    skipped = sum(1 for token in tokens if _ENV_ASSIGNMENT.fullmatch(token))
    assert program == tokens[skipped]
    assert shlex.split(command[start:end]) == [program]
    assert shlex.split(command[end:]) == tokens[skipped + 1 :]


def test_env_assignment_prefixes_keep_their_assignments() -> None:
    rendered = retarget_command(
        "AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan -c shipgate.yaml",
        prefix=_module_prefix(),
    )
    assert rendered.startswith("AGENTS_SHIPGATE_ENABLE_PLUGINS=1 ")
    assert f"{join_argv(_module_prefix())} scan" in rendered


@pytest.mark.parametrize("windows", [False, True])
def test_windows_paths_round_trip_through_render_and_split(monkeypatch, windows: bool) -> None:
    """One renderer and one parser, so a value cannot change in transit.

    Rendering with ``subprocess.list2cmdline`` and parsing with POSIX ``shlex``
    turned ``C:\\repo`` into ``C:repo`` — not an unrunnable command but a
    *runnable* one against the wrong workspace. Uniform POSIX quoting
    round-trips Windows paths exactly, because a single-quoted ``'C:\\repo'``
    keeps its backslashes.
    """

    monkeypatch.setattr("agents_shipgate.invocation._WINDOWS", windows)
    arguments = ["verify", "--workspace", r"C:\repo", "--config", r"C:\repo\shipgate.yaml"]

    rendered = render_command(arguments, prefix=("agents-shipgate",))
    assert split_invocation(rendered, prefix=("agents-shipgate",)) == (
        ["agents-shipgate"],
        arguments,
    )


def test_a_windows_interpreter_prefix_survives_the_round_trip(monkeypatch) -> None:
    monkeypatch.setattr("agents_shipgate.invocation._WINDOWS", True)
    windows_prefix = (r"C:\Program Files\Python312\python.exe", "-m", "agents_shipgate")

    rendered = retarget_command("agents-shipgate verify --json", prefix=windows_prefix)
    # The entry point is recovered from the resolved invocation, never by
    # parsing the string back.
    assert split_invocation(rendered, prefix=windows_prefix) == (
        list(windows_prefix),
        ["verify", "--json"],
    )


def test_a_windows_cli_override_keeps_its_backslashes(monkeypatch) -> None:
    """POSIX ``shlex`` reads ``C:\\Tools\\x.exe`` as ``C:Toolsx.exe``.

    The operator wrote a correct path; parsing it with the wrong rules produced
    one that does not exist, which silently undid the absolute-override fix on
    the only platform where backslashes are normal.
    """

    monkeypatch.setattr("agents_shipgate.invocation._WINDOWS", True)
    bare = invocation_prefix(
        argv=["x"],
        env={CLI_OVERRIDE_ENV_VAR: r"C:\Tools\agents-shipgate.exe"},
        main_module=_CONSOLE,
    )
    assert bare == (r"C:\Tools\agents-shipgate.exe",)

    quoted = invocation_prefix(
        argv=["x"],
        env={CLI_OVERRIDE_ENV_VAR: r'"C:\Program Files\ags\agents-shipgate.exe" --flag'},
        main_module=_CONSOLE,
    )
    assert quoted == (r"C:\Program Files\ags\agents-shipgate.exe", "--flag")


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
        f"{join_argv(_module_prefix())} detect"
    )


def test_an_explicit_override_is_never_treated_as_the_written_spelling() -> None:
    """An absolute override names *that* wrapper, not whatever ``PATH`` finds.

    The program name matches a console script, so a no-op keyed on the name
    alone silently reverted the operator's entry point — while the top-level
    ``command`` field, which does not go through this path, kept it. The two
    then disagreed inside one emitted error.
    """

    override = "/private/venv/bin/agents-shipgate"
    invocation = resolve_invocation(
        argv=["/usr/local/bin/agents-shipgate", "scan"],
        env={CLI_OVERRIDE_ENV_VAR: override},
        main_module=_CONSOLE,
    )
    assert invocation == Invocation((override,), "override")
    assert not invocation.keeps_written_spelling

    rewritten = retarget_command("agents-shipgate verify --json", prefix=invocation)
    assert rewritten == f"{override} verify --json"
    assert split_invocation(rewritten, prefix=invocation) == ([override], ["verify", "--json"])


def test_a_detected_console_script_still_keeps_the_written_spelling() -> None:
    """The other side of the same rule: the shipped path must not churn."""

    invocation = resolve_invocation(
        argv=["/usr/local/bin/agents-shipgate", "scan"], env={}, main_module=_CONSOLE
    )
    assert invocation.keeps_written_spelling
    assert retarget_command("shipgate detect --json", prefix=invocation) == (
        "shipgate detect --json"
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
    assert split_invocation("agents-shipgate scan --config 'oops", prefix=("shipgate",)) is None


@pytest.mark.parametrize(
    "command",
    [
        "printf left ' ' && printf right",  # control operator
        "agents-shipgate scan --format json | tee log",  # pipeline
        "agents-shipgate scan > out.json",  # redirection
        "agents-shipgate scan -c $CONFIG",  # parameter expansion
        "agents-shipgate scan -c `cat name`",  # command substitution
        "agents-shipgate apply-patches --from <report.json>",  # placeholder
        "agents-shipgate scan -c *.yaml",  # glob
    ],
)
def test_split_invocation_refuses_strings_that_need_a_shell(command: str) -> None:
    """``shlex.split`` succeeding does not prove a string is plain argv.

    It happily returns ``&&`` as an ordinary token, so publishing its output as
    ``executable``/``args`` would advertise a subprocess call that does not do
    what the string does. Refusing is the honest answer; the string stays
    authoritative.
    """

    assert split_invocation(command, prefix=("agents-shipgate",)) is None


def test_split_invocation_keeps_quoted_metacharacters() -> None:
    """A quoted metacharacter is one argv token, not shell syntax."""

    assert split_invocation(
        "agents-shipgate verify --base 'HEAD~1' --head HEAD", prefix=("agents-shipgate",)
    ) == (["agents-shipgate"], ["verify", "--base", "HEAD~1", "--head", "HEAD"])


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


def test_a_supplied_structured_pair_has_no_effect(monkeypatch) -> None:
    """A caller cannot publish an argv that disagrees with the string.

    The keys are accepted rather than rejected so the model can read back its
    own serialization, but they are dropped and recomputed — so accepting them
    is not believing them.
    """

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


@pytest.mark.parametrize("mutate", ["copy", "assign"])
def test_the_structured_pair_cannot_go_stale(monkeypatch, mutate: str) -> None:
    """Deriving at construction is not enough to make the guarantee true.

    ``model_copy(update=...)`` skips validation and the model is mutable, so a
    pair fixed once could outlive the command it described — serializing an
    argv for a command the action no longer holds.
    """

    monkeypatch.delenv(CLI_OVERRIDE_ENV_VAR, raising=False)
    action = NextAction(kind="command", command="agents-shipgate verify --json", why="x")
    replacement = "agents-shipgate scan -c shipgate.yaml --format json"

    if mutate == "copy":
        action = action.model_copy(update={"command": replacement})
    else:
        action.command = replacement

    payload = action.model_dump(mode="json")
    assert payload["command"] == replacement
    assert payload["args"] == ["scan", "-c", "shipgate.yaml", "--format", "json"]


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
    assert payload["command"].startswith(f"{join_argv(_module_prefix())} scan")


def test_module_run_recovery_commands_stay_on_python_m(tmp_path: Path) -> None:
    result = _run_module("scan", "-c", "missing.yaml", "--format", "json", cwd=tmp_path)
    payload = _agent_mode_line(result.stderr)
    prefix = f"{join_argv(_module_prefix())} "

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
        ("apply-patches", ["apply-patches", "--from", "missing-report.json"]),
        ("detect", ["detect", "--workspace", ".", "--json"]),
        # The two setup surfaces that gained a `control` envelope in contract
        # v24. Both are read-only here: `init` without `--write` renders only.
        ("init", ["init", "--workspace", ".", "--json"]),
        ("doctor", ["doctor", "--json"]),
        ("explain-finding", ["explain-finding", "--from", "missing.json", "--fingerprint", "x"]),
        ("findings", ["findings", "--from", "missing.json", "--json"]),
        ("explain", ["explain", "no-such-check-id", "--json"]),
        ("audit --host", ["audit", "--host", "--json"]),
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


def test_a_hand_built_action_dict_is_normalized_at_the_wire(tmp_path: Path) -> None:
    """``apply-patches`` builds ``next_actions[]`` as plain dicts.

    A dict cannot opt into the model's normalization, so this route published a
    bare ``agents-shipgate scan ...`` with no structured argv no matter how the
    process was started. Enforcing at the emission boundary is what makes the
    policy hold for constructions nobody has written yet.
    """

    report = tmp_path / "report.json"
    # A report without ``manifest_dir`` is the pre-v0.6 shape whose recovery
    # route is the hand-built command dict.
    report.write_text(json.dumps({"findings": []}), encoding="utf-8")

    result = _run_module("apply-patches", "--from", str(report), cwd=tmp_path)
    payload = _agent_mode_line(result.stderr)
    action = next(item for item in payload["next_actions"] if item["kind"] == "command")

    prefix = f"{join_argv(_module_prefix())} "
    assert action["command"].startswith(prefix)
    assert action["executable"] == list(_module_prefix())
    assert action["args"][0] == "scan"


def test_preflight_host_grant_route_is_spelled_for_this_invocation(tmp_path: Path) -> None:
    """Preflight signals carry ``related_command``, not ``command``.

    A differently-named field is exactly how an emitter escapes a policy keyed
    on one field name, so the sweep's key set covers it and this exercises the
    surface end to end.
    """

    plan = json.dumps(
        {
            "schema_version": "preflight_plan_v1",
            "changed_files": ["AGENTS.md", "shipgate.yaml"],
            "capability_requests": [
                {
                    "schema_version": "capability_request_v1",
                    "tool_name": "refund",
                    "effect": "financial_write",
                }
            ],
            "host_permission_requests": [],
        }
    )
    # An isolated copy: preflight's verdict depends on workspace state, and
    # running it against the shared repository root made this test depend on
    # whatever else the suite was writing there concurrently.
    workspace = tmp_path / "workspace"
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", workspace)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "1"
    env.pop("AGENTS_SHIPGATE_CLI", None)
    result = subprocess.run(
        [
            *_module_prefix(),
            "preflight",
            "--workspace",
            str(workspace),
            "--config",
            "shipgate.yaml",
            "--plan",
            "-",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(workspace),
        input=plan,
    )
    assert result.returncode == 0, result.stderr

    commands = [
        value
        for document in _json_documents(result.stdout)
        for _where, value in _command_values(document)
    ]
    assert commands, "preflight published no commands to check"
    assert not [value for value in commands if _CONSOLE_SCRIPT_COMMAND.match(value)]


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


def test_durable_artifacts_do_not_depend_on_how_the_process_started(
    tmp_path: Path,
) -> None:
    """``docs/architecture.md``: same inputs → same report.

    Process-entry spelling is not an input. Retargeting commands that land in
    ``report.json``/``packet.json`` gave one semantic run identity — the same
    ``run_id`` — two different artifact bodies, which is exactly the property
    the packet hash exists to rule out. Live routes carry the runnable
    spelling; durable evidence stays canonical.
    """

    script = Path(sys.executable).parent / "agents-shipgate"
    if not script.exists():  # pragma: no cover - depends on the local install
        pytest.skip("no console script installed alongside this interpreter")

    manifest = REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "0"
    # One output directory for both runs. Reports record their own artifact
    # paths, so scanning into two directories would differ for a reason that
    # has nothing to do with the invocation and would make this test pass
    # regardless of what it is meant to catch.
    out = tmp_path / "reports"

    def scan(argv: list[str]) -> dict[str, bytes]:
        result = subprocess.run(
            [
                *argv,
                "scan",
                "-c",
                str(manifest),
                "--out",
                str(out),
                "--format",
                "json",
                "--ci-mode",
                "advisory",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        return {
            path.name: path.read_bytes()
            for path in sorted(out.iterdir())
            if path.suffix in {".json", ".md"}
        }

    through_wrapper = scan([str(script)])
    through_module = scan(list(_module_prefix()))

    assert through_wrapper.keys() == through_module.keys()
    differing = [name for name in through_wrapper if through_wrapper[name] != through_module[name]]
    assert not differing, f"invocation-dependent bytes in durable artifacts: {differing}"


def test_the_determinism_check_would_notice_a_leak() -> None:
    """Negative control for the test above.

    The rule it enforces is not "these files never change" — it is "process
    entry is not an input to them". A canonical command string is what keeps
    that true, so this pins the property the durable producers rely on:
    ``retarget_command`` is what would have to be applied for a leak to occur,
    and applying it does change the bytes.
    """

    canonical = "agents-shipgate verify --workspace . --ci-mode advisory --json"
    assert retarget_command(canonical, prefix=_module_prefix()) != canonical


# --------------------------------------------------------------------------
# Round-3 regressions
# --------------------------------------------------------------------------


def test_an_escaped_space_in_the_program_token_is_one_token() -> None:
    """Locating the program by scanning to whitespace split a real path.

    ``/opt/my\\ tool`` is one program with one argument; the scan reported
    ``/opt/my\\`` plus a stray ``tool``, so the structured pair — the form the
    docs tell agents to prefer — executed a different program than the string.
    """

    assert split_invocation(r"/opt/my\ tool --flag", prefix=("agents-shipgate",)) == (
        ["/opt/my tool"],
        ["--flag"],
    )
    assert shlex.split(r"/opt/my\ tool --flag") == ["/opt/my tool", "--flag"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # An ordinary Windows path: POSIX shlex would read the backslashes as
        # escapes and produce C:Toolsagents-shipgate.exe.
        (r"C:\Tools\agents-shipgate.exe", [r"C:\Tools\agents-shipgate.exe"]),
        # Quoted program with spaces.
        (
            r'"C:\Program Files\ags\agents-shipgate.exe" --flag',
            [r"C:\Program Files\ags\agents-shipgate.exe", "--flag"],
        ),
        # A quote *inside* a token: shlex(posix=False) splits this in two.
        (
            r'uv run --project="C:\My Project" agents-shipgate',
            ["uv", "run", r"--project=C:\My Project", "agents-shipgate"],
        ),
        # The CRT's doubled-quote escape.
        (r'"a b" c "d""e"', ["a b", "c", "de"]),
        # 2n backslashes before a quote are n backslashes, and the quote toggles.
        (r'"C:\dir\\" next', ["C:\\dir\\", "next"]),
    ],
)
def test_windows_command_lines_follow_the_crt_rules(raw: str, expected: list[str]) -> None:
    assert split_windows_command_line(raw) == expected


def test_next_action_round_trips_through_its_own_model() -> None:
    """A wire model that cannot read what it wrote is broken for consumers.

    ``extra="forbid"`` plus computed fields rejected the model's own payload,
    so anything replaying agent-mode output — or a ``Diagnostic`` — through the
    schema failed on the two properties the schema advertises.
    """

    action = NextAction(kind="command", command="agents-shipgate verify --json", why="x")
    payload = action.model_dump(mode="json")
    assert NextAction.model_validate(payload).model_dump(mode="json") == payload

    diagnostic = Diagnostic(id="SHIP-DIAG-X", title="t", severity="info", next_actions=[action])
    dumped = diagnostic.model_dump(mode="json")
    assert Diagnostic.model_validate(dumped).model_dump(mode="json") == dumped


def test_an_argv_pair_edited_in_transit_is_replaced_not_trusted() -> None:
    """Accepting the keys must not mean believing them."""

    action = NextAction(kind="command", command="agents-shipgate verify --json", why="x")
    tampered = dict(action.model_dump(mode="json"), executable=["rm"], args=["-rf", "/"])
    assert NextAction.model_validate(tampered).args == ["verify", "--json"]


def test_the_published_schema_documents_the_computed_pair() -> None:
    properties = NextAction.model_json_schema()["properties"]
    assert {"executable", "args"} <= set(properties)
    assert properties["executable"]["readOnly"] is True


def _boundary_fixture_repo(tmp_path: Path) -> Path:
    """A two-commit repo whose head changes a recognised agent surface.

    The control surfaces only publish commands when the change reaches them, so
    running them against the Shipgate repository itself made the assertions
    depend on whatever that working tree happened to look like — they passed
    locally and found nothing in CI. This fixture is the subject instead, and
    it reliably produces an `allowed_next_commands` entry, which the repo-root
    runs never reached.
    """

    workspace = tmp_path / "boundary"
    workspace.mkdir()
    run = lambda *argv: subprocess.run(  # noqa: E731 - local helper
        argv, cwd=workspace, check=True, capture_output=True, text=True
    )
    run("git", "init", "-q", ".")
    run("git", "config", "user.email", "tests@example.invalid")
    run("git", "config", "user.name", "tests")
    (workspace / ".mcp.json").write_text("{}\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "node", "args": ["server.js"]}}}) + "\n",
        encoding="utf-8",
    )
    run("git", "add", "-A")
    run("git", "commit", "-qm", "head")
    return workspace


@pytest.mark.parametrize(
    "output_format", ["agent-boundary-json", "agent-control-json", "codex-boundary-json"]
)
def test_every_control_surface_command_recovers_exact_argv(
    tmp_path: Path, output_format: str
) -> None:
    """The documented recovery for surfaces that carry no structured pair.

    `executable`/`args` are scoped to `next_actions[]`; the operational control
    contracts — including `allowed_next_commands` — publish the string alone.
    Because every command is rendered with POSIX quoting on every platform,
    `shlex.split` recovers the exact argv there. This pins that as a guarantee
    rather than an accident, so a future renderer change cannot quietly strand
    a control consumer.
    """

    workspace = _boundary_fixture_repo(tmp_path)
    result = _run_module(
        "check", "--base", "HEAD~1", "--head", "HEAD", "--format", output_format, cwd=workspace
    )
    commands = [
        value
        for document in _json_documents(result.stdout + "\n" + result.stderr)
        for _where, value in _command_values(document, output_format)
    ]
    # No per-format non-vacuity assertion: a format may legitimately publish no
    # command in a given state — the compact envelope carries a human review
    # action here, and a human action never exposes one. Coverage of the
    # control contract is pinned once, below, where it is deterministic.
    for command in commands:
        tokens = shlex.split(command)
        assert tokens, command
        # Stable under a second pass: re-rendering and re-parsing the recovered
        # argv yields the same tokens, so nothing was lost or invented.
        assert shlex.split(join_argv(tokens)) == tokens, command


def test_the_control_surface_fixture_reaches_allowed_next_commands(tmp_path: Path) -> None:
    """Negative control: the recovery test must cover the control contract.

    Without this, a fixture that stopped triggering the boundary would leave
    the test above asserting over `trigger.matched_rules` alone and quietly
    stop covering what it names.
    """

    workspace = _boundary_fixture_repo(tmp_path)
    result = _run_module(
        "check",
        "--base",
        "HEAD~1",
        "--head",
        "HEAD",
        "--format",
        "agent-boundary-json",
        cwd=workspace,
    )
    document = json.loads(result.stdout)
    assert document["control"]["allowed_next_commands"]


def test_successful_init_routes_to_a_runnable_command(tmp_path: Path) -> None:
    """The success path had no error to normalize, so it kept the canonical name.

    Every earlier fix landed on failure routes; a source checkout that ran
    `init` successfully still dead-ended on the very next step it was told to
    take.

    Contract v24 changed *which* step a written manifest routes to, not whether
    it is runnable: `init --write` always leaves an unresolved
    `agent.declared_purpose`, and that is a declaration a person makes, so the
    rank-1 route is a human review carrying no command (#325). Both halves are
    asserted here — the dry run still emits a runnable command, and the written
    one emits no command at all rather than an unrunnable string.
    """

    dry = _run_module("init", "--minimal", "--json", cwd=tmp_path)
    assert dry.returncode == 0, dry.stderr
    payload = json.loads(dry.stdout)

    prefix = f"{join_argv(_module_prefix())} "
    assert payload["next_action"].startswith(prefix)
    action = payload["next_actions"][0]
    assert action["kind"] == "command"
    assert action["executable"] == list(_module_prefix())
    assert action["args"][0] == "init"
    # The legacy string is the rank-1 command verbatim, as documented.
    assert payload["next_action"] == action["command"]
    # And the compact envelope names the same step.
    assert payload["control"]["next_action"]["command"] == action["command"]

    written = _run_module("init", "--minimal", "--write", "--json", cwd=tmp_path)
    assert written.returncode == 0, written.stderr
    document = json.loads(written.stdout)
    assert document["control"]["control_state"] == "human_review_required"
    assert document["next_actions"][0]["kind"] == "review"
    assert document["next_actions"][0].get("command") is None
