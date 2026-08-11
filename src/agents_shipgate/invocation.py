"""How this process re-enters its own CLI, and how emitted commands say so.

Every agent-facing surface Shipgate emits ends in a command the caller is
supposed to run next. Those commands were written as the literal string
``agents-shipgate ...`` because that is the console script the wheel installs.
When Shipgate is run from a source checkout with ``python -m agents_shipgate``
the console script may not exist at all, so the recovery loop hands the caller
a command its environment cannot execute (#322).

The same defect had a second face: agent-mode errors reported the running
command as ``Path(sys.argv[0]).name``, which under ``python -m`` is the literal
``__main__.py`` — not a program, and a string that silently corrupts to
``**main**.py`` in any consumer that renders it as Markdown.

This module is the single answer to "what tokens re-enter this CLI *here*":

* Entered through a console script → that script's name. This is the shipped
  path, and its emitted commands stay byte-for-byte what they were, so nothing
  downstream of a normal install changes.
* Entered through ``python -m agents_shipgate`` → :data:`sys.executable` plus
  ``-m agents_shipgate``. The interpreter is spelled by path rather than as a
  bare ``python`` on purpose: a bare name resolves through ``PATH`` and can
  easily land on a *different* interpreter that has no Shipgate installed, or
  an older one that does — which is the failure this module exists to prevent,
  reintroduced one layer down.
* ``AGENTS_SHIPGATE_CLI`` overrides both. It already exists as the operator
  override for the command written into Claude Code hooks
  (:mod:`agents_shipgate.cli.install_hooks`), and an operator who has told
  Shipgate how to invoke itself there means it everywhere.

Reconstructing an invocation is only ever a best effort — ``sys.argv[0]`` can
be an absolute path, a symlink, or a lie — so every fallback in
:func:`invocation_prefix` lands on the canonical console script rather than on
anything derived from an untrusted argv. The worst case is therefore today's
behaviour, never a newly-invented one.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from pathlib import PurePath

__all__ = [
    "CANONICAL_CONSOLE_SCRIPT",
    "CLI_OVERRIDE_ENV_VAR",
    "CONSOLE_SCRIPTS",
    "MODULE_ENTRY_POINT",
    "invocation_prefix",
    "is_console_script_invocation",
    "render_command",
    "retarget_command",
    "split_invocation",
]

#: Console scripts declared in ``pyproject.toml`` that both resolve to
#: ``agents_shipgate.cli.main:app``. Either name is a correct spelling of the
#: CLI whenever a wrapper exists, so both are recognised on the way in.
CONSOLE_SCRIPTS: tuple[str, ...] = ("agents-shipgate", "shipgate")

#: The spelling emitted when nothing better is known. Matches the name used in
#: every hand-written command string in the codebase and in the docs.
CANONICAL_CONSOLE_SCRIPT = "agents-shipgate"

#: The importable package that ``python -m`` enters.
MODULE_ENTRY_POINT = "agents_shipgate"

#: Operator override, shared with the Claude Code hook installer.
CLI_OVERRIDE_ENV_VAR = "AGENTS_SHIPGATE_CLI"

# Leading ``NAME=VALUE`` assignments in a command string. These are shell
# syntax, not argv, so a command carrying them has no shell-independent
# ``executable``/``args`` form — but the program token *after* them is still
# ours to retarget.
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*")

# Fallback when the interpreter cannot name itself (embedded interpreters
# leave ``sys.executable`` empty).
_FALLBACK_INTERPRETER = "python3"


def _program_name(token: str) -> str:
    """The bare program name a command token refers to.

    Handles absolute paths (``/usr/local/bin/agents-shipgate``) and the
    Windows ``.exe`` wrappers setuptools generates.
    """

    name = PurePath(token).name
    if name.lower().endswith(".exe"):
        name = name[: -len(".exe")]
    return name


def _interpreter() -> str:
    return sys.executable or _FALLBACK_INTERPRETER


def _override_prefix(env: Mapping[str, str]) -> tuple[str, ...] | None:
    raw = env.get(CLI_OVERRIDE_ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None
    return tuple(tokens) or None


def _entered_via_module(argv0: str, main_module: object) -> bool:
    """Whether this process was started as ``python -m agents_shipgate``.

    ``runpy`` records the fact on the synthetic ``__main__`` module's spec,
    which is authoritative; ``sys.argv[0]`` is only consulted as a fallback for
    launchers that rewrite the spec. Both are checked because either one alone
    has a blind spot, and the answer only ever selects between two spellings of
    the same CLI — it is never a trust decision.

    Both checks name *this* package explicitly. A bare ``__main__.py`` in argv
    means only "some ``python -m`` run": running the suite as
    ``python -m pytest`` produces exactly that, and would otherwise have every
    in-process command claim it was reached through ``-m agents_shipgate``.
    """

    spec = getattr(main_module, "__spec__", None)
    parent = getattr(spec, "parent", None) or getattr(spec, "name", None) or ""
    if str(parent).split(".")[0] == MODULE_ENTRY_POINT:
        return True
    path = PurePath(argv0)
    return path.name == "__main__.py" and path.parent.name == MODULE_ENTRY_POINT


def invocation_prefix(
    *,
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    main_module: object | None = None,
) -> tuple[str, ...]:
    """The argv tokens that re-enter this CLI in the current environment.

    The returned tuple is always directly executable: no shell is required to
    interpret it, and it never contains ``__main__.py``.
    """

    source_env = os.environ if env is None else env
    override = _override_prefix(source_env)
    if override is not None:
        return override

    source_argv = sys.argv if argv is None else argv
    argv0 = source_argv[0] if source_argv else ""

    if _program_name(argv0) in CONSOLE_SCRIPTS:
        return (_program_name(argv0),)

    module = sys.modules.get("__main__") if main_module is None else main_module
    if _entered_via_module(argv0, module):
        return (_interpreter(), "-m", MODULE_ENTRY_POINT)

    # Anything else — a test runner driving the Typer app in-process, an
    # embedding host, a launcher that rewrote argv — keeps the documented
    # spelling. Guessing from an unrecognised argv would emit a command that is
    # merely differently wrong.
    return (CANONICAL_CONSOLE_SCRIPT,)


def is_console_script_invocation(
    *,
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    main_module: object | None = None,
) -> bool:
    """Whether emitted commands should keep their hand-written spelling.

    True for the shipped path (and for the fallback), which is what keeps
    ``retarget_command`` a no-op wherever a console script really is the way
    in.
    """

    prefix = invocation_prefix(argv=argv, env=env, main_module=main_module)
    return len(prefix) == 1 and _program_name(prefix[0]) in CONSOLE_SCRIPTS


def render_command(
    args: Sequence[str],
    *,
    program: str = CANONICAL_CONSOLE_SCRIPT,
    prefix: Sequence[str] | None = None,
) -> str:
    """Render one Shipgate invocation as a runnable shell string.

    ``args`` are the subcommand and its options *without* the program name;
    each element is one argv token and is quoted as needed. The result is the
    canonical ``program`` spelling under a console-script run — byte for byte
    what these call sites emitted before — and the current invocation
    otherwise.
    """

    return retarget_command(shlex.join([program, *args]), prefix=prefix)


def retarget_command(
    command: str,
    *,
    prefix: Sequence[str] | None = None,
) -> str:
    """Rewrite a hand-written Shipgate command for the current invocation.

    Only the leading program token is touched, and only when it names one of
    our own console scripts; everything after it is spliced through byte for
    byte. That matters because many of these strings carry deliberately
    unquoted placeholders (``--from <report.json>``) that a re-tokenising
    rewrite would quote into something the reader is no longer meant to
    substitute.

    Commands that belong to another program (``pip install ...``) and commands
    whose program token cannot be located are returned unchanged.
    """

    tokens = tuple(prefix) if prefix is not None else invocation_prefix()
    if len(tokens) == 1 and _program_name(tokens[0]) in CONSOLE_SCRIPTS:
        # A console script is already what the string says. Rewriting
        # ``agents-shipgate`` to ``shipgate`` would churn every emitted string
        # for no gain: both names resolve to the same entry point.
        return command

    span = _program_token_span(command)
    if span is None:
        return command
    start, end = span
    if _program_name(command[start:end]) not in CONSOLE_SCRIPTS:
        return command
    return command[:start] + shlex.join(tokens) + command[end:]


def _program_token_span(command: str) -> tuple[int, int] | None:
    """Locate the program token, skipping leading ``NAME=VALUE`` assignments."""

    index = 0
    length = len(command)
    while True:
        while index < length and command[index].isspace():
            index += 1
        if index >= length:
            return None
        end = index
        while end < length and not command[end].isspace():
            end += 1
        token = command[index:end]
        if _ENV_ASSIGNMENT.fullmatch(token):
            index = end
            continue
        return index, end


def split_invocation(
    command: str,
    *,
    prefix: Sequence[str] | None = None,
) -> tuple[list[str], list[str]] | None:
    """Split a rendered command into shell-independent ``(executable, args)``.

    Returns ``None`` when the string has no faithful argv form — an unbalanced
    quote, or a leading ``NAME=VALUE`` assignment, which is shell syntax rather
    than an argv token and so cannot be represented without inventing a field
    to hold it. The rendered string stays authoritative in those cases.
    """

    if _has_env_assignment_prefix(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    resolved = tuple(prefix) if prefix is not None else invocation_prefix()
    if _program_name(tokens[0]) in CONSOLE_SCRIPTS:
        # Ours. Under a console-script invocation ``retarget_command`` leaves
        # the written spelling alone, so the structured form must agree with
        # the string rather than normalise it to the canonical name behind the
        # reader's back.
        if len(resolved) == 1 and _program_name(resolved[0]) in CONSOLE_SCRIPTS:
            return [tokens[0]], tokens[1:]
        return list(resolved), tokens[1:]
    if len(tokens) >= len(resolved) and tokens[: len(resolved)] == list(resolved):
        return list(resolved), tokens[len(resolved) :]
    # Another program entirely (``pip install ...``). Its argv[0] is still a
    # perfectly good executable; only the split point differs.
    return [tokens[0]], tokens[1:]


def _has_env_assignment_prefix(command: str) -> bool:
    span = _program_token_span(command)
    if span is None:
        return False
    return command[: span[0]].strip() != ""
