"""Codex adoption-harness driver tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness.adoption.drivers.base import DriverInputs
from harness.adoption.drivers.codex import CodexDriver, _codex_command
from harness.adoption.observer.transcript import TranscriptWriter


def _inputs(tmp_path: Path) -> DriverInputs:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    (artifacts / "raw").mkdir(parents=True)
    return DriverInputs(
        workspace=workspace,
        prompt_text="Prepare this agent repo for production release.",
        artifacts_dir=artifacts,
        cell_id="cell",
        agent_name="codex",
        model="gpt-test",
        timeout_s=30,
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_codex_command_shape(tmp_path: Path) -> None:
    cmd = _codex_command(
        workspace=tmp_path / "ws",
        last_message=tmp_path / "last.md",
        model="gpt-test",
    )
    assert cmd[:3] == ["codex", "exec", "--json"]
    assert "--cd" in cmd
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--ephemeral" in cmd
    assert "--output-last-message" in cmd
    assert ["--model", "gpt-test"] == cmd[-3:-1]
    assert cmd[-1] == "-"


def test_codex_driver_normalizes_json_events(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    raw_dir = inputs.artifacts_dir / "raw"

    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd1",
                        "type": "command_execution",
                        "command": "agents-shipgate scan -c shipgate.yaml",
                        "aggregated_output": "ok",
                        "exit_code": 0,
                        "status": "completed",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "patch1",
                        "type": "file_change",
                        "changes": [{"path": "shipgate.yaml", "kind": "update"}],
                        "status": "completed",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "msg1",
                        "type": "agent_message",
                        "text": "Decision: blocked",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                }
            ),
        ]
    )

    def runner(cmd, **kwargs):
        last = Path(cmd[cmd.index("--output-last-message") + 1])
        last.write_text("Final summary from Codex", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    with TranscriptWriter(raw_dir) as writer:
        result = CodexDriver(runner=runner).run(inputs, writer)

    assert result.error is None
    assert result.summary_text == "Final summary from Codex"
    assert result.tokens_in == 11
    assert result.tokens_out == 7
    commands = _read_jsonl(raw_dir / "commands.jsonl")
    assert commands[0]["command"] == "agents-shipgate scan -c shipgate.yaml"
    assert commands[0]["exit_code"] == 0
    file_ops = _read_jsonl(raw_dir / "file_ops.jsonl")
    assert file_ops[0]["path"] == "shipgate.yaml"
    assert file_ops[0]["op"] == "update"


def test_codex_driver_turn_failed_is_error(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    raw_dir = inputs.artifacts_dir / "raw"
    stdout = json.dumps(
        {"type": "turn.failed", "error": {"message": "model unavailable"}}
    )

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    with TranscriptWriter(raw_dir) as writer:
        result = CodexDriver(runner=runner).run(inputs, writer)

    assert result.degraded is True
    assert result.error == "model unavailable"


def test_codex_driver_malformed_json_marks_degraded(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    raw_dir = inputs.artifacts_dir / "raw"

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="{not json}\n", stderr="")

    with TranscriptWriter(raw_dir) as writer:
        result = CodexDriver(runner=runner).run(inputs, writer)

    assert result.degraded is True
    transcript = _read_jsonl(raw_dir / "transcript.jsonl")
    assert transcript[0]["type"] == "driver_parse_error"


def test_codex_driver_nonzero_exit_is_error(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    raw_dir = inputs.artifacts_dir / "raw"

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="bad auth")

    with TranscriptWriter(raw_dir) as writer:
        result = CodexDriver(runner=runner).run(inputs, writer)

    assert result.degraded is True
    assert "bad auth" in (result.error or "")
