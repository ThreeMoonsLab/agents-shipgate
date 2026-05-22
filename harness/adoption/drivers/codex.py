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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter

Runner = Callable[..., subprocess.CompletedProcess[str]]

_INCREMENTAL_COST_KEYS = ("cost_usd", "turn_cost_usd", "incremental_cost_usd")
_CUMULATIVE_COST_KEYS = (
    "total_cost_usd",
    "cumulative_cost_usd",
    "cost_usd_estimate",
    "estimated_cost_usd",
)


@dataclass(frozen=True)
class _CostObservation:
    usd: float
    cumulative: bool


class CodexDriver:
    name = "codex"

    def __init__(self, *, runner: Runner | None = None) -> None:
        self._uses_default_runner = runner is None
        self._runner = runner or subprocess.run

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        if shutil.which("codex") is None and self._uses_default_runner:
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
        cost_usd_estimate = 0.0
        saw_cost_usd = False
        degraded = False

        for event in _parse_jsonl(proc.stdout):
            if event.get("_parse_error"):
                degraded = True
            writer.transcript(event)
            usage = _usage_from_event(event)
            if usage:
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
            event_cost = _cost_usd_observation(event)
            if event_cost is not None:
                if event_cost.cumulative:
                    cost_usd_estimate = max(cost_usd_estimate, event_cost.usd)
                else:
                    cost_usd_estimate += event_cost.usd
                saw_cost_usd = True
            event_error = _record_event(event, writer, summary_chunks)
            if event_error and error is None:
                error = event_error

        if proc.stderr:
            writer.transcript({"type": "stderr", "text": proc.stderr})
        if timed_out:
            error = f"driver timed out after {inputs.timeout_s}s (per-cell cap)"
        elif proc.returncode != 0 and error is None:
            error = f"codex exec exited {proc.returncode}: {proc.stderr.strip()}"
        if inputs.budget_usd is not None and (tokens_in or tokens_out) and not saw_cost_usd:
            degraded = True
            writer.transcript(
                {
                    "type": "driver_warning",
                    "message": (
                        "Codex emitted token usage without USD cost; harness "
                        "--budget-usd cannot be enforced from this cell."
                    ),
                }
            )

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
            cost_usd_estimate=cost_usd_estimate,
            degraded=degraded or bool(error),
            summary_text=summary_text,
            final_diff=diff,
            error=error,
        )


def _codex_command(*, workspace: Path, last_message: Path, model: str | None) -> list[str]:
    # Codex CLI's documented `exec` flags do not include a spend/budget flag.
    # The harness budget can only consume cost reported back in JSON events.
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


def _cost_usd_observation(event: dict[str, Any]) -> _CostObservation | None:
    containers: list[dict[str, Any]] = [event]
    usage = event.get("usage")
    if isinstance(usage, dict):
        containers.append(usage)

    for container in containers:
        cost = _first_non_negative_cost(container, _CUMULATIVE_COST_KEYS)
        if cost is not None:
            return _CostObservation(cost, cumulative=True)
        cost = _first_non_negative_cost(container, _INCREMENTAL_COST_KEYS)
        if cost is not None:
            return _CostObservation(cost, cumulative=False)
    return None


def _first_non_negative_cost(container: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        raw = container.get(key)
        if raw is None:
            continue
        try:
            cost = float(raw)
        except (TypeError, ValueError):
            continue
        if cost >= 0:
            return cost
    return None


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
    diff_proc = subprocess.run(
        ["git", "diff", "HEAD", "--"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    parts = [diff_proc.stdout] if diff_proc.stdout else []

    untracked_proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=workspace,
        capture_output=True,
        text=False,
        check=False,
    )
    for raw_path in untracked_proc.stdout.split(b"\0"):
        if not raw_path:
            continue
        rel_path = raw_path.decode("utf-8", errors="surrogateescape")
        file_diff = subprocess.run(
            ["git", "diff", "--no-index", "--", os.devnull, rel_path],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if file_diff.stdout:
            parts.append(file_diff.stdout)

    if not parts:
        return ""
    return "\n".join(part.rstrip("\n") for part in parts) + "\n"


__all__ = ["CodexDriver"]
