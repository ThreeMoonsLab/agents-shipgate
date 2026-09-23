"""The comparison's changed-file set, and a bounded look at both of its sides (#821).

A host comparison names the changed inputs no reader of this entry read, and
it can do that only from the change itself: the paths the comparison's own
base and head differ in. This module lists them and answers the two questions
the candidate rules in :mod:`agents_shipgate.core.unread_inputs` ask of a side
— which of these paths exist, and what are the bytes of these few files — with
the same bounded Git plumbing the identity question uses (#812). It runs Git
through :mod:`agents_shipgate.cli.verify.git`'s collector only, never follows
a link, and never reads beyond the repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from agents_shipgate.cli.verify.git import (
    _DETERMINISTIC_DIFF_OPTIONS,
    _DIFF_METADATA_LIMIT,
    _IDENTITY_ARGUMENT_BYTES,
    _IDENTITY_BATCH_BYTES,
    _IDENTITY_FILE_BYTES,
    _IDENTITY_LISTING_BYTES,
    _SAFE_DIFF_CONFIG,
    _blob_contents,
    _paths_from_name_status,
    _read_regular_file,
    _reject_unbound_diff_configuration,
    _run_git_bounded_output,
    _run_git_bounded_result,
    _TreeBlob,
    commit_sha,
    working_tree_paths,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.unread_inputs import ChangedInputs


def comparison_changed_paths(
    workspace: Path, base: str, head: str | None, *, exclude: Path | None = None
) -> tuple[str, ...]:
    """Every path the comparison's change touches, both names of a rename (#821).

    ``head=None`` is the working tree: tracked changes against ``base`` and
    untracked files, as :func:`working_tree_paths` lists them, without
    ``exclude``. Otherwise the committed ``base``..``head`` change. Metadata
    only, within the diff metadata bound; raises ``ConfigError`` when the set
    cannot be listed in full.
    """

    if head is None:
        return tuple(working_tree_paths(workspace, comparison_ref=base, exclude=exclude))
    _reject_unbound_diff_configuration(workspace)
    base_commit, head_commit = commit_sha(workspace, base), commit_sha(workspace, head)
    if base_commit is None or head_commit is None:
        raise ConfigError("The comparison's commits are not available locally")
    names = _run_git_bounded_result(
        workspace,
        [
            *_SAFE_DIFF_CONFIG,
            "diff",
            *_DETERMINISTIC_DIFF_OPTIONS,
            "--name-status",
            "-z",
            base_commit,
            head_commit,
        ],
        max_output_bytes=_DIFF_METADATA_LIMIT,
    )
    if names.payload is None:
        raise ConfigError("The comparison's changed paths could not be listed within bounds")
    return tuple(sorted(_paths_from_name_status(names.payload)))


def _tree_entries(
    workspace: Path, commit: str, paths: Sequence[str]
) -> dict[str, tuple[str, str, str, str]] | None:
    """``(mode, kind, oid, size)`` of each path that is an entry of ``commit``'s tree.

    Literal pathspecs, split as :func:`_regular_tree_blobs` splits them. Git
    does not descend through a link, so a path beneath one has no entry.
    ``None`` means a listing failed.
    """

    wanted = {path.encode("utf-8"): path for path in paths}
    entries: dict[str, tuple[str, str, str, str]] = {}
    chunk: list[str] = []
    length = 0
    for index, path in enumerate(paths):
        chunk.append(path)
        length += len(path.encode("utf-8")) + 3
        if index + 1 < len(paths) and length < _IDENTITY_ARGUMENT_BYTES:
            continue
        listing = _run_git_bounded_output(
            workspace,
            ["--literal-pathspecs", "ls-tree", "-l", "-z", "--full-tree", commit, "--", *chunk],
            max_output_bytes=_IDENTITY_LISTING_BYTES,
        )
        if listing is None:
            return None
        for record in listing.split(b"\0"):
            header, separator, name = record.partition(b"\t")
            fields = header.split()
            if separator and name in wanted and len(fields) == 4:
                entries[wanted[name]] = tuple(  # type: ignore[assignment]
                    field.decode("ascii", errors="replace") for field in fields
                )
        chunk, length = [], 0
    return entries


class _ComparisonSides:
    """Presence and bounded reads on each side of one comparison, never through a link (#821).

    ``base`` is a commit; ``head`` is a commit or, as ``None``, the working
    tree. Commit sides are answered from one tree listing and one
    `cat-file --batch` per question; the working tree from `lstat` and a
    no-follow read of a regular file whose parents are not links. A file past
    the host reader's per-file bound, or past the batch bound, is not read.
    """

    def __init__(self, workspace: Path, base: str, head: str | None) -> None:
        self._workspace = workspace
        self._commits = {"base": commit_sha(workspace, base), "head": None}
        if head is not None:
            self._commits["head"] = commit_sha(workspace, head)
            if self._commits["head"] is None:
                raise ConfigError("The comparison's head commit is not available locally")
        if self._commits["base"] is None:
            raise ConfigError("The comparison's base commit is not available locally")
        self._worktree = head is None
        self._entries: dict[str, dict[str, tuple[str, str, str, str]]] = {"base": {}, "head": {}}

    def _valid(self, path: str) -> bool:
        relative = PurePosixPath(path)
        if not path or relative.is_absolute() or ".." in relative.parts or "\\" in path or "\0" in path:
            return False
        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True

    def _worktree_target(self, path: str) -> Path | None:
        parts = PurePosixPath(path).parts
        try:
            if any(
                (self._workspace / Path(*parts[:index])).is_symlink()
                for index in range(1, len(parts))
            ):
                return None
        except OSError:
            return None
        return self._workspace / path

    def present(self, side: str, paths: Sequence[str]) -> set[str]:
        wanted = [path for path in dict.fromkeys(paths) if self._valid(path)]
        if side == "head" and self._worktree:
            found: set[str] = set()
            for path in wanted:
                target = self._worktree_target(path)
                try:
                    if target is not None:
                        target.lstat()
                        found.add(path)
                except OSError:
                    continue
            return found
        commit = self._commits[side]
        entries = _tree_entries(self._workspace, commit, wanted) if commit and wanted else {}
        if entries is None:
            raise ConfigError("The comparison's tree could not be listed within bounds")
        self._entries[side].update(entries)
        return set(entries)

    def read(self, side: str, paths: Sequence[str]) -> dict[str, bytes]:
        wanted = [path for path in dict.fromkeys(paths) if self._valid(path)]
        if side == "head" and self._worktree:
            contents: dict[str, bytes] = {}
            for path in wanted:
                target = self._worktree_target(path)
                try:
                    regular = target is not None and not target.is_symlink() and target.is_file()
                except OSError:
                    regular = False
                data = _read_regular_file(target) if regular and target is not None else None
                if data is not None:
                    contents[path] = data
            return contents
        missing = [path for path in wanted if path not in self._entries[side]]
        if missing:
            self.present(side, missing)
        blobs: dict[str, _TreeBlob] = {}
        total = 0
        for path in wanted:
            entry = self._entries[side].get(path)
            if entry is None:
                continue
            mode, kind, oid, size = entry
            if kind != "blob" or mode not in {"100644", "100755"} or not size.isdigit():
                continue
            if int(size) > _IDENTITY_FILE_BYTES or total + int(size) > _IDENTITY_BATCH_BYTES:
                continue
            total += int(size)
            blobs[path] = _TreeBlob(mode=mode, oid=oid, size=int(size))
        data = _blob_contents(self._workspace, list(blobs.values()))
        return {path: data[blob.oid] for path, blob in blobs.items() if blob.oid in data}


def comparison_changed_inputs(
    workspace: Path, base: str, head: str | None, *, exclude: Path | None = None
) -> ChangedInputs:
    """The comparison's changed-file set and a bounded look at both sides (#821).

    A :class:`~agents_shipgate.core.unread_inputs.ChangedInputs` whose
    ``paths`` is ``None`` when the set could not be listed, so the coverage it
    feeds says the changed inputs were not examined rather than that none of
    them is unread. A later presence or read failure is raised to the caller.
    """

    try:
        paths = comparison_changed_paths(workspace, base, head, exclude=exclude)
        sides = _ComparisonSides(workspace, base, head)
    except (ConfigError, OSError, ValueError):
        return ChangedInputs(paths=None)
    return ChangedInputs(paths=paths, present=sides.present, read=sides.read)


__all__ = ["comparison_changed_inputs", "comparison_changed_paths"]
