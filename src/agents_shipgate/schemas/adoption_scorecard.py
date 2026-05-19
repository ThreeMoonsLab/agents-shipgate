"""Adoption-harness scorecard model — ``ScorecardV1``.

The harness runner writes one of these JSON files per cell under
``.agents-private/adoption-sprint/<run-id>/<cell-id>/scorecard.json``. The
model lives here (in the packaged ``src/agents_shipgate.schemas`` tree)
rather than inside the local-only ``harness/`` package so a wheel-installed
agents-shipgate ships the contract. Downstream consumers can validate
scorecard artifacts without needing the harness on their path.

Detector functions live in
:mod:`harness.adoption.scorer.rules` (local-only); this module is data only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCORECARD_SCHEMA_VERSION = "1.0"

CriterionStatus = Literal["pass", "fail", "n_a"]
"""Detector outcome. ``n_a`` means the criterion did not apply to this cell."""

Severity = Literal["info", "warn", "blocker"]
"""Severity ladder. ``blocker`` is the only one that can flip ``headline_pass``."""


class CriterionResult(BaseModel):
    """One row in the ``criteria`` map of a scorecard."""

    model_config = ConfigDict(extra="forbid")

    status: CriterionStatus
    severity: Severity
    signal: str = Field(
        description=(
            "Short, human-readable description of what the detector observed. "
            "Used for the ``notes`` column in the public CSV when needed."
        ),
    )
    evidence_ref: str | None = Field(
        default=None,
        description=(
            "Pointer into a captured artifact, e.g. ``transcript.jsonl#L42`` or "
            "``final.diff#shipgate.yaml:L23``. Optional — informational only."
        ),
    )


class Blocker(BaseModel):
    """One element of the ``blockers`` array."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        description=(
            "Detector key whose criterion is severity=blocker and status=fail "
            "(e.g. ``replaces_change_me``, ``no_runtime_trace_synthesis``)."
        ),
    )
    detail: str
    evidence_ref: str | None = None


class ScorecardV1(BaseModel):
    """One cell of the adoption harness."""

    model_config = ConfigDict(extra="forbid")

    scorecard_schema_version: Literal["1.0"] = SCORECARD_SCHEMA_VERSION
    run_id: str
    cell_id: str

    archetype: str
    variant: str
    negative_overlay: str | None = None
    prompt_id: str
    agent: str
    model: str | None = None

    started_at: datetime
    ended_at: datetime
    duration_s: float

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd_estimate: float = 0.0

    criteria: dict[str, CriterionResult] = Field(default_factory=dict)
    blockers: list[Blocker] = Field(default_factory=list)

    rubric_score: int = Field(
        ge=0,
        le=100,
        description=(
            "0–100, computed from the existing rubric in "
            "docs/agent-adoption-harness.md for back-compat with "
            "benchmark/results/*.csv."
        ),
    )
    headline_pass: bool = Field(
        description=(
            "False iff any blocker tripped, regardless of rubric_score. "
            "This is the column the leaderboard groups by for go/no-go."
        ),
    )

    driver_degraded: bool = Field(
        default=False,
        description="True when the driver fell back to a degraded capture mode.",
    )
    redaction_applied: bool = True
    artifacts_dir: str = Field(
        description=(
            "Relative path under .agents-private/ where raw and redacted "
            "artifacts live for this cell. The path itself is safe to publish; "
            "the contents are not."
        ),
    )
    notes: str = ""


def adoption_scorecard_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`ScorecardV1`.

    Importable from a wheel install — has no dependency on the local
    ``harness/`` package.
    """
    schema = ScorecardV1.model_json_schema()
    schema.setdefault("title", "AdoptionHarnessScorecardV1")
    return schema


__all__ = [
    "Blocker",
    "CriterionResult",
    "CriterionStatus",
    "SCORECARD_SCHEMA_VERSION",
    "ScorecardV1",
    "Severity",
    "adoption_scorecard_json_schema",
]
