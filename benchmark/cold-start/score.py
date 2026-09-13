"""Score cold-start runs against the expectations fixed before they ran (#660).

A case succeeds only when all of these hold:

* the comparison reported ``comparable`` — an incomparable, failed, timed-out
  or unparsed run is never a success, however empty its rows;
* the expectation's scope is ``supported`` — an unsupported or unclassified
  change is attempted and reported, and counts against the rate;
* every expected change on the selected file is named by a row, and every row
  on that file names an expected change;
* at most two comparison commands, within five minutes.

Rows on other files the same commit touched are counted and shown, not scored:
the expectation covers the selected file only.

Matching reads what the published row shows a reviewer — ``subject``,
``before``, ``after``, ``direction`` — because that is the output the
comparison has to get right. Where the row cannot name a value (an additional
path renders as ``additional_path``), the match is by kind and the limitation
is reported rather than papered over.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

MAX_COMMANDS = 2
MAX_SECONDS = 300
ABSENT = "—"


def _render(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _tokens(change: dict[str, Any]) -> tuple[str | None, str | None]:
    """What the row's before/after cells should show for this change."""

    kind, key = change["kind"], change["key"]
    if kind == "permission_rule":
        name = key.split(":", 1)[1]
    elif kind in {"mcp_server", "hook", "plugin"}:
        name = key
    elif kind == "additional_path":
        name = "additional_path"
    elif kind == "setting":
        if ":" in key:  # a list-valued setting entry, e.g. enabledMcpjsonServers:x
            name = key.split(":", 1)[1]
        else:
            before = None if change["before"] is None else _render(change["before"])
            after = None if change["after"] is None else _render(change["after"])
            return before, after
    else:
        return None, None
    direction = change["direction"]
    return (name if direction != "added" else None), (name if direction != "removed" else None)


def _row_matches(row: dict[str, Any], change: dict[str, Any]) -> bool:
    before, after = _tokens(change)
    if before is None and after is None:
        return False
    if change["direction"] == "added":
        return row["after"] == after and row["before"] == ABSENT
    if change["direction"] == "removed":
        return row["before"] == before and row["after"] == ABSENT
    # Changed: the same identity rendered on both sides, or — for a value that
    # is part of the grant's identity — one removed row and one added row.
    return (row["before"] == before and row["after"] == after) or (
        row["before"] == before and row["after"] == ABSENT
    ) or (row["before"] == ABSENT and row["after"] == after)


def score_record(record: dict[str, Any]) -> dict[str, Any]:
    expectation = record.get("expectation") or {"scope": "not_derived", "changes": []}
    payload = record.get("payload") or {}
    rows = payload.get("rows") or []
    suffix = " " + record["kind"]
    on_file = [row for row in rows if str(row.get("subject", "")).endswith(suffix)]
    other = [row for row in rows if row not in on_file]
    changes = [c for c in expectation.get("changes", []) if c["kind"] != "unsupported_setting"]
    missing = [c for c in changes if not any(_row_matches(row, c) for row in on_file)]
    unexpected = [row for row in on_file if not any(_row_matches(row, c) for c in changes)]
    commands = len(record.get("commands") or [])
    seconds = record.get("comparison_seconds")
    comparable = record.get("stop_point") == "comparison_comparable"
    success = (
        comparable
        and expectation.get("scope") == "supported"
        and not missing
        and not unexpected
        and commands <= MAX_COMMANDS
        and seconds is not None
        and seconds <= MAX_SECONDS
    )
    return {
        "case": record["case"], "repo": record["repo"], "kind": record["kind"],
        "base_sha": record["base_sha"], "head_sha": record["head_sha"],
        "scope": expectation.get("scope"), "stop_point": record.get("stop_point"),
        "expected_changes": len(changes), "rows_on_file": len(on_file), "rows_other_files": len(other),
        "missing": len(missing), "unexpected": len(unexpected),
        "commands": commands, "comparison_seconds": seconds,
        "install_seconds": record.get("install_seconds"), "clone_seconds": record.get("clone_seconds"),
        "changed_case": bool(changes), "success": success,
        "missing_detail": "; ".join(f"{c['kind']} {c['key']} {c['direction']}" for c in missing),
        "unexpected_detail": "; ".join(f"{r['before']} -> {r['after']} ({r['direction']})" for r in unexpected),
        "unclassified_keys": ",".join(expectation.get("unclassified_keys", [])),
        "incomparable_reasons": ",".join(payload.get("incomparable_reasons") or []),
    }


def main(runs_path: Path, csv_path: Path) -> int:
    runs = json.loads(runs_path.read_text())
    scored = [score_record(record) for record in runs["records"]]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(scored[0]))
        writer.writeheader()
        writer.writerows(scored)
    changed = [s for s in scored if s["changed_case"]]
    unchanged = [s for s in scored if not s["changed_case"]]
    print(f"attempted={len(scored)} success={sum(s['success'] for s in scored)} "
          f"(changed {sum(s['success'] for s in changed)}/{len(changed)}, "
          f"no-change {sum(s['success'] for s in unchanged)}/{len(unchanged)})")
    for key in ("stop_point", "scope"):
        counts: dict[str, int] = {}
        for s in scored:
            counts[str(s[key])] = counts.get(str(s[key]), 0) + 1
        print(f"  by {key}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
