"""The #660 cold-start population replays offline to its recorded outcomes.

`benchmark/cold-start/replay.py` rebuilds each vendored case as a two-commit
repository and runs the same `shipgate diff` comparison the live run issues,
scored against an expectation derived from the files alone. Any change to a
case's outcome — a success that appears or disappears, a row gained or lost —
fails here until `replay.json` is re-recorded in the same change, so an
improvement is recorded and a regression cannot pass unnoticed.

A vendored case holds only the selected file, so this pins comparison
correctness on real-world shapes, not reach: the live run's stop points were
elsewhere in each repository, and its population rate is in
`benchmark/cold-start/results/`, never inferred from this test.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "benchmark" / "cold-start"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"cold_start_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _module("replay")
CASES = replay.case_dirs()


def test_unsupported_project_mcp_rejection_remains_a_changed_case() -> None:
    oracle = _module("expected")
    scorer = _module("score")
    expectation = oracle.claude_settings("{}", '{"disabledMcpjsonServers":["docs"]}')

    outcome = scorer.score_record({
        "case": "reject-project-mcp",
        "repo": "fixture",
        "kind": ".claude/settings.json",
        "base_sha": "base",
        "head_sha": "head",
        "expectation": expectation,
        "payload": {"rows": []},
        "stop_point": "comparison_comparable",
        "commands": [{"exit": 0}],
        "comparison_seconds": 1,
    })

    assert outcome["scope"] == "unsupported"
    assert outcome["changed_case"] is True
    assert outcome["expected_changes"] == 0
    assert outcome["success"] is False


def test_the_population_is_the_frozen_selection() -> None:
    accepted = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))["accepted"]

    assert len(accepted) == 30
    assert sorted({case["kind"] for case in accepted}) == [".claude/settings.json", ".cursor/mcp.json", ".mcp.json"]
    assert all(sum(case["kind"] == kind for case in accepted) == 10 for kind in {c["kind"] for c in accepted})
    assert len(CASES) == len(accepted), "every selected case must be vendored for replay"


@pytest.mark.parametrize("case_dir", CASES, ids=[path.name for path in CASES])
def test_a_vendored_case_replays_to_its_recorded_outcome(case_dir: Path, tmp_path: Path) -> None:
    observed = replay.replay_case(case_dir, tmp_path)

    recorded = json.loads((case_dir / "replay.json").read_text(encoding="utf-8"))
    assert observed == recorded, (
        f"{case_dir.name} replays differently from its replay.json. If this is an "
        "improvement, run `python benchmark/cold-start/replay.py --record` in the same change."
    )
