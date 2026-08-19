"""One classification of the ``--workspace`` argument, shared by every command.

A workspace is an input Shipgate *reads*, and like every other input it has
distinct failure states that mean distinct things to whoever has to fix it:
the path is not there, the path is there but is a file, the path is there but
cannot be read. Before this module each command discovered the difference on
its own — or did not. ``verify --preview`` created a four-deep directory tree
for a mistyped path and exited 0; ``init --write``, ``audit --host``, and the
verification workers raised a bare ``FileNotFoundError`` traceback;
``mcp audit`` answered ``decision: allow`` about a directory that did not
exist; and every command routed through :func:`ensure_git_workspace` reported
an absent path as "not inside a git checkout", which asserts the path exists
and sends the reader to install git rather than to fix the typo (#389, and
the second instance recorded on #384).

The rule this module enforces: **an absent ``--workspace`` is an invocation
error, decided before anything is created.** It is not a preview outcome, not
a gate verdict, and not a shape complaint about a directory that was never
there.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Literal

from agents_shipgate.core.errors import ConfigError

WorkspacePresence = Literal["present", "absent", "not_a_directory", "unreadable"]

__all__ = [
    "WorkspacePresence",
    "classify_workspace",
    "require_workspace_directory",
    "workspace_input_error",
]


def classify_workspace(workspace: Path) -> WorkspacePresence:
    """Report what the filesystem says about ``workspace``.

    Uses ``os.stat`` rather than ``Path.exists()`` so that a permission or
    I/O failure is reported as itself instead of silently reading as absent:
    a directory you cannot traverse is a different repair from one that is
    not there.
    """

    try:
        stat_result = os.stat(workspace)
    except FileNotFoundError:
        return "absent"
    except NotADirectoryError:
        # A path component that is a file, e.g. ``repo/shipgate.yaml/src``.
        return "not_a_directory"
    except OSError:
        return "unreadable"
    if not stat.S_ISDIR(stat_result.st_mode):
        return "not_a_directory"
    return "present"


def workspace_input_error(
    workspace: Path, *, option: str = "--workspace"
) -> ConfigError | None:
    """The invocation error for an unusable ``workspace``, or ``None``.

    Pure: it decides, it does not print, exit, or create anything. Callers
    that already sit inside a ``ConfigError`` boundary raise the result;
    the CLI guard turns it into the standard agent-mode error line.
    """

    presence = classify_workspace(workspace)
    if presence == "present":
        return None
    display = _display_path(workspace)
    if presence == "absent":
        return ConfigError(
            f"{option} does not exist: {display}. Point it at an existing "
            "checkout — clone or create the directory first. Nothing was "
            "written."
        )
    if presence == "not_a_directory":
        return ConfigError(
            f"{option} is not a directory: {display}. Point it at the "
            "checkout root rather than at a file inside it."
        )
    return ConfigError(
        f"{option} could not be read: {display}. Check the permissions on "
        "the path and every directory above it."
    )


def require_workspace_directory(
    workspace: Path, *, option: str = "--workspace"
) -> Path:
    """Return the resolved workspace, or raise the invocation error.

    Resolution happens *after* the check so the message names the path the
    caller typed rather than a normalized one they never wrote.
    """

    error = workspace_input_error(workspace, option=option)
    if error is not None:
        raise error
    try:
        return workspace.resolve()
    except OSError:  # pragma: no cover - resolvable by construction above
        return workspace


def _display_path(workspace: Path) -> str:
    """Absolutize for the message without requiring the path to exist.

    ``Path.resolve()`` is fine on a missing path, but a caller who typed a
    relative ``--workspace`` needs to see which directory it was relative
    *to* — that is the whole content of the "ran the preview before the
    clone" report.
    """

    if workspace.is_absolute():
        return str(workspace)
    return f"{workspace} (resolved to {Path(os.path.abspath(workspace))})"
