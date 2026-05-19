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


__all__ = ["AgentDriver", "DriverInputs", "RunResult"]
