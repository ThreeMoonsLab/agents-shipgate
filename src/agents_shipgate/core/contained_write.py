"""Write and remove output files only as plain files beneath a trusted root.

Shipgate writes its reports into a directory that repository content can
populate. A committed ``report.md`` that is a symbolic link, or a reports
directory that is one, would otherwise carry a scan's write — or its cleanup —
to a file outside the workspace (#1031). Every report, packet, control-artifact
and cleanup path a scan touches goes through this module, which refuses before
anything is written or removed when:

* the destination is not spelled under the trusted root, or climbs out of it
  with ``..``;
* any component between the root (exclusive) and the destination (inclusive)
  is a symbolic link — a link that stays inside the workspace included, since
  nothing about a link's target is trusted;
* the destination exists and is not a regular file, or is a hard link to
  another file.

The checks are repeated where the platform allows it at the moment of the
write: the destination is opened with ``O_NOFOLLOW`` and its type and link
count are re-read from the open descriptor before it is truncated.

What counts as the trusted root is the caller's decision: the manifest's
directory for ``output.directory``, the directory itself for an output
location the operator typed (``--out``), and a file's own directory where a
caller names nothing, so a link at the destination is refused everywhere.

A leaf module apart from the error type and the shared symlink walk.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from pathlib import Path

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.paths import first_symlink_in_chain

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_BINARY = getattr(os, "O_BINARY", 0)


class UnsafeOutputPathError(ConfigError):
    """An output destination that could carry a write or a removal elsewhere."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"Refusing to write or remove {path}: {reason}. Shipgate writes "
            "output only as plain files under plain directories; replace the "
            "link or file, or choose another output directory with --out."
        )


def check_output_path(path: Path, *, root: Path) -> Path:
    """The destination spelled under the resolved ``root``, or a refusal.

    Nothing is created or opened. A destination that does not exist yet is
    accepted when every component that does exist is a plain directory.
    """

    root_real = root.resolve()
    target = _spelled_under(path, root, root_real)
    link = first_symlink_in_chain(target, root_real)
    if link is not None:
        raise UnsafeOutputPathError(path, f"{link} is a symbolic link")
    try:
        metadata = os.lstat(target)
    except FileNotFoundError:
        return target
    except NotADirectoryError as exc:
        raise UnsafeOutputPathError(path, "a component of its parent is not a directory") from exc
    _require_plain_file(path, metadata)
    return target


def check_output_directory(directory: Path, *, root: Path) -> Path:
    """Like :func:`check_output_path`, for a directory output files go into."""

    root_real = root.resolve()
    target = _spelled_under(directory, root, root_real)
    link = first_symlink_in_chain(target, root_real)
    if link is not None:
        raise UnsafeOutputPathError(directory, f"{link} is a symbolic link")
    with contextlib.suppress(FileNotFoundError):
        if not stat.S_ISDIR(os.lstat(target).st_mode):
            raise UnsafeOutputPathError(directory, "it exists and is not a directory")
    return target


def refuse_links_inside(directory: Path, repository: Path) -> None:
    """Refuse an output directory whose components inside ``repository`` include a link.

    For an output location the operator typed or a command defaulted —
    ``scan --out``, ``verify --out`` and ``verify``'s default reports
    directory — before it is resolved. The operator chooses where the path
    goes until it enters the repository, so a link *above* the repository is
    theirs. Below that point, every component is repository content a pull
    request can commit, and a link there would carry the write out of the
    checkout (#1031). So the shallowest ancestor of ``directory`` that
    resolves into ``repository`` is found, and everything from there down is
    walked for a link, a junction included. A ``directory`` that never enters
    the repository is not checked here.
    """

    root_real = repository.resolve()
    for anchor in (*reversed(directory.parents), directory):
        try:
            anchor_real = anchor.resolve()
        except OSError:
            continue
        if anchor_real != root_real and root_real not in anchor_real.parents:
            continue
        remainder = directory.relative_to(anchor).parts
        link = first_symlink_in_chain(anchor_real.joinpath(*remainder), root_real)
        if link is not None:
            raise UnsafeOutputPathError(
                directory, f"{link} is a symbolic link inside the repository {root_real}"
            )
        return


def write_contained(
    path: Path,
    data: str | bytes,
    *,
    root: Path | None = None,
    atomic: bool = False,
    encoding: str = "utf-8",
) -> None:
    """Write ``data`` to ``path`` as a plain file under ``root``.

    ``root`` defaults to ``path``'s own directory, which refuses a link or a
    special file at the destination and trusts every directory above it.
    Text is written as :meth:`Path.write_text` writes it, newline translation
    included. ``atomic`` writes a temporary file beside the destination and
    renames it into place, which never follows a link at the destination.
    """

    if root is None:
        root = path.parent
    payload = data.replace("\n", os.linesep).encode(encoding) if isinstance(data, str) else data
    Path(root).mkdir(parents=True, exist_ok=True)
    root_real = Path(root).resolve()
    target = check_output_path(path, root=root)
    _make_directories(path, target.parent, root_real)
    if atomic:
        _replace(path, target, payload)
    else:
        _overwrite(path, target, payload)


def remove_contained(path: Path, *, root: Path | None = None) -> bool:
    """Remove the plain file at ``path`` under ``root``; ``False`` if there is none.

    A link at the destination is refused rather than removed, as a link
    anywhere above it is: cleanup must not reach a file outside the root, and
    it does not quietly drop what the repository put there either. A
    directory at the destination is left alone.
    """

    if root is None:
        root = path.parent
    root_real = Path(root).resolve()
    target = _spelled_under(path, root, root_real)
    link = first_symlink_in_chain(target, root_real)
    if link is not None:
        raise UnsafeOutputPathError(path, f"{link} is a symbolic link")
    try:
        metadata = os.lstat(target)
    except (FileNotFoundError, NotADirectoryError):
        return False
    if stat.S_ISDIR(metadata.st_mode):
        return False
    _require_plain_file(path, metadata)
    try:
        # ``Path.unlink``, as every cleanup did before this module: a removal
        # that fails raises its ``OSError`` to the caller, which maps it to its
        # own documented failure (``ArtifactLifecycleError`` for verifier
        # artifacts) rather than to an output-path refusal.
        target.unlink()
    except FileNotFoundError:
        return False
    return True


def _spelled_under(path: Path, root: Path, root_real: Path) -> Path:
    for anchor in (root, root_real):
        try:
            relative = path.relative_to(anchor)
        except ValueError:
            continue
        if ".." in relative.parts:
            raise UnsafeOutputPathError(path, f"its spelling climbs out of {root_real}")
        return root_real.joinpath(*relative.parts)
    raise UnsafeOutputPathError(path, f"it is not inside {root_real}")


def _require_plain_file(path: Path, metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafeOutputPathError(path, "it is a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeOutputPathError(path, "it exists and is not a regular file")
    if metadata.st_nlink > 1:
        raise UnsafeOutputPathError(path, "it is a hard link shared with another file")


def _make_directories(path: Path, directory: Path, root_real: Path) -> None:
    """Create the directories below ``root_real``, each one checked as created."""

    current = root_real
    for part in directory.relative_to(root_real).parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        metadata = os.lstat(current)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or current.is_junction()
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise UnsafeOutputPathError(path, f"{current} is not a plain directory")


def _overwrite(path: Path, target: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | _NOFOLLOW | _NONBLOCK | _BINARY
    try:
        descriptor = os.open(target, flags, 0o666)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeOutputPathError(path, "it is a symbolic link") from exc
        raise
    try:
        _require_plain_file(path, os.fstat(descriptor))
        os.ftruncate(descriptor, 0)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)


def _replace(path: Path, target: Path, payload: bytes) -> None:
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    temporary: Path | None = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Re-read just before the rename: a link planted at the destination
        # since the check is replaced, never followed, but is still refused.
        check_output_path(target, root=target.parent)
        os.replace(name, target)
        temporary = None
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


__all__ = [
    "UnsafeOutputPathError",
    "check_output_directory",
    "check_output_path",
    "refuse_links_inside",
    "remove_contained",
    "write_contained",
]
