from __future__ import annotations

import shlex
from pathlib import Path

# Reward-hacking moves that are never acceptable for an autonomous coding
# agent. These strings are shared by verify and preflight surfaces.
FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "Do not suppress the finding (checks.ignore in shipgate.yaml).",
    "Do not lower severity or add a waiver just to pass the gate.",
    "Do not invent or assume approval, idempotency, or audit evidence you "
    "cannot prove from the code.",
    "Do not weaken the release policy, CI gate, or agent instructions that "
    "evaluate this change.",
)

DEFAULT_VERIFY_COMMAND = "agents-shipgate verify --json"


def git_root_for(path: Path) -> Path | None:
    """The nearest ancestor containing ``.git``, or ``None``.

    A pure filesystem walk: this runs inside `check` and `preflight`, neither
    of which should spawn git just to phrase a command.
    """

    try:
        current = path.resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def verify_command_for(
    workspace: Path | None,
    config: Path | None,
    *,
    extra: tuple[str, ...] = (),
) -> str:
    """The verify invocation that evaluates the given target.

    Two things make this less obvious than it looks:

    ``verify`` resolves a relative ``--config`` against the repository root,
    not against ``--workspace``. Echoing the caller's own relative spelling
    therefore silently verified the *root* gate when the request named a
    nested one — the command succeeded and reported on the wrong manifest. The
    config is emitted relative to the git root when one can be found, and
    absolute otherwise, so it always names the file that was actually checked.

    The workspace is emitted as given: it is the anchor the caller already
    proved resolvable from where they are standing.
    """

    if workspace is None and config is None:
        return DEFAULT_VERIFY_COMMAND
    parts = ["agents-shipgate", "verify"]
    if workspace is not None:
        parts.extend(["--workspace", shlex.quote(str(workspace))])
    if config is not None:
        parts.extend(["--config", shlex.quote(_config_for_verify(workspace, config))])
    parts.extend(extra)
    parts.append("--json")
    return " ".join(parts)


def _config_for_verify(workspace: Path | None, config: Path) -> str:
    absolute = config if config.is_absolute() else (workspace or Path(".")) / config
    try:
        absolute = absolute.resolve()
    except OSError:
        return config.as_posix()
    root = git_root_for(absolute.parent)
    if root is not None:
        try:
            return absolute.relative_to(root).as_posix()
        except ValueError:
            pass
    return absolute.as_posix()


__all__ = [
    "DEFAULT_VERIFY_COMMAND",
    "FORBIDDEN_SHORTCUTS",
    "git_root_for",
    "verify_command_for",
]
