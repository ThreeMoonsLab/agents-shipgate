"""The #658 ten-server false-finding table re-scores as published.

`benchmark/mcp-servers/run.py` scans each frozen server with a candidate build
and records every published finding. Each finding carries a label in
`labels.csv`, judged against the server's own source. This test pins that the
committed table is exactly what `score.py` makes of the committed run and
labels, that no finding is unlabelled and no label is stale, and that the
false-finding rate stays under the 2% bar #643 sets.

It does not re-run the servers: third-party source is not vendored, and the
live run repeats for each release candidate.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "benchmark" / "mcp-servers"
RUN_OF_RECORD = ROOT / "results" / "2026-09-13-9946f80a"


def _score_module():
    spec = importlib.util.spec_from_file_location("mcp_servers_score", ROOT / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _labels() -> list[dict]:
    with (ROOT / "labels.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_selection_is_ten_pinned_servers() -> None:
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))
    accepted = selection["accepted"]

    assert len(accepted) == 10
    assert len({(server["repo"], server["route"]) for server in accepted}) == 10
    assert len({server["server"] for server in accepted}) == 10
    assert all(re.fullmatch(r"[0-9a-f]{40}", server["commit"]) for server in accepted)


def test_every_selected_server_was_run() -> None:
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))
    runs = json.loads((RUN_OF_RECORD / "runs.json").read_text(encoding="utf-8"))

    assert [record["server"] for record in runs["records"]] == [server["server"] for server in selection["accepted"]]
    assert all("error" not in record for record in runs["records"])


def test_the_run_of_record_scores_as_published() -> None:
    score = _score_module()
    runs = json.loads((RUN_OF_RECORD / "runs.json").read_text(encoding="utf-8"))
    with (RUN_OF_RECORD / "scores.csv").open(encoding="utf-8", newline="") as handle:
        published = list(csv.DictReader(handle))

    rows, problems = score.score(runs, _labels())

    assert problems == []
    assert [{key: str(value) for key, value in row.items()} for row in rows] == published


def test_the_false_finding_rate_is_under_the_bar() -> None:
    score = _score_module()
    runs = json.loads((RUN_OF_RECORD / "runs.json").read_text(encoding="utf-8"))
    rows, _problems = score.score(runs, _labels())
    total = rows[-1]

    assert total["findings"] > 0
    assert total["false"] / total["findings"] < score.BAR


def test_every_label_names_its_rule_and_source() -> None:
    for label in _labels():
        assert label["label"] in {"true", "false"}
        assert label["rule"] and label["note"], label
