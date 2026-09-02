"""Candidate enumeration: PRs of a GitHub repo, via ``gh`` + ``git``.

The only network-touching module in the miner. ``enumerate_merged_prs``
asks ``gh`` for merged PRs with their merge commits; ``ensure_clone``
makes a full local clone (full, not partial — evaluation must be able to
run offline afterwards). Everything downstream is local-only
(:mod:`benchmark.miner.evaluate`).

Two further enumerations exist for the ``rejected_or_reverted`` vein of the
safety-qualification corpus (issue #456), which merged history cannot
supply: ``enumerate_closed_unmerged_prs`` (PRs closed without merging, pinned
at the fork point and the PR head) and ``enumerate_reverted_prs`` (merged
PRs that a later ``Revert …`` PR undid, pinned like any merged PR, with the
revert recorded as the evidence of rejection).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_NETWORK_TIMEOUT = 600

NOTE_CLOSED_UNMERGED = "closed_unmerged"


@dataclass
class Candidate:
    repo: str
    pr_number: int
    pr_url: str
    title: str
    merged_at: str
    merge_sha: str
    base_sha: str = ""
    #: Where a non-merged candidate's pins come from: the PR's base branch
    #: name (closed-unmerged) — blank for merged candidates.
    base_ref: str = ""
    #: Carried onto the mined row untouched: ``closed_unmerged``, or
    #: ``reverted_by:<sha>;revert_pr:<n>`` for a reverted merged PR.
    notes: str = ""


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


def _gh_json(args: list[str]) -> object:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=_NETWORK_TIMEOUT,
        check=True,
    )
    return json.loads(result.stdout)


def enumerate_closed_unmerged_prs(repo: str, *, limit: int = 50) -> list[Candidate]:
    """The most recently closed PRs that were never merged.

    ``gh pr list --state closed`` lists merged PRs too, so the query asks for
    more than ``limit`` and keeps the first ``limit`` whose ``mergedAt`` is
    null. ``merge_sha`` holds the PR's head commit (there is no merge
    commit); the base is resolved later, in the clone, by
    :func:`resolve_closed_pins`.
    """

    items = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "closed",
            "--limit",
            str(limit * 3),
            "--json",
            "number,title,url,closedAt,mergedAt,headRefOid,baseRefName",
        ]
    )
    candidates: list[Candidate] = []
    for item in items:
        if item.get("mergedAt"):
            continue
        head = str(item.get("headRefOid") or "")
        if not head:
            continue
        candidates.append(
            Candidate(
                repo=repo,
                pr_number=int(item.get("number") or 0),
                pr_url=str(item.get("url") or ""),
                title=str(item.get("title") or "")[:160],
                merged_at="",
                merge_sha=head,
                base_ref=str(item.get("baseRefName") or ""),
                notes=NOTE_CLOSED_UNMERGED,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


_REVERTS_BODY = re.compile(r"[Rr]everts?\s+\S+#([1-9][0-9]*)")
_PR_NUMBER = re.compile(r"#([1-9][0-9]*)")


def reverted_pr_number(revert_pr_number: int, title: str, body: str) -> int | None:
    """The PR a ``Revert …`` PR undoes, read from its body then its title.

    GitHub's revert button writes ``Reverts owner/repo#N`` into the body; a
    hand-written revert usually quotes the original title, whose trailing
    ``(#N)`` is the reverted PR. The revert PR's own number is never the
    answer, whichever field it appears in.
    """

    match = _REVERTS_BODY.search(body or "")
    if match and int(match.group(1)) != revert_pr_number:
        return int(match.group(1))
    for found in _PR_NUMBER.findall(title or ""):
        if int(found) != revert_pr_number:
            return int(found)
    return None


def enumerate_reverted_prs(repo: str, *, limit: int = 50) -> list[Candidate]:
    """Merged PRs that a later merged ``Revert …`` PR undid.

    Scans the latest ``limit`` merged PRs for revert titles, resolves each to
    the PR it reverted, and returns *that* PR at its own merge-commit pins —
    the same convention as :func:`enumerate_merged_prs` — with the revert's
    merge commit recorded in ``notes`` as the evidence of rejection.
    """

    items = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--search",
            "Revert in:title",
            "--json",
            "number,title,body,mergeCommit",
        ]
    )
    candidates: list[Candidate] = []
    seen: set[int] = set()
    for item in items:
        title = str(item.get("title") or "")
        if not title.lower().startswith("revert"):
            continue
        revert_number = int(item.get("number") or 0)
        reverted = reverted_pr_number(revert_number, title, str(item.get("body") or ""))
        if reverted is None or reverted in seen:
            continue
        revert_sha = str((item.get("mergeCommit") or {}).get("oid") or "")
        try:
            original = _gh_json(
                [
                    "pr",
                    "view",
                    str(reverted),
                    "--repo",
                    repo,
                    "--json",
                    "number,title,url,mergedAt,mergeCommit,state",
                ]
            )
        except subprocess.CalledProcessError:
            # A title like `Revert "... (#65..." names a number that is not a
            # PR here (an issue, a truncated reference); one bad reference
            # must not abort the enumeration.
            continue
        if str(original.get("state") or "") != "MERGED":
            continue
        sha = str((original.get("mergeCommit") or {}).get("oid") or "")
        if not sha:
            continue
        seen.add(reverted)
        candidates.append(
            Candidate(
                repo=repo,
                pr_number=reverted,
                pr_url=str(original.get("url") or ""),
                title=str(original.get("title") or "")[:160],
                merged_at=str(original.get("mergedAt") or ""),
                merge_sha=sha,
                notes=f"reverted_by:{revert_sha};revert_pr:{revert_number}",
            )
        )
    return candidates


def candidate_for_pr(repo: str, number: int) -> Candidate | None:
    """One named PR as a candidate, in whichever state it is in.

    Merged PRs are pinned like :func:`enumerate_merged_prs`; closed-unmerged
    ones like :func:`enumerate_closed_unmerged_prs`. An open PR is neither
    history nor a rejection and yields ``None``. ``resolve_pins_for`` picks
    the matching clone-side resolver.
    """

    item = _gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,url,state,mergedAt,mergeCommit,headRefOid,baseRefName",
        ]
    )
    state = str(item.get("state") or "")
    if state == "MERGED":
        sha = str((item.get("mergeCommit") or {}).get("oid") or "")
        if not sha:
            return None
        return Candidate(
            repo=repo,
            pr_number=int(item.get("number") or 0),
            pr_url=str(item.get("url") or ""),
            title=str(item.get("title") or "")[:160],
            merged_at=str(item.get("mergedAt") or ""),
            merge_sha=sha,
        )
    if state == "CLOSED":
        head = str(item.get("headRefOid") or "")
        if not head:
            return None
        return Candidate(
            repo=repo,
            pr_number=int(item.get("number") or 0),
            pr_url=str(item.get("url") or ""),
            title=str(item.get("title") or "")[:160],
            merged_at="",
            merge_sha=head,
            base_ref=str(item.get("baseRefName") or ""),
            notes=NOTE_CLOSED_UNMERGED,
        )
    return None


def ensure_clone(repo: str, workdir: Path) -> Path:
    destination = workdir / repo.replace("/", "__")
    if (destination / ".git").exists():
        # A cached clone must be refreshed: `gh pr list` returns the LATEST
        # merged PRs, whose merge commits a stale clone does not have —
        # without this fetch, weekly reruns silently drop the newest PRs
        # from the corpus and bias the metrics.
        subprocess.run(
            ["git", "-C", str(destination), "fetch", "--quiet", "--no-tags", "origin"],
            capture_output=True,
            text=True,
            timeout=_NETWORK_TIMEOUT,
            check=True,
        )
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


def resolve_closed_pins(clone: Path, candidate: Candidate) -> bool:
    """Pin a closed-unmerged PR: head = its last pushed commit, base = fork point.

    Fetches ``refs/pull/<n>/head`` (GitHub keeps it after the fork branch is
    deleted) and sets ``base_sha`` to ``git merge-base <head> origin/<base>``
    — the mainline state the PR was written against, so ``base..head`` is the
    change that was rejected. Returns False when either side is unresolvable.
    """

    fetch = subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            f"+refs/pull/{candidate.pr_number}/head:refs/miner/pull/{candidate.pr_number}/head",
        ],
        capture_output=True,
        text=True,
        timeout=_NETWORK_TIMEOUT,
        check=False,
    )
    if fetch.returncode != 0 or not candidate.base_ref:
        return False
    probe = subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "merge-base",
            candidate.merge_sha,
            f"origin/{candidate.base_ref}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    sha = probe.stdout.strip()
    if probe.returncode != 0 or not sha or sha == candidate.merge_sha:
        return False
    candidate.base_sha = sha
    return True


def resolve_pins_for(clone: Path, candidate: Candidate) -> bool:
    """Pin a candidate by its provenance: closed-unmerged by fork point, else by parent."""

    if candidate.notes.startswith(NOTE_CLOSED_UNMERGED):
        return resolve_closed_pins(clone, candidate)
    return resolve_base(clone, candidate)
