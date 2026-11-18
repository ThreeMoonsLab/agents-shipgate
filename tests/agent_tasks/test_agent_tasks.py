"""Run the checked-in baseline or the explicitly requested agent driver."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import agents_shipgate


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
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("The deterministic agent tasks require Bash")
        package_root = Path(agents_shipgate.__file__).resolve().parents[1]
        python_path = os.environ.get("PYTHONPATH")
        paths = [str(package_root), python_path] if python_path else [str(package_root)]
        subprocess.run(
            [bash, str(run_script)],
            cwd=workdir,
            env={
                **os.environ,
                "AGENTS_SHIPGATE_TASK_PYTHON": sys.executable,
                "PYTHONPATH": os.pathsep.join(paths),
            },
            check=True,
        )

    assertions = _load_assertions(task_dir)
    assertions.assert_outcome(workdir)


def _run_with_agent(agent: str, task_dir: Path, workdir: Path) -> None:
    """Drive a real coding agent through the task's prompt.

    Delegates to the adoption-harness driver layer
    (``harness.adoption.drivers``). The harness package is local-only and
    not on the wheel install path; it lives at the repo root.
    """
    from datetime import UTC, datetime

    from harness.adoption.drivers.base import DriverInputs
    from harness.adoption.drivers.claude_code import ClaudeCodeDriver
    from harness.adoption.drivers.codex import CodexDriver
    from harness.adoption.drivers.cursor import CursorStaticDriver
    from harness.adoption.observer.transcript import TranscriptWriter

    drivers = {
        "claude-code": ClaudeCodeDriver(),
        "codex": CodexDriver(),
        "cursor-static": CursorStaticDriver(),
    }
    try:
        driver = drivers[agent]
    except KeyError as exc:
        raise NotImplementedError(
            f"Agent driver for {agent!r} not registered. "
            f"Known: {sorted(drivers)}"
        ) from exc

    prompt = (task_dir / "prompt.md").read_text(encoding="utf-8")
    artifacts_dir = workdir / ".harness_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    cell_id = f"{task_dir.name}__{agent}__{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    with TranscriptWriter(artifacts_dir) as writer:
        driver.run(
            DriverInputs(
                workspace=workdir,
                prompt_text=prompt,
                artifacts_dir=artifacts_dir,
                cell_id=cell_id,
                agent_name=agent,
                model=None,
            ),
            writer,
        )
