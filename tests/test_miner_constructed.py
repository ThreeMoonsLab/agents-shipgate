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
import json
import os
import subprocess
import sys
from pathlib import Path

from benchmark.miner.constructed import CONSTRUCTED_CASES, build_constructed_corpus
from benchmark.miner.evaluate import cli_env
from benchmark.miner.labels import LABELS, load_labels, score
from benchmark.miner.rows import STATUS_EVALUATED, read_jsonl

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "benchmark" / "miner" / "results"
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


def test_ie_threshold_is_exercised_and_robust_on_the_labeled_coverage_fixture(
    tmp_path: Path,
) -> None:
    """Calibration data point for the IE threshold (see CALIBRATION.md).

    ``openai_agents_sdk_agent`` is the one labeled constructed case that lands
    on the IE threshold (a dynamic SDK toolset static extraction can't
    resolve). It must be ``insufficient_evidence`` *because* its low-confidence
    ratio meets the current threshold — this ties the otherwise-untested
    ``_LOW_CONFIDENCE_TOOL_RATIO`` constant to a labeled fixture, so an
    extraction improvement that resolves the surface (or a threshold change)
    surfaces here rather than silently. It also documents why the constant
    cannot be *calibrated* from constructed data: the point sits at the robust
    extreme (every tool low-confidence), indistinguishable across ratios.
    """
    from agents_shipgate.ci.release_decision import _low_confidence_tool_threshold

    result = subprocess.run(
        [sys.executable, "-m", "agents_shipgate", "fixture", "run",
         "openai_agents_sdk_agent", "--out", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=180, env=cli_env(), check=False,
    )
    report_path = tmp_path / "out" / "report.json"
    assert report_path.is_file(), f"exit {result.returncode}: {result.stderr[:400]}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    decision = report["release_decision"]
    coverage = decision["evidence_coverage"]
    low = coverage["low_confidence_tool_count"]
    total = (report.get("tool_surface") or {}).get("total_tools") or len(
        report.get("tool_inventory") or []
    )
    assert decision["decision"] == "insufficient_evidence"
    # The current constant correctly classifies it as below-threshold.
    assert low >= _low_confidence_tool_threshold(total)
    # It sits at the robust extreme (every tool is low-confidence), so this
    # single labeled point cannot distinguish ratio values in (0, 1]. Moving
    # the constant needs the human labeling pass + a re-mine, not this fixture.
    assert low == total and total > 0


def test_cli_env_prepends_this_checkouts_src() -> None:
    """The child `python -m agents_shipgate` must import THIS checkout.

    Regression for the stale-install footgun: without src/ first on the
    child's PYTHONPATH, the documented commands resolve to whatever
    agents-shipgate is installed on the machine.
    """
    env = cli_env()
    first = env["PYTHONPATH"].split(os.pathsep)[0]
    assert Path(first) == (REPO_ROOT / "src"), first
    assert env["AGENTS_SHIPGATE_AGENT_MODE"] == "0"


def test_constructed_cli_is_source_hermetic(tmp_path: Path) -> None:
    """End-to-end: regenerate via the CLI with src/ removed from the parent
    env. The child must still find this checkout (via cli_env), not fall back
    to an installed wheel — so the command exits 0 and writes all 7 rows.
    """
    env = dict(os.environ)
    # Parent can import `benchmark` (repo root) but NOT agents_shipgate (no src) —
    # only the cli_env fix puts src on the child's path.
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("AGENTS_SHIPGATE_AGENT_MODE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmark.miner",
            "constructed",
            "--out",
            str(tmp_path / "constructed.jsonl"),
            "--labels-out",
            str(tmp_path / "constructed.labels.csv"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert len(read_jsonl(tmp_path / "constructed.jsonl")) == len(CONSTRUCTED_CASES)
