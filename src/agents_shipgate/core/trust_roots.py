"""Classification of a repository's release trust roots.

This is domain data, not check logic: the local boundary evaluator, the
preflight router, the installed hooks, and the verify-mode
``SHIP-VERIFY-TRUST-ROOT-TOUCHED`` check must all classify a changed path
identically.  It lives in :mod:`agents_shipgate.core` so every one of those
consumers can import it without a cycle;
:mod:`agents_shipgate.checks.verify` re-exports the names it has always
exported.
"""

from __future__ import annotations

import os
import posixpath
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.globbing import glob_match

# Ordered (class, glob) classification of a repo's release trust roots —
# the surfaces that define the gate in any repo that has adopted
# Shipgate (target-repo trust roots, §5.2). First match wins; one
# finding per changed file. ``**/`` prefixes match at any depth
# (including the repo root) per agents_shipgate.core.globbing.
_LEGACY_TRUST_ROOT_SURFACES: tuple[tuple[str, str], ...] = (
    ("manifest", "**/shipgate.yaml"),
    ("shipgate_state", "**/.agents-shipgate/**"),
    ("policy", "**/policies/**"),
    ("prompts", "**/prompts/**"),
    ("ci_gate", "**/.github/workflows/agents-shipgate.yml"),
    ("ci_gate", "**/.github/workflows/agents-shipgate.yaml"),
    ("agent_instructions", "**/AGENTS.md"),
    ("agent_instructions", "**/AGENTS.override.md"),
    ("agent_instructions", "**/CLAUDE.md"),
    ("agent_instructions", "**/.claude/**"),
    ("agent_instructions", "**/.cursor/rules/**"),
    ("agent_instructions", "**/.agents/skills/**"),
    ("agent_instructions", "**/.codex/**"),
    ("codex_plugin", "**/.codex-plugin/**"),
    ("tool_surface_decl", "**/.app.json"),
    ("tool_surface_decl", "**/.mcp.json"),
    # Host-boundary MCP declarations (Cursor / VS Code project servers).
    # Claude Code settings (.claude/settings.json, .claude/settings.local.json)
    # are already covered by the agent_instructions "**/.claude/**" glob above.
    ("tool_surface_decl", "**/.cursor/mcp.json"),
    ("tool_surface_decl", "**/.vscode/mcp.json"),
    ("tool_surface_decl", "**/SKILL.md"),
)


def _registry_trust_root_surfaces() -> tuple[tuple[str, str], ...]:
    """Project registry paths not already covered by a legacy trust-root glob.

    Existing classifications stay stable for finding fingerprints and reviewer
    copy. Newly registered boundary paths automatically become trust roots,
    which prevents trigger/check/preflight coverage from drifting apart.
    """

    existing_patterns = tuple(pattern for _kind, pattern in _LEGACY_TRUST_ROOT_SURFACES)
    additions: list[tuple[str, str]] = []
    seen: set[str] = set(existing_patterns)
    for adapter in BOUNDARY_ADAPTERS:
        for pattern in (*adapter.exact_paths, *adapter.globs):
            if pattern in seen:
                continue
            representative = pattern.replace("**", "nested").replace("*", "item")
            if any(glob_match(existing, representative) for existing in existing_patterns):
                continue
            seen.add(pattern)
            additions.append(("host_boundary", pattern))
    return tuple(additions)


TRUST_ROOT_SURFACES: tuple[tuple[str, str], ...] = (
    *_LEGACY_TRUST_ROOT_SURFACES,
    *_registry_trust_root_surfaces(),
)

# The deny-list of trust-root files a coding agent must never edit *to make a
# verdict pass*, derived from ``TRUST_ROOT_SURFACES`` (single source of truth),
# restricted to the classes whose trust boundary is the WHOLE FILE: the
# Shipgate CI gate, the agent-instruction surfaces, and policy packs.
#
# Deliberately EXCLUDES:
#   * ``shipgate.yaml`` and ``.agents-shipgate/**`` — their boundary is
#     *key-level* (editing an action's scope is a legitimate mechanical fix; a
#     ``checks.ignore`` / baseline / waiver expansion is reward-hacking). A
#     path-level deny cannot express that, so they are covered by
#     ``forbidden_actions`` (``FORBIDDEN_SHORTCUTS``) instead.
#   * the tool-surface declarations (``.mcp.json``, ``SKILL.md``, ``.app.json``,
#     ``.codex-plugin/**``) — those are the capability surface UNDER review,
#     which a PR may legitimately edit.
#
# Single home so the verifier and the ``agent_handoff`` preview fallback emit
# the IDENTICAL
# standing deny-list — a passing/preview verdict never reads as "anything goes".
_FORBIDDEN_EDIT_CLASSES = frozenset(
    {"ci_gate", "agent_instructions", "policy", "host_boundary"}
)
PROTECTED_FILE_EDITS: tuple[str, ...] = tuple(
    pattern for kind, pattern in TRUST_ROOT_SURFACES if kind in _FORBIDDEN_EDIT_CLASSES
)


def trust_root_class_for(path: str) -> str | None:
    """Classify ``path`` against the ordered trust-root table, first match wins.

    Git can carry a lowercase spelling that becomes the canonical host file on
    APFS/NTFS (for example ``agents.md`` resolving as ``AGENTS.md``). Treat
    ASCII case variants conservatively on every platform so a PR cannot be
    approved on Linux and acquire a privileged meaning when cloned elsewhere.
    """

    for trust_root_class, pattern in TRUST_ROOT_SURFACES:
        if glob_match(pattern, path) or glob_match(pattern.casefold(), path.casefold()):
            return trust_root_class
    return None


def _normalized(value: object) -> str:
    """A path with separators unified and ``.``/``..`` segments collapsed."""

    # Repository paths are exact identities. Stripping turns distinct legal
    # Git names such as ``gate.yml`` and `` gate.yml`` into one trust root.
    text = str(value).replace("\\", "/")
    if not text:
        return ""
    return PurePosixPath(posixpath.normpath(text)).as_posix()


def _is_absolute(path: str) -> bool:
    """Whether a normalized POSIX spelling is absolute, including Windows drives."""

    return posixpath.isabs(path) or (
        len(path) >= 3 and path[1] == ":" and path[2] == "/"
    )


def is_portable_repo_path(path: str) -> bool:
    """Whether ``path`` has one portable repository-relative identity."""

    if (
        not path
        or path.startswith(("/", "./", "//"))
        or "\\" in path
        or ":" in path
        or any(ord(character) < 32 for character in path)
    ):
        return False
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{number}" for number in range(1, 10))
    reserved.update(f"lpt{number}" for number in range(1, 10))
    parts = path.split("/")
    for part in parts:
        normalized = part.casefold()
        if (
            not part
            or part in {".", ".."}
            or part != part.rstrip(" .")
            or normalized.split(".", 1)[0] in reserved
        ):
            return False
    return True


def _under_workspace(workspace: str, path: str) -> str | None:
    """Return one canonical spelling only when it remains inside ``workspace``."""

    resolved = path if _is_absolute(path) else _normalized(posixpath.join(workspace, path))
    if workspace in {"", "."}:
        if _is_absolute(resolved) or resolved == ".." or resolved.startswith("../"):
            return None
        return resolved

    workspace_parts = PurePosixPath(workspace).parts
    resolved_parts = PurePosixPath(resolved).parts
    if (
        len(resolved_parts) < len(workspace_parts)
        or resolved_parts[: len(workspace_parts)] != workspace_parts
    ):
        return None
    return resolved


@dataclass(frozen=True)
class PathIdentityIssue:
    """A lexical repository path that does not identify one exact entry.

    ``alias`` is deliberately discovered from the filesystem rather than from
    case-folding or Unicode normalization. Case-sensitive repositories may
    legitimately contain both ``Gate.yml`` and ``gate.yml``; only a spelling
    that the host resolves to a *different* stored directory entry is unsafe.
    """

    kind: Literal["alias", "symlink", "reparse_point", "inspection_error"]
    requested: Path
    actual: Path | None = None
    detail: str | None = None


class IdentityReadBudgetExceeded(ValueError):
    """A graph-scoped identity reader exhausted its aggregate resource budget."""


@dataclass
class IdentityReadBudget:
    """Aggregate entry/byte budget shared by one or more read sessions."""

    max_entries: int
    max_total_bytes: int
    entries_scanned: int = 0
    bytes_read: int = 0

    def __post_init__(self) -> None:
        if self.max_entries < 0 or self.max_total_bytes < 0:
            raise ValueError("identity-read budgets must be non-negative")

    def consume_entries(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("identity-read entry count must be non-negative")
        self.entries_scanned += count
        if self.entries_scanned > self.max_entries:
            raise IdentityReadBudgetExceeded(
                "identity reads exceeded their aggregate filesystem entry bound"
            )

    @property
    def remaining_bytes(self) -> int:
        return self.max_total_bytes - self.bytes_read

    def consume_bytes(self, count: int) -> None:
        if count < 0:
            raise ValueError("identity-read byte count must be non-negative")
        self.bytes_read += count
        if self.bytes_read > self.max_total_bytes:
            raise IdentityReadBudgetExceeded(
                "identity reads exceeded their aggregate byte bound"
            )


@dataclass
class _DirectoryIdentitySnapshot:
    names: frozenset[str]
    observed: dict[str, os.stat_result]


class IdentityBoundReadSession:
    """Read several exact files while scanning each directory only twice.

    A standalone identity-bound read deliberately scans every lexical parent
    before and after opening the file. Repeating that operation for many files
    in one protected directory is quadratic. This graph-scoped reader caches
    the first exact-name inventory and revalidates every observed entry in one
    final pass per directory. Descriptor and metadata comparisons retain the
    same no-alias, no-symlink, no-reparse-point, singly-linked-file boundary.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int | None = None,
        max_total_bytes: int | None = None,
        budget: IdentityReadBudget | None = None,
    ) -> None:
        if budget is None:
            if max_entries is None or max_total_bytes is None:
                raise ValueError("identity-read session requires an aggregate budget")
            budget = IdentityReadBudget(
                max_entries=max_entries,
                max_total_bytes=max_total_bytes,
            )
        elif max_entries is not None or max_total_bytes is not None:
            raise ValueError(
                "identity-read session accepts either a shared budget or limits"
            )
        self.root = Path(os.path.abspath(os.path.normpath(os.fspath(root))))
        self._budget = budget
        self._snapshots: dict[Path, _DirectoryIdentitySnapshot] = {}
        self._finished = False
        self._root_metadata = self.root.lstat()
        if (
            not stat.S_ISDIR(self._root_metadata.st_mode)
            or stat.S_ISLNK(self._root_metadata.st_mode)
            or _is_reparse_point(self._root_metadata)
            or _is_junction(self.root)
        ):
            raise ValueError("identity-read root is not one lexical directory")

    @property
    def entries_scanned(self) -> int:
        return self._budget.entries_scanned

    @property
    def bytes_read(self) -> int:
        return self._budget.bytes_read

    def read_bytes(self, relative: Path, *, max_bytes: int) -> bytes:
        """Read one session-relative file within per-file and aggregate limits."""

        if self._finished:
            raise ValueError("identity-read session is already finished")
        if relative.is_absolute() or ".." in relative.parts or max_bytes < 0:
            raise ValueError("path is outside the identity-bound read root")
        components = self._inspect_components(relative)
        if not components:
            raise ValueError("identity-bound reads require a file path")
        final_metadata = components[-1][2]
        if not stat.S_ISREG(final_metadata.st_mode) or final_metadata.st_nlink != 1:
            raise ValueError("path is not one singly-linked regular file")
        if final_metadata.st_size > max_bytes:
            raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
        remaining = self._budget.remaining_bytes
        if final_metadata.st_size > remaining:
            raise IdentityReadBudgetExceeded(
                "identity reads exceeded their aggregate byte bound"
            )

        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        if os.open not in os.supports_dir_fd:
            raw = self._read_portable(
                relative,
                expected=final_metadata,
                file_flags=file_flags,
                max_bytes=max_bytes,
                remaining=remaining,
            )
        else:
            raw = self._read_with_dir_fds(
                components,
                file_flags=file_flags,
                max_bytes=max_bytes,
                remaining=remaining,
            )
        self._budget.consume_bytes(len(raw))
        return raw

    def directory_entries(
        self,
        relative: Path = Path(),
        *,
        max_entries: int | None = None,
    ) -> tuple[str, ...]:
        """Seed and return one exact directory inventory for final revalidation."""

        if self._finished:
            raise ValueError("identity-read session is already finished")
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("directory is outside the identity-bound read root")
        if max_entries is not None and max_entries < 0:
            raise IdentityReadBudgetExceeded(
                "directory inventory exceeded its static filesystem entry bound"
            )
        directory = self.root
        if relative.parts:
            components = self._inspect_components(relative)
            metadata = components[-1][2]
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("inventory path is not a directory")
            directory = self.root / relative
        snapshot = self._snapshot(directory, max_entries=max_entries)
        return tuple(sorted(snapshot.names))

    def finish(self) -> None:
        """Revalidate every observed lexical entry once after all reads."""

        if self._finished:
            return
        current_root = self.root.lstat()
        if _identity_stat(current_root) != _identity_stat(self._root_metadata):
            raise ValueError("identity-read root changed while files were read")
        for directory in sorted(self._snapshots, key=os.fspath):
            snapshot = self._snapshots[directory]
            current_names = self._scan_names(directory)
            if current_names != snapshot.names:
                raise ValueError(
                    "directory entries changed while identity-bound files were read"
                )
            for name, expected in sorted(snapshot.observed.items()):
                if name not in current_names:
                    raise ValueError("path changed lexical identity while it was read")
                requested = directory / name
                try:
                    current = requested.lstat()
                except OSError as exc:
                    raise ValueError("path changed while it was read") from exc
                if (
                    stat.S_ISLNK(current.st_mode)
                    or _is_reparse_point(current)
                    or _is_junction(requested)
                    or _identity_stat(current) != _identity_stat(expected)
                ):
                    raise ValueError("path changed identity while it was read")
        self._finished = True

    def _inspect_components(
        self,
        relative: Path,
    ) -> list[tuple[Path, str, os.stat_result]]:
        current = self.root
        components: list[tuple[Path, str, os.stat_result]] = []
        for index, part in enumerate(relative.parts):
            snapshot = self._snapshot(current)
            requested = current / part
            if part not in snapshot.names:
                try:
                    requested.lstat()
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise ValueError("path could not be inspected safely") from exc
                raise ValueError(
                    "path resolves through a differently spelled filesystem entry"
                )
            try:
                metadata = requested.lstat()
            except OSError as exc:
                raise ValueError("path could not be inspected safely") from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or _is_junction(requested)
            ):
                raise ValueError("path contains a symlink or reparse point")
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("path parent is not a directory")
            prior = snapshot.observed.get(part)
            if prior is not None and _identity_stat(prior) != _identity_stat(metadata):
                raise ValueError("path changed identity while it was read")
            snapshot.observed[part] = metadata
            components.append((current, part, metadata))
            current = requested
        return components

    def _snapshot(
        self,
        directory: Path,
        *,
        max_entries: int | None = None,
    ) -> _DirectoryIdentitySnapshot:
        cached = self._snapshots.get(directory)
        if cached is not None:
            return cached
        snapshot = _DirectoryIdentitySnapshot(
            names=self._scan_names(directory, max_entries=max_entries),
            observed={},
        )
        self._snapshots[directory] = snapshot
        return snapshot

    def _scan_names(
        self,
        directory: Path,
        *,
        max_entries: int | None = None,
    ) -> frozenset[str]:
        names: set[str] = set()
        local_entries = 0
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    local_entries += 1
                    if max_entries is not None and local_entries > max_entries:
                        raise IdentityReadBudgetExceeded(
                            "directory inventory exceeded its static filesystem "
                            "entry bound"
                        )
                    self._budget.consume_entries()
                    names.add(entry.name)
        except IdentityReadBudgetExceeded:
            raise
        except OSError as exc:
            raise ValueError("directory could not be inspected safely") from exc
        return frozenset(names)

    def _read_with_dir_fds(
        self,
        components: list[tuple[Path, str, os.stat_result]],
        *,
        file_flags: int,
        max_bytes: int,
        remaining: int,
    ) -> bytes:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptors: list[int] = []
        opened: os.stat_result | None = None
        after: os.stat_result | None = None
        raw = bytearray()
        try:
            current = os.open(self.root, directory_flags)
            descriptors.append(current)
            if _identity_stat(os.fstat(current)) != _identity_stat(self._root_metadata):
                raise ValueError("identity-read root changed before it was opened")
            for _directory, component, expected in components[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
                if _identity_stat(os.fstat(current)) != _identity_stat(expected):
                    raise ValueError("path parent changed before it was opened")
            final_name = components[-1][1]
            descriptor = os.open(final_name, file_flags, dir_fd=current)
            descriptors.append(descriptor)
            opened = os.fstat(descriptor)
            if _identity_stat(opened) != _identity_stat(components[-1][2]):
                raise ValueError("path changed identity before it was read")
            while chunk := os.read(
                descriptor,
                min(64 * 1024, min(max_bytes, remaining) + 1 - len(raw)),
            ):
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
                if len(raw) > remaining:
                    raise IdentityReadBudgetExceeded(
                        "identity reads exceeded their aggregate byte bound"
                    )
            after = os.fstat(descriptor)
        finally:
            for descriptor_to_close in reversed(descriptors):
                try:
                    os.close(descriptor_to_close)
                except OSError:
                    pass
        assert opened is not None and after is not None
        if _identity_stat(opened) != _identity_stat(after):
            raise ValueError("file changed while it was read")
        return bytes(raw)

    def _read_portable(
        self,
        relative: Path,
        *,
        expected: os.stat_result,
        file_flags: int,
        max_bytes: int,
        remaining: int,
    ) -> bytes:
        path = self.root / relative
        descriptor = os.open(path, file_flags)
        raw = bytearray()
        try:
            opened = os.fstat(descriptor)
            if _identity_stat(opened) != _identity_stat(expected):
                raise ValueError("path changed identity before it was read")
            while chunk := os.read(
                descriptor,
                min(64 * 1024, min(max_bytes, remaining) + 1 - len(raw)),
            ):
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
                if len(raw) > remaining:
                    raise IdentityReadBudgetExceeded(
                        "identity reads exceeded their aggregate byte bound"
                    )
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if _identity_stat(opened) != _identity_stat(after):
            raise ValueError("file changed while it was read")
        current = path.lstat()
        if _identity_stat(opened) != _identity_stat(current):
            raise ValueError("path changed identity while it was read")
        return bytes(raw)


def read_identity_bound_bytes(
    root: Path,
    relative: Path,
    *,
    max_bytes: int,
) -> bytes:
    """Read one exact contained file without following path aliases.

    The lexical path, every parent directory, the opened descriptor, and the
    post-read directory entry must all identify the same singly-linked regular
    file. ``O_NONBLOCK`` prevents a swapped FIFO/device from hanging the
    caller, while the explicit byte limit keeps trust-root reads bounded.
    """

    lexical_root = Path(os.path.abspath(os.path.normpath(os.fspath(root))))
    if relative.is_absolute() or ".." in relative.parts or max_bytes < 0:
        raise ValueError("path is outside the identity-bound read root")
    issue = inspect_lexical_path_identity(lexical_root, relative)
    if issue is not None:
        raise ValueError(
            "path contains a symlink, reparse point, alias, or uninspectable component"
        )

    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if os.open not in os.supports_dir_fd:
        return _read_identity_bound_bytes_portable(
            lexical_root,
            relative,
            max_bytes=max_bytes,
            file_flags=file_flags,
        )

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    opened: os.stat_result | None = None
    after: os.stat_result | None = None
    raw = bytearray()
    try:
        current = os.open(lexical_root, directory_flags)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(relative.name, file_flags, dir_fd=current)
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError("path is not one singly-linked regular file")
        if opened.st_size > max_bytes:
            raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
        while chunk := os.read(
            descriptor,
            min(64 * 1024, max_bytes + 1 - len(raw)),
        ):
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
        after = os.fstat(descriptor)
    finally:
        for descriptor_to_close in reversed(descriptors):
            try:
                os.close(descriptor_to_close)
            except OSError:
                pass

    assert opened is not None and after is not None
    if _identity_stat(opened) != _identity_stat(after):
        raise ValueError("file changed while it was read")
    issue = inspect_lexical_path_identity(lexical_root, relative)
    try:
        current_metadata = (lexical_root / relative).lstat()
    except OSError as exc:
        raise ValueError("path changed while it was read") from exc
    if issue is not None or _identity_stat(opened) != _identity_stat(current_metadata):
        raise ValueError("path changed identity while it was read")
    return bytes(raw)


def read_identity_bound_text(
    root: Path,
    relative: Path,
    *,
    max_bytes: int,
) -> str:
    """UTF-8 projection of :func:`read_identity_bound_bytes`."""

    return read_identity_bound_bytes(
        root,
        relative,
        max_bytes=max_bytes,
    ).decode("utf-8", errors="strict")


def read_absolute_identity_bound_text(path: Path, *, max_bytes: int) -> str:
    """Read an absolute/relative path with every lexical component bound."""

    lexical = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    anchor = Path(lexical.anchor)
    return read_identity_bound_text(
        anchor,
        lexical.relative_to(anchor),
        max_bytes=max_bytes,
    )


def _read_identity_bound_bytes_portable(
    root: Path,
    relative: Path,
    *,
    max_bytes: int,
    file_flags: int,
) -> bytes:
    """Best available identity checks where ``dir_fd`` is unavailable."""

    path = root / relative
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("path is not one singly-linked regular file")
    if before.st_size > max_bytes:
        raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
    descriptor = os.open(path, file_flags)
    raw = bytearray()
    try:
        opened = os.fstat(descriptor)
        if _identity_stat(before) != _identity_stat(opened):
            raise ValueError("path changed identity before it was read")
        while chunk := os.read(
            descriptor,
            min(64 * 1024, max_bytes + 1 - len(raw)),
        ):
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise ValueError(f"file exceeds the {max_bytes}-byte read limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _identity_stat(opened) != _identity_stat(after):
        raise ValueError("file changed while it was read")
    issue = inspect_lexical_path_identity(root, relative)
    current = path.lstat()
    if issue is not None or _identity_stat(opened) != _identity_stat(current):
        raise ValueError("path changed identity while it was read")
    return bytes(raw)


def _identity_stat(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def inspect_lexical_path_identity(
    root: Path,
    relative: Path,
) -> PathIdentityIssue | None:
    """Inspect ``relative`` without following aliases or symlink components.

    Missing suffix components are allowed so callers can retain their normal
    "manifest not found" recovery. If the host resolves a missing lexical
    spelling to an existing entry, however, the spelling is an alias (case,
    Unicode normalization, or another filesystem equivalence) and must be
    rejected before it becomes verification identity.
    """

    current = root
    for part in relative.parts:
        requested = current / part
        try:
            with os.scandir(current) as iterator:
                exact_found = any(entry.name == part for entry in iterator)
        except FileNotFoundError:
            break
        except OSError as exc:
            return PathIdentityIssue(
                kind="inspection_error",
                requested=current,
                detail=str(exc),
            )

        if not exact_found:
            try:
                requested_stat = requested.lstat()
            except FileNotFoundError:
                break
            except OSError as exc:
                return PathIdentityIssue(
                    kind="inspection_error",
                    requested=requested,
                    detail=str(exc),
                )

            matches: list[Path] = []
            try:
                with os.scandir(current) as iterator:
                    for entry in iterator:
                        try:
                            if os.path.samestat(
                                requested_stat,
                                entry.stat(follow_symlinks=False),
                            ):
                                matches.append(current / entry.name)
                        except OSError:
                            continue
            except OSError as exc:
                return PathIdentityIssue(
                    kind="inspection_error",
                    requested=current,
                    detail=str(exc),
                )
            return PathIdentityIssue(
                kind="alias",
                requested=requested,
                actual=matches[0] if len(matches) == 1 else None,
                detail=(
                    "filesystem identity matches multiple stored entries: "
                    + ", ".join(path.name for path in matches)
                    if len(matches) > 1
                    else None
                ),
            )

        try:
            metadata = requested.lstat()
        except OSError as exc:
            return PathIdentityIssue(
                kind="inspection_error",
                requested=requested,
                detail=str(exc),
            )
        if stat.S_ISLNK(metadata.st_mode):
            return PathIdentityIssue(kind="symlink", requested=requested)
        if _is_reparse_point(metadata) or _is_junction(requested):
            return PathIdentityIssue(kind="reparse_point", requested=requested)
        current = requested
    return None


def _is_reparse_point(metadata: os.stat_result) -> bool:
    """Whether Windows marked a lexical entry as a filesystem reparse point."""

    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))
    return bool(attributes & reparse_flag)


def _is_junction(path: Path) -> bool:
    """Reject Windows junctions even when their stat facade omits the flag."""

    predicate = getattr(path, "is_junction", None)
    if predicate is None:
        return False
    try:
        return bool(predicate())
    except OSError:
        return True


def configured_manifest_identity(context: object) -> object | None:
    """Return the stable logical manifest identity for a scan context.

    Committed-head verification scans load files from temporary archives.
    Their physical ``ScanContext.config_path`` must never be suffix-matched
    against repository-relative changed paths; ``VerificationContext`` carries
    the exact repository-relative identity for that comparison.
    """

    verification = getattr(context, "verification", None)
    logical = getattr(verification, "configured_manifest_path", None)
    if logical:
        return logical
    return getattr(context, "config_path", None)


def is_context_configured_manifest(context: object, path: str) -> bool:
    """Match a changed path against the best identity carried by a scan."""

    verification = getattr(context, "verification", None)
    logical = getattr(verification, "configured_manifest_path", None)
    if logical:
        if is_configured_manifest(logical, path, exact=True):
            return True
        physical = getattr(context, "config_path", None)
        logical_path = Path(str(logical))
        physical_path = Path(str(physical)) if physical is not None else None
        if (
            physical_path is not None
            and physical_path.is_absolute()
            and not logical_path.is_absolute()
            and logical_path.parts
            and ".." not in logical_path.parts
        ):
            root = physical_path
            for _part in logical_path.parts:
                root = root.parent
            return is_configured_manifest(logical, path, workspace=root)
        return False
    # Plain ``scan --changed-files`` predates the logical identity field and
    # may carry an absolute physical config path. Preserve that compatibility
    # fallback outside verifier contexts that have the exact identity.
    return is_configured_manifest(getattr(context, "config_path", None), path)


def is_configured_manifest(
    config_path: object | None,
    path: str,
    *,
    workspace: object | None = None,
    exact: bool = False,
) -> bool:
    """Whether a changed-file path is the manifest *this run* loaded as its gate.

    The trust-root table only knows ``**/shipgate.yaml``. A repository pointed
    at ``--config new-gate.yml`` therefore had no manifest trust root at all:
    the file defining its gate could be added or rewritten without a finding,
    and the release substrate carried nothing for the merge projection to fail
    closed on. Whatever a run loaded as the gate *is* the gate.

    Both sides are normalized before comparison, so an equivalent spelling —
    ``docs/engineering/../manifest.yaml`` for ``docs/manifest.yaml`` — cannot
    slip past by looking textually different from the changed path.

    ``workspace`` resolves both sides against the same lexical root and rejects
    either one when the normalized path escapes that boundary. Callers with a
    stable repository-relative identity can instead request ``exact=True``.
    The legacy no-workspace fallback remains for plain ``scan --changed-files``
    contexts that carry a physical config path, but verifier checks must not
    use it because it can classify an unrelated same-named file.
    """

    if config_path is None:
        return False
    configured = _normalized(config_path)
    candidate = _normalized(path)
    if not configured or not candidate:
        return False
    if workspace is not None:
        root = _normalized(workspace)
        if not root:
            return False
        absolute_config = _under_workspace(root, configured)
        absolute_candidate = _under_workspace(root, candidate)
        if not absolute_config or not absolute_candidate:
            return False
        if absolute_config == absolute_candidate:
            return True
        # Git may store a case- or Unicode-precomposed spelling that the host
        # resolves to the same exact worktree entry. Recognize only that
        # filesystem-proved identity; distinct files on a case-sensitive host
        # remain distinct.
        try:
            return os.path.samestat(
                os.lstat(absolute_config),
                os.lstat(absolute_candidate),
            )
        except OSError:
            return False
    if candidate == configured or exact:
        return candidate == configured
    return configured.endswith(f"/{candidate}") or candidate.endswith(f"/{configured}")


__all__ = [
    "IdentityBoundReadSession",
    "IdentityReadBudget",
    "IdentityReadBudgetExceeded",
    "PROTECTED_FILE_EDITS",
    "PathIdentityIssue",
    "configured_manifest_identity",
    "inspect_lexical_path_identity",
    "is_context_configured_manifest",
    "is_configured_manifest",
    "is_portable_repo_path",
    "read_absolute_identity_bound_text",
    "read_identity_bound_bytes",
    "read_identity_bound_text",
    "TRUST_ROOT_SURFACES",
    "trust_root_class_for",
]
