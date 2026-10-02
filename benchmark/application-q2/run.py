"""Run one engine's ``diff --application`` over an application review corpus.

Usage:
    python benchmark/application-q2/run.py --engine ./shipgate \
        --corpus benchmark/application-q2/development.json \
        --clones ~/.cache/shipgate-q2/repos --out ~/.cache/shipgate-q2/out-<build> \
        [--fetch] [--jobs 4]

Clones are full-object, one per repository (``<owner>__<repo>``), and each
member's pins are kept under ``refs/q2/<slug>/base`` and ``refs/q2/<slug>/head``
so garbage collection cannot drop a head no branch reaches. ``--fetch`` is the
only step that touches the network: it clones a missing repository and fetches
a member's pins when they are not already complete. Every other git call runs
with lazy fetching disabled, and the diff itself never fetches: each run is
``diff --application --base <merge base> --head <head> --json`` in the clone's
root with no ``--scope``, so the scope is derived from the change, as it is for
a new user.

Per member it writes ``<slug>.json`` (the engine's stdout, only when it exits
0), ``<slug>.err`` (its stderr tail) and a row in ``runs.json``: ``status`` is
``ok``, ``refused`` (the engine exited non-zero, e.g. ``objects_missing``),
``timeout`` or ``unavailable`` (a pin could not be obtained), with the exit
code, seconds and reason. Outputs a member already had in ``--out``, and the
previous ``runs.json``, are removed first, so a stale answer can never stand in
for this run's, and a run that stops early leaves no ``runs.json`` for
``summarize.py`` to read. The exit code is 0 only when every member answered
with ``ok``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DIFF_TIMEOUT_SECONDS = 900
NETWORK_TIMEOUT_SECONDS = 600
#: Never prompt for credentials (a deleted or private repository would block on
#: a terminal prompt) and never fetch lazily while probing.
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}


def _git(clone: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **_GIT_ENV},
    )


def clone_dir(clones: Path, repository: str) -> Path:
    return clones / repository.replace("/", "__")


def complete(clone: Path, sha: str | None) -> bool:
    """The commit and every object of its tree are present, fetched lazily or not."""

    if not sha:
        return False
    return _git(clone, "rev-list", "--objects", "--no-walk", "--quiet", sha).returncode == 0


def _pin(clone: Path, slug: str, side: str, sha: str) -> None:
    _git(clone, "update-ref", f"refs/q2/{slug}/{side}", sha)


def prepare_repository(repository: str, members: list[dict], clones: Path, fetch: bool) -> dict[str, str]:
    """Make every member's pins complete in its repository's clone.

    Returns ``{slug: reason}`` for each member whose pins could not be made
    complete; every other member is runnable.
    """

    clone = clone_dir(clones, repository)
    reasons: dict[str, str] = {}
    if not (clone / ".git").is_dir():
        if not fetch:
            return {member["slug"]: f"no clone of {repository}; rerun with --fetch" for member in members}
        clone.parent.mkdir(parents=True, exist_ok=True)
        try:
            cloned = subprocess.run(
                ["git", "clone", "-q", "--no-checkout", "--no-tags",
                 f"https://github.com/{repository}.git", str(clone)],
                capture_output=True, text=True, timeout=NETWORK_TIMEOUT_SECONDS,
                env={**os.environ, **_GIT_ENV},
            )
        except subprocess.TimeoutExpired:
            return {member["slug"]: f"clone of {repository} timed out" for member in members}
        if cloned.returncode != 0:
            detail = cloned.stderr.strip()[-300:] or f"exit {cloned.returncode}"
            return {member["slug"]: f"clone of {repository} failed: {detail}" for member in members}
    for member in members:
        slug, base, head = member["slug"], member.get("merge_base"), member.get("head")
        if not base or not head:
            reasons[slug] = member.get("unavailable_reason") or "the member has no pinned commits"
            continue
        try:
            reason = _prepare_member(clone, member, fetch)
        except subprocess.TimeoutExpired:
            reason = "a git call timed out while checking or pinning the commits"
        if reason is not None:
            reasons[slug] = reason
    return reasons


def _prepare_member(clone: Path, member: dict, fetch: bool) -> str | None:
    """Why ``member``'s pins are not complete in ``clone``, or None once pinned."""

    slug, base, head = member["slug"], member["merge_base"], member["head"]
    if not (complete(clone, base) and complete(clone, head)) and fetch:
        # Explicit refspecs, so a head no branch reaches is fetched, and
        # ``--refetch --no-filter`` so a blobless (promisor) clone receives
        # every object of both pins rather than inheriting its filter.
        try:
            fetched = _git(
                clone, "fetch", "-q", "--no-tags", "--refetch", "--no-filter", "origin",
                f"+refs/pull/{member['number']}/head:refs/q2/{slug}/fetched-head", base, head,
                timeout=NETWORK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return "fetching the pinned commits timed out"
        if fetched.returncode != 0 and not (complete(clone, base) and complete(clone, head)):
            return "fetching the pinned commits failed: " + (
                fetched.stderr.strip()[-300:] or f"exit {fetched.returncode}"
            )
    missing = [label for label, sha in (("merge base", base), ("head", head)) if not complete(clone, sha)]
    if missing:
        return " and ".join(missing) + " not complete in the clone" + (
            "" if fetch else "; rerun with --fetch"
        )
    _pin(clone, slug, "base", base)
    _pin(clone, slug, "head", head)
    return None


def run_member(member: dict, *, engine: list[str], clones: Path, out: Path) -> dict:
    slug = member["slug"]
    clone = clone_dir(clones, member["repository"])
    started = time.monotonic()
    command = [
        *engine, "diff", "--application",
        "--base", member["merge_base"], "--head", member["head"], "--json",
    ]
    try:
        result = subprocess.run(
            command, cwd=clone, capture_output=True, text=True,
            timeout=DIFF_TIMEOUT_SECONDS, env={**os.environ, **_GIT_ENV},
        )
    except subprocess.TimeoutExpired:
        return {"slug": slug, "status": "timeout", "exit_code": None,
                "seconds": round(time.monotonic() - started, 1),
                "reason": f"diff exceeded {DIFF_TIMEOUT_SECONDS} s"}
    (out / f"{slug}.err").write_text(result.stderr[-4000:], encoding="utf-8")
    if result.returncode == 0:
        (out / f"{slug}.json").write_text(result.stdout, encoding="utf-8")
    return {
        "slug": slug,
        "status": "ok" if result.returncode == 0 else "refused",
        "exit_code": result.returncode,
        "seconds": round(time.monotonic() - started, 1),
        "reason": None if result.returncode == 0 else (result.stderr.strip()[-300:] or None),
    }


def resolve_engine(spelling: str) -> list[str]:
    """The engine as an argv prefix: a path, or a console script on PATH."""

    path = Path(spelling).expanduser()
    if os.sep in spelling or path.exists():
        resolved = path.resolve()
        if not resolved.exists():
            raise SystemExit(f"engine not found: {spelling}")
        return [str(resolved)]
    found = shutil.which(spelling)
    if found is None:
        raise SystemExit(f"engine not found on PATH: {spelling}")
    return [found]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", required=True, help="Launcher path or console script to measure.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--clones", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Clone and fetch missing pins first.")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)

    engine = resolve_engine(args.engine)
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    members = corpus["members"]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "runs.json").unlink(missing_ok=True)
    for member in members:
        for suffix in (".json", ".err"):
            (args.out / f"{member['slug']}{suffix}").unlink(missing_ok=True)

    by_repository: dict[str, list[dict]] = defaultdict(list)
    for member in members:
        by_repository[member["repository"]].append(member)
    records: dict[str, dict] = {}
    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        prepared = pool.map(
            lambda item: prepare_repository(item[0], item[1], args.clones, args.fetch),
            sorted(by_repository.items()),
        )
        for reasons in prepared:
            for slug, reason in reasons.items():
                records[slug] = {"slug": slug, "status": "unavailable", "exit_code": None,
                                 "seconds": 0.0, "reason": reason}
        runnable = [member for member in members if member["slug"] not in records]
        for record in pool.map(
            lambda member: run_member(member, engine=engine, clones=args.clones, out=args.out),
            runnable,
        ):
            records[record["slug"]] = record
            print(f"{record['slug']}\t{record['status']}\t{record['seconds']}", flush=True)
    ordered = [records[member["slug"]] for member in members]
    (args.out / "runs.json").write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    failed = [record["slug"] for record in ordered if record["status"] != "ok"]
    if failed:
        print(f"{len(failed)} of {len(ordered)} members did not answer: {', '.join(failed)}", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
