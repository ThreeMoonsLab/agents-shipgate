"""Candidate enumeration: merged PRs of a GitHub repo, via ``gh`` + ``git``.

The only network-touching module in the miner. ``enumerate_merged_prs``
asks ``gh`` for merged PRs with their merge commits; ``ensure_clone``
makes a full local clone (full, not partial — evaluation must be able to
run offline afterwards). Everything downstream is local-only
(:mod:`benchmark.miner.evaluate`).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

_NETWORK_TIMEOUT = 600


@dataclass
class Candidate:
    repo: str
    pr_number: int
    pr_url: str
    title: str
    merged_at: str
    merge_sha: str
    base_sha: str = ""


def enumerate_merged_prs(repo: str, *, limit: int = 50) -> list[Candidate]:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,mergedAt,mergeCommit",
        ],
        capture_output=True,
        text=True,
        timeout=_NETWORK_TIMEOUT,
        check=True,
    )
    candidates: list[Candidate] = []
    for item in json.loads(result.stdout):
        merge_commit = item.get("mergeCommit") or {}
        sha = str(merge_commit.get("oid") or "")
        if not sha:
            continue
        candidates.append(
            Candidate(
                repo=repo,
                pr_number=int(item.get("number") or 0),
                pr_url=str(item.get("url") or ""),
                title=str(item.get("title") or "")[:160],
                merged_at=str(item.get("mergedAt") or ""),
                merge_sha=sha,
            )
        )
    return candidates


def ensure_clone(repo: str, workdir: Path) -> Path:
    destination = workdir / repo.replace("/", "__")
    if (destination / ".git").exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-tags",
            f"https://github.com/{repo}.git",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=_NETWORK_TIMEOUT,
        check=True,
    )
    return destination


def resolve_base(clone: Path, candidate: Candidate) -> bool:
    """Fill ``candidate.base_sha`` with the merge commit's first parent.

    Works for both merge commits and squash merges: ``<merge>^1`` is the
    mainline state immediately before the PR landed, so
    ``base..merge`` is the PR's net change. Returns False when the commit
    is unknown locally or has no parent (initial commit).
    """

    probe = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--verify", "--quiet", f"{candidate.merge_sha}^1"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    sha = probe.stdout.strip()
    if probe.returncode != 0 or not sha:
        return False
    candidate.base_sha = sha
    return True
