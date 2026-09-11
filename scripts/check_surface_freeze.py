#!/usr/bin/env python3
"""Refuse new public surface while the adoption freeze is on (#654).

The project has shipped surface faster than it has proven the surface it
already has: 144 check IDs, 27 schema families, and — measured over the 30
days to 2026-09-10 — 130 merged pull requests adding 372,000 lines, with 27
of 63 open issues filed as byproducts of reviewing that work. None of it was
reaching users, because the published build was two months old.

So until the M2 exit is recorded, three kinds of addition are refused:

* a new check ID,
* a new versioned schema family,
* a new input adapter.

Everything else is unaffected — bug fixes, readers and renderers for
families that already exist, and the milestone's own items. The escape
hatch is a `freeze-exception` label, which makes an addition a decision
somebody made rather than one that happened.

The line budget is reported, never enforced: a number nobody agreed to
should not block a merge.

Usage:  check_surface_freeze.py --base <ref> [--labels a,b] [--budget 800]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXCEPTION_LABEL = "freeze-exception"

#: Paths whose size says nothing about how much a reviewer must read.
GENERATED = re.compile(
    r"^(samples/.*/expected/|docs/.*schema.*\.json$|llms-full\.txt$|"
    r"docs/checks\.json$|.*\.lock$|CHANGELOG\.md$)"
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout


def _check_ids(ref: str | None) -> set[str]:
    """Every check ID, read from the generated catalog."""

    try:
        raw = _git("show", f"{ref}:docs/checks.json") if ref else (
            REPO_ROOT / "docs" / "checks.json"
        ).read_text(encoding="utf-8")
    except subprocess.CalledProcessError:
        return set()
    payload = json.loads(raw)
    checks = payload.get("checks") if isinstance(payload, dict) else payload
    ids: set[str] = set()
    for entry in checks or []:
        identifier = entry.get("id") if isinstance(entry, dict) else entry
        if identifier:
            ids.add(str(identifier))
    return ids


def _schema_families(ref: str | None) -> set[str]:
    """Family names, with the version suffix stripped."""

    if ref:
        listing = _git("ls-tree", "-r", "--name-only", ref, "docs/").splitlines()
    else:
        listing = [
            str(path.relative_to(REPO_ROOT))
            for path in (REPO_ROOT / "docs").rglob("*.json")
        ]
    families: set[str] = set()
    for name in listing:
        base = Path(name).name
        if "schema" not in base or not base.endswith(".json"):
            continue
        families.add(re.sub(r"\.v\d+\.json$|\.json$", "", base))
    return families


def _adapters(ref: str | None) -> set[str]:
    root = "src/agents_shipgate/inputs"
    if ref:
        listing = _git("ls-tree", "-r", "--name-only", ref, f"{root}/").splitlines()
    else:
        listing = [
            str(path.relative_to(REPO_ROOT))
            for path in (REPO_ROOT / root).rglob("*.py")
        ]
    return {
        Path(name).stem
        for name in listing
        if name.endswith(".py") and not Path(name).name.startswith("_")
    }


def _added_lines(base: str) -> tuple[int, int]:
    """(reviewable, generated) added-line counts."""

    reviewable = generated = 0
    for line in _git("diff", "--numstat", f"{base}...HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "-":
            continue
        added, _removed, path = int(parts[0]), parts[1], parts[2]
        if GENERATED.match(path):
            generated += added
        else:
            reviewable += added
    return reviewable, generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--labels", default="")
    parser.add_argument("--budget", type=int, default=800)
    args = parser.parse_args(argv)

    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    excepted = EXCEPTION_LABEL in labels

    additions: list[str] = []
    for name, before, after in (
        ("check ID", _check_ids(args.base), _check_ids(None)),
        ("schema family", _schema_families(args.base), _schema_families(None)),
        ("input adapter", _adapters(args.base), _adapters(None)),
    ):
        for item in sorted(after - before):
            additions.append(f"new {name}: {item}")

    reviewable, generated = _added_lines(args.base)
    print(f"Added lines: {reviewable} reviewable, {generated} generated.")
    if reviewable > args.budget:
        print(
            f"note: over the {args.budget}-line review budget. Not a failure — "
            "split it, or say in the PR body why this one is worth reading whole."
        )

    if not additions:
        print("No new public surface. Freeze respected.")
        return 0
    for addition in additions:
        print(addition)
    if excepted:
        print(f"Allowed: the {EXCEPTION_LABEL!r} label is present.")
        return 0
    print(
        "\nThe adoption freeze is on (#654): no new check ID, schema family or "
        "input adapter until the M2 exit is recorded. Extend something that "
        "exists, or add the 'freeze-exception' label and say in the PR body "
        "which headline metric the new surface moves."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
