"""Bounded hook executable references, without environment or filesystem reads.

Selection and byte capture belong to the host reader. This module establishes
only the lexical reference and its base; it never interprets script contents.
"""
from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Any, Literal

MAX_HOOK_COMMAND = 8192
MAX_HOOK_PATH = 1024
# Reading dependencies has a separate budget from displaying handler detail.
MAX_HOOK_SCRIPT_HANDLERS = 256
PathBasis = Literal["project_root_placeholder", "plugin_root_placeholder", "absolute_workspace_path"]


@dataclass(frozen=True)
class HookScriptReference:
    path: str | None = None
    basis: PathBasis | None = None
    limit: str | None = None


def _limited(reason: str) -> HookScriptReference:
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

    exec_form = "args" in handler
    if exec_form:
        # Claude's exec form substitutes documented placeholders as strings.
        # Codex's command-handler contract does not establish this field.
        if host != "claude-code" or not isinstance(handler["args"], list) or not all(
            isinstance(arg, str) for arg in handler["args"]
        ):
            return _limited("unsupported_exec_form")
        executable = command
    else:
        # A deliberately small shell word grammar: whole quoted words or
        # plain words. No concatenated quoting, escapes, expansion or operators.
        word = r'''(?:'[^'\n]*'|"[^"\n]*"|[A-Za-z0-9_./:@%+=,-]+)'''
        if not re.fullmatch(word + r"(?:[ \t]+" + word + r")*", command):
            return _limited("unsupported_shell_command")
        try:
            words = shlex.split(command, posix=True)
        except ValueError:
            return _limited("unsupported_shell_command")
        executable = words[0]
        if any("$" in arg or "`" in arg for arg in words[1:]):
            return _limited("dynamic_command_argument")
        if "$" in executable and not command.startswith('"'):
            # Single-quoted shell variables are literal text, not anchors.
            return _limited("unexpanded_path_placeholder")

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
        return _limited("working_directory_not_established" if "/" in executable else "path_lookup_or_wrapper")
    root = workspace_path.rstrip("/")
    if not root.startswith("/") or not executable.startswith(root + "/"):
        return _limited("external_executable")
    relative = _relative(executable[len(root) + 1:])
    if relative is None:
        return _limited("unsupported_or_escaping_path")
    return HookScriptReference(path=relative, basis="absolute_workspace_path")
