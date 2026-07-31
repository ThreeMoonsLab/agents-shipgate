from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from contextvars import ContextVar, Token
from pathlib import Path

from agents_shipgate.core.trust_roots import (
    IdentityBoundReadSession,
    IdentityReadBudget,
)

DEFAULT_STATIC_INPUT_FILE_BYTES = 32 * 1024 * 1024
DEFAULT_STATIC_INPUT_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_STATIC_INPUT_FILES = 100_000


class StaticInputSnapshot:
    """Identity-bound, graph-scoped bytes used by one worktree verification.

    Every path is read at most once. Later scan phases and receipt construction
    receive the same cached bytes, so a concurrent path replacement cannot make
    the decision evaluate one object while the request identity attests to
    another. Aggregate limits keep the snapshot deterministic and bounded.
    """

    def __init__(
        self,
        root: Path,
        *,
        external_paths: Iterable[Path] = (),
        excluded_paths: Iterable[Path] = (),
        max_total_bytes: int = DEFAULT_STATIC_INPUT_TOTAL_BYTES,
        max_files: int = DEFAULT_STATIC_INPUT_FILES,
    ) -> None:
        self.root = Path(os.path.abspath(os.path.normpath(os.fspath(root))))
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files
        self._external_paths = {
            Path(os.path.abspath(os.path.normpath(os.fspath(path))))
            for path in external_paths
        }
        self._excluded_paths = {
            Path(os.path.abspath(os.path.normpath(os.fspath(path))))
            for path in excluded_paths
        }
        self._entries: dict[Path, bytes] = {}
        self._total_bytes = 0
        self._budget = IdentityReadBudget(
            max_entries=max_files * 32,
            max_total_bytes=max_total_bytes,
        )
        self._sessions: dict[Path, IdentityBoundReadSession] = {}
        self._finished = False

    def preload(self, path: Path, data: bytes) -> None:
        key, _relative = self._key(path)
        existing = self._entries.get(key)
        if existing is not None:
            if existing != data:
                raise ValueError(f"static input changed before snapshot: {key}")
            return
        session, relative = self._session_for(key)
        captured = session.read_bytes(
            relative,
            max_bytes=max(len(data), DEFAULT_STATIC_INPUT_FILE_BYTES),
        )
        if captured != data:
            raise ValueError(f"static input changed before snapshot: {key}")
        self._record(key, data)

    def contains(self, path: Path) -> bool:
        raw = path if path.is_absolute() else self.root / path
        key = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
        if self.excludes(key):
            return False
        try:
            key.relative_to(self.root)
        except ValueError:
            return key in self._external_paths
        return key != self.root

    def excludes(self, path: Path) -> bool:
        raw = path if path.is_absolute() else self.root / path
        key = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
        return any(
            key == excluded or excluded in key.parents
            for excluded in self._excluded_paths
        )

    def has(self, path: Path) -> bool:
        raw = path if path.is_absolute() else self.root / path
        key = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
        return key in self._entries

    def paths_under(self, path: Path) -> list[Path]:
        raw = path if path.is_absolute() else self.root / path
        directory = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
        return sorted(
            key
            for key in self._entries
            if key != directory and directory in key.parents
        )

    def paths(self) -> list[Path]:
        return sorted(self._entries)

    def read_bytes(
        self,
        path: Path,
        *,
        max_bytes: int = DEFAULT_STATIC_INPUT_FILE_BYTES,
    ) -> bytes:
        key, relative = self._key(path)
        cached = self._entries.get(key)
        if cached is not None:
            if len(cached) > max_bytes:
                raise ValueError(
                    f"static input exceeds the {max_bytes}-byte read limit: {key}"
                )
            return cached
        if self._finished:
            raise ValueError("static input snapshot is already finalized")
        session, relative = self._session_for(key)
        data = session.read_bytes(
            relative,
            max_bytes=max_bytes,
        )
        self._record(key, data)
        return data

    def bind_directory(self, path: Path) -> tuple[str, ...]:
        """Bind one directory inventory to the graph-scoped snapshot."""

        if self._finished:
            raise ValueError("static input snapshot is already finalized")
        key = Path(
            os.path.abspath(
                os.path.normpath(os.fspath(path if path.is_absolute() else self.root / path))
            )
        )
        session, relative = self._session_for(key)
        return session.directory_entries(relative, max_entries=self.max_files)

    def finish(self) -> None:
        """Reject any identity or directory-membership change since capture."""

        if self._finished:
            return
        for session in self._sessions.values():
            session.finish()
        self._finished = True

    def _key(self, path: Path) -> tuple[Path, Path]:
        raw = path if path.is_absolute() else self.root / path
        key = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
        try:
            relative = key.relative_to(self.root)
        except ValueError as exc:
            if key not in self._external_paths:
                raise ValueError(
                    f"static input escapes verification workspace: {key}"
                ) from exc
            relative = key.relative_to(Path(key.anchor))
        if relative in {Path(), Path(".")}:
            raise ValueError("static input path must identify a file")
        return key, relative

    def _session_for(self, key: Path) -> tuple[IdentityBoundReadSession, Path]:
        if key == self.root or self.root in key.parents:
            session_root = self.root
        elif key in self._external_paths:
            session_root = key.parent
        else:
            raise ValueError(f"static input escapes verification workspace: {key}")
        session = self._sessions.get(session_root)
        if session is None:
            session = IdentityBoundReadSession(session_root, budget=self._budget)
            self._sessions[session_root] = session
        return session, key.relative_to(session_root)

    def _record(self, key: Path, data: bytes) -> None:
        if len(self._entries) >= self.max_files:
            raise ValueError(
                f"static input snapshot exceeds the {self.max_files}-file limit"
            )
        next_total = self._total_bytes + len(data)
        if next_total > self.max_total_bytes:
            raise ValueError(
                "static input snapshot exceeds the "
                f"{self.max_total_bytes}-byte aggregate limit"
            )
        self._entries[key] = data
        self._total_bytes = next_total


_ACTIVE_SNAPSHOT: ContextVar[StaticInputSnapshot | None] = ContextVar(
    "agents_shipgate_static_input_snapshot",
    default=None,
)


def activate_static_input_snapshot(
    snapshot: StaticInputSnapshot,
) -> Token[StaticInputSnapshot | None]:
    return _ACTIVE_SNAPSHOT.set(snapshot)


def reset_static_input_snapshot(token: Token[StaticInputSnapshot | None]) -> None:
    _ACTIVE_SNAPSHOT.reset(token)


def active_static_input_snapshot() -> StaticInputSnapshot | None:
    return _ACTIVE_SNAPSHOT.get()


def read_static_input_bytes(
    path: Path,
    *,
    max_bytes: int = DEFAULT_STATIC_INPUT_FILE_BYTES,
) -> bytes:
    snapshot = _ACTIVE_SNAPSHOT.get()
    if snapshot is not None and snapshot.contains(path):
        return snapshot.read_bytes(path, max_bytes=max_bytes)
    # Outside a graph-scoped worktree verification, retain the loader's
    # established support for repository-root aliases (for example macOS
    # ``/var`` -> ``/private/var`` and an explicitly accepted repo symlink).
    # The active snapshot path above supplies the stronger no-alias boundary
    # where request/decision identity must be frozen.
    lexical = Path(path)
    metadata = lexical.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("static input is not a regular file")
    if metadata.st_size > max_bytes:
        raise ValueError(f"Input file too large (limit: {max_bytes} bytes): {path}")
    with lexical.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Input file too large (limit: {max_bytes} bytes): {path}")
    return data


def read_static_input_text(
    path: Path,
    *,
    max_bytes: int = DEFAULT_STATIC_INPUT_FILE_BYTES,
) -> str:
    return read_static_input_bytes(path, max_bytes=max_bytes).decode(
        "utf-8",
        errors="strict",
    )


__all__ = [
    "StaticInputSnapshot",
    "active_static_input_snapshot",
    "activate_static_input_snapshot",
    "read_static_input_bytes",
    "read_static_input_text",
    "reset_static_input_snapshot",
]
