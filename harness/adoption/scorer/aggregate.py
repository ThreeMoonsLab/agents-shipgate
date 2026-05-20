"""CSV writer, budget guard, and exit-criteria checker.

The aggregate layer takes per-cell :class:`ScorecardV1` objects and:

* writes one row per cell to ``benchmark/results/<run-id>.csv`` using the
  v0.2 schema (new columns: ``headline_pass``, ``blocker_count``,
  ``blocker_kinds``, ``agent_version``, ``negative_overlay``);
* tracks cumulative cost against ``SHIPGATE_HARNESS_BUDGET_USD`` and aborts
  the run when the cap is exceeded;
* computes the three exit-criteria metrics from the plan.
"""
from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from harness.adoption.scorer.schema import ScorecardV1

CSV_COLUMNS_V0_2: tuple[str, ...] = (
    "model",
    "prompt",
    "archetype",
    "variant",
    "negative_overlay",
    "score",
    "headline_pass",
    "blocker_count",
    "blocker_kinds",
    "agent_version",
    "run_date",
    "transcript_path",
    "notes",
)


class BudgetExceeded(RuntimeError):
    """Raised when cumulative spend has crossed the per-run cap."""


@dataclass
class BudgetGuard:
    """Per-run cumulative-cost cap.

    The CLI ``--budget-usd`` flag is the only knob; there is no env-var
    fallback. A previous version honoured ``SHIPGATE_HARNESS_BUDGET_USD``
    but env-precedence let stale values silently override a deliberately
    lower CLI cap, which is unsafe for paid runs.
    """

    cap_usd: float
    spent_usd: float = 0.0
    cells_recorded: int = 0

    def record(self, scorecard: ScorecardV1) -> None:
        self.spent_usd += scorecard.cost_usd_estimate
        self.cells_recorded += 1
        if self.cap_usd >= 0 and self.spent_usd > self.cap_usd:
            raise BudgetExceeded(
                f"Cumulative spend {self.spent_usd:.4f} USD exceeded cap {self.cap_usd:.4f}. "
                f"Aborting after {self.cells_recorded} cell(s)."
            )


def write_csv(
    scorecards: Iterable[ScorecardV1],
    *,
    out_path: Path,
    schema_version: str = "0.2",  # kept for call-site compatibility; documented in README
) -> Path:
    """Write or append a CSV file.

    Uses the existing v0.2 column layout. ``transcript_path`` is the relative
    path under ``.agents-private/`` where the redacted artifacts live — the
    file is NOT the artifact itself.

    The schema version itself is **not** embedded as a row in the CSV — that
    would break standard CSV readers. It lives in
    ``benchmark/results/README.md`` and the per-run ``exit_criteria.json``.
    """
    _ = schema_version  # explicit acknowledgement of unused kwarg
    out_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS_V0_2)
        if new_file:
            writer.writeheader()
        for sc in scorecards:
            # The scorecard JSON sidecar path already runs through
            # ``write_scorecard_json``, which redacts in place. CSV calls
            # may write without that path (e.g., smoke aggregates), so
            # apply the same pass here defensively.
            redact_scorecard_in_place(sc)
            writer.writerow(_scorecard_to_row(sc))
    return out_path


def _scorecard_to_row(sc: ScorecardV1) -> dict[str, object]:
    blocker_kinds = ",".join(b.kind for b in sc.blockers)
    notes = _build_notes(sc)
    return {
        "model": sc.agent,
        "prompt": sc.prompt_id,
        "archetype": sc.archetype,
        "variant": sc.variant,
        "negative_overlay": sc.negative_overlay or "",
        "score": sc.rubric_score,
        "headline_pass": str(sc.headline_pass).lower(),
        "blocker_count": len(sc.blockers),
        "blocker_kinds": blocker_kinds,
        "agent_version": sc.model or "",
        "run_date": sc.started_at.date().isoformat(),
        "transcript_path": sc.artifacts_dir,
        "notes": notes,
    }


def _build_notes(sc: ScorecardV1) -> str:
    """Short, structured note — no transcript text. Keep under 200 chars."""
    failures = [k for k, v in sc.criteria.items() if v.status == "fail" and v.severity != "blocker"]
    parts: list[str] = []
    if sc.driver_degraded:
        parts.append("driver_degraded")
    if failures:
        parts.append("fails:" + ",".join(failures[:4]))
    return "; ".join(parts)[:200]


def redact_scorecard_in_place(scorecard: ScorecardV1) -> None:
    """Run every text-bearing field through the harness redactor.

    Detectors read live-workspace files (``shipgate.yaml``, ``.gitignore``)
    and may copy raw values — including tool names, policy entries, scope
    strings — into ``CriterionResult.signal``, ``Blocker.detail``, and
    ``ScorecardV1.notes``. Those values can carry secrets that the
    redacted JSONL stream never sees. This pass is the choke point that
    enforces the contract: the only scorecards that ever reach disk or
    CSV must be fully redacted.
    """
    from harness.adoption.observer.redact import default_config, redact_string

    cfg = default_config()
    for crit in scorecard.criteria.values():
        crit.signal = redact_string(crit.signal, config=cfg)
        if crit.evidence_ref:
            crit.evidence_ref = redact_string(crit.evidence_ref, config=cfg)
    for blocker in scorecard.blockers:
        blocker.detail = redact_string(blocker.detail, config=cfg)
        if blocker.evidence_ref:
            blocker.evidence_ref = redact_string(blocker.evidence_ref, config=cfg)
    if scorecard.notes:
        scorecard.notes = redact_string(scorecard.notes, config=cfg)


def write_scorecard_json(scorecard: ScorecardV1, path: Path) -> Path:
    redact_scorecard_in_place(scorecard)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scorecard.model_dump_json(indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- exit criteria


@dataclass
class ExitCriteriaReport:
    """Final pass/fail summary against the three exit criteria."""

    materially_outperforms_no_hints: bool
    near_perfect_activation: bool
    not_noisy_on_docs_only: bool
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "materially_outperforms_no_hints": self.materially_outperforms_no_hints,
            "near_perfect_activation": self.near_perfect_activation,
            "not_noisy_on_docs_only": self.not_noisy_on_docs_only,
            "details": self.details,
        }


# Behavioural agents whose rubric scores feed the three published exit
# criteria. Static lints (e.g., ``cursor-static``) are aggregated
# separately because their rubric semantics differ — a cursor-static
# ``00-no-hints`` cell correctly scores 100 when the rule is absent, which
# would inflate any Claude-uplift metric if mixed in.
BEHAVIORAL_AGENTS: frozenset[str] = frozenset({"claude-code", "codex"})


def check_exit_criteria(scorecards: list[ScorecardV1]) -> ExitCriteriaReport:
    """Compute the three exit-criteria metrics from the plan.

    Thresholds (all evaluated over BEHAVIORAL agent rows only — Claude
    Code, Codex; ``cursor-static`` is reported as a separate detail):

      * 10-agents-md mean − 00-no-hints mean ≥ +25 rubric points.
      * 40-shipgate-yaml mean ≥ 90 AND zero blockers across that subset.
      * docs-only-negative-overlay cells on un-adopted variants:
        ``runs_init OR runs_scan`` true in ≤ 10 % of cells.
    """
    behavioural = [sc for sc in scorecards if sc.agent in BEHAVIORAL_AGENTS]
    cursor = [sc for sc in scorecards if sc.agent == "cursor-static"]

    mean_00 = _mean_score(behavioural, variant="00-no-hints")
    mean_10 = _mean_score(behavioural, variant="10-agents-md")
    mean_40 = _mean_score(behavioural, variant="40-shipgate-yaml")
    blockers_40 = sum(
        len(sc.blockers) for sc in behavioural if sc.variant == "40-shipgate-yaml"
    )

    docs_cells = [
        sc
        for sc in behavioural
        if sc.negative_overlay == "60-docs-only-negative"
        and sc.variant in {"00-no-hints", "10-agents-md", "20-claude-md", "30-cursor-rule", "50-advisory-workflow"}
    ]
    noisy = sum(1 for sc in docs_cells if _ran_init_or_scan(sc))
    noisy_fraction = (noisy / len(docs_cells)) if docs_cells else 0.0

    cursor_pass = (
        sum(1 for sc in cursor if sc.headline_pass) / len(cursor) if cursor else None
    )

    has_behavioural = bool(behavioural)
    return ExitCriteriaReport(
        materially_outperforms_no_hints=has_behavioural and (mean_10 - mean_00) >= 25,
        near_perfect_activation=has_behavioural and mean_40 >= 90 and blockers_40 == 0,
        not_noisy_on_docs_only=noisy_fraction <= 0.10,
        details={
            "behavioural_cells": len(behavioural),
            "cursor_static_cells": len(cursor),
            "cursor_static_pass_rate": cursor_pass,
            "mean_score_00_no_hints": mean_00,
            "mean_score_10_agents_md": mean_10,
            "mean_score_40_shipgate_yaml": mean_40,
            "blockers_on_40_shipgate_yaml": blockers_40,
            "docs_only_cells": len(docs_cells),
            "docs_only_noisy_cells": noisy,
            "docs_only_noisy_fraction": noisy_fraction,
        },
    )


def _mean_score(scorecards: Iterable[ScorecardV1], *, variant: str) -> float:
    matching = [sc.rubric_score for sc in scorecards if sc.variant == variant]
    return sum(matching) / len(matching) if matching else 0.0


def _ran_init_or_scan(sc: ScorecardV1) -> bool:
    return any(
        sc.criteria.get(k) and sc.criteria[k].status == "pass"
        for k in ("runs_init", "runs_scan")
    )


__all__ = [
    "BudgetExceeded",
    "BudgetGuard",
    "CSV_COLUMNS_V0_2",
    "ExitCriteriaReport",
    "check_exit_criteria",
    "write_csv",
    "write_scorecard_json",
]
