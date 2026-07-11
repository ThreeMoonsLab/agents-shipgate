"""Constructed-adversarial accuracy corpus from the bundled fixtures.

The mined corpus (:mod:`benchmark.miner`) supplies real-history *negatives* and
the extraction-coverage (`insufficient_evidence`) cases — but the 2026-W25
widen-run showed that real merged PRs almost never contain a `must_block`
capability change. The *positives* come from here.

Each bundled fixture was built to be a specific safe / needs-review / unsafe
case, so its label is the fixture's **documented design intent** — external
ground truth, not a post-hoc opinion about what the engine returned. Scoring
the engine's verdict against those definitional labels is therefore a
*non-circular* blocked-recall / benign-escalation measurement: the moat claim
("the gate blocks what is known-unsafe, and doesn't escalate what is
known-safe"), measured.

Network-free: runs ``agents-shipgate fixture run <name> --out <tmp>`` and reads
the written ``report.json`` / ``verifier.json``. Reuses
:mod:`benchmark.miner.rows` + :mod:`benchmark.miner.labels` so the constructed
corpus scores through the very same confusion-matrix code as the mined corpus.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from benchmark.miner.evaluate import cli_env
from benchmark.miner.rows import STATUS_ERROR, STATUS_EVALUATED, MinedRow


@dataclass(frozen=True)
class ConstructedCase:
    fixture: str
    label: str  # one of benchmark.miner.labels.LABELS — the design intent
    rationale: str


# The curated set. Labels are definitional (each fixture's documented purpose),
# so this mapping is the ground truth — independent of the verdict the engine
# produces, which is exactly what makes the resulting score non-circular.
CONSTRUCTED_CASES: tuple[ConstructedCase, ...] = (
    ConstructedCase(
        "ai_generated_refund_pr",
        "must_block",
        "homepage story: adds stripe.create_refund with no approval policy and no idempotency evidence",
    ),
    ConstructedCase(
        "agent_weakens_gate",
        "must_block",
        "trust-root: the coding agent deletes the Shipgate CI gate to make its PR self-merge",
    ),
    ConstructedCase(
        "support_refund_agent",
        "must_block",
        "refund tool missing approval policy and idempotency evidence (two criticals by design)",
    ),
    ConstructedCase(
        "clean_read_only_agent",
        "safe_to_merge",
        "read-only tool surface; no risky actions by design",
    ),
    ConstructedCase(
        "hitl_evidence_covered_agent",
        "safe_to_merge",
        "refund domain with the approval/idempotency evidence a reviewer expects already covered",
    ),
    ConstructedCase(
        "hitl_evidence_agent",
        "needs_human",
        "authority-bearing refund surface a reviewer must sign off (human-in-the-loop evidence expected)",
    ),
    ConstructedCase(
        "openai_agents_sdk_agent",
        "safe_to_merge",
        "reviewed inventory is explicitly bound to the static SDK observations with complete read-only semantics",
    ),
)

_RUN_TIMEOUT = 180


def evaluate_constructed_case(case: ConstructedCase) -> MinedRow:
    """Run one bundled fixture and record its verdict as a score-able row."""

    row = MinedRow(
        repo=f"fixture/{case.fixture}",
        pr_number=0,
        pr_url=f"fixture://{case.fixture}",
        title=case.fixture,
        merged_at="",
        base_sha="",
        head_sha="",
        status=STATUS_ERROR,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="shipgate-constructed-") as tmp:
            out = Path(tmp) / "out"
            result = subprocess.run(
                [sys.executable, "-m", "agents_shipgate", "fixture", "run", case.fixture, "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=_RUN_TIMEOUT,
                env=cli_env(),
                check=False,
            )
            report = out / "report.json"
            if not report.is_file():
                row.notes = f"no report.json (exit {result.returncode})"
                return row
            data = json.loads(report.read_text(encoding="utf-8"))
            row.head_decision = str((data.get("release_decision") or {}).get("decision") or "")
            verifier = out / "verifier.json"
            if verifier.is_file():
                vdata = json.loads(verifier.read_text(encoding="utf-8"))
                row.verify_verdict = str(vdata.get("merge_verdict") or "")
                can_merge = vdata.get("can_merge_without_human")
                row.verify_can_merge = can_merge if isinstance(can_merge, bool) else None
            row.status = STATUS_EVALUATED
            return row
    except Exception as exc:  # noqa: BLE001 - one fixture must not abort the set.
        row.notes = f"exception:{type(exc).__name__}:{exc}"
        return row


def build_constructed_corpus() -> tuple[list[MinedRow], dict[str, str], dict[str, str]]:
    """Return (rows, labels, rationales) for the whole constructed set."""

    rows: list[MinedRow] = []
    labels: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for case in CONSTRUCTED_CASES:
        row = evaluate_constructed_case(case)
        rows.append(row)
        labels[row.pr_url] = case.label
        rationales[row.pr_url] = case.rationale
    return rows, labels, rationales
