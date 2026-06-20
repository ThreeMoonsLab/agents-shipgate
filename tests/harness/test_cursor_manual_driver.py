from __future__ import annotations

import json

from harness.adoption.drivers.base import DriverInputs
from harness.adoption.drivers.cursor_manual import CursorManualDriver
from harness.adoption.observer.transcript import TranscriptWriter


def test_cursor_manual_driver_replays_operator_captured_artifacts(tmp_path) -> None:
    artifacts = tmp_path / "cell"
    manual = artifacts / "manual"
    manual.mkdir(parents=True)
    manual.joinpath("transcript.jsonl").write_text(
        json.dumps(
            {
                "type": "tool_result",
                "output": '{"schema_version":"shipgate.codex_boundary_result/v1"}',
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manual.joinpath("commands.jsonl").write_text(
        json.dumps(
            {
                "command": "shipgate check --agent cursor --workspace . --format codex-boundary-json",
                "exit_code": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manual.joinpath("file_ops.jsonl").write_text(
        json.dumps({"op": "Read", "path": "AGENTS.md"}) + "\n",
        encoding="utf-8",
    )
    manual.joinpath("summary.md").write_text(
        "shipgate.codex_boundary_result/v1 decision=allow must_stop=false\n",
        encoding="utf-8",
    )
    manual.joinpath("final.diff").write_text("diff --git a/a b/a\n", encoding="utf-8")

    raw = artifacts / "raw"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inputs = DriverInputs(
        workspace=workspace,
        prompt_text="",
        artifacts_dir=artifacts,
        cell_id="openai-agents-sdk__30-cursor-rule__01-prepare-for-release__cursor-manual",
        agent_name="cursor-manual",
        model=None,
    )

    with TranscriptWriter(raw) as writer:
        result = CursorManualDriver().run(inputs, writer)

    assert result.degraded is False
    assert "decision=allow" in result.summary_text
    assert "diff --git" in result.final_diff
    assert "shipgate.codex_boundary_result/v1" in (raw / "transcript.jsonl").read_text(encoding="utf-8")
    assert "shipgate check --agent cursor" in (raw / "commands.jsonl").read_text(
        encoding="utf-8"
    )


def test_cursor_manual_driver_degrades_when_evidence_is_missing(tmp_path) -> None:
    artifacts = tmp_path / "cell"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inputs = DriverInputs(
        workspace=workspace,
        prompt_text="",
        artifacts_dir=artifacts,
        cell_id="cell",
        agent_name="cursor-manual",
        model=None,
    )

    with TranscriptWriter(artifacts / "raw") as writer:
        result = CursorManualDriver().run(inputs, writer)

    assert result.degraded is True
    assert "manual Cursor evidence directory not found" in (result.error or "")


def test_cursor_manual_driver_degrades_when_manual_dir_has_no_events(tmp_path) -> None:
    artifacts = tmp_path / "cell"
    manual = artifacts / "manual"
    manual.mkdir(parents=True)
    manual.joinpath("summary.md").write_text("I ran Cursor manually.\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inputs = DriverInputs(
        workspace=workspace,
        prompt_text="",
        artifacts_dir=artifacts,
        cell_id="cell",
        agent_name="cursor-manual",
        model=None,
    )

    with TranscriptWriter(artifacts / "raw") as writer:
        result = CursorManualDriver().run(inputs, writer)

    assert result.degraded is True
    assert "manual Cursor behavioral evidence not found" in (result.error or "")
