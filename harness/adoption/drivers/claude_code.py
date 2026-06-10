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

from datetime import UTC, datetime
from subprocess import run
from typing import Any

from harness.adoption.drivers.base import DriverInputs, RunResult, _sandbox_env
from harness.adoption.observer.transcript import TranscriptWriter

# Agent-init failure signals observed on SDK events. The stream ends
# normally after these (no exception), so without explicit detection the
# cell would score as a real-but-idle run.
_INIT_FAILURE_ERRORS = frozenset(
    {"authentication_failed", "invalid_api_key", "billing_error"}
)


def _init_failure(message: Any) -> str | None:
    """Return a driver-error string when an SDK event signals init failure."""
    error_value = getattr(message, "error", None)
    if not isinstance(error_value, str) and isinstance(message, dict):
        error_value = message.get("error")
    if isinstance(error_value, str) and error_value in _INIT_FAILURE_ERRORS:
        return (
            f"agent failed to initialise: {error_value}. The spawned claude "
            "CLI found no usable credentials — with the default scoped "
            "sandbox HOME the real ~/.claude login is unreachable; either "
            "export ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN into the "
            "run, or set SHIPGATE_HARNESS_SCOPE_HOME=0 (post-hoc "
            "stayed_in_workspace blocker still applies)."
        )
    return None

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
        # Bash commands awaiting their tool_result so stdout can be attached to
        # the commands.jsonl row. Keyed by tool_use id.
        self._pending_bash: dict[str, str] = {}

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        self._pending_bash = {}
        # Refuse unknown models BEFORE attempting any other setup. With
        # (0, 0) pricing the mid-loop budget check would never abort and
        # the outer BudgetGuard would never see spend — a model typo
        # could bypass the entire budget cap. The error flows through
        # _mark_infrastructure_failure → blocker → exit 4. Running this
        # check before the SDK import also lets unit tests exercise the
        # preflight without needing claude-agent-sdk on path.
        model = inputs.model or self.default_model
        if model not in PRICE_TABLE_USD_PER_M:
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=True,
                error=(
                    f"unknown model {model!r}: not in PRICE_TABLE_USD_PER_M. "
                    "Add the pricing tuple before paid runs — otherwise the "
                    "budget cap is ineffective."
                ),
                summary_text="(driver refused to start due to unknown model pricing)",
            )

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

        options = ClaudeAgentOptions(
            cwd=str(inputs.workspace),
            allowed_tools=list(DEFAULT_ALLOWED_TOOLS),
            max_turns=25,
            model=model,
            env=_sandbox_env(inputs),
        )

        tokens_in = 0
        tokens_out = 0
        summary_chunks: list[str] = []
        error: str | None = None

        import anyio  # type: ignore[import-untyped]

        timed_out = False
        budget_aborted = False
        # Resolve prices once up front so the per-event budget check is cheap.
        # Safe to index directly — model membership was checked above.
        cost_in_per_m, cost_out_per_m = PRICE_TABLE_USD_PER_M[model]

        async def _drive() -> None:
            nonlocal tokens_in, tokens_out, error, timed_out, budget_aborted
            # ``move_on_after`` returns silently when the deadline elapses;
            # we mark ``timed_out`` so the outer summary records the abort.
            with anyio.move_on_after(inputs.timeout_s) as scope:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(inputs.prompt_text)
                    async for message in client.receive_response():
                        self._record(message, writer, summary_chunks)
                        # Fail closed on agent-init failures. A "Not logged
                        # in" run ends the stream NORMALLY (no exception),
                        # which previously scored as a legitimate 0-point
                        # cell — the 2026-W24 paid run produced 31 such
                        # vacuous rows before this guard existed. Any init
                        # error must surface as a driver error so the cell
                        # is marked degraded, never scored.
                        init_error = _init_failure(message)
                        if init_error is not None:
                            error = init_error
                            break
                        usage = _extract_usage(message)
                        if usage:
                            tokens_in += usage.get("input_tokens", 0) or 0
                            tokens_out += usage.get("output_tokens", 0) or 0
                        # Mid-loop hard cap: a single expensive cell must abort
                        # the moment its cumulative cost would exceed the
                        # remaining per-run budget. Without this, an
                        # ``--max-turns=25`` cell could blow the cap before
                        # the outer BudgetGuard ever sees the result.
                        if inputs.budget_usd is not None:
                            current = (
                                tokens_in * cost_in_per_m + tokens_out * cost_out_per_m
                            ) / 1_000_000
                            if current >= inputs.budget_usd:
                                budget_aborted = True
                                break
            if scope.cancel_called:
                timed_out = True

        try:
            anyio.run(_drive)
        except Exception as exc:  # noqa: BLE001 — driver-boundary catch
            error = repr(exc)
        if timed_out and error is None:
            error = f"driver timed out after {inputs.timeout_s}s (per-cell cap)"
        if budget_aborted and error is None:
            error = (
                f"driver aborted mid-loop: budget cap {inputs.budget_usd:.4f} USD "
                "would have been exceeded"
            )

        # Flush Bash commands whose tool_result never arrived (timeout / abort /
        # final turn): record them without output so no command is lost.
        for command in self._pending_bash.values():
            writer.command(command)
        self._pending_bash.clear()

        ended = datetime.now(UTC)
        cost = (tokens_in * cost_in_per_m + tokens_out * cost_out_per_m) / 1_000_000

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

    @staticmethod
    def _detect_init_failure(message: Any) -> str | None:
        return _init_failure(message)

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
            # Tool result: match it back to a deferred Bash command and emit the
            # commands.jsonl row with the captured stdout. (If the id does not
            # match a pending command — e.g. the SDK shape differs — nothing is
            # lost; the command is flushed without output at run end.)
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if tool_use_id in self._pending_bash:
                    writer.command(
                        self._pending_bash.pop(tool_use_id),
                        output=_tool_result_text(block.get("content")),
                    )
                continue
            tool_name = block.get("name") or block.get("tool_use", {}).get("name")
            tool_input = block.get("input") or block.get("tool_use", {}).get("input") or {}
            if tool_name == "Bash":
                command = tool_input.get("command") or ""
                tool_use_id = block.get("id")
                if tool_use_id is None:
                    # No id to match a result against — emit immediately.
                    writer.command(command)
                else:
                    self._pending_bash[tool_use_id] = command
            elif tool_name in ("Edit", "Write"):
                writer.file_op(tool_name, tool_input.get("file_path") or "")
            elif tool_name == "Read":
                writer.file_op("Read", tool_input.get("file_path") or "")
            if block.get("type") == "text" and block.get("text"):
                summary.append(block["text"])


def _tool_result_text(content: Any) -> str:
    """Extract stdout text from a tool_result block's ``content`` — either a
    plain string or a list of ``{type: text, text: ...}`` blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


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
