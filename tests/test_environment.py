"""The repository launcher and the environment diagnosis behind it (#334).

The five environments Agents Shipgate has to be honest about — clean checkout,
editable install, released install, stale console script, unsupported
interpreter — are covered twice here, and deliberately not in the same way.

:func:`describe_environment` is judged from a stated :class:`EnvironmentProbe`,
so all five are reachable without building five real installations, and each
case says exactly which fact produces which verdict. The launcher is then
driven as a real subprocess against a synthetic checkout, because the claims
that matter about it — that it works with nothing installed, that it runs *this*
tree, that the commands it emits are runnable as printed — are claims about a
process, and an in-process test would assert them against the process that is
already correct.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
import warnings
from pathlib import Path

import pytest

from agents_shipgate import __version__
from agents_shipgate.environment import (
    MINIMUM_PYTHON,
    EnvironmentProbe,
    describe_environment,
    environment_report,
)
from agents_shipgate.invocation import (
    CLI_OVERRIDE_ENV_VAR,
    Invocation,
    render_cli_override,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "shipgate"

_CONSOLE_SCRIPT = Invocation(("agents-shipgate",), "console_script")


def _probe(**overrides: object) -> EnvironmentProbe:
    """A supported, unremarkable environment, minus whatever the case states."""

    defaults: dict[str, object] = {
        "executable": "/venv/bin/python",
        "python_version": (3, 12, 7),
        "package_file": "/venv/lib/python3.12/site-packages/agents_shipgate/__init__.py",
        "imported_version": "1.2.3",
        "installed_version": "1.2.3",
        "path_entries": (),
        "invocation": _CONSOLE_SCRIPT,
        "search_from": (),
    }
    defaults.update(overrides)
    return EnvironmentProbe(**defaults)  # type: ignore[arg-type]


def _checkout(root: Path, version: str = "1.2.3") -> Path:
    """A directory that reads as an Agents Shipgate source checkout."""

    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "agents-shipgate"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src" / "agents_shipgate").mkdir(parents=True, exist_ok=True)
    shutil.copy(LAUNCHER, root / "shipgate")
    return root


# ---------------------------------------------------------------------------
# The constant that has to be stated twice
# ---------------------------------------------------------------------------


def test_the_minimum_python_matches_requires_python() -> None:
    """The floor is declared in `pyproject.toml`; two copies restate it."""

    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = declared["project"]["requires-python"]
    assert requires == ">=" + ".".join(str(part) for part in MINIMUM_PYTHON)


def test_the_launcher_states_the_same_minimum_as_the_package() -> None:
    """The launcher cannot import the module that holds the floor.

    It has to reject interpreters too old to parse ``tomllib``, so it carries
    its own copy. This is the pin that keeps the copy from drifting.
    """

    namespace = _launcher_namespace()
    assert namespace["MINIMUM_PYTHON"] == MINIMUM_PYTHON


def test_the_launcher_compiles_without_warnings() -> None:
    """A `SyntaxWarning` on every invocation would be the first thing seen."""

    source = LAUNCHER.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(source, str(LAUNCHER), "exec")


def _launcher_namespace() -> dict[str, object]:
    """Execute the launcher's module body without running `main()`.

    It has no ``.py`` suffix — it is a command, not a module — so it is read
    and compiled rather than imported. ``__name__`` is not ``"__main__"``, so
    the trailing ``sys.exit(main())`` does not fire.
    """

    namespace: dict[str, object] = {"__file__": str(LAUNCHER), "__name__": "launcher"}
    exec(compile(LAUNCHER.read_text(encoding="utf-8"), str(LAUNCHER), "exec"), namespace)
    return namespace


# ---------------------------------------------------------------------------
# The five environments, judged from stated facts
# ---------------------------------------------------------------------------


def test_a_clean_checkout_with_nothing_installed_is_not_a_fault(tmp_path: Path) -> None:
    """No installed distribution is the state the launcher exists to support."""

    root = _checkout(tmp_path / "checkout")
    report = describe_environment(
        _probe(
            package_file=str(root / "src" / "agents_shipgate" / "__init__.py"),
            installed_version=None,
        )
    )

    assert report["import_source"]["kind"] == "source_checkout"
    assert report["installed_version"] is None
    assert report["imported_version"] == "1.2.3"
    assert report["source_tree"]["root"] == str(root)
    assert report["source_tree"]["version"] == "1.2.3"
    assert report["source_tree"]["launcher"] == str(root / "shipgate")
    assert report["mismatches"] == []


def test_an_editable_install_reports_the_tree_it_points_at(tmp_path: Path) -> None:
    """`pip install -e .` runs the checkout, so that is what it must report."""

    root = _checkout(tmp_path / "checkout")
    report = describe_environment(
        _probe(package_file=str(root / "src" / "agents_shipgate" / "__init__.py"))
    )

    assert report["import_source"]["kind"] == "source_checkout"
    assert report["source_tree"]["contains_import"] is True
    assert report["mismatches"] == []


def test_an_editable_install_whose_metadata_lags_a_bump_is_not_flagged(
    tmp_path: Path,
) -> None:
    """The recorded distribution version is captured once, at install time.

    Every version bump therefore leaves it behind while the checkout — which is
    what actually runs — moves on. Reporting that as a mismatch would put a
    permanent warning in front of every contributor for a state that is both
    normal and correct.
    """

    root = _checkout(tmp_path / "checkout", version="1.2.4")
    report = describe_environment(
        _probe(
            package_file=str(root / "src" / "agents_shipgate" / "__init__.py"),
            imported_version="1.2.4",
            installed_version="1.2.3",
        )
    )

    assert report["installed_version"] == "1.2.3"
    assert report["imported_version"] == "1.2.4"
    assert report["mismatches"] == []


def test_a_released_install_reports_no_source_tree() -> None:
    """A wheel is a build no edit reaches, and there is no checkout to name."""

    report = describe_environment(_probe(search_from=()))

    assert report["import_source"]["kind"] == "installed"
    assert report["source_tree"] == {
        "root": None,
        "version": None,
        "launcher": None,
        "contains_import": None,
    }
    assert report["mismatches"] == []


def test_a_stale_console_script_is_named_before_it_is_run(tmp_path: Path) -> None:
    """The failure the epic hit: a console script that cannot start at all.

    Its interpreter is gone, so it dies with `ModuleNotFoundError` before any
    Shipgate code runs and can explain itself. The shebang says so without
    executing anything, which is the only way this can be detected from inside
    a process the script would never have started.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "agents-shipgate"
    script.write_text(
        f"#!{tmp_path / 'deleted-venv' / 'bin' / 'python'}\nimport sys\n", encoding="utf-8"
    )

    report = describe_environment(_probe(path_entries=(str(bin_dir),)))

    (described,) = report["launcher"]["console_scripts"]
    assert described["name"] == "agents-shipgate"
    assert described["interpreter_exists"] is False
    (mismatch,) = report["mismatches"]
    assert mismatch["code"] == "console_script_interpreter_missing"
    assert mismatch["severity"] == "error"
    # An entry point that does work, spelled for the run that found the problem.
    assert mismatch["command"] == "agents-shipgate doctor --json"


def test_an_unsupported_interpreter_is_an_error_with_a_way_out() -> None:
    report = describe_environment(_probe(python_version=(3, 11, 9)))

    assert report["interpreter"]["supported"] is False
    assert report["interpreter"]["version"] == "3.11.9"
    (mismatch,) = report["mismatches"]
    assert mismatch["code"] == "interpreter_unsupported"
    assert "AGENTS_SHIPGATE_PYTHON" in mismatch["detail"]


def test_a_shadowed_checkout_is_an_error_that_routes_to_the_launcher(
    tmp_path: Path,
) -> None:
    """Standing in the checkout while a different build answers.

    This is the `0.8.0` shadow: the version reported looks like a coherent
    answer, so nothing about the output says the edits under the cursor are not
    the code that ran. The recovery is the same command through the entry point
    that cannot get it wrong.
    """

    root = _checkout(tmp_path / "checkout", version="1.2.3")
    report = describe_environment(
        _probe(
            package_file="/opt/conda/lib/python3.12/site-packages/agents_shipgate/__init__.py",
            imported_version="0.8.0",
            installed_version="0.8.0",
            search_from=(root,),
        )
    )

    assert report["source_tree"]["root"] == str(root)
    assert report["source_tree"]["contains_import"] is False
    (mismatch,) = report["mismatches"]
    assert mismatch["code"] == "import_outside_source_tree"
    assert mismatch["severity"] == "error"
    assert mismatch["command"] == f"{root / 'shipgate'} doctor --json"


def test_a_worktree_running_the_main_checkouts_install_is_an_error(
    tmp_path: Path,
) -> None:
    """The shadow this repository's own workflow produces.

    A `git worktree` has no virtualenv of its own, so the editable install it
    borrows points at the *main* checkout — and both trees are real Agents
    Shipgate checkouts at matching versions, so every version string agrees
    while none of the worktree's edits are what ran. Only the caller's own
    location distinguishes them, which is why it is searched first.
    """

    main = _checkout(tmp_path / "main")
    worktree = _checkout(tmp_path / "worktree")
    report = describe_environment(
        _probe(
            package_file=str(main / "src" / "agents_shipgate" / "__init__.py"),
            search_from=(worktree,),
        )
    )

    assert report["source_tree"]["root"] == str(worktree)
    assert report["source_tree"]["contains_import"] is False
    (mismatch,) = report["mismatches"]
    assert mismatch["code"] == "import_outside_source_tree"
    assert mismatch["command"] == f"{worktree / 'shipgate'} doctor --json"


def test_a_console_script_on_another_interpreter_is_a_warning(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    other = tmp_path / "other-python"
    other.write_text("", encoding="utf-8")
    (bin_dir / "shipgate").write_text(f"#!{other}\n", encoding="utf-8")

    report = describe_environment(_probe(path_entries=(str(bin_dir),)))

    (mismatch,) = report["mismatches"]
    assert mismatch["code"] == "console_script_runs_other_interpreter"
    assert mismatch["severity"] == "warning"


def test_an_env_shebang_is_not_reported_as_a_missing_interpreter(
    tmp_path: Path,
) -> None:
    """`#!/usr/bin/env python3` defers the choice; it does not name a file.

    Reading it as a named interpreter would accuse a working install of being
    stale, which is worse than saying nothing.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "agents-shipgate").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    report = describe_environment(_probe(path_entries=(str(bin_dir),)))

    (described,) = report["launcher"]["console_scripts"]
    assert described["interpreter"] is None
    assert described["interpreter_exists"] is None
    assert report["mismatches"] == []


def test_only_the_first_console_script_on_the_path_is_described(
    tmp_path: Path,
) -> None:
    """The shadowed one is not a choice the caller has."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()
        (directory / "agents-shipgate").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    report = describe_environment(_probe(path_entries=(str(first), str(second))))

    (described,) = report["launcher"]["console_scripts"]
    assert described["path"] == str(first / "agents-shipgate")


def test_an_unrelated_pyproject_is_not_mistaken_for_the_checkout(
    tmp_path: Path,
) -> None:
    """Only a manifest that names *this* distribution identifies the tree."""

    root = tmp_path / "someone-elses-project"
    (root / "src" / "agents_shipgate").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "something-else"\nversion = "9.9.9"\n', encoding="utf-8"
    )

    report = describe_environment(
        _probe(package_file=str(root / "src" / "agents_shipgate" / "__init__.py"))
    )

    assert report["import_source"]["kind"] == "unknown"
    assert report["source_tree"]["root"] is None
    assert report["mismatches"] == []


def test_the_live_report_describes_this_process() -> None:
    report = environment_report(workspace=REPO_ROOT)

    assert report["imported_version"] == __version__
    assert report["interpreter"]["executable"] == sys.executable
    assert report["source_tree"]["root"] == str(REPO_ROOT)
    assert report["source_tree"]["launcher"] == str(LAUNCHER)


# ---------------------------------------------------------------------------
# `AGENTS_SHIPGATE_CLI` has a writer now, so it needs an inverse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tokens",
    [
        ("/usr/local/bin/agents-shipgate",),
        ("/Users/someone/my checkout/shipgate",),
        ("/opt/it's there/shipgate",),
        ('/opt/quote"inside/shipgate',),
        ("C:\\Users\\me\\my checkout\\shipgate",),
        ("/usr/bin/python3", "-m", "agents_shipgate"),
    ],
)
def test_a_written_override_is_read_back_verbatim(
    tokens: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher writes this variable, so writing must invert the parsing.

    `join_argv` is POSIX on every platform on purpose, but this variable is
    parsed with the *host's* rules — so on Windows a POSIX rendering would come
    back with literal quotes in the path. One renderer per parser.
    """

    from agents_shipgate.invocation import resolve_invocation

    monkeypatch.setenv(CLI_OVERRIDE_ENV_VAR, render_cli_override(tokens))
    resolved = resolve_invocation(env=os.environ, argv=["agents-shipgate"])

    assert resolved.tokens == tokens
    assert resolved.source == "override"


@pytest.mark.skipif(os.name == "nt", reason="the POSIX renderer is shlex.join")
@pytest.mark.parametrize(
    "tokens",
    [
        ("C:\\Program Files\\shipgate.exe",),
        ('a"b',),
        ("trailing\\",),
        ("",),
    ],
)
def test_the_windows_renderer_matches_the_crt_quoting_rules(
    tokens: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-checked against the reference implementation of the same rules.

    `subprocess.list2cmdline` is the wrong renderer for a *shell* string — that
    is why `join_argv` does not use it — but it is the reference for the parser
    on the other side of this variable, which is the CRT's own argv parser.
    Importing it in the package would trip the trust-model lint; a test is where
    the comparison belongs.

    Equality of the two renderings is *not* asserted, because more than one
    spelling is correct: `a"b` needs no surrounding quotes and this renderer
    adds them anyway. What must hold is that both spellings parse back to the
    same argv, which is checked in both directions so the parser is validated
    against the reference too.
    """

    import agents_shipgate.invocation as invocation

    monkeypatch.setattr(invocation, "_WINDOWS", True)
    rendered = invocation.render_cli_override(tokens)

    assert invocation.split_windows_command_line(rendered) == list(tokens)
    assert invocation.split_windows_command_line(subprocess.list2cmdline(tokens)) == list(
        tokens
    )


# ---------------------------------------------------------------------------
# The launcher, as a real process
# ---------------------------------------------------------------------------


def _clean_checkout(tmp_path: Path) -> Path:
    """A checkout with the launcher and this tree's sources, and nothing else.

    ``src`` is a symlink so the test does not copy a large tree, but the
    launcher itself is copied: it resolves its own path to find the checkout,
    and a symlinked launcher would resolve straight back to this repository and
    prove nothing.
    """

    root = tmp_path / "clean-checkout"
    root.mkdir()
    shutil.copy(LAUNCHER, root / "shipgate")
    (root / "shipgate").chmod(0o755)
    (root / "src").symlink_to(REPO_ROOT / "src")
    shutil.copy(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    return root


def _bare_environment(tmp_path: Path) -> dict[str, str]:
    """An environment with no Shipgate on `PATH` and no `PYTHONPATH` help.

    ``conftest.py`` exports a ``PYTHONPATH`` pointing at this worktree so that
    subprocess tests import it. Leaving that in place here would prove the
    launcher works in the one condition it was written to remove.
    """

    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir(exist_ok=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PATH", CLI_OVERRIDE_ENV_VAR, "AGENTS_SHIPGATE_PYTHON"}
    }
    environment["PATH"] = str(empty_bin)
    # The interpreter that has this project's dependencies. A clean checkout
    # still needs its dependencies from somewhere; what it must not need is an
    # installed *Agents Shipgate*, or a `PYTHONPATH` the caller had to know.
    environment["AGENTS_SHIPGATE_PYTHON"] = sys.executable
    # These are the agent-facing routes, and `conftest.py` scrubs the harness
    # variables that would otherwise switch agent mode on for the whole suite.
    environment["AGENTS_SHIPGATE_AGENT_MODE"] = "1"
    return environment


def _run_launcher(
    root: Path, *args: str, env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "shipgate"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or root),
        check=False,
    )


def test_the_launcher_runs_from_a_clean_checkout(tmp_path: Path) -> None:
    """The whole acceptance criterion, as one command.

    No installed console script, no `PYTHONPATH`, no activated virtualenv —
    and the version reported is this tree's, not some other copy's.
    """

    root = _clean_checkout(tmp_path)
    result = _run_launcher(root, "--version", env=_bare_environment(tmp_path))

    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout


def test_the_environment_is_reported_when_there_is_no_manifest(tmp_path: Path) -> None:
    """`--json` prints no payload on this route, so the error line carries it.

    A caller who cannot find a manifest is exactly the caller who might be
    running the wrong build, and telling them to read a payload that was never
    printed is how a loop stalls. The block also has to name *this* checkout:
    the whole claim of the launcher is that the tree it sits in is the tree
    that ran.
    """

    root = _clean_checkout(tmp_path)
    result = _run_launcher(
        root, "doctor", "--config", "missing.yaml", "--json", env=_bare_environment(tmp_path)
    )

    assert result.returncode == 2
    assert result.stdout.strip() == ""
    payload = _agent_mode_line(result.stderr)
    assert payload["error"] == "config_error"
    assert set(payload["environment"]) == {
        "interpreter",
        "launcher",
        "import_source",
        "installed_version",
        "imported_version",
        "source_tree",
        "mismatches",
    }
    assert payload["environment"]["import_source"]["root"] == str(root / "src")
    assert payload["environment"]["source_tree"]["root"] == str(root)


def _agent_mode_line(stderr: str) -> dict:
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
    raise AssertionError(f"no agent-mode line in stderr:\n{stderr}")


def test_every_command_the_launcher_emits_names_the_launcher(tmp_path: Path) -> None:
    """The defect #322 fixed, one layer down.

    `argv[0]` is named `shipgate`, so without the announcement the invocation
    policy would conclude a console script was the way in and emit
    `agents-shipgate …` — a command a clean checkout has no way to run.
    """

    root = _clean_checkout(tmp_path)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", tmp_path / "workspace")
    result = _run_launcher(
        root,
        "doctor",
        "--config",
        str(tmp_path / "workspace" / "shipgate.yaml"),
        "--json",
        env=_bare_environment(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)[0]
    launcher = str(root / "shipgate")
    assert payload["next_action"].startswith(launcher)
    assert payload["next_actions"][0]["executable"] == [launcher]
    assert payload["control"]["next_action"]["command"].startswith(launcher)
    assert payload["environment"]["launcher"]["source"] == "override"
    assert payload["environment"]["launcher"]["executable"] == [launcher]


def test_an_operator_override_still_wins_over_the_launcher(tmp_path: Path) -> None:
    """The launcher announces itself; it does not overrule the operator.

    Someone who has told Shipgate how to invoke itself knows something about
    their environment that the launcher does not.
    """

    root = _clean_checkout(tmp_path)
    environment = _bare_environment(tmp_path)
    environment[CLI_OVERRIDE_ENV_VAR] = render_cli_override(["/opt/wrapper/agents-shipgate"])
    result = _run_launcher(
        root, "doctor", "--config", "missing.yaml", "--json", env=environment
    )

    payload = _agent_mode_line(result.stderr)
    assert payload["command"].startswith("/opt/wrapper/agents-shipgate")


@pytest.mark.skipif(os.name == "nt", reason="the stand-in interpreter is a shell script")
def test_the_launcher_reexecutes_into_the_selected_interpreter(tmp_path: Path) -> None:
    """What the switch hands the new interpreter, and the guard against looping.

    The stand-in is a shell script rather than a real interpreter so the test
    can read the argv and environment the re-execution produced. Both matter:
    the launcher's own path has to be passed through (an interpreter alone
    would start a REPL), and the guard has to be set, because the second hop
    would mean the selection disagrees with itself and looping is worse than a
    wrong answer.
    """

    root = _clean_checkout(tmp_path)
    stand_in = tmp_path / "fake-python"
    stand_in.write_text(
        '#!/bin/sh\nprintf "argv:%s\\n" "$@"\nprintf "guard:%s\\n" '
        '"${_AGENTS_SHIPGATE_LAUNCHER_REEXEC}"\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    environment = _bare_environment(tmp_path)
    environment["AGENTS_SHIPGATE_PYTHON"] = str(stand_in)

    result = _run_launcher(root, "doctor", "--json", env=environment)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"argv:{root / 'shipgate'}",
        "argv:doctor",
        "argv:--json",
        "guard:1",
    ]


def test_an_interpreter_that_does_not_exist_is_explained(tmp_path: Path) -> None:
    """Guidance, not a traceback, for the case that cannot reach Shipgate."""

    root = _clean_checkout(tmp_path)
    environment = _bare_environment(tmp_path)
    environment["AGENTS_SHIPGATE_PYTHON"] = str(tmp_path / "no-such-python")

    result = _run_launcher(root, "--version", env=environment)

    assert result.returncode == 4
    assert "Traceback" not in result.stderr
    payload = _agent_mode_line(result.stderr)
    assert payload["error"] == "environment_error"
    assert "AGENTS_SHIPGATE_PYTHON" in payload["message"]


def test_a_checkout_without_sources_says_so(tmp_path: Path) -> None:
    root = tmp_path / "not-a-checkout"
    root.mkdir()
    shutil.copy(LAUNCHER, root / "shipgate")

    result = _run_launcher(root, "--version", env=_bare_environment(tmp_path))

    assert result.returncode == 4
    assert "Traceback" not in result.stderr
    assert str(root / "src") in result.stderr


@pytest.fixture(scope="module")
def isolated_interpreter(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real interpreter with no Agents Shipgate and none of its dependencies.

    The only way to reach the import-failure paths honestly: the interpreter
    running the suite has both, and no amount of environment scrubbing removes
    a package from its own `site-packages`. Built once for the module — it is
    the one thing here that costs real time.
    """

    root = tmp_path_factory.mktemp("isolated")
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root)],
        check=True,
        capture_output=True,
    )
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def test_missing_dependencies_are_reported_as_the_environment_they_are(
    tmp_path: Path, isolated_interpreter: Path
) -> None:
    """The interpreter has this checkout's code but not what it imports.

    What must come back is the structured diagnosis and an install command
    spelled with *that* interpreter — the one that was actually selected, named
    by absolute path — rather than an import traceback and a `pip install` the
    reader has to aim themselves.
    """

    root = _clean_checkout(tmp_path)
    environment = _bare_environment(tmp_path)
    environment["AGENTS_SHIPGATE_PYTHON"] = str(isolated_interpreter)

    result = _run_launcher(root, "doctor", "--json", env=environment)

    assert result.returncode == 4
    assert "Traceback" not in result.stderr
    payload = _agent_mode_line(result.stderr)
    assert payload["error"] == "environment_error"
    assert payload["next_actions"][0]["executable"] == [str(isolated_interpreter)]
    assert payload["next_actions"][0]["args"] == ["-m", "pip", "install", "-e", str(root)]
    assert payload["environment"]["interpreter"]["executable"] == str(isolated_interpreter)


def test_an_incomplete_checkout_is_not_offered_an_install(
    tmp_path: Path, isolated_interpreter: Path
) -> None:
    """`src/` exists but holds no package: installing it would not fix that.

    Distinguished from missing dependencies on purpose. Both surface as an
    `ImportError` from the same line, and answering both with
    `pip install -e .` would send half of the callers to a command that cannot
    work.
    """

    root = tmp_path / "hollow-checkout"
    (root / "src").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "shipgate")
    environment = _bare_environment(tmp_path)
    environment["AGENTS_SHIPGATE_PYTHON"] = str(isolated_interpreter)

    result = _run_launcher(root, "--version", env=environment)

    assert result.returncode == 4
    assert "Traceback" not in result.stderr
    assert "looks incomplete" in result.stderr
    assert "pip install" not in result.stderr
