"""The one place a command refuses an unusable ``--workspace``.

Kept as a leaf module — typer plus
:mod:`agents_shipgate.core.workspace_input` — so that every command can call
it as its first statement without importing the scan machinery, and so the
refusal is provably ahead of any output directory, manifest write, or git
probe.
"""

from __future__ import annotations

from pathlib import Path

import typer

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.workspace_input import require_workspace_directory

__all__ = ["require_workspace"]


def require_workspace(
    workspace: Path | None, *, option: str = "--workspace"
) -> Path | None:
    """Return the resolved workspace, or refuse the invocation with exit 2.

    Call this before anything else in a command body. ``verify --preview`` is
    the reason for the placement: it resolved an output directory from a
    ``--workspace`` that did not exist, created the entire four-deep path,
    wrote a full artifact set into it, and exited 0 — so a typo produced a
    confident result about a workspace that was never there, and in CI it
    looked healthy on both signals a caller can gate on (#389).

    An absent workspace is an invocation error, not an evaluation outcome, so
    this exits 2 (``config_error``) on every command — ``--preview``
    included. The preview's documented "always exits 0" is a promise about
    workspaces it evaluated; there is nothing here to evaluate, and the exit
    code is the signal the silent 0 was misleading.

    ``None`` passes through: commands whose ``--workspace`` is optional use it
    to mean "discover from the manifest instead", which is not a workspace
    claim to check.
    """

    from agents_shipgate.cli._helpers import _echo_next_action_hint
    from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
    from agents_shipgate.schemas.diagnostics import NextAction

    if workspace is None:
        return None
    try:
        return require_workspace_directory(workspace, option=option)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        action = NextAction(
            kind="review",
            why=str(exc),
            expects=(
                f"Re-run with {option} pointing at an existing checkout "
                "directory."
            ),
        )
        _echo_next_action_hint([action])
        emit_agent_mode_error_action(
            "config_error",
            message=str(exc),
            exit_code=2,
            action=action,
        )
        raise typer.Exit(2) from exc
