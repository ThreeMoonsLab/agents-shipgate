"""Run one engine's ``diff --application`` over an application review corpus.

Usage:
    python benchmark/application-q2/run.py --engine ./shipgate \
        --corpus benchmark/application-q2/development.json \
        --clones ~/.cache/shipgate-q2/clones --out /tmp/q2-out [--fetch] [--jobs 4]

``--fetch`` clones a missing repository with full objects and fetches each
member's pinned commits; it is the only step that touches the network. The
diff itself never fetches: every run is ``diff --application --base <merge
base> --head <head> --json`` in the clone's root with no ``--scope``, so the
scope is derived from the change, as it is for a new user. One
``<slug>.json`` (stdout), ``<slug>.err`` (stderr tail) and a line in
``runs.tsv`` (slug, exit code, seconds) are written per member.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DIFF_TIMEOUT_SECONDS = 900


def _git(clone: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True, text=True, check=check
    )


def _has_commit(clone: Path, sha: str) -> bool:
    return _git(clone, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def ensure_clone(member: dict, clones: Path) -> str | None:
    """Full-object clone holding both pinned commits; a reason when it cannot."""

    clone = clones / member["slug"]
    if not (clone / ".git").is_dir():
        clone.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "-q", "--no-checkout", f"https://github.com/{member['repository']}.git", str(clone)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return f"clone failed: {result.stderr.strip()[-300:]}"
    if _git(clone, "config", "--get", "remote.origin.promisor").stdout.strip() == "true":
        # A blobless or treeless clone makes `diff` refuse with objects_missing.
        _git(clone, "fetch", "-q", "--refetch", "--no-filter", "origin")
    _git(clone, "fetch", "-q", "origin", f"pull/{member['number']}/head")
    for sha in (member["merge_base"], member["head"]):
        if not _has_commit(clone, sha):
            _git(clone, "fetch", "-q", "origin", sha)
        if not _has_commit(clone, sha):
            return f"commit {sha} not obtainable"
    return None


def run_member(member: dict, *, engine: Path, clones: Path, out: Path) -> tuple[str, str, float]:
    slug = member["slug"]
    clone = clones / slug
    started = time.monotonic()
    command = [
        str(engine), "diff", "--application",
        "--base", member["merge_base"], "--head", member["head"], "--json",
    ]
    try:
        result = subprocess.run(
            command, cwd=clone, capture_output=True, text=True, timeout=DIFF_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return slug, "timeout", round(time.monotonic() - started, 1)
    (out / f"{slug}.json").write_text(result.stdout, encoding="utf-8")
    (out / f"{slug}.err").write_text(result.stderr[-4000:], encoding="utf-8")
    return slug, str(result.returncode), round(time.monotonic() - started, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", type=Path, required=True, help="Launcher or console script to measure.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--clones", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Clone and fetch missing commits first.")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    members = corpus["members"]
    args.out.mkdir(parents=True, exist_ok=True)
    runnable = []
    for member in members:
        reason = ensure_clone(member, args.clones) if args.fetch else None
        clone = args.clones / member["slug"]
        if reason is None and not all(_has_commit(clone, sha) for sha in (member["merge_base"], member["head"])):
            reason = "pinned commits absent; rerun with --fetch"
        if reason is not None:
            print(f"{member['slug']}\tunavailable\t{reason}", file=sys.stderr)
            continue
        runnable.append(member)
    rows = []
    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        for slug, code, seconds in pool.map(
            lambda m: run_member(m, engine=args.engine.resolve(), clones=args.clones, out=args.out),
            runnable,
        ):
            rows.append(f"{slug}\t{code}\t{seconds}")
            print(rows[-1], flush=True)
    (args.out / "runs.tsv").write_text("\n".join(sorted(rows)) + "\n", encoding="utf-8")
    return 0 if len(runnable) == len(members) else 1


if __name__ == "__main__":
    raise SystemExit(main())
