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

:class:`Invocation` carries *how* the prefix was chosen, not only what it is.
The distinction matters: leaving a written ``agents-shipgate ...`` string alone
is right when a console script really is the way in, and wrong when an operator
has said the entry point is ``/private/venv/bin/agents-shipgate`` — same
program name, different program.

Reconstructing an invocation is only ever a best effort — ``sys.argv[0]`` can
be an absolute path, a symlink, or a lie — so every fallback in
:func:`resolve_invocation` lands on the canonical console script rather than on
anything derived from an untrusted argv. The worst case is therefore today's
behaviour, never a newly-invented one.

**Scope.** The policy governs values that are *commands to run*. It stops at
two borders on purpose:

* Durable evidence artifacts (``report.json``, ``report.md``, ``packet.*``)
  stay canonical. ``docs/architecture.md`` makes "same inputs → same report" a
  non-negotiable invariant, and process-entry spelling is not an input — the
  same scan through a wrapper and through ``python -m`` must produce the same
  bytes. Live routes carry the runnable spelling instead.
* Published contract vocabulary (``primary_commands``,
  ``.well-known/agents-shipgate.json``) describes the installed CLI rather than
  routing one run, and its generator must never bake an interpreter path into a
  committed file.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from subprocess import list2cmdline
from typing import Literal

__all__ = [
    "CANONICAL_CONSOLE_SCRIPT",
    "CLI_OVERRIDE_ENV_VAR",
    "CONSOLE_SCRIPTS",
    "MODULE_ENTRY_POINT",
    "Invocation",
    "InvocationSource",
    "invocation_prefix",
    "join_argv",
    "render_command",
    "resolve_invocation",
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

InvocationSource = Literal["console_script", "module", "override", "fallback"]

# Leading ``NAME=VALUE`` assignments in a command string. These are shell
# syntax, not argv, so a command carrying them has no shell-independent
# ``executable``/``args`` form — but the program token *after* them is still
# ours to retarget.
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*")

# Characters that make a string a *shell* command rather than an argv: control
# operators, redirection, substitution, expansion, and globbing. ``shlex.split``
# happily returns ``&&`` as an ordinary token, so a command containing one would
# otherwise be published as structured argv that does not do what the string
# does. Only occurrences outside quotes count — ``--base 'HEAD~1'`` is argv.
_SHELL_METACHARACTERS = frozenset("|&;<>()$`*?[]{}~!#\n\r")

# Fallback when the interpreter cannot name itself (embedded interpreters
# leave ``sys.executable`` empty).
_FALLBACK_INTERPRETER = "python3"

_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Invocation:
    """The entry point that re-enters this CLI, and how it was determined."""

    tokens: tuple[str, ...]
    source: InvocationSource

    @property
    def keeps_written_spelling(self) -> bool:
        """Whether hand-written ``agents-shipgate ...`` strings are already right.

        True only when a console script really is the way in — the shipped
        path, where rewriting would churn every emitted string to say the same
        thing. An operator override is excluded even when it *names* a console
        script: ``AGENTS_SHIPGATE_CLI=/private/venv/bin/agents-shipgate`` means
        that wrapper, not whichever one ``PATH`` happens to resolve.
        """

        return self.source in {"console_script", "fallback"}


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


def join_argv(tokens: Sequence[str]) -> str:
    """Render argv as a string the *local* shell would parse back to it.

    ``shlex.join`` is POSIX-only: it quotes with single quotes, which
    ``cmd.exe`` treats as ordinary characters rather than as quoting. A Windows
    interpreter path is exactly the case that needs quoting (backslashes, often
    a space in ``Program Files``), so rendering it POSIX-style produces a string
    Windows cannot run — and, worse, one where a metacharacter in an argument
    is no longer contained.
    """

    if _WINDOWS:
        return list2cmdline(list(tokens))
    return shlex.join(tokens)


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


def resolve_invocation(
    *,
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    main_module: object | None = None,
) -> Invocation:
    """The entry point that re-enters this CLI in the current environment.

    ``tokens`` is always directly executable: no shell is required to
    interpret it, and it never contains ``__main__.py``.
    """

    source_env = os.environ if env is None else env
    override = _override_prefix(source_env)
    if override is not None:
        return Invocation(override, "override")

    source_argv = sys.argv if argv is None else argv
    argv0 = source_argv[0] if source_argv else ""

    if _program_name(argv0) in CONSOLE_SCRIPTS:
        return Invocation((_program_name(argv0),), "console_script")

    module = sys.modules.get("__main__") if main_module is None else main_module
    if _entered_via_module(argv0, module):
        return Invocation((_interpreter(), "-m", MODULE_ENTRY_POINT), "module")

    # Anything else — a test runner driving the Typer app in-process, an
    # embedding host, a launcher that rewrote argv — keeps the documented
    # spelling. Guessing from an unrecognised argv would emit a command that is
    # merely differently wrong.
    return Invocation((CANONICAL_CONSOLE_SCRIPT,), "fallback")


def invocation_prefix(
    *,
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    main_module: object | None = None,
) -> tuple[str, ...]:
    """The argv tokens that re-enter this CLI. See :func:`resolve_invocation`."""

    return resolve_invocation(argv=argv, env=env, main_module=main_module).tokens


def _as_invocation(prefix: Sequence[str] | Invocation | None) -> Invocation:
    """Normalize the ``prefix`` argument the rewriting helpers accept.

    A bare token sequence carries no provenance, so it is read the way a
    caller passing one means it: a single console-script name is the shipped
    spelling, anything else is an explicit entry point to splice in.
    """

    if prefix is None:
        return resolve_invocation()
    if isinstance(prefix, Invocation):
        return prefix
    tokens = tuple(prefix)
    if len(tokens) == 1 and tokens[0] in CONSOLE_SCRIPTS:
        return Invocation(tokens, "console_script")
    return Invocation(tokens, "override")


def render_command(
    args: Sequence[str],
    *,
    program: str = CANONICAL_CONSOLE_SCRIPT,
    prefix: Sequence[str] | Invocation | None = None,
) -> str:
    """Render one Shipgate invocation as a runnable command string.

    ``args`` are the subcommand and its options *without* the program name;
    each element is one raw argv token and is quoted here, so callers must not
    pre-quote them. The result is the canonical ``program`` spelling under a
    console-script run — byte for byte what these call sites emitted before —
    and the current invocation otherwise.
    """

    return retarget_command(join_argv([program, *args]), prefix=prefix)


def retarget_command(
    command: str,
    *,
    prefix: Sequence[str] | Invocation | None = None,
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

    invocation = _as_invocation(prefix)
    if invocation.keeps_written_spelling:
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
    return command[:start] + join_argv(invocation.tokens) + command[end:]


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


def _has_shell_syntax(text: str) -> bool:
    """Whether ``text`` needs a shell to mean what it says.

    Quoted regions are exempt: ``--base 'HEAD~1'`` is one argv token, while a
    bare ``&&`` is a control operator no ``[*executable, *args]`` call can
    reproduce. Backslash escapes are honoured so an escaped metacharacter is
    read as the literal it is.
    """

    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is None and char == "\\":
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            elif quote == '"' and char == "\\":
                index += 2
                continue
        elif char in "'\"":
            quote = char
        elif char in _SHELL_METACHARACTERS:
            return True
        index += 1
    return False


def split_invocation(
    command: str,
    *,
    prefix: Sequence[str] | Invocation | None = None,
) -> tuple[list[str], list[str]] | None:
    """Split a rendered command into shell-independent ``(executable, args)``.

    The entry point is never recovered by parsing: for our own commands it is
    the resolved invocation itself, spliced in by :func:`retarget_command`.
    That matters on Windows, where the rendered prefix is double-quoted and a
    POSIX parse would eat the backslashes in ``C:\\Python312\\python.exe``.

    Returns ``None`` when the string has no faithful argv form — an unbalanced
    quote, a leading ``NAME=VALUE`` assignment, or any unquoted shell
    metacharacter. Publishing an argv that runs something other than what the
    string says would be worse than publishing no argv at all; the rendered
    string stays authoritative in those cases.
    """

    if _has_shell_syntax(command):
        return None

    invocation = _as_invocation(prefix)
    rendered = join_argv(invocation.tokens)

    if rendered and command == rendered:
        return list(invocation.tokens), []
    if rendered and command.startswith(rendered) and command[len(rendered) :][:1].isspace():
        head, tail = list(invocation.tokens), command[len(rendered) :]
    else:
        span = _program_token_span(command)
        if span is None or span[0] != 0:
            # Either nothing to split, or leading ``NAME=VALUE`` assignments,
            # which are shell syntax rather than argv tokens.
            return None
        head, tail = [command[span[0] : span[1]]], command[span[1] :]

    try:
        args = shlex.split(tail)
    except ValueError:
        return None
    return head, args
