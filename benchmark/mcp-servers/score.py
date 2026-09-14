"""Score the #658 ten-server table: every published finding against its label.

A finding is identified by server, check, tool and source path. Every finding
in `runs.json` must have exactly one row in `labels.csv`, and every label must
match a finding; a missing or stale label fails rather than being guessed. A
label is `true` or `false`, with the rule that decided it and a note pointing
at the source that was read.

    python benchmark/mcp-servers/score.py benchmark/mcp-servers/results/<run>/runs.json benchmark/mcp-servers/labels.csv benchmark/mcp-servers/results/<run>/scores.csv
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BAR = 0.02
LABELS = {"true", "false"}


def key(server: str, row: dict) -> tuple[str, str, str, str]:
    return (server, row["check_id"], row["tool"], row["source_path"])


def score(runs: dict, labels: list[dict]) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    by_key: dict[tuple[str, str, str, str], dict] = {}
    for label in labels:
        k = (label["server"], label["check_id"], label["tool"], label["source_path"])
        if k in by_key:
            problems.append(f"duplicate label {k}")
        if label["label"] not in LABELS:
            problems.append(f"label {label['label']!r} is not true or false: {k}")
        by_key[k] = label
    seen: set[tuple[str, str, str, str]] = set()
    rows = []
    for record in runs["records"]:
        server = record["server"]
        if "error" in record:
            problems.append(f"{server}: no report ({record['error'][:120]})")
            continue
        true_count = false_count = 0
        for finding in record["findings"]:
            k = key(server, finding)
            if k in seen:
                problems.append(f"two findings share one identity {k}")
            seen.add(k)
            label = by_key.get(k)
            if label is None:
                problems.append(f"unlabelled finding {k}")
                continue
            true_count += label["label"] == "true"
            false_count += label["label"] == "false"
        rows.append({"server": server, "tools": record["tools"], "findings": len(record["findings"]), "true": true_count, "false": false_count})
    for k in sorted(set(by_key) - seen):
        problems.append(f"stale label matches no finding {k}")
    findings = sum(row["findings"] for row in rows)
    false_total = sum(row["false"] for row in rows)
    rows.append({
        "server": "TOTAL",
        "tools": sum(row["tools"] for row in rows),
        "findings": findings,
        "true": sum(row["true"] for row in rows),
        "false": false_total,
    })
    return rows, problems


def main(runs_path: Path, labels_path: Path, scores_path: Path) -> int:
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    with labels_path.open(encoding="utf-8", newline="") as handle:
        labels = list(csv.DictReader(handle))
    rows, problems = score(runs, labels)
    with scores_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["server", "tools", "findings", "true", "false"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    total = rows[-1]
    rate = total["false"] / total["findings"] if total["findings"] else 0.0
    print(f"findings {total['findings']} true {total['true']} false {total['false']} false-finding rate {rate:.3%} (bar < {BAR:.0%})")
    for problem in problems:
        print("PROBLEM:", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(*(Path(arg) for arg in sys.argv[1:4])))
