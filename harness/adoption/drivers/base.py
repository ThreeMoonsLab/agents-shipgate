"""Driver protocol and shared types.

A driver takes a prepared workspace + prompt and produces three artifacts:

* ``transcript.jsonl`` (event stream), ``commands.jsonl``, ``file_ops.jsonl``
  — written by the driver via :class:`harness.adoption.observer.transcript.TranscriptWriter`.
* ``final.diff`` — output of ``git diff HEAD`` after the agent finishes.
* ``summary.md`` — the agent's final assistant message.

Drivers must NOT write the scorecard themselves — scoring happens after the
run, against the captured artifacts. Drivers MAY mark themselves degraded
(returning ``RunResult.degraded=True``) if they fell back to a less reliable
capture mode (e.g. Codex without ``--json-logs``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from harness.adoption.observer.transcript import TranscriptWriter


@dataclass
class DriverInputs:
    """All the state a driver needs to run one cell."""

    workspace: Path
    prompt_text: str
    artifacts_dir: Path
    cell_id: str
    agent_name: str
    model: str | None
    timeout_s: int = 600
    budget_usd: float | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    """What a driver returns after a single cell completes."""

    started_at: datetime
    ended_at: datetime
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd_estimate: float = 0.0
    degraded: bool = False
    summary_text: str = ""
    final_diff: str = ""
    error: str | None = None

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())


class AgentDriver(Protocol):
    """Common driver contract."""

    name: str

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        """Execute the agent against ``inputs.workspace`` until completion."""
        ...


def _sandbox_env(inputs: DriverInputs) -> dict[str, str]:
    """Build a spawned agent's environment with HOME scoped to the cell.

    By default HOME points at a per-cell scratch dir so an under-specified
    prompt cannot reach a real checkout via ``cd $HOME/...`` — the exact escape
    that let an agent edit the real repo on a developer machine. Opt out with
    ``SHIPGATE_HARNESS_SCOPE_HOME=0``. Fails closed: a HOME the agent can't use
    surfaces as an infra error, never a sandbox escape. Absolute-path escapes
    (``cd /Users/...``) remain caught post-hoc by the ``stayed_in_workspace``
    scorer blocker — defence in depth.

    VALIDATED (2026-W24 paid run): with the scoped HOME, a
    subscription-authenticated ``claude`` CLI does NOT initialise — its
    OAuth login lives in the real ``~/.claude`` and the stream ends
    normally with ``authentication_failed``. The claude_code driver now
    detects that as a driver error (see ``_init_failure``); to run
    subscription-authenticated cells, set
    ``SHIPGATE_HARNESS_SCOPE_HOME=0`` or export an API key/OAuth token.
    """
    env = {**os.environ, **inputs.extra_env, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
    if os.environ.get("SHIPGATE_HARNESS_SCOPE_HOME", "1") != "0":
        scratch = inputs.artifacts_dir / "sandbox_home"
        scratch.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(scratch)
    return env


__all__ = ["AgentDriver", "DriverInputs", "RunResult", "_sandbox_env"]
