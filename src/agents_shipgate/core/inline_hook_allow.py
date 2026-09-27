"""Bounded literal shell output recognition for Claude PreToolUse (#826).

This parses declarations only. It never runs a command or opens a script.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Any

MAX_INLINE_COMMAND = 8192
_HERE = re.compile(r"cat[ \t]+<<(?P<dash>-?)[ \t]*(?P<quote>['\"]?)(?P<end>[A-Za-z_][A-Za-z0-9_]{0,39})(?P=quote)[ \t]*\n(?P<body>.*)\n(?P<tabs>\t*)(?P=end)\n?", re.DOTALL)
#: An echo/printf followed by ``; exit 0`` or ``&& exit 0``: the same output, exit status 0.
_EXIT_ZERO = re.compile(r"(?P<emit>.*?\S)[ \t]*(?:;|&&)[ \t]*exit[ \t]+0[ \t]*", re.DOTALL)


def broad_tool_matcher(matcher: Any) -> bool:
    if matcher in (None, "", "*", ".*"):
        return True
    if not isinstance(matcher, str) or len(matcher) > 120:
        return False
    text = matcher.removeprefix("^").removesuffix("$")
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\|[A-Za-z][A-Za-z0-9_]*)*", text)) and "Bash" in text.split("|")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key")
        obj[key] = value
    return obj


def _literal_output(command: str) -> str | None:
    here = _HERE.fullmatch(command)
    if here:
        if here["tabs"] and not here["dash"]:
            return None
        body = here["body"]
        if here["dash"]:
            # `<<-` strips leading tabs from each body line and the delimiter.
            body = "\n".join(line.lstrip("\t") for line in body.split("\n"))
        # Unquoted heredocs process backslash escapes; this grammar declines
        # those rather than emulating the shell. Quoted delimiters are literal.
        return body if here["quote"] or "\\" not in body else None
    exit_zero = _EXIT_ZERO.fullmatch(command)
    if exit_zero:
        command = exit_zero["emit"]
    # Entire operands must be quoted. shlex alone would accept escaped
    # quotes inside unquoted JSON, where a real shell can expand braces.
    quoted = r"(?:'[^']*'|\"(?:[^\"\\]|\\.)*\")"
    if not re.fullmatch(
        r"(?:echo(?:[ \t]+-n)?|printf)[ \t]+" + quoted + r"(?:[ \t]+" + quoted + r")?", command, re.DOTALL,
    ):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return None
    if words[:2] == ["echo", "-n"]:
        words = words[:1] + words[2:]
    if len(words) == 2 and words[0] == "echo" and "\\" not in words[1]:
        return words[1]
    if len(words) == 3 and words[0] == "printf" and words[1] in {"%s", "%s\\n"}:
        return words[2]
    if len(words) == 2 and words[0] == "printf":
        # A literal format; its one escape may be a trailing `\n`.
        literal = words[1].removesuffix("\\n")
        return literal if not any(c in literal for c in "%\\") else None
    return None


def literal_permission_decision(command: Any) -> str | None:
    """Only an unambiguous literal PreToolUse decision, never runtime behavior."""
    if (
        not isinstance(command, str) or not 0 < len(command) <= MAX_INLINE_COMMAND
        or any(c in command for c in "$`\x00\r") or "\\\n" in command
    ):
        return None
    output = _literal_output(command.strip())
    if output is None:
        return None
    try:
        obj = json.loads(output, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict) or set(obj) != {"hookSpecificOutput"}:
        return None
    detail = obj["hookSpecificOutput"]
    if (
        not isinstance(detail, dict)
        or set(detail) - {"hookEventName", "permissionDecision", "permissionDecisionReason"}
        or detail.get("hookEventName") != "PreToolUse"
        or detail.get("permissionDecision") not in ("allow", "deny", "ask")
        or ("permissionDecisionReason" in detail and not isinstance(detail["permissionDecisionReason"], str))
    ):
        return None
    return detail["permissionDecision"]


def inline_allow_facts(group: dict, handler: dict) -> dict[str, Any]:
    """Display facts only: unknown script/command behavior stays a named limit.

    A handler whose decision was read carries no ``decision_limit`` key, as its
    published form omits a null one; a handler never examined carries neither.
    """
    unknown = {"inline_allow": False, "decision_limit": "script_or_command_behavior_not_read"}
    if (
        set(group) - {"matcher", "hooks"}
        or set(handler) - {"type", "command", "timeout", "statusMessage"}
        or handler.get("type") != "command"
        or ("matcher" in group and not isinstance(group["matcher"], str))
    ):
        return unknown
    decision = literal_permission_decision(handler.get("command"))
    if decision is None:
        return unknown
    return {"inline_allow": decision == "allow" and broad_tool_matcher(group.get("matcher"))}
