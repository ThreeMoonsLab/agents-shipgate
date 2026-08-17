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
    "render_cli_override",
    "render_command",
    "resolve_invocation",
    "retarget_command",
    "split_invocation",
    "split_windows_command_line",
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

# The subset that a double quote does *not* neutralize: parameter expansion and
# command substitution both still happen inside ``"..."``.
_EXPANSION_CHARACTERS = frozenset("$`")

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
    """Render argv as one POSIX-shell string. **Not** a host-shell promise.

    There is deliberately one renderer and one parser, both POSIX, because the
    two must agree: a string rendered one way and parsed another silently
    changes the values it carries. ``subprocess.list2cmdline`` looked like the
    Windows answer and is not — it implements MS C-runtime *argv* quoting, so
    it leaves ``feature&whoami`` unquoted (a ``cmd.exe`` command separator) and
    produces a quoted path PowerShell will not invoke without ``&``. Rendering
    with it and parsing with POSIX ``shlex`` also turned ``C:\\repo`` into
    ``C:repo``, which is worse than an unrunnable string: it is a *runnable*
    command against the wrong workspace.

    Uniform POSIX quoting round-trips Windows paths exactly, because a
    single-quoted ``'C:\\repo'`` preserves backslashes literally. What it does
    not do is make the string safe to paste into ``cmd.exe`` — nothing would,
    since single quotes are not quoting there. So the promise is narrowed
    rather than faked: ``command`` is a POSIX rendering for display and for
    POSIX shells, and ``[*executable, *args]`` is the authoritative runnable
    form on every platform. That pair is exact by construction and needs no
    shell at all.
    """

    return shlex.join(tokens)


def split_windows_command_line(raw: str) -> list[str]:
    """Split a Windows command line into argv using the documented CRT rules.

    ``shlex`` cannot do this in either mode. POSIX mode treats a backslash as
    an escape, so ``C:\\Tools\\agents-shipgate.exe`` collapses to
    ``C:Toolsagents-shipgate.exe`` — a path that does not exist, produced from
    a value the operator wrote correctly. Non-POSIX mode keeps the backslashes
    but splits on whitespace regardless of quoting *inside* a token, so
    ``--project="C:\\My Project"`` becomes two arguments.

    So the rules are implemented rather than approximated. These are the ones
    ``CommandLineToArgvW`` and the MS C runtime document:

    * A quote toggles "in quotes"; ``""`` inside a quoted run is one literal
      quote (the doubled-quote escape).
    * ``2n`` backslashes before a quote produce ``n`` backslashes and toggle
      quoting; ``2n+1`` produce ``n`` backslashes and one literal quote.
    * Backslashes not followed by a quote are literal — which is what makes an
      ordinary Windows path survive.
    * Unquoted whitespace separates arguments.
    """

    argv: list[str] = []
    token: list[str] = []
    pending_backslashes = 0
    in_quotes = False
    started = False

    def flush_backslashes(count: int) -> None:
        token.extend("\\" * count)

    for char in raw:
        if char == "\\":
            pending_backslashes += 1
            continue
        if char == '"':
            flush_backslashes(pending_backslashes // 2)
            if pending_backslashes % 2:
                token.append('"')
            elif in_quotes:
                # A doubled quote inside a quoted run is one literal quote.
                in_quotes = False
                started = True
            else:
                in_quotes = True
                started = True
            pending_backslashes = 0
            continue
        flush_backslashes(pending_backslashes)
        pending_backslashes = 0
        if char.isspace() and not in_quotes:
            if token or started:
                argv.append("".join(token))
                token.clear()
                started = False
            continue
        token.append(char)

    flush_backslashes(pending_backslashes)
    if token or started:
        argv.append("".join(token))
    return argv


def _quote_windows(token: str) -> str:
    """Quote one argv token so the CRT rules read it back verbatim.

    The inverse of :func:`split_windows_command_line`, and deliberately not
    ``shlex.quote``: single quotes are ordinary characters to the CRT parser,
    so a POSIX-quoted path would come back with the quotes still in it.
    """

    if token and not any(char in token for char in ' \t\n\v"'):
        return token
    out = ['"']
    backslashes = 0
    for char in token:
        if char == "\\":
            backslashes += 1
            continue
        if char == '"':
            # An embedded quote is escaped, and every backslash run in front of
            # it must be doubled so the parser reads them as literals.
            out.append("\\" * (backslashes * 2 + 1))
            out.append('"')
        else:
            out.append("\\" * backslashes)
            out.append(char)
        backslashes = 0
    # The closing quote is also a quote: double the run that precedes it.
    out.append("\\" * (backslashes * 2))
    out.append('"')
    return "".join(out)


def render_cli_override(tokens: Sequence[str]) -> str:
    """Render an entry point as a value :data:`CLI_OVERRIDE_ENV_VAR` reads back.

    ``AGENTS_SHIPGATE_CLI`` is parsed with the *host's* rules — POSIX ``shlex``
    on POSIX, the documented CRT rules on Windows (:func:`_split_override`) —
    because an operator writes it in their own shell. A writer must therefore
    use the same host rules, not :func:`join_argv`: that renderer is POSIX
    everywhere by design, so on Windows it would emit ``'C:\\repo\\shipgate'``
    and the CRT parser would hand back a path with literal quotes in it.

    One renderer per parser is the rule this pairs with. It exists because
    something now *writes* the variable — the repository launcher announces
    itself through it, so every command Shipgate emits names a program the
    caller can actually run — and a launcher whose checkout path contains a
    space must survive the round trip.
    """

    if _WINDOWS:
        return " ".join(_quote_windows(token) for token in tokens)
    return join_argv(tokens)


def _split_override(raw: str) -> list[str] | None:
    """Parse ``AGENTS_SHIPGATE_CLI`` with the host's own rules."""

    if _WINDOWS:
        return split_windows_command_line(raw)
    try:
        return shlex.split(raw)
    except ValueError:
        return None


def _override_prefix(env: Mapping[str, str]) -> tuple[str, ...] | None:
    raw = env.get(CLI_OVERRIDE_ENV_VAR, "").strip()
    if not raw:
        return None
    tokens = _split_override(raw)
    if tokens is None:
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

    located = _program_token(command)
    if located is None:
        return command
    start, end, program = located
    if _program_name(program) not in CONSOLE_SCRIPTS:
        return command
    return command[:start] + join_argv(invocation.tokens) + command[end:]


def _program_token(command: str) -> tuple[int, int, str] | None:
    """Locate the program token and read its value, skipping ``NAME=VALUE``.

    The span is found by a scan that honours quoting, and the *value* comes
    from ``shlex`` — the same grammar the string was rendered with — rather
    than from the scan. The two are then cross-checked, and a disagreement
    returns ``None``: locating a token and reading it are different jobs, and
    only the second one decides whether this command is ours to rewrite.

    Scanning to raw whitespace was wrong, and not only for the escaped-space
    case ``split_invocation`` already documents. A *quoted* path whose own
    directory contains a space and whose first segment ends in one of our
    console-script names — ``'/tmp/agents-shipgate worktree/.venv/bin/python'``,
    which is what a clone of this repository plus a virtualenv produces — was
    cut at the space, leaving ``'/tmp/agents-shipgate``. Its basename *is*
    ``agents-shipgate``, so a `python -m pip install` recovery was rewritten
    into a command that named the launcher and carried a dangling quote: not an
    unrunnable string but a runnable one that runs the wrong program, which is
    the outcome this module exists to prevent.
    """

    index = 0
    length = len(command)
    while True:
        while index < length and command[index].isspace():
            index += 1
        if index >= length:
            return None
        end = _token_end(command, index)
        token = command[index:end]
        if _ENV_ASSIGNMENT.fullmatch(token):
            index = end
            continue
        try:
            parsed = shlex.split(token)
        except ValueError:
            # An unbalanced quote: there is no faithful reading, so there is no
            # basis for deciding the token is ours.
            return None
        if len(parsed) != 1:
            return None
        return index, end, parsed[0]


def _token_end(command: str, start: int) -> int:
    """Where the token starting at ``start`` ends under POSIX quoting rules."""

    index = start
    quote: str | None = None
    while index < len(command):
        char = command[index]
        if quote is None:
            if char == "\\":
                index += 2
                continue
            if char in "'\"":
                quote = char
            elif char.isspace():
                break
        elif quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = None
        elif char == "'":
            quote = None
        index += 1
    return min(index, len(command))


def _has_shell_syntax(text: str) -> bool:
    """Whether ``text`` needs a shell to mean what it says.

    Single quotes are the only construct that makes every metacharacter inert:
    ``--base 'HEAD~1'`` is one argv token. Double quotes suppress word
    splitting and globbing but **not** substitution, so ``printf "$HOME"`` and
    ``"$(id)"`` still expand — a structured argv would pass the dollar sign
    through literally and do something the string does not. Those forms are
    therefore detected inside double quotes too.

    Backslash escapes are honoured so an escaped metacharacter is read as the
    literal it is.
    """

    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is None and char == "\\":
            index += 2
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = None
            elif char in _EXPANSION_CHARACTERS:
                return True
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

    The whole string is parsed with **one** grammar — the same POSIX grammar it
    was rendered with — and the split point is then chosen by token count.
    Locating the program by scanning to the first whitespace was wrong for the
    same reason a second grammar is: ``/opt/my\\ tool --flag`` is one program
    with one argument, and the scan cut it into ``/opt/my\\`` plus a stray
    ``tool``, publishing an argv that runs a different program than the string.

    How many tokens the entry point occupies still comes from the resolved
    invocation rather than from guessing, because ``python -m agents_shipgate``
    is three tokens and a console script is one.

    Returns ``None`` when the string has no faithful argv form — an unbalanced
    quote, a leading ``NAME=VALUE`` assignment, or any unquoted shell
    metacharacter. Publishing an argv that runs something other than what the
    string says would be worse than publishing no argv at all; the rendered
    string stays authoritative in those cases.
    """

    if _has_shell_syntax(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    if _ENV_ASSIGNMENT.fullmatch(tokens[0]):
        # Leading ``NAME=VALUE`` assignments are shell syntax, not argv.
        return None

    invocation = _as_invocation(prefix)
    entry = list(invocation.tokens)
    if entry and tokens[: len(entry)] == entry:
        return entry, tokens[len(entry) :]
    # Another program entirely (``pip install ...``), or our own command still
    # spelled as the console script it was written with. Either way its argv[0]
    # is the entry point; only the split point differs.
    return tokens[:1], tokens[1:]
