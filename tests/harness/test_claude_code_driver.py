"""Claude Code adoption-harness driver tests.

These exercise ``ClaudeCodeDriver._record`` directly with synthetic SDK message
dicts (no live API), the same way ``test_codex_driver`` replays events. They pin
the output-capture behaviour: a Bash command's row carries the stdout from its
matching ``tool_result``, and a command whose result never arrives is still
recorded (without output) rather than dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.adoption.drivers.claude_code import ClaudeCodeDriver, _tool_result_text
from harness.adoption.observer.transcript import TranscriptWriter


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _commands_for(tmp_path: Path, messages: list[dict]) -> list[dict]:
    raw = tmp_path / "raw"
    driver = ClaudeCodeDriver()
    driver._pending_bash = {}
    with TranscriptWriter(raw) as writer:
        for message in messages:
            driver._record(message, writer, [])
        # Mimic run()'s end-of-run flush of unmatched commands.
        for command in driver._pending_bash.values():
            writer.command(command)
    return _read_jsonl(raw / "commands.jsonl")


def test_record_attaches_tool_result_output_to_bash_command(tmp_path: Path) -> None:
    commands = _commands_for(
        tmp_path,
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "agents-shipgate verify --format json"},
                    }
                ]
            },
            {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": '{"merge_verdict": "blocked"}',
                    }
                ]
            },
        ],
    )
    assert len(commands) == 1
    assert commands[0]["command"] == "agents-shipgate verify --format json"
    assert commands[0]["output"] is not None
    assert "blocked" in commands[0]["output"]


def test_record_handles_list_tool_result_content(tmp_path: Path) -> None:
    commands = _commands_for(
        tmp_path,
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ]
            },
            {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [
                            {"type": "text", "text": "file-a"},
                            {"type": "text", "text": "file-b"},
                        ],
                    }
                ]
            },
        ],
    )
    assert commands[0]["output"] == "file-a\nfile-b"


def test_record_flushes_bash_without_result(tmp_path: Path) -> None:
    # A command whose tool_result never arrives (timeout / abort / last turn) is
    # still recorded — just without output. Degrades to the pre-capture behaviour.
    commands = _commands_for(
        tmp_path,
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "agents-shipgate scan -c shipgate.yaml"},
                    }
                ]
            }
        ],
    )
    assert len(commands) == 1
    assert commands[0]["command"] == "agents-shipgate scan -c shipgate.yaml"
    assert commands[0]["output"] is None


def test_tool_result_text_extracts_string_and_list() -> None:
    assert _tool_result_text("hello") == "hello"
    assert (
        _tool_result_text(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        == "a\nb"
    )
    assert _tool_result_text(None) == ""


def test_init_failure_detected_from_auth_error_event():
    """Regression: a 'Not logged in' run ends the SDK stream normally, so
    without explicit detection the cell scores as a real-but-idle run
    (the first 2026-W24 paid run produced 31 such vacuous rows). Any
    init-failure event must surface as a driver error."""
    from harness.adoption.drivers.claude_code import _init_failure

    class _Event:
        error = "authentication_failed"

    message = _Event()
    detail = _init_failure(message)
    assert detail is not None
    assert "authentication_failed" in detail
    assert "SHIPGATE_HARNESS_SCOPE_HOME" in detail

    assert _init_failure({"error": "invalid_api_key"}) is not None
    assert _init_failure({"error": None}) is None
    assert _init_failure({"content": [{"text": "hello"}]}) is None

    class _Normal:
        pass

    assert _init_failure(_Normal()) is None
