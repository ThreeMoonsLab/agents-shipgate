"""Score #659 host-config runs: row precision, widening recall, benign zero-row rate.

* A row is on the case file when its subject ends with the file path.
* Precision: rows on the file that name an expected change, over all rows on the file.
* Widening recall: expected `widening` changes named by a row, over expected widenings.
* Benign zero-row rate: supported cases whose file had no expected capability change
  and produced zero rows on the file, over such cases.
* Workflow files are matched at file level: the engine renders one row per
  workflow's authority, so any expected change is named by any row on that file.
  Workflow precision is therefore weaker evidence than the other kinds'.
* Incomparable, failed or unparsed runs are reported separately and count as
  missed for recall; they never raise precision or the benign rate.

`benchmark/cold-start/score.py` supplies the row matcher, so a row means the
same thing in both harnesses.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_COLD = Path(__file__).resolve().parent.parent / "cold-start" / "score.py"
_spec = importlib.util.spec_from_file_location("cold_start_score", _COLD)
assert _spec and _spec.loader
cold = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cold)

WORKFLOW_KIND = ".github/workflows/"


def score_record(record: dict[str, Any]) -> dict[str, Any]:
    """One case's outcome; the keys `scores.csv` and `replay.json` both publish."""

    payload = record.get("payload") or {}
    expectation = record.get("expectation") or {"scope": "not_derived", "changes": []}
    changes = [c for c in expectation.get("changes", []) if c["kind"] != "unsupported_setting"]
    widenings = [c for c in changes if c.get("semantic_direction") == "widening"]
    comparable = record.get("stop_point") == "comparison_comparable"
    path = record["path"]
    rows = payload.get("rows") or [] if comparable else []
    on_file = [row for row in rows if str(row.get("subject", "")).endswith(" " + path)]
    if record["kind"] == WORKFLOW_KIND:
        def named(_change: dict[str, Any]) -> bool:
            return bool(on_file)
        correct_rows = len(on_file) if changes else 0
    else:
        def named(change: dict[str, Any]) -> bool:
            return any(cold._row_matches(row, change) for row in on_file)
        correct_rows = sum(1 for row in on_file if any(cold._row_matches(row, c) for c in changes))
    benign_case = expectation.get("scope") == "supported" and not changes
    return {
        "case": record["case"],
        "kind": record["kind"],
        "stop_point": record.get("stop_point"),
        "scope": expectation.get("scope"),
        "expected_changes": len(changes),
        "expected_widenings": len(widenings),
        "rows_on_file": len(on_file),
        "correct_rows": correct_rows,
        "widenings_named": sum(1 for c in widenings if comparable and named(c)),
        "benign_case": benign_case,
        "benign_zero_row": benign_case and comparable and not on_file,
        "coding_agent_marked": record.get("coding_agent_marked"),
        "incomparable_reasons": ",".join(payload.get("incomparable_reasons") or []),
        "unclassified_keys": ",".join(expectation.get("unclassified_keys", [])),
    }


def main(runs_path: Path, csv_path: Path) -> int:
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    outcomes = [score_record(record) for record in runs["records"]]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(outcomes[0]))
        writer.writeheader()
        writer.writerows(outcomes)
    per_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for outcome in outcomes:
        tally = per_kind[outcome["kind"]]
        tally["cases"] += 1
        tally["comparable"] += outcome["stop_point"] == "comparison_comparable"
        for key in ("rows_on_file", "correct_rows", "expected_widenings", "widenings_named"):
            tally[key] += outcome[key]
        tally["benign_cases"] += outcome["benign_case"]
        tally["benign_zero_row"] += outcome["benign_zero_row"]
    total: dict[str, int] = defaultdict(int)
    for kind, tally in sorted(per_kind.items()):
        for key, value in tally.items():
            total[key] += value
        print(
            f"{kind:22} cases={tally['cases']} comparable={tally['comparable']} "
            f"rows={tally['rows_on_file']} correct={tally['correct_rows']} "
            f"widenings {tally['widenings_named']}/{tally['expected_widenings']} "
            f"benign {tally['benign_zero_row']}/{tally['benign_cases']}"
        )

    def ratio(numerator: int, denominator: int) -> str:
        return f"{numerator}/{denominator} = {numerator / denominator:.3f}" if denominator else f"{numerator}/0"

    print(
        f"TOTAL precision {ratio(total['correct_rows'], total['rows_on_file'])} | "
        f"widening recall {ratio(total['widenings_named'], total['expected_widenings'])} | "
        f"benign zero-row {ratio(total['benign_zero_row'], total['benign_cases'])} | "
        f"comparable {total['comparable']}/{total['cases']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
