"""Mock driver — replays a canned transcript fixture.

Used by the smoke test (``python -m harness.adoption smoke``) and by CI to
exercise the full pipeline without spending on real API calls. Reads a
fixture directory containing:

  fixture/
    transcript.jsonl        # events to replay verbatim into the writer
    commands.jsonl          # likewise
    file_ops.jsonl          # likewise (may include workspace mutations)
    summary.md              # final assistant message
    final.diff              # the git-diff output the cell should produce
    actions.yaml            # *optional* — list of {op, path, content}
                            #              applied to the workspace before
                            #              the run "completes" (lets a
                            #              fixture make the workspace look
                            #              like the agent really edited it)

Fixtures live under ``tests/harness/fixtures/<scenario>/`` and ship in repo.
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter


class MockDriver:
    name = "mock"

    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        for line in _read_lines(self.fixture / "transcript.jsonl"):
            writer.transcript(json.loads(line))
        for line in _read_lines(self.fixture / "commands.jsonl"):
            payload = json.loads(line)
            writer.command(
                payload["command"],
                exit_code=payload.get("exit_code"),
                output=payload.get("output"),
            )
        for line in _read_lines(self.fixture / "file_ops.jsonl"):
            payload = json.loads(line)
            writer.file_op(payload["op"], payload["path"], detail=payload.get("detail"))

        # Apply optional workspace mutations so a "good" or "bad" fixture can
        # leave the workspace looking like the agent really edited it.
        _apply_actions(self.fixture / "actions.yaml", inputs.workspace)

        summary = _read_text(self.fixture / "summary.md")
        # Stage everything so untracked files (e.g. a freshly-written
        # shipgate.yaml) show up in the diff. Then capture diff against HEAD.
        from subprocess import run

        run(["git", "add", "-A"], cwd=inputs.workspace, check=False, capture_output=True)
        diff_proc = run(
            ["git", "diff", "--cached", "HEAD"],
            cwd=inputs.workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        final_diff = diff_proc.stdout or _read_text(self.fixture / "final.diff")

        ended = datetime.now(UTC)
        return RunResult(
            started_at=started,
            ended_at=ended,
            tokens_in=0,
            tokens_out=0,
            cost_usd_estimate=0.0,
            degraded=False,
            summary_text=summary,
            final_diff=final_diff,
        )


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _apply_actions(actions_yaml: Path, workspace: Path) -> None:
    if not actions_yaml.is_file():
        return
    raw = yaml.safe_load(actions_yaml.read_text(encoding="utf-8")) or {}
    for action in raw.get("actions") or []:
        _apply_action(action, workspace)


def _apply_action(action: dict[str, Any], workspace: Path) -> None:
    op = action.get("op")
    if op == "write":
        path = workspace / action["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action["content"], encoding="utf-8")
    elif op == "append":
        path = workspace / action["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(existing + action["content"], encoding="utf-8")
    elif op == "delete":
        path = workspace / action["path"]
        if path.is_file():
            path.unlink()
    elif op == "copy_from_fixture":
        src = Path(action["fixture_path"])
        if not src.is_absolute():
            src = (workspace.parent / src).resolve()
        dest = workspace / action["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


__all__ = ["MockDriver"]
