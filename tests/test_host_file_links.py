"""#700, step one: a symlinked in-tree file no host reads is not a coverage limit.

The audit treated every link as a directory that might hide a `**/` match, so
one symlinked asset anywhere refused every host's comparison. #659 measured it
on `MetaMask/metamask-mobile#29139` (`android/app/src/main/assets/branch.json`)
and `bencherdev/bencher#673` (`changelog.md`). By owner decision, a link whose
target is an in-tree regular file, typed inside the identity-bound read, no
longer counts. An external, dangling or directory target still does, and a link
*at* a boundary path is still read as a link until step two.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.host_audit import host_audit_inventory
from agents_shipgate.cli.main import app

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _settings(root: Path, allow: list[str]) -> None:
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": allow}}), encoding="utf-8")


def _issues(inventory: dict, source: str) -> list[dict]:
    return [item for item in inventory["issues"] if item["source"] == source]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


@pytest.mark.parametrize("link", ["changelog.md", "android/app/src/main/assets/branch.json", "chain/LICENSE-APACHE"])
def test_a_link_to_an_in_tree_file_is_not_a_limit(tmp_path: Path, link: str) -> None:
    _settings(tmp_path, ["Read(**)"])
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")
    target = tmp_path / link
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(os.path.relpath(tmp_path / "README.md", target.parent))

    inventory = host_audit_inventory(tmp_path)

    assert _issues(inventory, link) == []
    assert [item for item in inventory["issues"] if item.get("blocking")] == []


def test_a_chain_of_in_tree_links_to_a_file_is_not_a_limit(tmp_path: Path) -> None:
    _settings(tmp_path, ["Read(**)"])
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")
    (tmp_path / "middle.md").symlink_to("README.md")
    (tmp_path / "changelog.md").symlink_to("middle.md")

    assert [item for item in host_audit_inventory(tmp_path)["issues"] if item.get("blocking")] == []


@pytest.mark.parametrize("shape", ["dangling", "directory", "external", "through-linked-directory"])
def test_a_target_that_is_not_an_in_tree_file_still_conceals(tmp_path: Path, shape: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _settings(root, ["Read(**)"])
    if shape == "dangling":
        (root / "vendor").symlink_to("missing")
    elif shape == "directory":
        (root / "shared").mkdir()
        (root / "vendor").symlink_to("shared", target_is_directory=True)
    elif shape == "external":
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        (root / "vendor").symlink_to(outside)
    else:
        (root / "real").mkdir()
        (root / "real" / "file.md").write_text("x", encoding="utf-8")
        (root / "docs").symlink_to("real", target_is_directory=True)
        (root / "vendor").symlink_to("docs/file.md")

    issues = _issues(host_audit_inventory(root), "vendor")

    assert issues, shape
    assert all(item["kind"] == "unreadable" and item["blocking"] for item in issues), shape


def test_a_link_at_a_boundary_path_is_still_read_as_a_link(tmp_path: Path) -> None:
    """Step two of #700 reads through it; pinned so that step shows up as a change."""

    _settings(tmp_path, ["Read(**)"])
    (tmp_path / "AGENTS.md").write_text("# agents", encoding="utf-8")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")

    issues = _issues(host_audit_inventory(tmp_path), "CLAUDE.md")

    assert issues
    assert all(item["kind"] == "unreadable" for item in issues)


def test_a_comparison_beside_an_unrelated_file_link_names_the_change(tmp_path: Path) -> None:
    """The #659 shape end to end: the base tree must type the link the same way."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _settings(repo, ["Read(**)"])
    (repo / "README.md").write_text("readme", encoding="utf-8")
    (repo / "changelog.md").symlink_to("README.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _settings(repo, ["Bash(*)", "Read(**)"])

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", base, "--json"])
    payload = json.loads(result.stdout)

    assert payload["comparison_status"] == "comparable", payload
    assert [(row["direction"], row["after"]) for row in payload["rows"]] == [("added", "Bash(*)")]


def test_a_scoped_archive_never_writes_through_a_linked_directory(tmp_path: Path) -> None:
    from agents_shipgate.cli.verify.git import archive_tree
    from agents_shipgate.core.boundary_registry import is_boundary_surface_path

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _settings(repo, ["Read(**)"])
    (repo / "real").mkdir()
    (repo / "real" / "file.md").write_text("out of scope", encoding="utf-8")
    (repo / "docs").symlink_to("real", target_is_directory=True)
    (repo / "pointer.md").symlink_to("docs/file.md")
    (repo / "README.md").write_text("never materialized", encoding="utf-8")
    (repo / "changelog.md").symlink_to("README.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    archive = tmp_path / "archive"

    archive_tree(repo, "HEAD", archive, scope=is_boundary_surface_path)

    assert not (archive / "real" / "file.md").exists()
    assert not (archive / "pointer.md").exists()
    assert (archive / "changelog.md").is_file()
    assert (archive / "README.md").read_bytes() == b""
