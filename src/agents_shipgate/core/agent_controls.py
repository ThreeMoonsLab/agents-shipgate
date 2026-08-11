from __future__ import annotations

import os
from pathlib import Path

from agents_shipgate.invocation import render_command, retarget_command

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

# The canonical spelling, as installed by the wheel. Every command this module
# hands out is rendered through the invocation policy before it leaves, so a
# run started with ``python -m agents_shipgate`` proposes a command that
# environment can actually execute (#322). The constant itself stays canonical:
# it is also the documented spelling, and the policy is a no-op for it whenever
# a console script really is the way in.
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
    base: str | None = None,
    head: str | None = None,
    extra: tuple[str, ...] = (),
) -> str:
    """The verify invocation that evaluates the given target.

    Every argument — including each element of ``extra`` — is one **raw** argv
    token, quoted here exactly once. Callers must not pre-quote: a value that
    arrives already shell-quoted is quoted a second time, and the reader that
    follows the command gets the quote characters as part of the value (a
    ``--out`` path that names a directory literally called ``'/tmp/x y'``).

    Two more things make this less obvious than it looks:

    ``verify`` resolves a relative ``--config`` against the repository root,
    not against ``--workspace``. Echoing the caller's own relative spelling
    therefore silently verified the *root* gate when the request named a
    nested one — the command succeeded and reported on the wrong manifest. The
    config is emitted relative to the git root only when the workspace and
    config belong to that same repository. Otherwise it stays absolute so
    ``verify`` can reject a config outside the requested workspace instead of
    silently selecting a same-named file there.

    The workspace is anchored lexically to the invocation directory. Control
    commands are commonly executed after a tool/cwd transition; retaining a
    relative spelling would silently retarget the authorized operation.
    """

    if (base is None) != (head is None):
        raise ValueError("verify command requires both base and head, or neither")
    if workspace is None and config is None and base is None and not extra:
        return retarget_command(DEFAULT_VERIFY_COMMAND)
    args = ["verify"]
    if workspace is not None:
        args.extend(["--workspace", _cwd_anchored(workspace)])
    if config is not None:
        args.extend(["--config", _config_for_verify(workspace, config)])
    if base is not None and head is not None:
        args.extend(["--base", base, "--head", head])
    args.extend(extra)
    args.append("--json")
    return render_command(args)


def detect_command_for(workspace: Path | None) -> str:
    """The detect invocation for the same checkout as a boundary check."""

    requested_workspace = workspace or Path(".")
    return render_command(
        ["detect", "--workspace", _cwd_anchored(requested_workspace), "--json"],
        program="shipgate",
    )


def _cwd_anchored(path: Path) -> str:
    """Absolute lexical spelling that preserves symlink/``..`` traversal."""

    return str(path if path.is_absolute() else Path.cwd() / path)


def preview_command_for(
    workspace: Path | None,
    config: Path | None,
    *,
    base: str | None = None,
    head: str | None = None,
) -> str:
    """The verify-preview invocation for the same target and ref range."""

    return verify_command_for(
        workspace,
        config,
        base=base,
        head=head,
        extra=("--preview",),
    )


def _config_for_verify(workspace: Path | None, config: Path) -> str:
    requested_workspace = workspace or Path(".")
    lexical_workspace = Path(
        os.path.abspath(os.path.normpath(os.fspath(requested_workspace)))
    )
    try:
        workspace_anchor = requested_workspace.resolve()
    except OSError:
        workspace_anchor = lexical_workspace
    if config.is_absolute():
        lexical_config = Path(os.path.normpath(os.fspath(config)))
        try:
            workspace_tail = lexical_config.relative_to(lexical_workspace)
        except ValueError:
            absolute = lexical_config
        else:
            absolute = workspace_anchor / workspace_tail
    else:
        absolute = workspace_anchor / config
    # Normalize lexical ``.``/``..`` components without following the
    # configured manifest itself. Following a symlink here rewrites the
    # authorized command to its target and bypasses verify's lexical
    # configured-manifest rejection. Only the workspace anchor is canonical:
    # callers may spell that anchor through a symlink or filesystem alias, but
    # the manifest's own relative tail remains the identity verify must inspect.
    absolute = Path(os.path.abspath(os.path.normpath(os.fspath(absolute))))
    config_root = git_root_for(absolute.parent)
    workspace_root = git_root_for(workspace_anchor)
    if config_root is not None and config_root == workspace_root:
        try:
            return absolute.relative_to(config_root).as_posix()
        except ValueError:
            pass
    return absolute.as_posix()


__all__ = [
    "DEFAULT_VERIFY_COMMAND",
    "FORBIDDEN_SHORTCUTS",
    "detect_command_for",
    "git_root_for",
    "preview_command_for",
    "verify_command_for",
]
