from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents_shipgate.cli.verify.git import (
    archive_tree,
    resolve_source_head_identity,
    tree_sha,
    validate_source_head_identity,
)
from agents_shipgate.core.verification_identity import build_verification_plan


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class GitGraph:
    repo: Path
    base: str
    source: str
    evaluated_merge: str
    unrelated: str


@pytest.fixture
def graph(tmp_path: Path) -> GitGraph:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Shipgate Tests")

    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "base", base)

    _git(repo, "switch", "-q", "-c", "source")
    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-q", "-m", "source")
    source = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-q", "--detach", base)
    _git(repo, "merge", "-q", "--no-ff", source, "-m", "synthetic merge")
    evaluated_merge = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "evaluated-merge", evaluated_merge)

    _git(repo, "switch", "-q", "-c", "unrelated", base)
    (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "-q", "-m", "unrelated")
    unrelated = _git(repo, "rev-parse", "HEAD")

    return GitGraph(
        repo=repo,
        base=base,
        source=source,
        evaluated_merge=evaluated_merge,
        unrelated=unrelated,
    )


def test_local_explicit_head_is_its_own_source(graph: GitGraph) -> None:
    identity = resolve_source_head_identity(graph.repo, head_ref="source")

    assert identity.evaluated_head_commit_sha == graph.source
    assert identity.source_head_commit_sha == graph.source
    assert identity.relation == "evaluated_head"


def test_default_github_pr_merge_is_authorization_ineligible(
    graph: GitGraph,
) -> None:
    identity = resolve_source_head_identity(
        graph.repo,
        head_ref=graph.evaluated_merge,
        github_actions=True,
        event_name="pull_request",
        evaluated_head_sha=graph.evaluated_merge,
    )

    assert identity.evaluated_head_commit_sha == graph.evaluated_merge
    assert identity.source_head_commit_sha is None
    assert identity.relation == "authorization_ineligible"


def test_explicit_action_head_override_authorizes_only_the_evaluated_override(
    graph: GitGraph,
) -> None:
    identity = resolve_source_head_identity(
        graph.repo,
        head_ref=graph.source,
        github_actions=True,
        event_name="pull_request",
        # The host default still names the synthetic merge, proving that the
        # effective --head was explicitly overridden to the source commit.
        evaluated_head_sha=graph.evaluated_merge,
    )

    assert identity.evaluated_head_commit_sha == graph.source
    assert identity.source_head_commit_sha == graph.source
    assert identity.relation == "evaluated_head"


def test_ambient_source_head_sha_is_never_read_as_authority(
    graph: GitGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_HEAD_SHA", graph.unrelated)

    identity = resolve_source_head_identity(graph.repo, head_ref=graph.source)

    assert identity.source_head_commit_sha == graph.source
    assert identity.source_head_commit_sha != graph.unrelated


def test_replace_ref_cannot_change_the_tree_bound_to_a_source_commit(
    graph: GitGraph,
) -> None:
    raw_source_tree = _git(
        graph.repo,
        "--no-replace-objects",
        "rev-parse",
        f"{graph.source}^{{tree}}",
    )
    replacement_tree = _git(
        graph.repo,
        "--no-replace-objects",
        "rev-parse",
        f"{graph.unrelated}^{{tree}}",
    )
    assert raw_source_tree != replacement_tree

    _git(graph.repo, "replace", graph.source, graph.unrelated)

    # Authorization pushes the raw object named by ``graph.source``. The
    # verifier therefore must bind that object's real tree, never Git's local
    # replacement view of the same object ID.
    assert tree_sha(graph.repo, graph.source) == raw_source_tree


def test_replace_ref_cannot_change_the_tree_archived_for_verification(
    graph: GitGraph,
    tmp_path: Path,
) -> None:
    _git(graph.repo, "replace", graph.source, graph.unrelated)
    archive = tmp_path / "archive"

    archive_tree(graph.repo, graph.source, archive)

    assert (archive / "source.txt").read_text(encoding="utf-8") == "source\n"
    assert not (archive / "unrelated.txt").exists()


def test_even_exact_second_parent_cannot_be_a_distinct_authorized_source(
    graph: GitGraph,
) -> None:
    with pytest.raises(ValueError, match="must equal the evaluated head"):
        validate_source_head_identity(
            graph.repo,
            evaluated_head_commit_sha=graph.evaluated_merge,
            source_head_commit_sha=graph.source,
        )


def test_missing_source_is_a_valid_but_ineligible_receipt_state(
    graph: GitGraph,
) -> None:
    relation = validate_source_head_identity(
        graph.repo,
        evaluated_head_commit_sha=graph.evaluated_merge,
        source_head_commit_sha=None,
    )
    assert relation == "authorization_ineligible"


def test_plan_builder_rejects_distinct_source_even_with_rehashed_identity(
    graph: GitGraph,
) -> None:
    with pytest.raises(ValueError, match="must equal the evaluated committed head"):
        build_verification_plan(
            git_root=graph.repo,
            input_root=graph.repo,
            config_path=graph.repo / "not-read-before-invariant.yaml",
            config_logical_path="shipgate.yaml",
            base_ref=graph.base,
            head_ref=graph.evaluated_merge,
            archived_head=True,
            repository_id="local:test",
            base_commit_sha=graph.base,
            base_tree_sha="a" * 40,
            source_head_commit_sha=graph.source,
            head_commit_sha=graph.evaluated_merge,
            head_tree_sha="b" * 40,
            merge_base_sha=graph.base,
            changed_files=[],
            diff_text="",
            baseline_path=None,
            diff_from_path=None,
            policy_pack_paths=[],
            evaluation_date="2026-07-18",
            options={},
            plugins_enabled=False,
        )


def test_worktree_plan_cannot_carry_source_authority(graph: GitGraph) -> None:
    with pytest.raises(ValueError, match="worktree-overlay plans cannot declare"):
        build_verification_plan(
            git_root=graph.repo,
            input_root=graph.repo,
            config_path=graph.repo / "not-read-before-invariant.yaml",
            config_logical_path="shipgate.yaml",
            base_ref=None,
            head_ref="HEAD",
            archived_head=False,
            repository_id="local:test",
            base_commit_sha=None,
            base_tree_sha=None,
            source_head_commit_sha=graph.unrelated,
            head_commit_sha=graph.unrelated,
            head_tree_sha="b" * 40,
            merge_base_sha=None,
            changed_files=[],
            diff_text="",
            baseline_path=None,
            diff_from_path=None,
            policy_pack_paths=[],
            evaluation_date="2026-07-18",
            options={},
            plugins_enabled=False,
        )
