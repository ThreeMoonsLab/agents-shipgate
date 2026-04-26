"""Pytest harness for agent-task fixtures.

Each subdirectory under tests/agent_tasks/ that contains a `prompt.md` plus a
`starter_repo/` and `expected/` is auto-discovered as a parametrized test.

By default the harness runs the deterministic `expected/run.sh` baseline so we
always know the task is well-formed. `--agent=<name>` swaps in a real agent
invocation; that mode requires API keys and is not run on PRs.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

TASKS_DIR = Path(__file__).parent


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--agent",
        action="store",
        default=None,
        help="Run the harness against a real coding agent (requires API keys).",
    )


def _discover_tasks() -> list[Path]:
    return sorted(
        path
        for path in TASKS_DIR.iterdir()
        if path.is_dir()
        and (path / "prompt.md").is_file()
        and (path / "starter_repo").is_dir()
        and (path / "expected" / "assertions.py").is_file()
    )


@pytest.fixture
def workdir(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Copy the task's starter_repo into a tempdir."""
    task_dir: Path = request.param
    target = tmp_path / "workdir"
    shutil.copytree(task_dir / "starter_repo", target)
    return target


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "task_dir" in metafunc.fixturenames:
        tasks = _discover_tasks()
        metafunc.parametrize(
            "task_dir",
            tasks,
            ids=[task.name for task in tasks],
        )


def _load_assertions(task_dir: Path):
    spec = importlib.util.spec_from_file_location(
        f"agent_tasks.{task_dir.name}.assertions",
        task_dir / "expected" / "assertions.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task(task_dir: Path, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    workdir = tmp_path / "workdir"
    shutil.copytree(task_dir / "starter_repo", workdir)

    agent = request.config.getoption("--agent")
    if agent:
        _run_with_agent(agent, task_dir, workdir)
    else:
        run_script = task_dir / "expected" / "run.sh"
        if not run_script.is_file():
            pytest.skip(f"No deterministic run.sh for {task_dir.name}; agent-only task")
        subprocess.run(
            ["bash", str(run_script)],
            cwd=workdir,
            check=True,
        )

    assertions = _load_assertions(task_dir)
    assertions.assert_outcome(workdir)


def _run_with_agent(agent: str, task_dir: Path, workdir: Path) -> None:
    # Hooks for real agent invocations live behind environment-variable gates;
    # they are not exercised on PRs. Implementations should:
    #   1. Read prompt.md
    #   2. Set the agent's working directory to `workdir`
    #   3. Drive the agent until it returns or times out
    raise NotImplementedError(
        f"Agent driver for {agent!r} not implemented in this harness. "
        "Set up an out-of-band runner that invokes the agent and then "
        "calls expected/assertions.py:assert_outcome(workdir)."
    )
