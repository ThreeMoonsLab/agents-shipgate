from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from agents_shipgate.invocation import (
    invocation_prefix,
    join_argv,
    retarget_command,
    split_invocation,
)

AGENT_MODE_ENV_VAR = "AGENTS_SHIPGATE_AGENT_MODE"

# Environment variables that coding-agent harnesses export in every shell they
# spawn, and which agent each one identifies. Their presence auto-enables agent
# mode so agents get structured output without remembering to set
# AGENTS_SHIPGATE_AGENT_MODE, and it names the actor for commands that record
# one. Claude Code sets CLAUDECODE=1; Cursor sets CURSOR_TRACE_ID.
_ACTOR_BY_ENV_HINT: tuple[tuple[str, str], ...] = (
    ("CLAUDECODE", "claude-code"),
    ("CURSOR_TRACE_ID", "cursor"),
)

AGENT_ENV_HINTS = tuple(hint for hint, _actor in _ACTOR_BY_ENV_HINT)

# The actor assumed when the environment says nothing. Kept as the historical
# default so a plain shell behaves exactly as before.
DEFAULT_ACTOR = "codex"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def is_agent_mode(env: Mapping[str, str] | None = None) -> bool:
    """Whether agent mode is active for structured agent-facing output.

    An explicit ``AGENTS_SHIPGATE_AGENT_MODE`` value wins in both
    directions (truthy forces on, falsy forces off). Otherwise agent mode
    auto-enables when a known coding-agent harness variable from
    ``AGENT_ENV_HINTS`` is present and non-empty.
    """
    source = os.environ if env is None else env
    explicit = source.get(AGENT_MODE_ENV_VAR, "").strip().lower()
    if explicit in _TRUTHY:
        return True
    if explicit in _FALSY:
        return False
    return any(source.get(hint) for hint in AGENT_ENV_HINTS)


def detect_actor(env: Mapping[str, str] | None = None) -> str:
    """Which coding agent is running this command.

    ``check`` records the actor in its result and audit id, so a Claude Code
    run that defaults to ``codex`` mislabels every row it writes. The same
    harness variables that switch on agent mode also identify the harness, so
    detection costs nothing extra. Falls back to :data:`DEFAULT_ACTOR` when the
    environment says nothing; an explicit ``--agent`` always wins.
    """

    source = os.environ if env is None else env
    for hint, actor in _ACTOR_BY_ENV_HINT:
        if source.get(hint):
            return actor
    return DEFAULT_ACTOR


def emit_agent_mode_error_action(
    error_kind: str,
    *,
    message: object,
    exit_code: int,
    action: Any,
    **fields: object,
) -> None:
    """Emit a structured error whose recovery is one ranked ``NextAction``.

    ``docs/errors.json`` states that an agent-mode error line *always* carries
    both ``next_action`` (the single-string back-compat form) and
    ``next_actions`` (the ranked array). Emitting only the legacy string leaves
    every agent that consumes the documented array with nothing to route on, so
    this derives both from one object and they cannot disagree.
    """

    emit_agent_mode_error(
        error_kind,
        message=message,
        exit_code=exit_code,
        next_action=action.to_legacy_string(),
        next_actions=[action.model_dump(mode="json")],
        **fields,
    )


def emit_agent_mode_error(
    error_kind: str,
    *,
    message: object | None = None,
    exit_code: int | None = None,
    command: str | None = None,
    next_action: object | None = None,
    next_actions: object | None = None,
    diagnostics: object | None = None,
    artifacts: object | None = None,
    **fields: object,
) -> None:
    """Emit a structured one-line error for coding-agent callers."""
    if not is_agent_mode():
        return
    payload: dict[str, object] = {"error": error_kind}
    if message is not None:
        payload["message"] = str(message)
    if exit_code is not None:
        payload["exit_code"] = exit_code
    payload["command"] = command or _command_string()
    normalized_actions = _normalized_actions(next_actions) if next_actions is not None else None
    if next_action is not None:
        payload["next_action"] = _legacy_action(next_action, normalized_actions)
    if normalized_actions is not None:
        payload["next_actions"] = normalized_actions
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    if artifacts is not None:
        payload["artifacts"] = artifacts
    payload.update(fields)
    print(json.dumps(payload, default=str), file=sys.stderr)


def _normalized_actions(next_actions: object) -> object:
    """Apply the invocation policy at the wire, not at the call site.

    Most callers build :class:`~agents_shipgate.schemas.diagnostics.NextAction`
    objects, which normalize themselves. Some build the same shape as a plain
    dict — ``apply-patches`` does — and a hand-built dict has no way to opt
    into the policy, so it silently published a bare ``agents-shipgate ...``
    with no structured argv. Normalizing here means a surface cannot opt out by
    choosing a different construction, which is the only version of this that
    stays true as new surfaces are written.
    """

    if not isinstance(next_actions, list):
        return next_actions
    return [_normalized_action(item) for item in next_actions]


def _normalized_action(action: object) -> object:
    if not isinstance(action, dict):
        return action
    command = action.get("command")
    if action.get("kind") != "command" or not isinstance(command, str) or not command:
        return action
    normalized = dict(action)
    normalized["command"] = retarget_command(command)
    split = split_invocation(normalized["command"])
    if split is not None:
        normalized["executable"], normalized["args"] = split
    else:
        normalized.pop("executable", None)
        normalized.pop("args", None)
    return normalized


def _legacy_action(next_action: object, normalized_actions: object) -> object:
    """The single-string form, kept from contradicting the ranked array.

    ``docs/diagnostics.md`` says a rank-1 `command` action projects to this
    field *verbatim*. Normalizing the two independently broke that: on the
    ``apply-patches`` pre-v0.6 path the array's rank-1 was a retargeted scan
    command while the legacy field stayed a sentence, so a caller reading the
    documented back-compat field got prose where a runnable command existed —
    and, worse, a field that could still name a console script the array had
    already retargeted away from.

    A rank-1 command therefore wins outright. The other kinds are deliberately
    left alone: there is no program to disagree about, and the documented
    ``Edit <path>`` / ``Review: <why>`` projections are a lossy formatting of
    what a caller often says better ("Remove <path> and re-run scan" carries an
    instruction that ``Edit <path>`` drops). Prose is still retargeted so it
    cannot name a stale entry point either.
    """

    command = _rank_one_command(normalized_actions)
    if command is not None:
        return command
    if isinstance(next_action, str) and next_action:
        return retarget_command(next_action)
    return next_action


def _rank_one_command(normalized_actions: object) -> str | None:
    """The rank-1 action's command, when it has one."""

    if not isinstance(normalized_actions, list) or not normalized_actions:
        return None
    first = normalized_actions[0]
    if not isinstance(first, dict) or first.get("kind") != "command":
        return None
    command = first.get("command")
    return command if isinstance(command, str) and command else None


def _command_string() -> str:
    """The command that produced this error, spelled so it can be re-run.

    ``sys.argv[0]`` is not a program name in general: under
    ``python -m agents_shipgate`` it is the package's ``__main__.py``, which is
    neither executable on its own nor even safe to display — the double
    underscores turn it into ``**main**.py`` in any consumer that renders the
    field as Markdown. The invocation policy answers this once, for every
    surface that has to name the CLI.
    """

    return join_argv([*invocation_prefix(), *sys.argv[1:]])
