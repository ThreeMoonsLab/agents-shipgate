"""Tests for the miner's rejected-vein enumeration (benchmark/miner/candidates).

Network-free: ``gh`` is replaced by a fake that answers from canned JSON, and
the git side runs against a throwaway local repository with a fake ``origin``.
The merged-PR path stays untested here on purpose (see ``test_miner.py``);
what these guard is the *pinning* of the two new states, because a pin that
drifts from the convention would put a rater in front of the wrong tree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmark.miner import candidates
from benchmark.miner.candidates import (
    NOTE_CLOSED_UNMERGED,
    Candidate,
    enumerate_closed_unmerged_prs,
    enumerate_reverted_prs,
    resolve_closed_pins,
    reverted_pr_number,
)


def _fake_gh(answers: dict[str, object]):
    """A ``subprocess.run`` stand-in answering ``gh`` calls by their subcommand."""

    calls: list[list[str]] = []

    def run(cmd, **kwargs):  # noqa: ANN001 - mirrors subprocess.run
        calls.append(list(cmd))
        assert cmd[0] == "gh", cmd
        key = (
            " ".join(cmd[1:3])
            if cmd[1] == "pr" and cmd[2] == "list"
            else f"{cmd[1]} {cmd[2]} {cmd[3]}"
        )
        payload = answers[key]
        if payload is None:  # gh exits non-zero: no such PR
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_closed_unmerged_enumeration_drops_merged_prs_and_honours_the_limit(monkeypatch) -> None:
    listing = [
        {
            "number": 30,
            "title": "merged",
            "url": "u30",
            "mergedAt": "2026-01-01T00:00:00Z",
            "headRefOid": "a" * 40,
            "baseRefName": "main",
        },
        {
            "number": 29,
            "title": "closed one",
            "url": "u29",
            "mergedAt": None,
            "headRefOid": "b" * 40,
            "baseRefName": "main",
        },
        {
            "number": 28,
            "title": "no head",
            "url": "u28",
            "mergedAt": None,
            "headRefOid": "",
            "baseRefName": "main",
        },
        {
            "number": 27,
            "title": "closed two",
            "url": "u27",
            "mergedAt": None,
            "headRefOid": "c" * 40,
            "baseRefName": "release",
        },
        {
            "number": 26,
            "title": "closed three",
            "url": "u26",
            "mergedAt": None,
            "headRefOid": "d" * 40,
            "baseRefName": "main",
        },
    ]
    fake = _fake_gh({"pr list": listing})
    monkeypatch.setattr(candidates.subprocess, "run", fake)

    found = enumerate_closed_unmerged_prs("o/r", limit=2)

    assert [c.pr_number for c in found] == [29, 27]
    assert all(c.merged_at == "" and c.notes == NOTE_CLOSED_UNMERGED for c in found)
    assert found[1].base_ref == "release"
    assert found[0].merge_sha == "b" * 40 and found[0].base_sha == ""
    # Over-asks gh so that the merged rows it also returns do not eat the limit.
    assert "6" in fake.calls[0]


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ('Revert "feat: add delete tool (#41)"', "Reverts o/r#41", 41),
        ('Revert "feat: add delete tool (#41)" (#57)', "", 41),
        ("Revert #57", "", None),  # its own number is never the answer
        ("Revert the thing", "no reference here", None),
    ],
)
def test_reverted_pr_number_prefers_the_body_and_never_returns_itself(
    title: str, body: str, expected: int | None
) -> None:
    assert reverted_pr_number(57, title, body) == expected


def test_reverted_enumeration_returns_the_reverted_pr_at_its_own_merge_pins(monkeypatch) -> None:
    fake = _fake_gh(
        {
            "pr list": [
                {
                    "number": 57,
                    "title": 'Revert "feat: delete tool (#41)"',
                    "body": "Reverts o/r#41",
                    "mergeCommit": {"oid": "e" * 40},
                },
                {
                    "number": 58,
                    "title": "feat: unrelated",
                    "body": "",
                    "mergeCommit": {"oid": "f" * 40},
                },
                {
                    "number": 59,
                    "title": 'Revert "docs (#12)"',
                    "body": "",
                    "mergeCommit": {"oid": "9" * 40},
                },
                {
                    "number": 60,
                    "title": 'Revert "thing (#65"',
                    "body": "",
                    "mergeCommit": {"oid": "6" * 40},
                },
            ],
            "pr view 41": {
                "number": 41,
                "title": "feat: delete tool",
                "url": "u41",
                "mergedAt": "2026-02-02T00:00:00Z",
                "mergeCommit": {"oid": "4" * 40},
                "state": "MERGED",
            },
            "pr view 12": {
                "number": 12,
                "title": "docs",
                "url": "u12",
                "mergedAt": None,
                "mergeCommit": None,
                "state": "CLOSED",
            },
            # Not a PR: gh fails, and the enumeration must carry on.
            "pr view 65": None,
        }
    )
    monkeypatch.setattr(candidates.subprocess, "run", fake)

    found = enumerate_reverted_prs("o/r", limit=10)

    assert [c.pr_number for c in found] == [41]
    assert found[0].merge_sha == "4" * 40
    assert found[0].merged_at == "2026-02-02T00:00:00Z"
    assert found[0].notes == f"reverted_by:{'e' * 40};revert_pr:57"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@e",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            name,
        ],
        check=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def test_resolve_closed_pins_uses_the_fork_point_not_the_base_tip(tmp_path: Path) -> None:
    """The base of a rejected PR is where it forked, never where main is now."""

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    _commit(origin, "one")
    fork_point = _commit(origin, "two")
    subprocess.run(["git", "-C", str(origin), "checkout", "-qb", "pr"], check=True)
    head = _commit(origin, "pr-change")
    subprocess.run(["git", "-C", str(origin), "checkout", "-q", "main"], check=True)
    main_tip = _commit(origin, "three")  # main moved on after the PR forked
    _git(origin, "update-ref", "refs/pull/7/head", head)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    candidate = Candidate(
        repo="o/r",
        pr_number=7,
        pr_url="u7",
        title="pr",
        merged_at="",
        merge_sha=head,
        base_ref="main",
        notes=NOTE_CLOSED_UNMERGED,
    )

    assert resolve_closed_pins(clone, candidate) is True
    assert candidate.base_sha == fork_point
    assert candidate.base_sha != main_tip
    assert _git(clone, "rev-parse", "refs/miner/pull/7/head") == head


def test_resolve_closed_pins_fails_closed_without_a_base_branch(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    head = _commit(origin, "one")
    _git(origin, "update-ref", "refs/pull/1/head", head)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)

    unrooted = Candidate(
        repo="o/r", pr_number=1, pr_url="u", title="t", merged_at="", merge_sha=head, base_ref=""
    )
    assert resolve_closed_pins(clone, unrooted) is False
    # Head is the base branch tip: there is no change to pin, so no pin.
    degenerate = Candidate(
        repo="o/r",
        pr_number=1,
        pr_url="u",
        title="t",
        merged_at="",
        merge_sha=head,
        base_ref="main",
    )
    assert resolve_closed_pins(clone, degenerate) is False
