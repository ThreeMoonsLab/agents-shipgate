"""Whether a Claude Code tool-event hook matcher can match any tool name (#940).

A ``PreToolUse`` group whose matcher is ``Bash(git push*)`` reads like a
permission rule, but Claude Code compares a tool event's matcher with the
tool's *name* (https://code.claude.com/docs/en/hooks#matcher-patterns):

* ``"*"``, ``""`` or an omitted matcher matches every tool;
* a matcher of only letters, digits, ``_``, ``-``, spaces, ``,`` and ``|`` is
  an exact name, or a list of exact names separated by ``|`` or ``,`` with
  optional surrounding whitespace;
* any other matcher is a JavaScript regular expression, unanchored, tested
  with ``RegExp.prototype.test`` against the tool name.

``Bash(git push*)`` holds parentheses, so it is a regular expression, and
every string it matches holds ``"git pus"``, with its space: it matches no
tool name. A permission-rule pattern filters a handler through the handler's
``if`` field (https://code.claude.com/docs/en/hooks#common-fields), not
through the group's matcher.

The answer is decided only where it holds for *every* tool name, whatever
tools a session has: a built-in tool name
(https://code.claude.com/docs/en/tools-reference, all ASCII letters) or an
MCP tool's ``mcp__<server>__<tool>`` name
(https://code.claude.com/docs/en/hooks#match-mcp-tools). A tool's name is the
name Claude is given it under, which the Claude API restricts to
``^[a-zA-Z0-9_-]{1,128}$``
(https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools),
and Claude Code replaces any other character of a plugin's MCP tool name with
``_`` (https://code.claude.com/docs/en/mcp, "Plugin MCP tool names"). The
alphabet read here adds ``.``, which MCP's own tool-name guidance allows, so
a name passed through without that normalization is still covered. A matcher
is :data:`NO_TOOL_NAME` only when every string it can match holds a character
outside that alphabet; then neither a built-in nor an MCP tool name, nor one
added later, can match it.

Nothing here executes a matcher. A regular expression is read by a bounded,
linear parser that accepts a small subset of JavaScript's syntax, the part
whose meaning does not depend on the flags the documentation does not state
(Unicode mode or not): ASCII literals, ``.``, character classes, groups,
lookarounds, anchors, quantifiers, and the escapes both modes share.
Anything else — a non-ASCII character, a named group, a modifier group, a
backreference, a ``\\x``/``\\u``/``\\c`` escape, a quantifier with nothing to
repeat, an unbalanced bracket, a ``{`` that is not a quantifier, a matcher
longer than
:data:`MAX_MATCHER_CHARS` or nested deeper than :data:`MAX_GROUP_DEPTH` — is
:data:`POSSIBLE`, today's reading. So is every matcher Claude Code would
reject: its behavior for one is not documented. Every rule over-approximates
what a matcher can match, so a matcher this module calls
:data:`NO_TOOL_NAME` matches no tool name under the documented semantics.
"""

from __future__ import annotations

import re
import string
from typing import Any, Literal

MatcherReach = Literal["possible", "no_tool_name"]

#: The matcher may match some tool name, or this reader could not decide.
POSSIBLE: MatcherReach = "possible"
#: Every string the matcher can match holds a character no tool name holds.
NO_TOOL_NAME: MatcherReach = "no_tool_name"

#: The Claude Code events whose matcher filters the tool name
#: (https://code.claude.com/docs/en/hooks#matcher-patterns). Every other
#: event's matcher filters something else, or the event has no matcher.
CLAUDE_TOOL_NAME_EVENTS = frozenset({
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied",
})

#: Every character a tool name may hold (see the module docstring).
TOOL_NAME_CHARACTERS = frozenset(string.ascii_letters + string.digits + "_-.")

#: A matcher longer than this is not read: :data:`POSSIBLE`. A cost bound
#: only: the host grant reader also reads as possible every matcher it does
#: not publish (longer than 1,024 characters once redacted), so this applies
#: to the declared text behind a redacted one.
MAX_MATCHER_CHARS = 65_536
#: Groups nested deeper than this are not read: :data:`POSSIBLE`.
MAX_GROUP_DEPTH = 32

#: Characters a pattern escapes to mean themselves in either mode: the
#: ECMAScript ``SyntaxCharacter`` set and ``/``. Inside a class, ``-`` too.
_IDENTITY_ESCAPES = frozenset("^$\\.*+?()[]{}|/")
#: The control escapes, each one character no tool name holds.
_CONTROL_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v"}
_BRACE_QUANTIFIER = re.compile(r"\{([0-9]+)(?:(,)([0-9]*))?\}")


class _Unresolved(Exception):
    """The matcher is outside the subset this reader decides."""


def claude_tool_matcher_reach(matcher: Any) -> MatcherReach:
    """Whether a Claude Code tool-event matcher can match any tool name (#940).

    ``matcher`` is the group's value as the file declares it; ``None`` is an
    omitted matcher. :data:`NO_TOOL_NAME` is returned only when no tool name
    can match it, under both of its documented readings where it has
    characters both share, and whether or not Claude Code trims its
    whitespace. Everything else is :data:`POSSIBLE`.
    """

    if not isinstance(matcher, str) or len(matcher) > MAX_MATCHER_CHARS:
        return POSSIBLE
    readings = {matcher, matcher.strip()}
    if any(text in {"", "*"} for text in readings):
        return POSSIBLE
    for text in readings:
        if _exact_eligible(text) and _exact_names_can_match(text):
            return POSSIBLE
        # Read as a pattern too, so a version that reads every matcher as a
        # pattern is covered: only a matcher neither reading can match is
        # decided.
        try:
            if _Pattern(text).can_match():
                return POSSIBLE
        except _Unresolved:
            return POSSIBLE
    return NO_TOOL_NAME


def _exact_eligible(text: str) -> bool:
    """Whether Claude Code may read ``text`` as exact names.

    Read broadly, so a matcher that may take either path is read both ways:
    any letter or digit, ``_``, ``-``, ``,``, ``|`` and any white space.
    """

    return all(char.isalnum() or char in "_-,|" or char.isspace() for char in text)


def _exact_names_can_match(text: str) -> bool:
    """Whether a list of exact names can name a tool: an empty entry is read as possible."""

    entries = [entry.strip() for entry in re.split(r"[|,]", text)]
    return any(not entry or set(entry) <= TOOL_NAME_CHARACTERS for entry in entries)


class _Pattern:
    """A bounded recursive-descent reading of a JavaScript pattern (no flags).

    Each method consumes its construct and returns whether it can match some
    string of tool-name characters. Zero-width constructs (anchors, word
    boundaries, lookarounds) are read as always satisfiable, and so is any
    construct whose possible matches include a tool-name character: an
    over-approximation, never an under-approximation. Every branch of an
    alternation is parsed, so a malformed later branch is still refused.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0

    def can_match(self) -> bool:
        if not self.text.isascii():
            # Case folding and surrogate pairs depend on flags the
            # documentation does not state.
            raise _Unresolved
        result = self._disjunction(0)
        if self.at != len(self.text):
            raise _Unresolved  # an unbalanced ")"
        return result

    def _peek(self, offset: int = 0) -> str | None:
        index = self.at + offset
        return self.text[index] if index < len(self.text) else None

    def _disjunction(self, depth: int) -> bool:
        if depth > MAX_GROUP_DEPTH:
            raise _Unresolved
        result = self._alternative(depth)
        while self._peek() == "|":
            self.at += 1
            branch = self._alternative(depth)
            result = result or branch
        return result

    def _alternative(self, depth: int) -> bool:
        result = True
        while (char := self._peek()) is not None and char not in "|)":
            term = self._term(depth)
            result = result and term
        return result

    def _term(self, depth: int) -> bool:
        char = self._peek()
        if char in {"^", "$"} or (char == "\\" and self._peek(1) in {"b", "B"}):
            self.at += 1 if char != "\\" else 2
            self._refuse_quantifier()
            return True
        if char == "(":
            lookaround, inner = self._group(depth)
            if lookaround:
                self._refuse_quantifier()
                return True
            return self._quantified(inner)
        return self._quantified(self._atom())

    def _refuse_quantifier(self) -> None:
        # A quantified assertion is an error, or Annex B-only for a lookahead.
        if self._peek() in {"*", "+", "?", "{"}:
            raise _Unresolved

    def _group(self, depth: int) -> tuple[bool, bool]:
        self.at += 1
        lookaround = False
        if self.text.startswith("?:", self.at):
            self.at += 2
        elif self.text.startswith(("?=", "?!"), self.at):
            self.at += 2
            lookaround = True
        elif self.text.startswith(("?<=", "?<!"), self.at):
            self.at += 3
            lookaround = True
        elif self._peek() == "?":
            raise _Unresolved  # a named group, or a modifier such as (?i:…)
        inner = self._disjunction(depth + 1)
        if self._peek() != ")":
            raise _Unresolved
        self.at += 1
        return lookaround, inner

    def _quantified(self, atom: bool) -> bool:
        char = self._peek()
        if char in {"*", "?"}:
            self.at += 1
            minimum = 0
        elif char == "+":
            self.at += 1
            minimum = 1
        elif char == "{":
            brace = _BRACE_QUANTIFIER.match(self.text, self.at)
            if brace is None:
                raise _Unresolved  # an Annex B literal "{"
            low = int(brace.group(1))
            if brace.group(2) and brace.group(3) and int(brace.group(3)) < low:
                raise _Unresolved  # out of order: a SyntaxError
            self.at = brace.end()
            minimum = low
        else:
            return atom
        if self._peek() == "?":
            self.at += 1  # lazy
        if self._peek() in {"*", "+", "?", "{"}:
            raise _Unresolved  # nothing to repeat
        return True if minimum == 0 else atom

    def _atom(self) -> bool:
        char = self._peek()
        if char is None or char in {"*", "+", "?", "{", "}", "]"}:
            # A quantifier with nothing to repeat is a SyntaxError; a lone
            # "}" or "]" is a literal only under Annex B.
            raise _Unresolved
        if char == ".":
            self.at += 1
            return True
        if char == "[":
            return self._class()
        if char == "\\":
            kind, value = self._escape(in_class=False)
            return value if kind == "set" else value in TOOL_NAME_CHARACTERS
        self.at += 1
        return char in TOOL_NAME_CHARACTERS

    def _escape(self, *, in_class: bool) -> tuple[str, Any]:
        """One escape: ``("char", c)`` for one character, ``("set", bool)`` for a class escape."""

        self.at += 1
        char = self._peek()
        if char is None:
            raise _Unresolved  # a trailing "\" is a SyntaxError
        self.at += 1
        if char in {"d", "D", "w", "W", "S"}:
            # Each of these matches a letter, a digit, "." or "-".
            return "set", True
        if char == "s":
            return "set", False  # white space and line terminators only
        if char in _CONTROL_ESCAPES:
            return "char", _CONTROL_ESCAPES[char]
        if char == "b" and in_class:
            return "char", "\b"
        if char == "0" and not (self._peek() or "").isdigit():
            return "char", "\0"
        if char in _IDENTITY_ESCAPES or (in_class and char == "-"):
            # An identity escape: the character itself, in either mode.
            return "char", char
        # \c, \x, \u, \k, \p, a backreference, a legacy octal, or an
        # identity escape only Annex B accepts.
        raise _Unresolved

    def _class(self) -> bool:
        self.at += 1
        negated = self._peek() == "^"
        if negated:
            self.at += 1
        result = False
        while True:
            char = self._peek()
            if char is None:
                raise _Unresolved
            if char == "]":
                self.at += 1
                break
            low = self._class_atom()
            if self._peek() == "-" and self._peek(1) not in {None, "]"}:
                self.at += 1
                high = self._class_atom()
                if low[0] != "char" or high[0] != "char":
                    raise _Unresolved  # an Annex B range around a class escape
                if ord(low[1]) > ord(high[1]):
                    raise _Unresolved  # out of order: a SyntaxError
                member = any(low[1] <= item <= high[1] for item in TOOL_NAME_CHARACTERS)
            else:
                member = low[1] if low[0] == "set" else low[1] in TOOL_NAME_CHARACTERS
            result = result or member
        # A negated class matches every character it does not list: read as
        # possible rather than proving it lists every tool-name character.
        return True if negated else result

    def _class_atom(self) -> tuple[str, Any]:
        char = self._peek()
        if char == "\\":
            return self._escape(in_class=True)
        self.at += 1
        return "char", char


__all__ = [
    "CLAUDE_TOOL_NAME_EVENTS",
    "MAX_GROUP_DEPTH",
    "MAX_MATCHER_CHARS",
    "NO_TOOL_NAME",
    "POSSIBLE",
    "TOOL_NAME_CHARACTERS",
    "MatcherReach",
    "claude_tool_matcher_reach",
]
