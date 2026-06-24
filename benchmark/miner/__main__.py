"""CLI for the merged-PR history miner (maintainer tool).

Usage:

    python -m benchmark.miner mine --repo stripe/agent-toolkit --limit 50 \
        --workdir .miner-work --out benchmark/miner/results/<run>.csv

    python -m benchmark.miner evaluate --repo-path <clone> \
        --base <sha> --head <sha>

``mine`` needs the network (gh + git clone); ``evaluate`` is local-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmark.miner.candidates import (
    Candidate,
    ensure_clone,
    enumerate_merged_prs,
    resolve_base,
)
from benchmark.miner.evaluate import evaluate_pr
from benchmark.miner.rows import (
    STATUS_ERROR,
    MinedRow,
    read_jsonl,
    summarize,
    write_csv,
    write_jsonl,
)


def unresolved_candidate_row(candidate: Candidate) -> MinedRow:
    """Explicit error row for a candidate whose merge commit isn't local.

    Silently dropping these would bias row counts and the IE/trigger
    metrics on reruns with a cached clone; the corpus must show the gap.
    """

    return MinedRow(
        repo=candidate.repo,
        pr_number=candidate.pr_number,
        pr_url=candidate.pr_url,
        title=candidate.title,
        merged_at=candidate.merged_at,
        base_sha="",
        head_sha=candidate.merge_sha,
        status=STATUS_ERROR,
        notes="merge_commit_not_in_clone",
    )


def _cmd_mine(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    rows: list[MinedRow] = []
    for repo in args.repo:
        print(f"[miner] enumerating merged PRs: {repo} (limit {args.limit})", flush=True)
        candidates = enumerate_merged_prs(repo, limit=args.limit)
        print(f"[miner] cloning {repo} …", flush=True)
        clone = ensure_clone(repo, workdir)
        for candidate in candidates:
            if not resolve_base(clone, candidate):
                row = unresolved_candidate_row(candidate)
                rows.append(row)
                print(
                    f"[miner] {candidate.repo}#{candidate.pr_number}: {row.status} "
                    f"({row.notes})",
                    flush=True,
                )
                continue
            row = evaluate_pr(
                repo_path=clone,
                base_sha=candidate.base_sha,
                head_sha=candidate.merge_sha,
                repo=candidate.repo,
                pr_number=candidate.pr_number,
                pr_url=candidate.pr_url,
                title=candidate.title,
                merged_at=candidate.merged_at,
                force_run=args.force_run,
            )
            rows.append(row)
            print(
                f"[miner] {candidate.repo}#{candidate.pr_number}: {row.status}"
                + (f" head={row.head_decision}" if row.head_decision else "")
                + (f" check={row.check_decision}" if row.check_decision else ""),
                flush=True,
            )
    if args.out:
        write_csv(rows, Path(args.out))
        print(f"[miner] wrote {len(rows)} rows → {args.out}")
    if args.jsonl:
        write_jsonl(rows, Path(args.jsonl))
        print(f"[miner] wrote JSONL → {args.jsonl}")
    print(json.dumps(summarize(rows), indent=2))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    row = evaluate_pr(
        repo_path=Path(args.repo_path),
        base_sha=args.base,
        head_sha=args.head,
        repo=args.repo or "",
        pr_url=args.pr_url or "",
        force_run=args.force_run,
    )
    print(json.dumps(row.to_json(), indent=2, sort_keys=True))
    return 0


def _cmd_constructed(args: argparse.Namespace) -> int:
    import csv

    from benchmark.miner.constructed import build_constructed_corpus

    rows, labels, rationales = build_constructed_corpus()
    failed = [r.pr_url for r in rows if r.status != "evaluated"]
    write_jsonl(rows, Path(args.out))
    write_csv(rows, Path(args.out).with_suffix(".csv"))
    labels_path = Path(args.labels_out)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    with labels_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["pr_url", "label", "rationale"])
        for row in rows:
            writer.writerow([row.pr_url, labels[row.pr_url], rationales[row.pr_url]])
    print(f"[miner] wrote {len(rows)} constructed rows → {args.out} (+ labels → {labels_path})")
    if failed:
        print(f"[miner] ERROR: {len(failed)} fixture(s) did not evaluate: {failed}", file=sys.stderr)
        return 1
    return 0


def _cmd_labels(args: argparse.Namespace) -> int:
    from benchmark.miner.labels import build_worksheet, write_worksheet

    rows = read_jsonl(Path(args.results))
    worksheet = build_worksheet(rows)
    write_worksheet(worksheet, Path(args.out))
    print(f"[miner] wrote {len(worksheet)} worksheet rows → {args.out}")
    print("[miner] fill the `label` and `rationale` columns per benchmark/miner/LABELING.md")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    from benchmark.miner.labels import load_labels, render_matrix_markdown, score

    rows = read_jsonl(Path(args.results))
    try:
        labels = load_labels(Path(args.labels))
    except ValueError as exc:
        print(f"[miner] ERROR: {exc}", file=sys.stderr)
        return 1
    scored = score(rows, labels)

    # This command publishes the headline accuracy table — fail loud rather
    # than print a misleading partial matrix.
    unmatched = scored["unmatched_labels"]
    if unmatched and not args.allow_unmatched:
        print(
            f"[miner] ERROR: {len(unmatched)} labeled pr_url(s) not found in the "
            f"results (stale labels or wrong --results?): {', '.join(unmatched[:5])}"
            f"{' …' if len(unmatched) > 5 else ''}. Pass --allow-unmatched to override.",
            file=sys.stderr,
        )
        return 1
    if scored["labeled_rows"] == 0 and not args.allow_empty:
        print(
            "[miner] ERROR: no labeled rows to score — fill labels per "
            "benchmark/miner/LABELING.md (or pass --allow-empty).",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(scored, indent=2))
    print()
    print(render_matrix_markdown(scored))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark.miner", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mine = sub.add_parser("mine", help="Enumerate, clone, and evaluate merged PRs.")
    mine.add_argument("--repo", action="append", required=True, help="owner/name; repeatable.")
    mine.add_argument("--limit", type=int, default=50, help="Merged PRs per repo.")
    mine.add_argument("--workdir", default=".miner-work", help="Clone cache directory.")
    mine.add_argument("--out", default=None, help="CSV output path.")
    mine.add_argument("--jsonl", default=None, help="JSONL output path.")
    mine.add_argument(
        "--force-run",
        action="store_true",
        help="Evaluate every PR even when the trigger catalog says skip.",
    )
    mine.set_defaults(func=_cmd_mine)

    evaluate = sub.add_parser("evaluate", help="Evaluate one base/head pair in a local clone.")
    evaluate.add_argument("--repo-path", required=True)
    evaluate.add_argument("--base", required=True)
    evaluate.add_argument("--head", required=True)
    evaluate.add_argument("--repo", default="")
    evaluate.add_argument("--pr-url", default="")
    evaluate.add_argument("--force-run", action="store_true")
    evaluate.set_defaults(func=_cmd_evaluate)

    constructed = sub.add_parser(
        "constructed",
        help="Build the constructed-adversarial corpus from bundled fixtures (definitional labels).",
    )
    constructed.add_argument("--out", required=True, help="constructed corpus .jsonl to write.")
    constructed.add_argument("--labels-out", required=True, help="definitional labels CSV to write.")
    constructed.set_defaults(func=_cmd_constructed)

    labels = sub.add_parser(
        "labels", help="Generate a blank labeling worksheet from a results JSONL."
    )
    labels.add_argument("--results", required=True, help="Mined results .jsonl.")
    labels.add_argument("--out", required=True, help="Worksheet CSV to write.")
    labels.set_defaults(func=_cmd_labels)

    score_cmd = sub.add_parser(
        "score", help="Confusion matrix + accuracy metrics from an adjudicated labels file."
    )
    score_cmd.add_argument("--results", required=True, help="Mined results .jsonl.")
    score_cmd.add_argument("--labels", required=True, help="Adjudicated labels CSV.")
    score_cmd.add_argument(
        "--allow-unmatched",
        action="store_true",
        help="Do not fail when a labeled pr_url is absent from the results.",
    )
    score_cmd.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not fail when no rows are labeled yet.",
    )
    score_cmd.set_defaults(func=_cmd_score)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
