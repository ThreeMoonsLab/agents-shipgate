"""The constructed-adversarial accuracy benchmark — the blocked-recall proof.

The mined corpus measures the noise bound + extraction coverage on real
history; this measures the thing real history almost never supplies — whether
the gate BLOCKS what is known-unsafe and does not escalate what is known-safe.
The labels are each fixture's documented design intent (external ground
truth), so the score is non-circular.

Two layers: a fast check that the committed corpus still scores to the
published headline, and a slower live-engine regression that re-runs the
fixtures so a future change that breaks a verdict fails here, not silently.
"""

from __future__ import annotations

import csv
from pathlib import Path

from benchmark.miner.constructed import CONSTRUCTED_CASES, build_constructed_corpus
from benchmark.miner.labels import LABELS, load_labels, score
from benchmark.miner.rows import STATUS_EVALUATED, read_jsonl

RESULTS = Path(__file__).resolve().parent.parent / "benchmark" / "miner" / "results"
CORPUS = RESULTS / "constructed.jsonl"
LABELS_FILE = RESULTS / "constructed.labels.csv"


def test_committed_constructed_accuracy_headline() -> None:
    scored = score(read_jsonl(CORPUS), load_labels(LABELS_FILE))
    metrics = scored["metrics"]
    assert metrics["blocked_recall"] == 1.0, scored
    assert metrics["benign_escalation_rate"] == 0.0, scored
    assert metrics["needs_human_caught"] == 1.0, scored
    assert scored["unmatched_labels"] == []
    assert scored["labeled_rows"] == len(CONSTRUCTED_CASES)


def test_committed_constructed_is_well_formed_and_fully_labeled() -> None:
    rows = read_jsonl(CORPUS)
    assert len(rows) == len(CONSTRUCTED_CASES)
    assert all(r.status == STATUS_EVALUATED for r in rows), [r.notes for r in rows]
    labels = load_labels(LABELS_FILE)
    assert set(labels) == {r.pr_url for r in rows}  # every row labeled, no extras
    assert set(labels.values()) <= set(LABELS)
    assert b"\r" not in CORPUS.read_bytes()
    assert b"\r" not in LABELS_FILE.read_bytes()
    with CORPUS.with_suffix(".csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == len(rows)


def test_live_engine_still_produces_the_constructed_verdicts() -> None:
    """Regression: the gate must keep blocking the known-unsafe fixtures.

    Re-runs the bundled fixtures through the live engine; a change that
    regresses a blocked verdict (or escalates a safe one) fails here. Also
    catches the committed corpus going stale relative to the engine.
    """
    rows, labels, _ = build_constructed_corpus()
    unevaluated = [(r.pr_url, r.notes) for r in rows if r.status != STATUS_EVALUATED]
    assert not unevaluated, f"fixtures failed to evaluate: {unevaluated}"
    metrics = score(rows, labels)["metrics"]
    assert metrics["blocked_recall"] == 1.0
    assert metrics["benign_escalation_rate"] == 0.0
    assert metrics["needs_human_caught"] == 1.0
    live = {r.pr_url: (r.head_decision, r.verify_verdict) for r in rows}
    committed = {r.pr_url: (r.head_decision, r.verify_verdict) for r in read_jsonl(CORPUS)}
    assert live == committed, "committed constructed corpus is stale vs the live engine"
