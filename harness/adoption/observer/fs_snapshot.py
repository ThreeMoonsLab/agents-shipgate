"""Pre/post filesystem snapshots.

Used by the ``no_runtime_trace_synthesis`` detector to identify trace files
the agent fabricated during the run. We need a real pre-state because the
agent could create files under ``traces/`` during execution and then claim
they're evidence; existence alone is not a sufficient signal.

The snapshot is a flat dict mapping relative paths → sha256 of file
contents. ``diff_snapshot`` returns added/removed/changed sets.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

IGNORED_DIRS: frozenset[str] = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache"})


@dataclass(frozen=True)
class FsSnapshot:
    root: Path
    files: dict[str, str] = field(default_factory=dict)

    def diff(self, other: FsSnapshot) -> FsDiff:
        added = sorted(set(other.files) - set(self.files))
        removed = sorted(set(self.files) - set(other.files))
        changed = sorted(
            path for path in set(self.files) & set(other.files) if self.files[path] != other.files[path]
        )
        return FsDiff(added=added, removed=removed, changed=changed)


@dataclass(frozen=True)
class FsDiff:
    added: list[str]
    removed: list[str]
    changed: list[str]


def snapshot(root: Path) -> FsSnapshot:
    """Walk ``root`` and capture sha256 hashes for every regular file."""
    files: dict[str, str] = {}
    root = root.resolve()
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except (OSError, PermissionError):
            continue
        digest = hashlib.sha256(data).hexdigest()
        files[str(path.relative_to(root))] = digest
    return FsSnapshot(root=root, files=files)


__all__ = ["FsDiff", "FsSnapshot", "snapshot"]
