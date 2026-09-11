"""#598: a concurrent test run must not collapse the host inventory.

Observed: `check` run while pytest was active returned
`human_review_required` with *"Directory inventory could not complete at
tests/__pycache__"*, and a quiescent rerun passed. The refusal itself was
correct — `IdentityBoundReadSession.finish()` revalidates every directory
it scanned, and a `.pyc` appearing between the two reads really is a
directory that changed while it was read.

The defect is that a machine-written bytecode cache was an identity-bound
input at all. No host reads configuration from one, so inventorying it
buys no coverage while exposing every local run to unrelated churn. These
cases fix the boundary and prove the integrity guarantee still holds where
it means something.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
)
from agents_shipgate.core.trust_roots import IdentityBoundReadSession

GENERATED_CACHES = ["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox"]

SETTINGS = '{"permissions": {"allow": ["Bash(pytest *)"]}}'


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(SETTINGS, encoding="utf-8")
    return tmp_path


def _grant_sources(snapshot) -> set[str]:
    return {row["source"] for row in snapshot.inventory["grants"]}


def _reasons(snapshot) -> set[str]:
    return {failure.reason for failure in snapshot.input_failures.values()}


# --- the boundary ----------------------------------------------------------


@pytest.mark.parametrize("cache_name", GENERATED_CACHES)
def test_a_generated_cache_directory_is_not_an_input(
    tmp_path: Path, cache_name: str
) -> None:
    """Its contents cannot change the answer, so its churn cannot either."""

    workspace = _workspace(tmp_path)
    without = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())

    cache_dir = workspace / "tests" / cache_name
    cache_dir.mkdir(parents=True)
    (cache_dir / "module.cpython-312.pyc").write_bytes(b"\x00\x0f")
    # Even a recognized *name* under a generated cache is not host
    # configuration: no host loads settings from a bytecode directory. The
    # same reasoning already excludes `.venv` and `node_modules`.
    (cache_dir / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    with_cache = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())

    assert _grant_sources(with_cache) == _grant_sources(without)
    assert not any(
        cache_name in row["path"] for row in with_cache.inventory["artifacts"]
    )


def test_a_churning_generated_cache_never_reaches_the_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported failure, deterministically: a cache directory whose
    entries change between the scan and its revalidation. Before #598 the
    whole repository inventory collapsed; now the directory is never read,
    so there is nothing to destabilise."""

    workspace = _workspace(tmp_path)
    cache_dir = workspace / "tests" / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "one.cpython-312.pyc").write_bytes(b"\x00")

    scanned: list[str] = []
    original = IdentityBoundReadSession._scan_names

    def recording(self, directory, *, max_entries=None):
        scanned.append(Path(directory).name)
        if Path(directory).name == "__pycache__":
            # A concurrent writer: a new .pyc lands mid-session, which is
            # exactly what makes finish() refuse the whole read.
            (cache_dir / f"two{len(scanned)}.cpython-312.pyc").write_bytes(b"\x00")
        return original(self, directory, max_entries=max_entries)

    monkeypatch.setattr(IdentityBoundReadSession, "_scan_names", recording)
    snapshot = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())

    assert "__pycache__" not in scanned, "a generated cache was read"
    assert ".claude/settings.json" in _grant_sources(snapshot)
    assert not _reasons(snapshot)


# --- the guarantee that must not move --------------------------------------


def test_a_churning_recognized_directory_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrency is only ignorable where the input cannot matter. A
    recognized host directory that changes mid-read is still refused."""

    workspace = _workspace(tmp_path)
    original = IdentityBoundReadSession._scan_names
    churned: list[str] = []

    def recording(self, directory, *, max_entries=None):
        names = original(self, directory, max_entries=max_entries)
        if Path(directory).name == ".claude" and not churned:
            churned.append("yes")
            (workspace / ".claude" / "settings.local.json").write_text(
                "{}", encoding="utf-8"
            )
        return names

    monkeypatch.setattr(IdentityBoundReadSession, "_scan_names", recording)
    snapshot = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())

    assert _reasons(snapshot), (
        "a recognized directory changing mid-read must still be refused"
    )
    assert not _grant_sources(snapshot), (
        "an incoherent read must not yield grants"
    )


def test_an_unreadable_recognized_directory_remains_fail_closed(tmp_path: Path) -> None:
    """Skipping generated caches must not soften a real access failure."""

    workspace = _workspace(tmp_path)
    blocked = workspace / ".claude" / "nested"
    blocked.mkdir()
    blocked.chmod(0o000)
    try:
        snapshot = build_host_boundary_snapshot(
            workspace, cache=HostStaticParseCache()
        )
        reasons = _reasons(snapshot)
        assert reasons == {"input_unreadable"}
    finally:
        blocked.chmod(0o755)


def test_a_quiescent_rerun_obtains_new_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cached denial, and no cached completion either: the second read
    is a real read."""

    workspace = _workspace(tmp_path)
    original = IdentityBoundReadSession.finish
    calls: list[int] = []

    def unstable_once(self):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(
                "directory entries changed while identity-bound files were read"
            )
        return original(self)

    monkeypatch.setattr(IdentityBoundReadSession, "finish", unstable_once)
    first = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())
    assert _reasons(first), "the churned read must be reported, not hidden"

    monkeypatch.setattr(IdentityBoundReadSession, "finish", original)
    second = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())

    assert not _reasons(second)
    assert ".claude/settings.json" in _grant_sources(second)
