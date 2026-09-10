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
        budget: IdentityReadBudget | None = None,
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
        self._dependency_paths: set[Path] = set()
        self._absent_dependency_paths: set[Path] = set()
        self._present_dependency_paths: set[Path] = set()
        self._unconfirmable_dependency_paths: set[Path] = set()
        self._input_directories: dict[Path, tuple[tuple[str, str], ...]] = {}
        self._unconfirmable_input_directories: set[Path] = set()
        self._directory_members = 0
        # These exclusions apply only to source discovery, never to file reads
        # or named negative lookups. Git metadata is not an agent tool surface.
        self._discovery_exclusions = self._excluded_paths | {self.root / ".git"}
        self._output_only: dict[Path, bool] = {}
        self._total_bytes = 0
        self._budget = budget if budget is not None else IdentityReadBudget(
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

    def mark_dependency_input(self, path: Path) -> None:
        """Select already captured reader bytes for live dependency currency."""

        key, _relative = self._key(path)
        if key not in self._entries or self._finished:
            raise ValueError("dependency input was not captured by this active snapshot")
        self._dependency_paths.add(key)

    def bind_dependency_absence(self, path: Path) -> bool:
        """Capture a named absent lookup candidate, including its parent listing."""

        key, _relative = self._key(path)
        names = self.bind_directory(key.parent)
        if key.name in names:
            self._present_dependency_paths.add(key)
            return False
        self._absent_dependency_paths.add(key)
        return True

    def dependency_paths(self) -> list[Path]:
        return sorted(self._dependency_paths)

    def absent_dependency_paths(self) -> list[Path]:
        return sorted(self._absent_dependency_paths)

    def present_dependency_paths(self) -> list[Path]:
        return sorted(self._present_dependency_paths)

    def mark_unconfirmable_dependency(self, path: Path) -> None:
        """An attempted read without captured bytes grants no current identity."""

        key, _relative = self._key(path)
        if self._finished:
            raise ValueError("static input snapshot is already finalized")
        self._unconfirmable_dependency_paths.add(key)

    def unconfirmable_dependency_paths(self) -> list[Path]:
        return sorted(self._unconfirmable_dependency_paths)

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
        """Bind an incidental inventory for this session, not future currency."""

        if self._finished:
            raise ValueError("static input snapshot is already finalized")
        key = Path(
            os.path.abspath(
                os.path.normpath(os.fspath(path if path.is_absolute() else self.root / path))
            )
        )
        session, relative = self._session_for(key)
        return session.directory_entries(relative, max_entries=self.max_files)

    def mark_unconfirmable_input_directory(self, path: Path) -> None:
        """Keep a refused directory lookup visible even if an adapter recovers."""

        if self._finished:
            raise ValueError("static input snapshot is already finalized")
        key = Path(os.path.abspath(path))
        key.relative_to(self.root)
        self._unconfirmable_input_directories.add(key)

    def enumerate_input_directory(self, path: Path) -> tuple[str, ...]:
        """Record a reader-selected inventory, including empty directories.

        Merely reading a file or checking a named absent import does not select
        its whole parent as an input. Only readers that discover inputs through
        this API publish membership obligations in the verification plan.
        """

        key = Path(os.path.abspath(os.path.normpath(os.fspath(
            path if path.is_absolute() else self.root / path
        ))))
        try:
            if self._finished or (key != self.root and self.root not in key.parents):
                raise ValueError("input directory is outside the active snapshot")
            if self.excludes(key) or key == self.root / ".git":
                raise ValueError("input directory overlaps excluded verification output")
            cached = self._input_directories.get(key)
            if cached is not None:
                return tuple(name for name, _kind in cached)
            names = self.bind_directory(key)
            session, relative = self._session_for(key)
            members = []
            for name in names:
                child = key / name
                if child in self._discovery_exclusions:
                    continue
                kind = session.directory_entry_kind(relative / name)
                if kind == "directory" and self._is_output_only_ancestor(child):
                    continue
                members.append((name, kind))
            if self._directory_members + len(members) + 1 > self.max_files:
                raise ValueError("input directory census exceeds its aggregate entry limit")
            self._directory_members += len(members) + 1
            self._input_directories[key] = tuple(members)
            return tuple(name for name, _kind in members)
        except (OSError, ValueError):
            self._unconfirmable_input_directories.add(key)
            raise

    def _is_output_only_ancestor(self, path: Path) -> bool:
        """Project away only scaffolding on an exact output-path prefix.

        A live build/reports directory need not exist in an archived tree. But
        build/agent.ts or even build/other-empty-directory is a real new source
        member. Never inspect arbitrary sibling subtrees to call them empty.
        These identity-bound inspections do not select directories as inputs.
        """

        if not any(path in excluded.parents for excluded in self._discovery_exclusions):
            return False
        if path in self._output_only:
            return self._output_only[path]
        names = self.bind_directory(path)
        session, relative = self._session_for(path)
        result = True
        for name in names:
            child = path / name
            if child in self._discovery_exclusions:
                continue
            kind = session.directory_entry_kind(relative / name)
            if kind != "directory" or not self._is_output_only_ancestor(child):
                result = False
                break
        self._output_only[path] = result
        return result

    def input_directory_identity(self, *, source: str) -> dict[str, object]:
        """Export the selected census, never incidental lexical parents."""

        return {
            "version": 1,
            "source": source,
            "excluded_paths": sorted(
                path.relative_to(self.root).as_posix()
                for path in self._discovery_exclusions
                if path == self.root or self.root in path.parents
            ),
            "directories": [
                {"path": path.relative_to(self.root).as_posix(),
                 "members": [{"name": name, "kind": kind} for name, kind in members]}
                for path, members in sorted(
                    self._input_directories.items(),
                    key=lambda item: item[0].relative_to(self.root).as_posix(),
                )
            ],
            "unconfirmable_paths": sorted(
                path.relative_to(self.root).as_posix()
                for path in self._unconfirmable_input_directories
            ),
        }

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
        metadata = key.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"static input is not a regular file: {key}")
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
