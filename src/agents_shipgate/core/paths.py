"""Two path helpers every layer shares: showing a path, and finding a link in it.

Both used to be copied into the modules that needed them — a root-relative
display path into seventeen modules and the symlink-chain walk into seven — and
the copies had begun to differ in ways nobody chose (#1031). Each difference a caller depends on is
now a named argument here, so a reader sees it at the call site.

A leaf module: standard library only, so adapters, checks and the CLI can all
import it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal


def display_path(
    path: Path,
    root: Path,
    *,
    resolve: bool = True,
    outside: Literal["as_given", "resolved"] = "as_given",
) -> str:
    """``path`` relative to ``root`` in forward-slash form, for a report or a message.

    ``resolve`` compares the two after resolving both, which is right for a
    path read from disk that may be spelled through an alias (``/tmp`` and
    ``/private/tmp``). ``resolve=False`` compares them as spelled: ``verify``
    and ``preflight`` hold paths whose identity was established lexically, and
    resolving would show the target of the very link a message is about.

    A path outside ``root`` is shown as given (``outside="as_given"``) or as
    its resolved absolute form (``outside="resolved"``), which the adapters
    whose findings name an input file outside the manifest directory use.

    Every branch returns forward slashes. Some copies returned the platform's
    separator for a path outside the root, so on Windows one function emitted
    two spellings depending on where the file was.
    """

    candidate, anchor = (path.resolve(), root.resolve()) if resolve else (path, root)
    try:
        return candidate.relative_to(anchor).as_posix()
    except ValueError:
        return (path.resolve() if outside == "resolved" else path).as_posix()


def first_symlink_in_chain(
    path: Path,
    root: Path,
    *,
    outside: Literal["refuse", "defer"] = "refuse",
) -> Path | None:
    """The first symlink between ``root`` (exclusive) and ``path`` (inclusive).

    Returns ``None`` when no existing component is a link. Walking every
    component, not only the last, is what stops a directory link from routing
    a write out of the workspace: with ``.github -> /tmp/outside``,
    ``.github/pull_request_template.md`` is not itself a link. A component that
    does not exist cannot be a link, and nothing below it can exist, so the
    walk stops there. Each component is ``lstat``-ed; no link target is
    followed to decide what it is.

    ``path`` is compared with ``root`` lexically, as ``root`` is spelled and
    as it resolves, and the components below it are walked from the resolved
    root. A ``path`` spelled under neither cannot be walked, and ``outside``
    says what that means for the caller:

    * ``"refuse"`` (the default) returns ``path`` itself, so the caller refuses
      it as it would a link. Every writer uses this: nothing proves a path
      spelled elsewhere free of links.
    * ``"defer"`` walks the resolved path when it lies under ``root`` and
      otherwise returns ``None``. The adoption-kit reader uses this: each of
      its reads is followed by a containment check on the resolved path,
      which reports a path outside the workspace as being outside it rather
      than as a link.
    """

    root_real = root.resolve()
    parts = _parts_under(path, root, root_real)
    if parts is None:
        if outside == "refuse":
            return path
        try:
            parts = path.resolve().relative_to(root_real).parts
        except ValueError:
            return None
    current = root_real
    for part in parts:
        current = current / part
        # A Windows junction redirects like a link but is not one to
        # ``is_symlink``; both are refused.
        if current.is_symlink() or current.is_junction():
            return current
        if not current.exists():
            return None
    return None


def _parts_under(path: Path, root: Path, root_real: Path) -> tuple[str, ...] | None:
    """``path``'s components below ``root``, spelled under it as given or resolved."""

    for anchor in (root, root_real):
        try:
            return path.relative_to(anchor).parts
        except ValueError:
            continue
    return None


__all__ = ["display_path", "first_symlink_in_chain"]
