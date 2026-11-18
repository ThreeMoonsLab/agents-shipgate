"""Pytest harness for agent-task fixtures.

Each subdirectory under tests/agent_tasks/ that contains a `prompt.md` plus a
`starter_repo/` and `expected/` is auto-discovered as a parametrized test.

By default the harness runs the deterministic `expected/run.sh` baseline so we
always know the task is well-formed. `--agent=<name>` swaps in a real agent
invocation; that mode requires API keys and is not run on PRs.
"""

from __future__ import annotations

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


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "task_dir" in metafunc.fixturenames:
        tasks = _discover_tasks()
        metafunc.parametrize(
            "task_dir",
            tasks,
            ids=[task.name for task in tasks],
        )
