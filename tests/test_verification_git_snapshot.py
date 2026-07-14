from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agents_shipgate.cli.verify.git import archive_tree, repository_identity
from agents_shipgate.core.errors import ConfigError


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


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
