"""Count what a corpus run produced, and join it with the hand-scored levels.

Usage:
    python benchmark/application-q2/summarize.py \
        --corpus benchmark/application-q2/development.json --out /tmp/q2-out \
        [--scores benchmark/application-q2/results/<ledger>.scores.json] [--markdown]

The counts are mechanical: comparison status, rows by change kind, and the row
sides that name an outbound call or a hop the reach reader could not follow.
The Q levels are not; they come from ``--scores``, written by hand against the
source (see the README's scoring protocol), and are only printed beside the
counts, never derived from them.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

CHANGE_KINDS = ("added", "removed", "changed", "not_established")


def summarize_member(payload: dict | None) -> dict:
    if payload is None:
        return {"status": "no_output", "rows": dict.fromkeys(CHANGE_KINDS, 0),
                "sides_with_calls": 0, "sides_with_unresolved_hop": 0}
    rows = collections.Counter(row.get("change") for row in payload.get("rows", []))
    sides_with_calls = sides_with_hop = 0
    for row in payload.get("rows", []):
        for side in (row.get("before"), row.get("after")):
            reach = (side or {}).get("reach") or {}
            sides_with_calls += bool(reach.get("calls"))
            sides_with_hop += bool(reach.get("limits"))
    return {
        "status": payload.get("comparison_status") or "refused",
        "rows": {kind: rows.get(kind, 0) for kind in CHANGE_KINDS},
        "sides_with_calls": sides_with_calls,
        "sides_with_unresolved_hop": sides_with_hop,
    }


def load_run(corpus: dict, out: Path) -> dict[str, dict]:
    members = {}
    for member in corpus["members"]:
        path = out / f"{member['slug']}.json"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        try:
            payload = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            payload = None
        members[member["slug"]] = summarize_member(payload)
    return members


def totals(members: dict[str, dict], scores: dict[str, dict]) -> dict:
    status = collections.Counter(item["status"] for item in members.values())
    rows = collections.Counter()
    for item in members.values():
        rows.update(item["rows"])
    level = collections.Counter()
    for slug in members:
        score = scores.get(slug, {})
        for name in ("q0", "q1", "q2"):
            level[name] += score.get(name) is True
    return {
        "members": len(members),
        "status": dict(sorted(status.items())),
        "members_with_rows": sum(1 for item in members.values() if sum(item["rows"].values())),
        "rows": {kind: rows.get(kind, 0) for kind in CHANGE_KINDS},
        "sides_with_calls": sum(item["sides_with_calls"] for item in members.values()),
        "sides_with_unresolved_hop": sum(item["sides_with_unresolved_hop"] for item in members.values()),
        "scored": sum(1 for slug in members if slug in scores),
        "q0": level["q0"],
        "q1": level["q1"],
        "q2": level["q2"],
    }


def _mark(value: object) -> str:
    return {True: "yes", False: "no"}.get(value, "—")


def markdown(corpus: dict, members: dict[str, dict], scores: dict[str, dict]) -> str:
    lines = [
        "| Pull request | Status | +added | −removed | ~changed | ?not established | calls | hops | Q0 | Q1 | Q2 | Rationale |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for member in corpus["members"]:
        item = members[member["slug"]]
        score = scores.get(member["slug"], {})
        rows = item["rows"]
        lines.append(
            f"| [{member['repository']}#{member['number']}]({member['url']}) | `{item['status']}` "
            f"| {rows['added']} | {rows['removed']} | {rows['changed']} | {rows['not_established']} "
            f"| {item['sides_with_calls']} | {item['sides_with_unresolved_hop']} "
            f"| {_mark(score.get('q0'))} | {_mark(score.get('q1'))} | {_mark(score.get('q2'))} "
            f"| {score.get('rationale', '')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="A run.py output directory.")
    parser.add_argument("--scores", type=Path, help="Hand scores: {slug: {q0, q1, q2, rationale}}.")
    parser.add_argument("--markdown", action="store_true", help="Print the per-member ledger table.")
    args = parser.parse_args(argv)
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    scores = json.loads(args.scores.read_text(encoding="utf-8")) if args.scores else {}
    unknown = sorted(set(scores) - {member["slug"] for member in corpus["members"]})
    if unknown:
        print(f"scores name members outside the corpus: {unknown}", file=sys.stderr)
        return 2
    members = load_run(corpus, args.out)
    if args.markdown:
        sys.stdout.write(markdown(corpus, members, scores))
    else:
        print(json.dumps(totals(members, scores), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
