"""Replay closure: a committed governance-benchmark baseline, regression-pinned.

The AgentPR governance catalog (``benchmark/agent-pr-governance/cases.yaml``)
is the flywheel's ground truth — hand-authored cases with the verdict a correct
gate must return. ``test_governance_benchmark.py`` already *replays* every
``executable`` case through the live verifier on each CI run and fails if a
verdict drifts. This file closes the last piece: a **committed, deterministic
baseline result artifact** so the loop is not only re-run but pinned to a
checked-in reference, and the ``results/`` directory carries citable evidence
instead of being empty.

Because ``run_governance_benchmark`` renders byte-identically across runs (no
wall-clock fields, relative ``catalog_path``, portable case rows), a fresh run
must reproduce the committed baseline exactly. Any change to a governance
verdict, metric total, or capability-diff shows up here as a one-line diff with
a clear regenerate instruction.
"""
from __future__ import annotations

from pathlib import Path

from scripts.run_governance_benchmark import (
    load_governance_benchmark_catalog,
    render_governance_benchmark_result_json,
    run_governance_benchmark,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "benchmark/agent-pr-governance/cases.yaml"
BASELINE_PATH = REPO_ROOT / "benchmark/agent-pr-governance/results/baseline.v0.2.json"
# The committed baseline records the catalog path as the runner's CLI default
# writes it — repo-relative, so the artifact is portable across machines. The
# reproduction test must pass the same relative path or the ``catalog_path``
# provenance field (embedded in the result) would differ.
CATALOG_PATH_REL = "benchmark/agent-pr-governance/cases.yaml"


def test_committed_baseline_exists_and_is_all_green() -> None:
    assert BASELINE_PATH.is_file(), (
        "benchmark/agent-pr-governance/results/baseline.v0.2.json is missing — "
        "regenerate with: PYTHONPATH=src python scripts/run_governance_benchmark.py "
        "--out benchmark/agent-pr-governance/results/baseline.v0.2.json"
    )
    import json

    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    summary = data["summary"]
    # The baseline records a fully-passing run: every executable case replayed
    # and matched. A committed baseline with failures would be evidence of a
    # known-broken gate, which we never ship.
    assert summary["executable_cases"] == 10
    assert summary["selected_cases"] == 10
    assert summary["passed_cases"] == 10
    assert summary["failed_cases"] == 0
    assert data["governance_benchmark_result_schema_version"] == "0.2"


def test_fresh_run_reproduces_committed_baseline(tmp_path: Path) -> None:
    catalog = load_governance_benchmark_catalog(CATALOG_PATH)
    fresh = run_governance_benchmark(
        catalog,
        catalog_path=Path(CATALOG_PATH_REL),
        work_root=tmp_path / "run",
    )
    rendered = render_governance_benchmark_result_json(fresh)
    committed = BASELINE_PATH.read_text(encoding="utf-8")

    assert rendered == committed, (
        "The live governance-benchmark result diverged from the committed "
        "baseline (a verdict, metric total, or capability-diff changed). If the "
        "change is intended, regenerate the baseline:\n"
        "  PYTHONPATH=src python scripts/run_governance_benchmark.py "
        "--out benchmark/agent-pr-governance/results/baseline.v0.2.json"
    )
