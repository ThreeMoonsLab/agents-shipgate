"""Every Git child Shipgate starts runs with one hardened environment (#1031).

The variables used to be written out by hand at each place that started Git,
and the copies drifted. These tests hold every Git child in ``src/`` — and the
Claude Code hook runner, which is rendered from a template — to
``agents_shipgate.core.git_env``:

* the hardening variables are spelled nowhere else in the source;
* every process spawn site is classified, and each Git one names the
  function that builds its environment, which must call ``hardened_git_env``;
* the Git children of a real ``fixture run`` (fixture Git plus ``verify``'s
  Git plumbing) and of workspace discovery carry every override;
* the hook runner's Git environment equals ``hardened_git_env()``.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.git_env import (
    hardened_git_env,
    hardened_git_overrides,
    with_allowed_git_protocols,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src" / "agents_shipgate"
GIT_ENV_MODULE = "core/git_env.py"
HOOK_RUNNER = ".claude/hooks/agents-shipgate.py"

#: Every variable one of the hand-written copies set before #1031. The helper
#: may add to this set; dropping one would silently weaken a Git child.
FORMER_HARDENING = {
    "GIT_ALLOW_PROTOCOL": "",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_PROTOCOL_FROM_USER": "0",
    "GIT_TERMINAL_PROMPT": "0",
}

#: Every process spawn site in ``src/``, as ``<path>::<function>``, mapped to
#: the function that builds the environment its Git child runs with, or to
#: ``None`` for a spawn site that does not start Git. A new spawn site fails
#: ``test_every_spawn_site_is_classified`` until it is added here.
SPAWN_SITES: dict[str, tuple[str, ...] | None] = {
    # Shipgate's own CLI (`bootstrap`) and the operator-pinned native prover.
    "cli/bootstrap.py::_run_step": None,
    "core/openshell_native.py::_execute": None,
    "cli/discovery/artifacts.py::_git_candidate_files": (
        "cli/discovery/artifacts.py::_git_candidate_files",
    ),
    # Receives the environment `_git_candidate_files` built.
    "cli/discovery/artifacts.py::_run_git_inventory_bounded": (
        "cli/discovery/artifacts.py::_git_candidate_files",
    ),
    "cli/verify/git.py::_run_process": ("cli/verify/git.py::_git_object_environment",),
    "cli/verify/git.py::_run_git_bounded_result": (
        "cli/verify/git.py::_git_object_environment",
    ),
    "cli/fixture.py::_git": ("cli/fixture.py::_git",),
    "core/authorization_execution.py::_run_process": (
        "core/authorization_execution.py::_read_only_git_environment",
        "core/authorization_execution.py::_sanitized_git_environment",
    ),
    "core/authorization_execution.py::_run_bounded_stdout_process": (
        "core/authorization_execution.py::_sanitized_git_environment",
    ),
}

_SPAWN_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})


def _sources() -> list[Path]:
    return sorted(p for p in SOURCE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function in ``tree`` by its qualified name (``Class.method``)."""

    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                found[name] = child
                visit(child, f"{name}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _is_spawn(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SPAWN_CALLS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def _spawn_sites() -> dict[str, list[ast.Call]]:
    sites: dict[str, list[ast.Call]] = {}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owned: set[int] = set()
        for name, function in sorted(_functions(tree).items(), key=lambda item: -len(item[0])):
            for node in ast.walk(function):
                if _is_spawn(node) and id(node) not in owned:
                    owned.add(id(node))
                    sites.setdefault(f"{_relative(path)}::{name}", []).append(node)
        for node in ast.walk(tree):
            if _is_spawn(node) and id(node) not in owned:
                sites.setdefault(f"{_relative(path)}::<module>", []).append(node)
    return sites


def _calls_hardened_git_env(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "hardened_git_env":
                return True
    return False


def test_the_helper_keeps_every_variable_the_former_copies_set() -> None:
    overrides = hardened_git_overrides()
    missing = {
        name: value for name, value in FORMER_HARDENING.items() if overrides.get(name) != value
    }
    assert not missing, f"hardened_git_overrides() dropped or changed {missing}"


def test_the_helper_drops_every_inherited_git_variable() -> None:
    base = {
        "PATH": "/usr/bin",
        "GIT_DIR": "/elsewhere/.git",
        "GIT_WORK_TREE": "/elsewhere",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "/bin/true",
        "GIT_ALLOW_PROTOCOL": "file:ext",
    }

    env = hardened_git_env(base)

    assert env["PATH"] == "/usr/bin"
    assert {key for key in env if key.startswith("GIT_")} == set(hardened_git_overrides())
    assert env["GIT_ALLOW_PROTOCOL"] == ""
    assert hardened_git_env({}) == hardened_git_overrides()


def test_the_one_widening_changes_only_the_protocol_allowlist() -> None:
    base = hardened_git_env({"LANG": "C"})
    widened = with_allowed_git_protocols(base, "https")

    assert widened["GIT_ALLOW_PROTOCOL"] == "https"
    assert {key: value for key, value in widened.items() if key != "GIT_ALLOW_PROTOCOL"} == {
        key: value for key, value in base.items() if key != "GIT_ALLOW_PROTOCOL"
    }
    assert base["GIT_ALLOW_PROTOCOL"] == ""


def test_hardening_variables_are_spelled_only_in_the_helper() -> None:
    """No module writes its own copy of a hardening variable.

    A string literal naming one is a hand-written environment in waiting: the
    next variable added after a Git advisory would reach the helper and miss
    the copy. The hook runner's template is checked too; its values are
    rendered from the helper at install time.
    """

    names = set(hardened_git_overrides())
    offenders: list[str] = []
    for path in _sources():
        if _relative(path) == GIT_ENV_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in names:
                offenders.append(f"{_relative(path)}:{node.lineno}: {node.value}")
    from agents_shipgate.cli.install_hooks import _HOOK_SCRIPT_TEMPLATE

    hook_tree = ast.parse(_HOOK_SCRIPT_TEMPLATE)
    for node in ast.walk(hook_tree):
        if isinstance(node, ast.Constant) and node.value in names:
            offenders.append(f"{HOOK_RUNNER} template:{node.lineno}: {node.value}")
    assert not offenders, (
        "Git hardening variables spelled outside core/git_env.py; build the "
        "environment with hardened_git_env():\n  " + "\n  ".join(offenders)
    )


def test_every_spawn_site_is_classified() -> None:
    sites = _spawn_sites()

    assert sites, "found no spawn site at all; the scan is broken"
    assert set(sites) == set(SPAWN_SITES), (
        "A process spawn site was added, removed or moved. Classify it in "
        "SPAWN_SITES: a Git child names the function that builds its "
        f"environment.\n  unclassified: {sorted(set(sites) - set(SPAWN_SITES))}"
        f"\n  stale: {sorted(set(SPAWN_SITES) - set(sites))}"
    )


def test_every_git_spawn_site_passes_an_environment_built_by_the_helper() -> None:
    """Each Git child gets an explicit ``env=``, built by a function that calls the helper."""

    sites = _spawn_sites()
    functions: dict[str, ast.AST] = {}
    for path in _sources():
        for name, node in _functions(ast.parse(path.read_text(encoding="utf-8"))).items():
            functions[f"{_relative(path)}::{name}"] = node

    problems: list[str] = []
    for site, builders in SPAWN_SITES.items():
        if builders is None:
            continue
        for call in sites.get(site, []):
            if not any(keyword.arg == "env" for keyword in call.keywords):
                problems.append(f"{site}: spawns without env=, inheriting the caller's")
        for builder in builders:
            if builder not in functions:
                problems.append(f"{site}: environment builder {builder} does not exist")
            elif not _calls_hardened_git_env(functions[builder]):
                problems.append(f"{site}: {builder} does not call hardened_git_env()")
    assert not problems, "\n".join(problems)


def test_the_hook_runner_git_environment_is_the_helpers() -> None:
    from agents_shipgate.cli.install_hooks import _hook_script_text

    namespace: dict[str, object] = {"__name__": "generated_hook"}
    exec(compile(_hook_script_text(), "generated-hook.py", "exec"), namespace)

    git_environment = namespace["_git_environment"]
    assert callable(git_environment)
    assert git_environment() == hardened_git_env()


def test_the_zero_install_detector_git_environment_is_the_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tools/shipgate-detect.py`` cannot import Shipgate, so it keeps a copy; held equal."""

    import importlib.util
    import sys

    name = "shipgate_detect_git_env_probe"
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "tools" / "shipgate-detect.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered for the duration only: its dataclasses resolve their module.
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    assert module._git_env() == hardened_git_env()


class _Recorder:
    """Records the argv and environment of every Git child started."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.git: list[tuple[list[str], dict[str, str] | None]] = []
        real = subprocess.Popen
        recorder = self

        class _RecordingPopen(real):  # type: ignore[misc, valid-type]
            def __init__(self, args, *rest, **kwargs):
                argv = [os.fspath(arg) for arg in args] if not isinstance(args, str) else [args]
                if argv and Path(argv[0]).name in {"git", "git.exe"}:
                    recorder.git.append((argv, kwargs.get("env")))
                super().__init__(args, *rest, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", _RecordingPopen)

    def unhardened(self) -> list[str]:
        expected = hardened_git_overrides()
        bad: list[str] = []
        for argv, env in self.git:
            if env is None:
                bad.append(f"{argv[:4]}: inherited the caller's environment")
                continue
            wrong = {key: env.get(key) for key, value in expected.items() if env.get(key) != value}
            if wrong:
                bad.append(f"{argv[:4]}: {wrong}")
        return bad


def _clean_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AGENTS_SHIPGATE_CLI",
        "AGENTS_SHIPGATE_ENABLE_PLUGINS",
        "CLAUDECODE",
        "CURSOR_TRACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_every_git_child_of_a_fixture_run_is_hardened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture's own Git and verify's Git plumbing, observed as they run.

    The fixture used to run Git with the operator's whole configuration, so a
    global hook path or signing helper ran while the demo history was written.
    """

    _clean_cli_environment(monkeypatch)
    # An inherited variable that would redirect Git, and a global config the
    # fixture must not read.
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hookspath=/nonexistent-hooks'")
    recorder = _Recorder(monkeypatch)

    result = CliRunner().invoke(
        app, ["fixture", "run", "ai_generated_refund_pr", "--out", str(tmp_path / "out")]
    )

    assert result.exit_code == 0, result.output
    fixture_children = [argv for argv, _env in recorder.git if "commit" in argv]
    assert fixture_children, "the fixture wrote no commit; nothing was observed"
    for argv in fixture_children:
        assert f"core.hooksPath={os.devnull}" in argv
        assert "commit.gpgsign=false" in argv
    # verify's own Git reads ran too, or this proves only the fixture.
    assert any("--git-dir" in " ".join(argv) or "rev-parse" in argv for argv, _ in recorder.git)
    assert not recorder.unhardened(), "\n".join(recorder.unhardened())


@pytest.mark.skipif(os.name != "posix", reason="the probe hook is a shell script")
def test_the_fixture_history_is_written_without_the_operators_git_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook the operator's global configuration names does not run."""

    from agents_shipgate.cli.fixture import materialize_git_pr_fixture

    hooks = tmp_path / "operator-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    global_config = tmp_path / "operator.gitconfig"
    global_config.write_text(f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    repo = tmp_path / "fixture"
    repo.mkdir()

    materialize_git_pr_fixture(
        repo,
        base_files={"README.md": "base\n"},
        head_files={"README.md": "head\n"},
        user_email="fixture@example.invalid",
        user_name="Fixture",
        base_commit_message="base",
        head_commit_message="head",
    )

    assert (repo / ".git").is_dir()
    assert not marker.exists()


def test_every_git_child_of_workspace_discovery_is_hardened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents_shipgate.cli.discovery.artifacts import _candidate_files

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q", str(workspace)],
        check=True,
        capture_output=True,
        env=hardened_git_env(),
    )
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-this-repository"))
    recorder = _Recorder(monkeypatch)

    files = _candidate_files(workspace)

    assert [path.name for path in files] == ["agent.py"]
    assert len(recorder.git) >= 2, recorder.git
    assert not recorder.unhardened(), "\n".join(recorder.unhardened())


def test_the_authorization_executor_environments_are_hardened(tmp_path: Path) -> None:
    from agents_shipgate.core import authorization_execution

    git = Path("/usr/bin/git")
    overrides = hardened_git_overrides()

    probe = authorization_execution._read_only_git_environment(git, include_exec_path=False)
    assert {key: probe.get(key) for key in overrides} == overrides
    # Built from nothing: no variable of this process reaches the probe.
    assert set(probe) == set(overrides) | {"HOME", "PATH"}

    if not git.is_file():
        pytest.skip("the executor's snapshot environment resolves /usr/bin/git")
    (tmp_path / "home").mkdir()
    (tmp_path / "xdg").mkdir()
    snapshot = authorization_execution._sanitized_git_environment(
        git=git, execution_root=tmp_path
    )
    assert {key: snapshot.get(key) for key in overrides} == overrides
