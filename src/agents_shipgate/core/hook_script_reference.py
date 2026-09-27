"""Bounded hook executable references, without environment or filesystem reads.

Selection and byte capture belong to the host reader. This module establishes
only the lexical reference and its base; it never interprets script contents.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Any, Literal

MAX_HOOK_COMMAND = 8192
MAX_HOOK_PATH = 1024
# Reading dependencies has a separate budget from displaying handler detail.
MAX_HOOK_SCRIPT_HANDLERS = 256
PathBasis = Literal["project_root_placeholder", "plugin_root_placeholder", "absolute_workspace_path"]

#: Why one handler's command established no repository file (#702). Closed:
#: the inventory publishes these beside the reader's own dependency limits.
#: ``path_lookup`` is a bare command with no path- or script-shaped argument,
#: such as ``npx prettier --write``; ``interpreter_wrapper`` is a bare command
#: with one, such as ``python3 .claude/hooks/check.py`` or ``bash guard.sh``,
#: whose script this grammar does not follow. ``external_executable`` is an
#: absolute path outside the workspace.
ReferenceLimit = Literal[
    "unsupported_host",
    "not_command_handler",
    "unsupported_command_shape",
    "platform_command_override",
    "unsupported_shell",
    "unsupported_exec_form",
    "unsupported_shell_command",
    "dynamic_command_argument",
    "unexpanded_path_placeholder",
    "unsupported_path_placeholder",
    "plugin_root_not_established",
    "unsupported_or_escaping_path",
    "dynamic_or_conditional_path",
    "working_directory_not_established",
    "interpreter_wrapper",
    "path_lookup",
    "external_executable",
]

#: Limits that establish that the command names no repository file: a bare
#: command with no path-shaped argument, or an absolute path outside the
#: workspace. Every other reference limit leaves open which file runs.
NO_REPOSITORY_REFERENCE_LIMITS = frozenset({"path_lookup", "external_executable"})

_ANCHOR = "CLAUDE_PROJECT_DIR|CLAUDE_PLUGIN_ROOT"
# Characters a shell word may hold unquoted without any expansion, split or glob.
_PLAIN = r"[A-Za-z0-9_./:@%+=,-]"


def _anchored(named: bool) -> str:
    """One anchored executable word, in each spelling the shell expands alike (#702).

    The variable alone in double quotes and a plain tail after it
    (``"${VAR}"/tail``, ``"$VAR"/tail``), the variable and its tail in one
    pair of double quotes (``"${VAR}/tail"``, ``"$VAR/tail"``), or both
    unquoted with a plain tail (``${VAR}/tail``, ``$VAR/tail``). Each expands
    to the variable's value followed by the literal tail. Nothing else is
    concatenated, and no other variable is an anchor.
    """

    def group(name: str, pattern: str) -> str:
        return f"(?P<{name}>{pattern})" if named else f"(?:{pattern})"

    def variable(suffix: str) -> str:
        return rf"\$(?:\{{{group('b' + suffix, _ANCHOR)}\}}|{group('u' + suffix, _ANCHOR)})"

    return (
        rf'"{variable("1")}"{group("t1", "/" + _PLAIN + "*")}'
        rf'|"{variable("2")}{group("t2", r"/[^\"$`\\]*")}"'
        rf"|{variable('3')}{group('t3', '/' + _PLAIN + '*')}"
    )


_ANCHORED = re.compile(_anchored(named=True))
# A deliberately small shell word grammar: an anchored executable, a whole
# quoted word or a plain word. No other concatenated quoting, escapes,
# expansion or operators.
_WORD = re.compile(rf"""(?:{_anchored(named=False)}|'[^'\n]*'|"[^"\n]*"|{_PLAIN}+)""")
_GAP = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class HookScriptReference:
    path: str | None = None
    basis: PathBasis | None = None
    limit: ReferenceLimit | None = None


def _limited(reason: ReferenceLimit) -> HookScriptReference:
    return HookScriptReference(limit=reason)


def _relative(path: str) -> str | None:
    # Decline traversal rather than normalizing away a symlink-sensitive '..'.
    parts = path.split("/")
    if (
        not path or len(path) > MAX_HOOK_PATH or path.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(c in path for c in "\\:$`\x00\r\n")
    ):
        return None
    return path


def _shell_words(command: str) -> list[str] | None:
    """The command's words as written, or ``None`` outside the word grammar."""

    words: list[str] = []
    position = 0
    while position < len(command):
        word = _WORD.match(command, position)
        if word is None:
            return None
        words.append(word.group(0))
        position = word.end()
        gap = _GAP.match(command, position)
        if gap is None:
            if position < len(command):
                return None
        else:
            position = gap.end()
    return words or None


def _anchored_executable(word: str) -> str | None:
    """``${VAR}/tail`` for an anchored word, the one spelling the anchors below read."""

    match = _ANCHORED.fullmatch(word)
    if match is None:
        return None
    name = next(match.group(key) for key in ("b1", "u1", "b2", "u2", "b3", "u3") if match.group(key))
    tail = next(match.group(key) for key in ("t1", "t2", "t3") if match.group(key) is not None)
    return "${" + name + "}" + tail


def _unquoted(word: str) -> tuple[str, bool]:
    """A whole-quoted or plain word's text, and whether the shell expands anything in it."""

    if word[:1] == "'":
        return word[1:-1], False
    if word[:1] == '"':
        return word[1:-1], "$" in word
    return word, False


def _names_a_path(argument: str) -> bool:
    """Whether an argument is shaped like a file an interpreter may run: a path or a script name."""

    text = argument.strip("'\"")
    return "/" in text or "$" in text or re.search(r"[A-Za-z0-9_-]\.[A-Za-z0-9]+$", text) is not None


def hook_script_reference(
    handler: Any, *, host: str, workspace_path: str, plugin_root: str | None = None,
) -> HookScriptReference:
    """Resolve a direct executable against explicit static host path evidence.

    ``workspace_path`` is the original checkout's absolute path, also when the
    reader materializes a historical tree elsewhere. ``plugin_root`` is a
    unique selected in-repository plugin root, never guessed from a filename.
    The caller must separately establish that the declaration is selected.
    """
    if host not in {"claude-code", "codex"}:
        return _limited("unsupported_host")
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return _limited("not_command_handler")
    command = handler.get("command")
    if not isinstance(command, str) or not command or len(command) > MAX_HOOK_COMMAND:
        return _limited("unsupported_command_shape")
    if any(c in command for c in "\x00\r\n`\\"):
        return _limited("unsupported_command_shape")
    if any(key in handler for key in ("commandWindows", "command_windows")):
        return _limited("platform_command_override")
    if handler.get("shell") not in (None, "bash", "sh"):
        return _limited("unsupported_shell")

    if "args" in handler:
        # Claude's exec form substitutes documented placeholders as strings,
        # braced only: no shell reads `$VAR`. Codex's command-handler
        # contract does not establish this field.
        if host != "claude-code" or not isinstance(handler["args"], list) or not all(
            isinstance(arg, str) for arg in handler["args"]
        ):
            return _limited("unsupported_exec_form")
        executable, arguments = command, list(handler["args"])
        expanded = False
    else:
        words = _shell_words(command)
        if words is None:
            return _limited("unsupported_shell_command")
        anchored = _anchored_executable(words[0])
        arguments = words[1:]
        if anchored is not None:
            executable, expanded = anchored, True
        else:
            executable, expanded = _unquoted(words[0])
            if "$" in executable and not expanded:
                # Single-quoted shell variables are literal text, not anchors.
                return _limited("unexpanded_path_placeholder")
            if expanded:
                return _limited("dynamic_or_conditional_path")

    if "/" not in executable and "$" not in executable:
        # A bare command is looked up on PATH. Its path-shaped argument may
        # be a repository script an interpreter runs, which is not followed.
        return _limited(
            "interpreter_wrapper" if any(_names_a_path(arg) for arg in arguments) else "path_lookup"
        )
    if "args" not in handler and any("$" in arg for arg in arguments):
        return _limited("dynamic_command_argument")

    anchors = {
        "${CLAUDE_PROJECT_DIR}/": ("", "project_root_placeholder"),
        "${CLAUDE_PLUGIN_ROOT}/": (plugin_root, "plugin_root_placeholder"),
    }
    for prefix, (base, basis) in anchors.items():
        if executable.startswith(prefix):
            if host != "claude-code":
                return _limited("unsupported_path_placeholder")
            if base is None:
                return _limited("plugin_root_not_established")
            tail = _relative(executable[len(prefix):])
            if tail is None or (base and _relative(base) is None):
                return _limited("unsupported_or_escaping_path")
            return HookScriptReference(path=posixpath.join(base, tail), basis=basis)
    if "$" in executable:
        return _limited("dynamic_or_conditional_path")
    if not executable.startswith("/"):
        return _limited("working_directory_not_established")
    root = workspace_path.rstrip("/")
    if not root.startswith("/") or not executable.startswith(root + "/"):
        return _limited("external_executable")
    relative = _relative(executable[len(root) + 1:])
    if relative is None:
        return _limited("unsupported_or_escaping_path")
    return HookScriptReference(path=relative, basis="absolute_workspace_path")
