from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.inputs.common import resolve_input_path, walk_input_tree


def test_recursive_input_inventory_rejects_late_nested_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        assert walk_input_tree(source) == [nested]
        (nested / "late.json").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="changed (?:identity|entries)"):
            snapshot.finish()
    finally:
        reset_static_input_snapshot(token)


def test_outer_snapshot_does_not_capture_immutable_archive_directory(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    archive = tmp_path / "archive"
    source = archive / "plugins"
    source.mkdir(parents=True)
    snapshot = StaticInputSnapshot(worktree)
    token = activate_static_input_snapshot(snapshot)
    try:
        assert resolve_input_path(archive, "plugins") == source.resolve()
    finally:
        reset_static_input_snapshot(token)


def test_directory_limit_is_enforced_after_an_incidental_file_read(tmp_path):
    from agents_shipgate.core.trust_roots import IdentityBoundReadSession

    (tmp_path / 'a.txt').write_text('a')
    (tmp_path / 'b.txt').write_text('b')
    session = IdentityBoundReadSession(tmp_path, max_entries=100, max_total_bytes=100)
    session.read_bytes(Path('a.txt'), max_bytes=10)
    with pytest.raises(ValueError, match='entry bound'):
        session.directory_entries(max_entries=1)


@pytest.mark.parametrize('mutation', ['directory', 'symlink', 'special'])
def test_unread_member_kind_is_rechecked_when_the_read_session_finishes(tmp_path, mutation):
    import os

    source = tmp_path / 'source'
    source.mkdir()
    member = source / 'unread'
    member.write_text('not an input blob')
    snapshot = StaticInputSnapshot(tmp_path)
    assert snapshot.enumerate_input_directory(source) == ('unread',)
    member.unlink()
    if mutation == 'directory':
        member.mkdir()
    elif mutation == 'symlink':
        member.symlink_to(tmp_path, target_is_directory=True)
    elif hasattr(os, 'mkfifo'):
        os.mkfifo(member)
    else:
        pytest.skip('FIFO creation unavailable')
    with pytest.raises(ValueError, match='(?:kind changed|changed identity)'):
        snapshot.finish()


def test_member_kinds_do_not_make_unread_sibling_bytes_an_input(tmp_path):
    (tmp_path / 'unused.txt').write_text('before')
    snapshot = StaticInputSnapshot(tmp_path)
    snapshot.enumerate_input_directory(tmp_path)
    (tmp_path / 'unused.txt').write_text('after')
    snapshot.finish()


@pytest.mark.parametrize('extra', ['none', 'file', 'empty_directory', 'symlink'])
def test_output_projection_excludes_only_scaffolding_on_its_exact_prefix(tmp_path, extra):
    output = tmp_path / 'build' / 'artifacts' / 'reports'
    output.mkdir(parents=True)
    (output / 'report.json').write_text('{}')
    if extra == 'file':
        (tmp_path / 'build' / 'agent.ts').write_text('new tool')
    elif extra == 'empty_directory':
        (tmp_path / 'build' / 'empty').mkdir()
    elif extra == 'symlink':
        import shutil
        shutil.rmtree(tmp_path / 'build' / 'artifacts')
        (tmp_path / 'build' / 'artifacts').symlink_to(tmp_path, target_is_directory=True)
    snapshot = StaticInputSnapshot(tmp_path, excluded_paths=[output])
    assert snapshot.enumerate_input_directory(tmp_path) == (() if extra == 'none' else ('build',))
    snapshot.finish()
    identity = snapshot.input_directory_identity(source='worktree')
    # Projection probes are session obligations, never selected source rows.
    assert [row['path'] for row in identity['directories']] == ['.']


def test_named_negative_lookup_and_file_parent_are_not_directory_sources(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'entry.py').write_text('entry')
    snapshot = StaticInputSnapshot(tmp_path)
    snapshot.read_bytes(source / 'entry.py')
    assert snapshot.bind_dependency_absence(source / 'guard.py')
    snapshot.finish()
    assert snapshot.input_directory_identity(source='worktree')['directories'] == []


def test_named_negative_lookup_keeps_a_failed_probe_after_successful_retry(tmp_path, monkeypatch):
    from pathlib import Path

    candidate = tmp_path / 'missing'
    snapshot = StaticInputSnapshot(tmp_path)
    original = Path.lstat

    def unreadable(path):
        if path == candidate:
            raise PermissionError('synthetic named lookup failure')
        return original(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'lstat', unreadable)
        with pytest.raises(PermissionError):
            snapshot.bind_dependency_absence(candidate)
    assert snapshot.absent_dependency_paths() == []
    assert snapshot.bind_dependency_absence(candidate)
    snapshot.finish()
    assert snapshot.unconfirmable_dependency_paths() == [candidate]


def test_named_negative_lookup_parent_cap_is_not_absence(tmp_path):
    for name in ('a', 'b'):
        (tmp_path / name).write_text(name)
    candidate = tmp_path / 'missing'
    snapshot = StaticInputSnapshot(tmp_path, max_files=1)
    with pytest.raises(ValueError):
        snapshot.bind_dependency_absence(candidate)
    assert snapshot.absent_dependency_paths() == []
    assert snapshot.unconfirmable_dependency_paths() == [candidate]


def test_named_negative_lookup_rechecks_parent_at_finish(tmp_path):
    snapshot = StaticInputSnapshot(tmp_path)
    candidate = tmp_path / 'missing'
    assert snapshot.bind_dependency_absence(candidate)
    candidate.write_text('appeared during capture')
    with pytest.raises(ValueError):
        snapshot.finish()


def test_caught_directory_cap_retains_an_unconfirmable_obligation(tmp_path):
    for name in ('a', 'b'):
        (tmp_path / name).write_text(name)
    snapshot = StaticInputSnapshot(tmp_path, max_files=1)
    with pytest.raises(ValueError):
        snapshot.enumerate_input_directory(tmp_path)
    assert snapshot.input_directory_identity(source='worktree')['unconfirmable_paths'] == ['.']


def test_selected_root_is_bound_without_selecting_a_directory_census(tmp_path):
    snapshot = StaticInputSnapshot(tmp_path)
    with pytest.raises(ValueError, match="directory, expected a regular file"):
        snapshot.capture_selected_path(tmp_path, allow_directory=False)
    snapshot.capture_selected_path(tmp_path)
    snapshot.finish()
    assert snapshot.input_directory_identity(source="worktree")["directories"] == []
    with pytest.raises(ValueError, match="invalid selected input lookup"):
        snapshot.capture_selected_path(tmp_path)


def test_selected_file_obeys_the_parser_byte_bound_even_when_cached(tmp_path):
    path = tmp_path / "selected.json"
    path.write_bytes(b"12345")
    snapshot = StaticInputSnapshot(tmp_path)
    snapshot.read_bytes(path)
    with pytest.raises(ValueError, match="4-byte read limit"):
        snapshot.capture_selected_path(path, max_bytes=4)
    with pytest.raises(ValueError, match="invalid selected input lookup"):
        snapshot.capture_selected_path(tmp_path / "alias" / ".." / path.name)
