from __future__ import annotations

import subprocess
import zlib
from pathlib import Path

import pytest

from agents_shipgate.cli.verify.git import archive_tree, repository_identity
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
