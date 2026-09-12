"""#686 / #688: read the base tree the reader will read, and no more.

Two defects with one cause — the base tree was materialized whole:

*Cost.* One `git cat-file` subprocess per blob. On a 26 MB repository with
1,168 files and four host-surface files, `shipgate diff` took 25 seconds
against 1 second on a toy scenario.

*Reach.* Every entry was checked for portability, name collision and
external binding, so one symlink anywhere refused the whole repository.
Four of twelve public repositories with committed host configuration were
uncomparable on every step of their history — one for a symlinked PNG under
`website/public/`, nowhere near any file a reader opens.

The scope is `is_boundary_surface_path`, the same predicate the live reader
classifies with, so the archive and the reader cannot disagree about what
the surface is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agents_shipgate.cli.verify.git import archive_tree
from agents_shipgate.core.boundary_registry import is_boundary_surface_path
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    return root


def _commit(root: Path, message: str = "c") -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


def _host_repo(tmp_path: Path, name: str = "repo") -> Path:
    """A repository shaped like the ones this was measured on."""

    root = _repo(tmp_path, name)
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        '{"permissions": {"allow": ["Bash(pytest *)"]}}', encoding="utf-8"
    )
    (root / "src").mkdir()
    for index in range(30):
        (root / "src" / f"m{index}.py").write_text(f"X = {index}\n", encoding="utf-8")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    return root


class TestScopeMaterializesOnlyTheSurface:
    def test_files_outside_the_surface_are_not_written(self, tmp_path: Path) -> None:
        root = _host_repo(tmp_path)
        _commit(root)
        out = tmp_path / "base"

        archive_tree(root, "HEAD", out, scope=is_boundary_surface_path)

        assert (out / ".claude" / "settings.json").is_file()
        assert not (out / "src").exists()
        assert not (out / "README.md").exists()
        written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        assert written == [".claude/settings.json"]

    def test_without_a_scope_the_whole_tree_is_still_written(self, tmp_path: Path) -> None:
        """The unscoped contract is unchanged: `verify` materializes trees for
        the release decision and this must not quietly narrow them."""

        root = _host_repo(tmp_path)
        _commit(root)
        out = tmp_path / "full"

        archive_tree(root, "HEAD", out)

        assert (out / "README.md").is_file()
        assert len(list((out / "src").iterdir())) == 30

    def test_the_scoped_archive_reads_the_same_grants(self, tmp_path: Path) -> None:
        """Cheaper must not mean different. The inventory from the scoped
        archive equals the inventory from the whole tree."""

        root = _host_repo(tmp_path)
        _commit(root)
        scoped, whole = tmp_path / "scoped", tmp_path / "whole"
        archive_tree(root, "HEAD", scoped, scope=is_boundary_surface_path)
        archive_tree(root, "HEAD", whole)

        def grants(where: Path) -> list[tuple[str, str, str]]:
            inventory = build_host_boundary_snapshot(
                where, cache=HostStaticParseCache()
            ).inventory
            return sorted(
                (g["host"], g["source"], g.get("rule") or g["kind"])
                for g in inventory["grants"]
            )

        assert grants(scoped) == grants(whole)
        assert grants(scoped)


class TestTheHistoryIsNotWalked:
    def test_a_scoped_archive_packs_no_commit(self, tmp_path: Path) -> None:
        """The 25 seconds were per-blob subprocesses, but the pack carried
        every historical tree and blob too. A tree pack carries neither."""

        root = _host_repo(tmp_path)
        for index in range(4):
            (root / "src" / f"extra{index}.py").write_text("Y = 1\n", encoding="utf-8")
            _commit(root, f"c{index}")
        out = tmp_path / "base"

        archive_tree(root, "HEAD", out, scope=is_boundary_surface_path)

        assert (out / ".claude" / "settings.json").is_file()

    def test_a_shallow_clone_is_comparable_when_scoped(self, tmp_path: Path) -> None:
        """A tree has no parents, so nothing crosses the graft (#683)."""

        origin = _host_repo(tmp_path, "origin")
        _commit(origin, "first")
        (origin / "src" / "later.py").write_text("Z = 1\n", encoding="utf-8")
        head = _commit(origin, "second")
        shallow = tmp_path / "shallow"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(shallow)],
            check=True, capture_output=True,
        )
        assert _git(shallow, "rev-parse", "--is-shallow-repository") == "true"

        out = tmp_path / "base"
        archive_tree(shallow, head, out, scope=is_boundary_surface_path)

        assert (out / ".claude" / "settings.json").is_file()


class TestSymlinks:
    def test_a_symlink_outside_the_surface_does_not_refuse_the_tree(
        self, tmp_path: Path
    ) -> None:
        """The measured case: three symlinked PNGs under `website/public/`."""

        root = _host_repo(tmp_path)
        (root / "website").mkdir()
        (root / "website" / "shot.png").symlink_to("../src/m0.py")
        _commit(root)
        out = tmp_path / "base"

        archive_tree(root, "HEAD", out, scope=is_boundary_surface_path)

        assert (out / ".claude" / "settings.json").is_file()

    def test_a_symlink_is_recreated_as_a_symlink(self, tmp_path: Path) -> None:
        """Not resolved and not skipped: the live reader sees a link and
        opens it with O_NOFOLLOW, so the base tree must present the same
        thing or the two sides disagree about what the file is."""

        root = _host_repo(tmp_path)
        (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        (root / "CLAUDE.md").symlink_to("AGENTS.md")
        _commit(root)
        out = tmp_path / "base"

        archive_tree(root, "HEAD", out, scope=is_boundary_surface_path)

        assert (out / "CLAUDE.md").is_symlink()
        assert (out / "CLAUDE.md").readlink().as_posix() == "AGENTS.md"

    def test_a_tree_that_puts_a_blob_under_a_symlinked_name_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The escape recreating symlinks could have opened.

        A valid tree cannot express it — `.claude` would have to be both a
        symlink blob and a tree, which is a duplicate entry — so this builds
        the invalid tree by hand and checks it is refused before anything is
        written. `fsck --strict` is what refuses it, which is why the
        materializer carries no separate ancestor check: that check could
        never fire, and an unreachable guard is not a guard.
        """

        root = _repo(tmp_path)
        (root / "seed").write_text("x\n", encoding="utf-8")
        _commit(root)

        def hash_object(text: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                input=text, check=True, capture_output=True, text=True,
            ).stdout.strip()

        def mktree(spec: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), "mktree"],
                input=spec, check=True, capture_output=True, text=True,
            ).stdout.strip()

        inner = mktree(f"100644 blob {hash_object('{}')}\tsettings.json\n")
        crafted = mktree(
            f"120000 blob {hash_object('/etc')}\t.claude\n"
            f"040000 tree {inner}\t.claude\n"
        )
        commit = subprocess.run(
            ["git", "-C", str(root), "commit-tree", crafted, "-m", "crafted"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        with pytest.raises(ConfigError, match="integrity validation|duplicate"):
            archive_tree(root, commit, tmp_path / "base", scope=is_boundary_surface_path)

        assert not (tmp_path / "base" / ".claude").exists()


class TestNamesOutsideTheSurface:
    def test_case_colliding_paths_outside_the_surface_do_not_refuse(
        self, tmp_path: Path
    ) -> None:
        """A repository of deception templates carried
        `.AwS__CrEdEnTiAlS.html` beside `.aws__credentials.html`. Neither is
        a file any reader opens, and on a case-insensitive filesystem the
        pair refused every comparison."""

        root = _host_repo(tmp_path)
        templates = root / "templates"
        templates.mkdir()
        (templates / "A.html").write_text("a\n", encoding="utf-8")
        _commit(root)
        colliding = subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input="b\n", check=True, capture_output=True, text=True,
        ).stdout.strip()
        inner = subprocess.run(
            ["git", "-C", str(root), "mktree"],
            input=f"100644 blob {colliding}\tA.html\n100644 blob {colliding}\ta.html\n",
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        claude = _git(root, "rev-parse", "HEAD:.claude")
        top = subprocess.run(
            ["git", "-C", str(root), "mktree"],
            input=f"040000 tree {claude}\t.claude\n040000 tree {inner}\ttemplates\n",
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(root), "commit-tree", top, "-m", "colliding"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        out = tmp_path / "base"
        archive_tree(root, commit, out, scope=is_boundary_surface_path)

        assert (out / ".claude" / "settings.json").is_file()

    def test_the_same_collision_inside_the_surface_still_refuses(
        self, tmp_path: Path
    ) -> None:
        """Narrowing what is examined must not narrow what is enforced."""

        root = _host_repo(tmp_path)
        _commit(root)
        blob = subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input="{}", check=True, capture_output=True, text=True,
        ).stdout.strip()
        inner = subprocess.run(
            ["git", "-C", str(root), "mktree"],
            input=f"100644 blob {blob}\tSettings.json\n100644 blob {blob}\tsettings.json\n",
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        top = subprocess.run(
            ["git", "-C", str(root), "mktree"],
            input=f"040000 tree {inner}\t.claude\n",
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(root), "commit-tree", top, "-m", "colliding surface"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        with pytest.raises(ConfigError, match="colliding"):
            archive_tree(root, commit, tmp_path / "base", scope=is_boundary_surface_path)


@pytest.mark.parametrize("kind", ["file", "directory", "dangling"])
def test_unbound_symlink_targets_remain_explicit_coverage_limits(tmp_path, kind):
    # Target type is not part of the identity-bound read. It cannot justify
    # dropping a candidate, even when it happens to be a regular file now.
    from agents_shipgate.core.host_grants import inventory_is_complete

    root = _host_repo(tmp_path)
    target = root / "target"
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("file")
    (root / "linked").symlink_to("target", target_is_directory=kind == "directory")
    inventory = build_host_boundary_snapshot(root).inventory
    assert not inventory_is_complete(inventory)
    assert any(issue.get("source") == "linked" and issue.get("blocking")
               for issue in inventory["issues"])
    assert inventory["grants"]


def test_symlink_target_kind_is_not_unbound_negative_coverage(tmp_path, monkeypatch):
    from agents_shipgate.core import host_grants

    root = _host_repo(tmp_path)
    target = tmp_path / "external-target"
    target.write_text("file")
    (root / "linked").symlink_to(target)
    original = host_grants._repository_paths

    def enumerate_then_replace(*args, **kwargs):
        result = original(*args, **kwargs)
        target.unlink()
        target.mkdir()
        (target / "CLAUDE.md").write_text("instructions")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(host_grants, "_repository_paths", enumerate_then_replace)
        snapshot = build_host_boundary_snapshot(root)
    fresh = build_host_boundary_snapshot(root)
    assert not host_grants.inventory_is_complete(snapshot.inventory)
    assert not host_grants.inventory_is_complete(fresh.inventory)
    assert snapshot.inventory["issues"]
