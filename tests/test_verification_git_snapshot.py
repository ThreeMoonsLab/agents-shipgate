from __future__ import annotations

import subprocess
import zlib
from pathlib import Path

import pytest

from agents_shipgate.cli.verify.git import (
    archive_tree,
    diff_revspec_context,
    repository_identity,
    working_tree_context,
)
from agents_shipgate.core.errors import ConfigError


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _git_output(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    return root


def test_exact_snapshot_does_not_honor_export_ignore(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("secret.yaml export-ignore\n", encoding="utf-8")
    (root / "secret.yaml").write_text("policy: bound\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    out = tmp_path / "snapshot"
    archive_tree(root, "HEAD", out)
    assert (out / "secret.yaml").read_text(encoding="utf-8") == "policy: bound\n"


def test_exact_snapshot_rejects_git_symlink_bindings(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "target").write_text("value", encoding="utf-8")
    (root / "link").symlink_to("target")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    with pytest.raises(ConfigError, match="unsupported external binding"):
        archive_tree(root, "HEAD", tmp_path / "snapshot")


def test_exact_snapshot_rejects_portable_filesystem_path_collisions(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    blob = _git_output(root, "hash-object", "-w", "--stdin", input_text="bound\n")
    tree = _git_output(
        root,
        "mktree",
        input_text=(
            f"100644 blob {blob}\tPolicy.yaml\n"
            f"100644 blob {blob}\tpolicy.yaml\n"
        ),
    )
    commit = _git_output(root, "commit-tree", tree, "-m", "colliding tree")

    with pytest.raises(ConfigError, match="filesystem-colliding paths"):
        archive_tree(root, commit, tmp_path / "snapshot")


def test_exact_snapshot_rejects_blob_bytes_that_do_not_match_their_oid(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "policy.yaml").write_text("safe\n", encoding="utf-8")
    _git(root, "add", "policy.yaml")
    _git(root, "commit", "-m", "fixture")
    oid = _git_output(root, "rev-parse", "HEAD:policy.yaml")
    loose_object = root / ".git" / "objects" / oid[:2] / oid[2:]
    if not loose_object.is_file():
        pytest.skip("fixture blob is not stored as a loose object")
    loose_object.chmod(0o644)
    loose_object.write_bytes(zlib.compress(b"blob 5\0evil\n"))

    with pytest.raises(ConfigError, match="Git object"):
        archive_tree(root, "HEAD", tmp_path / "snapshot")


def test_diff_rejects_binary_source_like_paths(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = root / "agent.py"
    source.write_text("print('base')\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "base")
    source.write_bytes(b"\x00binary capability payload\n")
    _git(root, "add", "agent.py")
    _git(root, "commit", "-m", "binary head")

    with pytest.raises(ConfigError, match="source-like changed paths as binary"):
        diff_revspec_context(root, "HEAD~1...HEAD")


def test_repository_identity_normalizes_ssh_and_https_remotes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _git(root, "remote", "add", "origin", "git@GitHub.com:ThreeMoonsLab/shipgate.git")
    assert repository_identity(root) == "github.com/ThreeMoonsLab/shipgate"
    _git(
        root,
        "remote",
        "set-url",
        "origin",
        "https://token@example.test/org/repo.git?credential=secret",
    )
    assert repository_identity(root) == "example.test/org/repo"


def test_effective_worktree_diff_coalesces_committed_staged_and_unstaged_edits(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    target = root / "AGENTS.md"
    target.write_text("base\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-m", "base")

    target.write_text("committed\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-m", "issue fix")
    target.write_text("staged review\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    target.write_text("unstaged review\n", encoding="utf-8")

    changed, diff_text = working_tree_context(
        root,
        comparison_ref="HEAD~1",
        reject_index_hidden=True,
    )

    assert changed == ["AGENTS.md"]
    assert diff_text.count("diff --git a/AGENTS.md b/AGENTS.md") == 1
    assert "unstaged review" in diff_text
    assert "committed" not in diff_text
    assert "+staged review" not in diff_text


def test_effective_worktree_diff_preserves_rename_and_mode_semantics(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    source = root / "AGENTS.md"
    source.write_text(
        "shared rule\nshared scope\nbase instructions\n",
        encoding="utf-8",
    )
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-m", "base")

    destination = root / "CLAUDE.md"
    source.rename(destination)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "rename instructions")
    destination.write_text(
        "shared rule\nshared scope\nreviewed instructions\n",
        encoding="utf-8",
    )
    destination.chmod(0o755)

    changed, diff_text = working_tree_context(
        root,
        comparison_ref="HEAD~1",
        reject_index_hidden=True,
    )

    assert changed == ["AGENTS.md", "CLAUDE.md"]
    assert diff_text.count("diff --git ") == 1
    assert "rename from AGENTS.md" in diff_text
    assert "rename to CLAUDE.md" in diff_text
    assert "old mode 100644" in diff_text
    assert "new mode 100755" in diff_text
