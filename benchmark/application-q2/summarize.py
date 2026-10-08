"""Count what a corpus run produced, and join it with the hand-scored levels.

Usage:
    python benchmark/application-q2/summarize.py \
        --corpus benchmark/application-q2/development.json --out <run.py --out dir> \
        [--scores benchmark/application-q2/results/<ledger>.scores.json] [--markdown]

The counts are mechanical: each member's run status (``runs.json``), the
comparison status, rows by change kind, and the row sides that name an
outbound call or a hop the reach reader could not follow. The Q levels are
not; they come from ``--scores``, written by hand against the source (see the
README's scoring protocol), and are only joined, never derived. A score names
the ``answer_id`` of the output it judged: the digest of the answer without
the engine's own identity and without its ``summary``, a reading of the rows.
When this run produced a different answer, the score is stale, listed as such and not counted, so a level cannot carry over
onto an output nobody read.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

CHANGE_KINDS = ("added", "removed", "changed", "not_established")
#: What identifies the engine rather than its answer. ``comparison_id`` digests
#: these too (version, Python, platform, build), so the same answer from
#: another machine or release has another ``comparison_id``. A multi-scope
#: answer repeats the engine block inside each of its ``comparisons``.
ENGINE_FIELDS = ("engine", "comparison_id")
#: A reading of the rows, derived from them (#914). It restates no fact the
#: rows lack, so a hand score that judged the rows keeps judging the answer
#: whether or not the build that produced it added or reworded this block.
PRESENTATION_FIELDS = ("summary",)
NOT_THE_ANSWER = (*ENGINE_FIELDS, *PRESENTATION_FIELDS)


def _without_engine(node: dict) -> dict:
    return {key: value for key, value in node.items() if key not in NOT_THE_ANSWER}


def answer_id(payload: dict) -> str:
    """The digest of an answer: the engine's output without its own identity or its summary."""

    answer = _without_engine(payload)
    if isinstance(answer.get("comparisons"), list):
        answer["comparisons"] = [
            _without_engine(part) if isinstance(part, dict) else part for part in answer["comparisons"]
        ]
    encoded = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def summarize_member(record: dict, payload: dict | None) -> dict:
    rows = collections.Counter()
    sides_with_calls = sides_with_hop = 0
    for row in (payload or {}).get("rows", []):
        rows[row.get("change")] += 1
        for side in (row.get("before"), row.get("after")):
            reach = (side or {}).get("reach") or {}
            sides_with_calls += bool(reach.get("calls"))
            sides_with_hop += bool(reach.get("limits"))
    if record.get("status") == "ok" and payload is not None:
        status = payload.get("comparison_status") or "no_status"
    else:
        status = record.get("status") or "no_record"
    return {
        "status": status,
        "answer_id": answer_id(payload) if payload is not None else None,
        "rows": {kind: rows.get(kind, 0) for kind in CHANGE_KINDS},
        "sides_with_calls": sides_with_calls,
        "sides_with_unresolved_hop": sides_with_hop,
    }


def load_run(corpus: dict, out: Path) -> dict[str, dict]:
    runs = out / "runs.json"
    if not runs.is_file():
        raise SystemExit(f"{out} holds no runs.json; it is not a run.py output directory")
    records = {record["slug"]: record for record in json.loads(runs.read_text(encoding="utf-8"))}
    members = {}
    for member in corpus["members"]:
        record = records.get(member["slug"], {})
        payload = None
        if record.get("status") == "ok":
            path = out / f"{member['slug']}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = {**record, "status": "unreadable_output"}
        members[member["slug"]] = summarize_member(record, payload)
    return members


def current_scores(members: dict[str, dict], scores: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """The scores that judged this run's answer, and the slugs whose score did not."""

    current: dict[str, dict] = {}
    stale: list[str] = []
    for slug, score in scores.items():
        if score.get("answer_id") == members.get(slug, {}).get("answer_id"):
            current[slug] = score
        else:
            stale.append(slug)
    return current, sorted(stale)


def totals(members: dict[str, dict], scores: dict[str, dict]) -> dict:
    current, stale = current_scores(members, scores)
    status = collections.Counter(item["status"] for item in members.values())
    rows = collections.Counter()
    for item in members.values():
        rows.update(item["rows"])
    return {
        "members": len(members),
        "status": dict(sorted(status.items())),
        "members_with_rows": sum(1 for item in members.values() if sum(item["rows"].values())),
        "rows": {kind: rows.get(kind, 0) for kind in CHANGE_KINDS},
        "sides_with_calls": sum(item["sides_with_calls"] for item in members.values()),
        "sides_with_unresolved_hop": sum(item["sides_with_unresolved_hop"] for item in members.values()),
        "scored": len(current),
        "stale_scores": stale,
        "q0": sum(score["q0"] is True for score in current.values()),
        "q1": sum(score["q1"] is True for score in current.values()),
        "q2": sum(score["q2"] is True for score in current.values()),
    }


def _mark(value: object) -> str:
    return {True: "yes", False: "no"}.get(value, "—")


def markdown(corpus: dict, members: dict[str, dict], scores: dict[str, dict]) -> str:
    current, _ = current_scores(members, scores)
    lines = [
        "| Pull request | Status | +added | −removed | ~changed | ?not established | calls | hops | Q0 | Q1 | Q2 | Rationale |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for member in corpus["members"]:
        item = members[member["slug"]]
        score = current.get(member["slug"], {})
        rows = item["rows"]
        lines.append(
            f"| [{member['repository']}#{member['number']}]({member['url']}) | `{item['status']}` "
            f"| {rows['added']} | {rows['removed']} | {rows['changed']} | {rows['not_established']} "
            f"| {item['sides_with_calls']} | {item['sides_with_unresolved_hop']} "
            f"| {_mark(score.get('q0'))} | {_mark(score.get('q1'))} | {_mark(score.get('q2'))} "
            f"| {score.get('rationale', 'not scored for this answer')} |"
        )
    return "\n".join(lines) + "\n"


def load_scores(path: Path | None, corpus: dict) -> dict[str, dict]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("corpus") != corpus.get("corpus"):
        raise SystemExit(
            f"{path} scores corpus {document.get('corpus')!r}, not {corpus.get('corpus')!r}"
        )
    scores = document["scores"]
    unknown = sorted(set(scores) - {member["slug"] for member in corpus["members"]})
    if unknown:
        raise SystemExit(f"scores name members outside the corpus: {unknown}")
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="A run.py output directory.")
    parser.add_argument("--scores", type=Path, help="A results/*.scores.json for this corpus.")
    parser.add_argument("--markdown", action="store_true", help="Print the per-member ledger table.")
    args = parser.parse_args(argv)
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    scores = load_scores(args.scores, corpus)
    members = load_run(corpus, args.out)
    if args.markdown:
        sys.stdout.write(markdown(corpus, members, scores))
    else:
        print(json.dumps(totals(members, scores), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
