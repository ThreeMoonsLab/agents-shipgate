"""Claude Code driver via the Claude Agent SDK.

Loads the SDK lazily so the harness still imports when ``claude-agent-sdk``
isn't installed (the mock driver and Cursor-static driver have no SDK
dependency, and CI smoke tests only exercise those paths).

Implementation notes:

* The SDK is configured with ``cwd=workspace``, allowed tools
  ``[Bash, Read, Edit, Write, Glob, Grep, WebFetch]``, and ``max_turns=25``.
* ``AGENTS_SHIPGATE_AGENT_MODE=1`` is set so any agents-shipgate CLI errors
  emit structured ``next_action`` JSON.
* The driver streams events into ``transcript.jsonl`` (every block),
  ``commands.jsonl`` (Bash tool_use blocks), and ``file_ops.jsonl``
  (Read/Edit/Write tool_use blocks).
* Token usage is summed from the SDK's reported ``usage`` blocks and
  multiplied by a published price table to estimate USD spend; this is the
  signal the budget guard consumes.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from subprocess import run
from typing import Any

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter

# Published Anthropic API prices in USD per 1M tokens. Update when prices change.
# Read by the budget guard via the ``cost_usd_estimate`` field on RunResult.
PRICE_TABLE_USD_PER_M: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),       # (input, output) per 1M tokens
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
}

DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "WebFetch",
)


class ClaudeCodeDriver:
    name = "claude-code"

    def __init__(self, *, default_model: str = "claude-opus-4-7") -> None:
        self.default_model = default_model

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        try:
            from claude_agent_sdk import (  # type: ignore[import-untyped]
                ClaudeAgentOptions,
                ClaudeSDKClient,
            )
        except ImportError as exc:
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=True,
                error=f"claude-agent-sdk not installed: {exc}",
                summary_text="(driver could not load; install harness/requirements.txt)",
            )

        model = inputs.model or self.default_model
        options = ClaudeAgentOptions(
            cwd=str(inputs.workspace),
            allowed_tools=list(DEFAULT_ALLOWED_TOOLS),
            max_turns=25,
            model=model,
            env={
                **os.environ,
                **inputs.extra_env,
                "AGENTS_SHIPGATE_AGENT_MODE": "1",
            },
        )

        tokens_in = 0
        tokens_out = 0
        summary_chunks: list[str] = []
        error: str | None = None

        import anyio  # type: ignore[import-untyped]

        timed_out = False

        async def _drive() -> None:
            nonlocal tokens_in, tokens_out, error, timed_out
            # ``move_on_after`` returns silently when the deadline elapses;
            # we mark ``timed_out`` so the outer summary records the abort.
            with anyio.move_on_after(inputs.timeout_s) as scope:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(inputs.prompt_text)
                    async for message in client.receive_response():
                        self._record(message, writer, summary_chunks)
                        usage = _extract_usage(message)
                        if usage:
                            tokens_in += usage.get("input_tokens", 0) or 0
                            tokens_out += usage.get("output_tokens", 0) or 0
            if scope.cancel_called:
                timed_out = True

        try:
            anyio.run(_drive)
        except Exception as exc:  # noqa: BLE001 — driver-boundary catch
            error = repr(exc)
        if timed_out and error is None:
            error = f"driver timed out after {inputs.timeout_s}s (per-cell cap)"

        ended = datetime.now(UTC)
        cost_in, cost_out = PRICE_TABLE_USD_PER_M.get(model, (0.0, 0.0))
        cost = (tokens_in * cost_in + tokens_out * cost_out) / 1_000_000

        run(["git", "add", "-A"], cwd=inputs.workspace, check=False, capture_output=True)
        diff_proc = run(
            ["git", "diff", "--cached", "HEAD"],
            cwd=inputs.workspace,
            capture_output=True,
            text=True,
            check=False,
        )

        return RunResult(
            started_at=started,
            ended_at=ended,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd_estimate=cost,
            degraded=False,
            summary_text="\n".join(summary_chunks).strip(),
            final_diff=diff_proc.stdout or "",
            error=error,
        )

    def _record(self, message: Any, writer: TranscriptWriter, summary: list[str]) -> None:
        """Record an SDK event into the JSONL streams.

        The SDK's message shape varies by event type. We extract what we need
        defensively — missing keys fall through to the raw transcript line.
        """
        try:
            payload = _to_jsonable(message)
        except Exception:  # noqa: BLE001 — we never want a serializer error to abort a run
            payload = {"repr": repr(message)}
        writer.transcript(payload)
        # Tool-use → command / file-op streams.
        for block in payload.get("content") or []:
            if not isinstance(block, dict):
                continue
            tool_name = block.get("name") or block.get("tool_use", {}).get("name")
            tool_input = block.get("input") or block.get("tool_use", {}).get("input") or {}
            if tool_name == "Bash":
                command = tool_input.get("command") or ""
                writer.command(command)
            elif tool_name in ("Edit", "Write"):
                writer.file_op(tool_name, tool_input.get("file_path") or "")
            elif tool_name == "Read":
                writer.file_op("Read", tool_input.get("file_path") or "")
            if block.get("type") == "text" and block.get("text"):
                summary.append(block["text"])


def _to_jsonable(obj: Any) -> dict[str, Any]:
    """Coerce an SDK message into a JSON-friendly dict.

    Falls back to ``__dict__`` then ``repr`` so we never lose an event due to
    a serialization edge case.
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: _safe(v) for k, v in obj.__dict__.items()}
    return {"repr": repr(obj)}


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return _to_jsonable(value)


def _extract_usage(message: Any) -> dict[str, int] | None:
    if isinstance(message, dict):
        return message.get("usage")
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return None


__all__ = ["ClaudeCodeDriver", "PRICE_TABLE_USD_PER_M"]
