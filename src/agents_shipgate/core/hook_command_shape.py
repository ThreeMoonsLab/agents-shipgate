"""A bounded, static reading of a hook's inline shell command (#934).

A hook row used to show an inline command as the name of its first word and a
digest: ``PreToolUse: command changed (<not-shown> sha256:f23acba4b10f →
<not-shown> sha256:3f1dc36980b7)``. Two digests tell a reviewer nothing about
what the edit does to the hook, and a command that opens with an assignment, as
a script that reads its input first does, has no name to show at all.

This module reads the command the way a POSIX shell groups it, never runs it,
and reports only its *structure*: the program each command position names, how
many commands, pipes, command substitutions, control-flow keywords and quoted
strings it holds, the redirect operators with their targets, and a script
path the way a hook's ``args`` publish one (#972). It reads no value: a word
is returned as the text it spells so the caller can decide, with the
redaction rules the grants already apply, whether any of it is published.

The reading is deliberately small and fails closed. It accepts simple commands,
the operators ``|``, ``|&``, ``&&``, ``||``, ``;``, ``&`` and a newline,
``( … )`` and ``{ … }`` groups, ``if``/``elif``/``then``/``else``/``fi``,
``while``/``until``/``for``/``do``/``done``, ``! cmd``, ``[[ … ]]``,
``NAME=value`` prefixes, ``$( … )`` substitutions, quoting, and redirects
(``>``, ``>>``, ``<``, ``2>&1``, ``&>``). Anything else is refused whole, not
guessed at: a here-document or here-string, a backquote, ``$(( … ))``, ``$'…'``,
a process substitution, ``case``, a function definition, ``coproc``/``select``,
an unterminated quote or group, a command longer than
:data:`MAX_COMMAND_CHARS` or past :data:`MAX_WORDS` words or
:data:`MAX_DEPTH` levels of nesting. A command that runs ``bash -c '…'`` with a
literal script is read once more inside that script, up to
:data:`MAX_INLINE_SHELL_LEVELS` levels deep.

It follows no wrapper (``env``, ``sudo``, ``xargs``, ``timeout``) and no
alias: the command position names the wrapper, and what it runs is not
established. The reading is linear in the command's length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

#: The longest command, as ``config_sha256``'s input holds it, that is read.
MAX_COMMAND_CHARS = 8192
#: The most words one command may hold, across every level it is read at.
MAX_WORDS = 2000
#: How deep ``( … )``, ``$( … )`` and ``{ … }`` may nest.
MAX_DEPTH = 8
#: How many ``bash -c '…'`` scripts deep a command is read.
MAX_INLINE_SHELL_LEVELS = 2
#: How many of a statement's arguments are kept, for a script path to be found in.
MAX_STATEMENT_ARGS = 32

CommandLimit = Literal["too_long", "unsupported_syntax"]


class UnsupportedCommand(Exception):
    """The command is outside the small grammar this module reads."""

    def __init__(self, limit: CommandLimit = "unsupported_syntax") -> None:
        super().__init__(limit)
        self.limit = limit


@dataclass
class Statement:
    """One simple command: the word at its command position and the words after it.

    ``word`` is the command word's value when it is a fixed string, else
    ``None``; ``args`` holds the values of the first words after it, ``None``
    for one that is not a fixed string.
    """

    word: str | None
    args: list[str | None] = field(default_factory=list)

    def inline_script(self) -> str | None:
        """The literal script a shell runs with ``-c``, or ``None``."""

        if self.word is None or self.word.rsplit("/", 1)[-1] not in _SHELLS:
            return None
        for index, arg in enumerate(self.args):
            if arg is not None and _SHELL_C_FLAG.fullmatch(arg):
                following = self.args[index + 1] if index + 1 < len(self.args) else None
                return following if following else None
        return None


@dataclass
class CommandStructure:
    """What a command is made of, in the order it is written."""

    statements: list[Statement] = field(default_factory=list)
    pipes: int = 0
    substitutions: int = 0
    control: int = 0
    quoted: int = 0
    #: ``(operator, target)`` for each redirect to a file: ``>``, ``>>`` or
    #: ``<``, and the target's value, ``None`` when it is not a fixed string.
    redirects: list[tuple[str, str | None]] = field(default_factory=list)

    def merge(self, other: CommandStructure) -> None:
        self.statements.extend(other.statements)
        self.pipes += other.pipes
        self.substitutions += other.substitutions
        self.control += other.control
        self.quoted += other.quoted
        self.redirects.extend(other.redirects)


_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_SHELL_C_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: The variables whose value is a path inside the repository or the plugin, the
#: two a hook's script path is documented to start with (#972).
_PATH_VARIABLES = frozenset({"CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT"})

_WORD_END = frozenset(" \t\r\n;&|()<>")
_SPECIAL_PARAMETERS = frozenset("0123456789@*#?$!-")
#: Words read as a keyword at a command position.
_KEEP_COMMAND_POSITION = frozenset({"then", "else", "do", "!", "{", "time"})
_OPENS_CONTROL = frozenset({"if", "elif", "while", "until"})
_CLOSES = frozenset({"fi", "done", "}"})
_UNSUPPORTED_KEYWORDS = frozenset({"case", "esac", "function", "select", "coproc"})


class _Parser:
    def __init__(self, text: str, budget: list[int]) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0
        self.budget = budget
        self.structure = CommandStructure()

    # --- words -------------------------------------------------------------

    def _spend(self) -> None:
        self.budget[0] -= 1
        if self.budget[0] < 0:
            raise UnsupportedCommand()

    def _word(self, depth: int) -> tuple[str | None, bool]:
        """One word: its value when it is a fixed string, and whether its text is bare.

        Bare means unquoted, unescaped and holding no expansion, so a
        keyword is only ever a bare word.
        """

        parts: list[str] = []
        dynamic = False
        bare = True
        text, n = self.text, self.n
        while self.i < n:
            char = text[self.i]
            if char in _WORD_END:
                break
            if char == "'":
                end = text.find("'", self.i + 1)
                if end < 0:
                    raise UnsupportedCommand()
                parts.append(text[self.i + 1 : end])
                self.i = end + 1
                self.structure.quoted += 1
                bare = False
            elif char == '"':
                dynamic |= self._double_quoted(parts, depth)
                self.structure.quoted += 1
                bare = False
            elif char == "\\":
                if self.i + 1 >= n:
                    raise UnsupportedCommand()
                if text[self.i + 1] != "\n":
                    # An escape can spell a path separator or a space; the
                    # word is then not a fixed string this reading vouches for.
                    dynamic = True
                self.i += 2
                bare = False
            elif char == "`":
                raise UnsupportedCommand()
            elif char == "$":
                dynamic |= self._dollar(parts, depth)
                bare = False
            else:
                parts.append(char)
                self.i += 1
        return (None if dynamic else "".join(parts)), bare

    def _double_quoted(self, parts: list[str], depth: int) -> bool:
        text, n = self.text, self.n
        dynamic = False
        self.i += 1
        while self.i < n:
            char = text[self.i]
            if char == '"':
                self.i += 1
                return dynamic
            if char == "\\":
                if self.i + 1 >= n:
                    raise UnsupportedCommand()
                following = text[self.i + 1]
                if following in '\\"$`':
                    parts.append(following)
                elif following != "\n":
                    parts.append("\\" + following)
                self.i += 2
            elif char == "$":
                dynamic |= self._dollar(parts, depth)
            elif char == "`":
                raise UnsupportedCommand()
            else:
                parts.append(char)
                self.i += 1
        raise UnsupportedCommand()

    def _dollar(self, parts: list[str], depth: int) -> bool:
        """An expansion at ``$``; True when it makes the word not a fixed string."""

        text, n, i = self.text, self.n, self.i
        following = text[i + 1] if i + 1 < n else ""
        if following == "(":
            if text.startswith("$((", i) or depth + 1 > MAX_DEPTH:
                raise UnsupportedCommand()
            self.i = i + 2
            self.structure.substitutions += 1
            self._list(")", depth + 1)
            return True
        if following == "{":
            end = text.find("}", i + 2)
            if end < 0:
                raise UnsupportedCommand()
            inner = text[i + 2 : end]
            if any(mark in inner for mark in "$`\"'\\({"):
                raise UnsupportedCommand()
            self.i = end + 1
            if inner in _PATH_VARIABLES:
                parts.append("${" + inner + "}")
                return False
            return True
        if following in ("'", '"'):
            raise UnsupportedCommand()
        name = _NAME.match(text, i + 1)
        if name:
            self.i = name.end()
            if name.group(0) in _PATH_VARIABLES:
                parts.append("${" + name.group(0) + "}")
                return False
            return True
        if following and following in _SPECIAL_PARAMETERS:
            self.i = i + 2
            return True
        parts.append("$")
        self.i = i + 1
        return False

    # --- redirects ---------------------------------------------------------

    def _redirect(self, depth: int) -> None:
        """A redirect operator at ``self.i`` and its target word."""

        text = self.text
        char = text[self.i]
        if char == "&":
            # `&>` and `&>>`.
            self.i += 2
            operator = ">>" if text.startswith(">", self.i) and self._take(">") else ">"
            duplicate = False
        elif char == ">":
            self.i += 1
            if text.startswith(">", self.i):
                self.i += 1
                operator, duplicate = ">>", False
            elif text.startswith("&", self.i):
                self.i += 1
                operator, duplicate = ">", True
            elif text.startswith("|", self.i):
                self.i += 1
                operator, duplicate = ">", False
            elif text.startswith("(", self.i):
                raise UnsupportedCommand()
            else:
                operator, duplicate = ">", False
        else:
            self.i += 1
            if text.startswith(("<", ">", "("), self.i):
                # A here-document, a here-string, `<>` or a process substitution.
                raise UnsupportedCommand()
            if text.startswith("&", self.i):
                self.i += 1
                operator, duplicate = "<", True
            else:
                operator, duplicate = "<", False
        self._skip_blanks()
        if self.i >= self.n or self.text[self.i] in _WORD_END:
            raise UnsupportedCommand()
        self._spend()
        target, _bare = self._word(depth)
        if duplicate and target is not None and (target == "-" or target.isdigit()):
            return
        self.structure.redirects.append((operator, target))

    def _take(self, char: str) -> bool:
        if self.text.startswith(char, self.i):
            self.i += 1
            return True
        return False

    def _skip_blanks(self) -> None:
        while self.i < self.n and self.text[self.i] in " \t":
            self.i += 1

    # --- lists -------------------------------------------------------------

    def _list(self, closer: str | None, depth: int) -> None:
        """Commands up to ``closer`` (``)`` for a group or substitution), or the end."""

        text, n = self.text, self.n
        command_position = True
        in_test = False
        in_for_header = False
        current: Statement | None = None
        while True:
            if self.i >= n:
                if closer is not None or in_test:
                    raise UnsupportedCommand()
                return
            char = text[self.i]
            if char in " \t\r":
                self.i += 1
                continue
            if char == "\n":
                self.i += 1
                if not in_test:
                    command_position, in_for_header, current = True, False, None
                continue
            if char == "#":
                while self.i < n and text[self.i] != "\n":
                    self.i += 1
                continue
            if char == "\\" and text.startswith("\n", self.i + 1):
                self.i += 2
                continue
            if in_test and char in "&|()<>":
                # Inside `[[ … ]]` these are comparisons and grouping, not lists
                # or redirects; a single `|` or `&` is not valid there.
                pair = text[self.i : self.i + 2]
                if pair in ("&&", "||"):
                    self.i += 2
                elif char in "()<>":
                    self.i += 1
                else:
                    raise UnsupportedCommand()
                continue
            if char == ";":
                if text.startswith((";;", ";&"), self.i):
                    raise UnsupportedCommand()
                if in_test:
                    raise UnsupportedCommand()
                self.i += 1
                command_position, in_for_header, current = True, False, None
                continue
            if char == "&":
                if text.startswith("&>", self.i):
                    self._redirect(depth)
                    continue
                self.i += 2 if text.startswith("&&", self.i) else 1
                command_position, current = True, None
                continue
            if char == "|":
                self.structure.pipes += 0 if text.startswith("||", self.i) else 1
                self.i += 2 if text.startswith(("||", "|&"), self.i) else 1
                command_position, current = True, None
                continue
            if char == "(":
                if not command_position or depth + 1 > MAX_DEPTH or text.startswith("((", self.i):
                    raise UnsupportedCommand()
                self.i += 1
                self._list(")", depth + 1)
                command_position, current = False, None
                continue
            if char == ")":
                if closer != ")":
                    raise UnsupportedCommand()
                self.i += 1
                return
            if char in "<>":
                self._redirect(depth)
                continue

            start = self.i
            self._spend()
            value, bare = self._word(depth)
            raw = text[start : self.i]
            if bare and raw.isdigit() and self.i < n and text[self.i] in "<>":
                # The file descriptor of the redirect that follows (`2>&1`).
                continue
            if in_test:
                if bare and raw == "]]":
                    in_test, command_position = False, False
                continue
            if command_position:
                if _ASSIGNMENT.match(raw):
                    if raw.endswith("=") and text.startswith("(", self.i):
                        raise UnsupportedCommand()
                    continue
                keyword = raw if bare else ""
                if keyword in _UNSUPPORTED_KEYWORDS:
                    raise UnsupportedCommand()
                if keyword == "[[":
                    in_test, command_position, current = True, False, None
                    continue
                if keyword == "for":
                    self.structure.control += 1
                    in_for_header, command_position, current = True, False, None
                    continue
                if keyword in _OPENS_CONTROL:
                    self.structure.control += 1
                    continue
                if keyword in _KEEP_COMMAND_POSITION:
                    continue
                if keyword in _CLOSES:
                    command_position, current = False, None
                    continue
                command_position = False
                if value == "[":
                    # The test builtin: not a program this output names.
                    current = None
                    continue
                current = Statement(value)
                self.structure.statements.append(current)
                continue
            if current is not None and len(current.args) < MAX_STATEMENT_ARGS and not in_for_header:
                current.args.append(value)


def parse_command(text: str) -> CommandStructure:
    """The structure of ``text``, or :class:`UnsupportedCommand` (never a guess)."""

    if len(text) > MAX_COMMAND_CHARS:
        raise UnsupportedCommand("too_long")
    return _parse(text, 0, [MAX_WORDS])


def _parse(text: str, level: int, budget: list[int]) -> CommandStructure:
    parser = _Parser(text, budget)
    parser._list(None, 0)
    structure = parser.structure
    if level < MAX_INLINE_SHELL_LEVELS:
        for statement in list(structure.statements):
            script = statement.inline_script()
            if script is not None:
                structure.merge(_parse(script, level + 1, budget))
    return structure
