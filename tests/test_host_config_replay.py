"""The #659 host-config population replays offline to its recorded outcomes.

`benchmark/host-config/replay.py` rebuilds each vendored case, the selected file
at its base and at its merge commit, as a two-commit repository and runs the
same `shipgate diff` comparison the live run issues, scored against an
expectation derived from the files alone. Any change to a case's outcome fails
here until `replay.json` is re-recorded in the same change, so an improvement is
recorded and a regression cannot pass unnoticed.

A vendored case holds only the selected file, so this pins comparison
correctness on real-world shapes, not reach. The population rates, including
every refusal, are in `benchmark/host-config/results/` and never inferred here.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "benchmark" / "host-config"
RUN_OF_RECORD = ROOT / "results" / "2026-09-13-71ef771d"
QUOTAS = {
    ".claude/settings.json": 10,
    ".mcp.json": 10,
    ".github/workflows/": 10,
    ".cursor/mcp.json": 7,
    ".codex/config.toml": 7,
    ".vscode/mcp.json": 6,
}


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"host_config_test_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _module("replay")
CASES = replay.case_dirs()


def test_the_population_is_the_frozen_selection() -> None:
    accepted = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))["accepted"]

    assert Counter(case["kind"] for case in accepted) == QUOTAS
    assert len({case["repo"] for case in accepted}) == len(accepted), "one PR per repository"
    assert len(CASES) == len(accepted), "every selected case must be vendored for replay"


def test_the_run_of_record_scores_as_published() -> None:
    """`scores.csv` is what `score.py` makes of `runs.json`, so the published rates are reproducible."""

    score = _module("score")
    runs = json.loads((RUN_OF_RECORD / "runs.json").read_text(encoding="utf-8"))
    with (RUN_OF_RECORD / "scores.csv").open(encoding="utf-8", newline="") as handle:
        published = list(csv.DictReader(handle))

    rescored = [{key: str(value) for key, value in score.score_record(record).items()} for record in runs["records"]]
    assert rescored == published


@pytest.mark.parametrize("case_dir", CASES, ids=[path.name for path in CASES])
def test_a_vendored_case_replays_to_its_recorded_outcome(case_dir: Path, tmp_path: Path) -> None:
    observed = replay.replay_case(case_dir, tmp_path)

    recorded = json.loads((case_dir / "replay.json").read_text(encoding="utf-8"))
    assert observed == recorded, (
        f"{case_dir.name} replays differently from its replay.json. If this is an "
        "improvement, run `python benchmark/host-config/replay.py --record` in the same change."
    )
