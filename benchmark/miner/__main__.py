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

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
