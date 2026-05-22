"""Codex CLI driver for the adoption harness.

Runs ``codex exec --json`` against a prepared workspace and normalizes the
JSONL event stream into the harness transcript, command, and file-op streams.
The driver is intentionally subprocess-based so tests can mock the command
without requiring an authenticated local Codex install.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter

Runner = Callable[..., subprocess.CompletedProcess[str]]


class CodexDriver:
    name = "codex"

    def __init__(self, *, runner: Runner | None = None) -> None:
        self._runner = runner or subprocess.run

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        if shutil.which("codex") is None and self._runner is subprocess.run:
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=True,
                error="codex CLI not found on PATH",
                summary_text="(driver could not load; install and authenticate Codex CLI)",
            )

        last_message = inputs.artifacts_dir / "raw" / "codex-last-message.md"
        cmd = _codex_command(
            workspace=inputs.workspace,
            last_message=last_message,
            model=inputs.model,
        )
        env = {
            **os.environ,
            **inputs.extra_env,
            "AGENTS_SHIPGATE_AGENT_MODE": "1",
            "NO_COLOR": "1",
        }

        timed_out = False
        error: str | None = None
        try:
            proc = self._runner(
                cmd,
                cwd=str(inputs.workspace),
                input=inputs.prompt_text,
                text=True,
                capture_output=True,
                timeout=inputs.timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            proc = subprocess.CompletedProcess(cmd, 124, stdout="", stderr="")
        except OSError as exc:
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=True,
                error=f"codex CLI failed to start: {exc}",
                summary_text="(driver could not start Codex CLI)",
            )

        summary_chunks: list[str] = []
        tokens_in = 0
        tokens_out = 0
        degraded = False

        for event in _parse_jsonl(proc.stdout):
            if event.get("_parse_error"):
                degraded = True
            writer.transcript(event)
            usage = _usage_from_event(event)
            if usage:
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
            event_error = _record_event(event, writer, summary_chunks)
            if event_error and error is None:
                error = event_error

        if proc.stderr:
            writer.transcript({"type": "stderr", "text": proc.stderr})
        if timed_out:
            error = f"driver timed out after {inputs.timeout_s}s (per-cell cap)"
        elif proc.returncode != 0 and error is None:
            error = f"codex exec exited {proc.returncode}: {proc.stderr.strip()}"

        if last_message.is_file():
            summary_text = last_message.read_text(encoding="utf-8").strip()
        else:
            summary_text = "\n".join(summary_chunks).strip()

        ended = datetime.now(UTC)
        diff = _final_diff(inputs.workspace)

        return RunResult(
            started_at=started,
            ended_at=ended,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd_estimate=0.0,
            degraded=degraded or bool(error),
            summary_text=summary_text,
            final_diff=diff,
            error=error,
        )


def _codex_command(*, workspace: Path, last_message: Path, model: str | None) -> list[str]:
    cmd = [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(workspace),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--ephemeral",
        "--output-last-message",
        str(last_message),
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")
    return cmd


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            events.append({"type": "driver_parse_error", "line": line, "_parse_error": True})
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            events.append({"type": "driver_parse_error", "line": line, "_parse_error": True})
    return events


def _usage_from_event(event: dict[str, Any]) -> dict[str, int] | None:
    if event.get("type") != "turn.completed":
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def _record_event(
    event: dict[str, Any], writer: TranscriptWriter, summary: list[str]
) -> str | None:
    event_type = event.get("type")
    if event_type == "error":
        return str(event.get("message") or "codex event stream error")
    if event_type == "turn.failed":
        err = event.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or "codex turn failed")
        return "codex turn failed"

    item = event.get("item")
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if item_type == "agent_message":
        text = item.get("text")
        if isinstance(text, str) and text:
            summary.append(text)
    elif item_type == "command_execution" and event_type == "item.completed":
        writer.command(
            str(item.get("command") or ""),
            exit_code=_optional_int(item.get("exit_code")),
            output=str(item.get("aggregated_output") or ""),
        )
    elif item_type == "file_change" and event_type == "item.completed":
        changes = item.get("changes") or []
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                writer.file_op(
                    str(change.get("kind") or "update"),
                    str(change.get("path") or ""),
                    detail=str(item.get("status") or ""),
                )
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _final_diff(workspace: Path) -> str:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=False, capture_output=True)
    diff_proc = subprocess.run(
        ["git", "diff", "--cached", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return diff_proc.stdout or ""


__all__ = ["CodexDriver"]
