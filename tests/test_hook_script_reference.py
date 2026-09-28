"""#702: path evidence must precede any filesystem dependency read."""
from pathlib import Path

import pytest

from agents_shipgate.core.hook_script_reference import hook_script_reference


def reference(command, *, host="claude-code", plugin_root=None, **fields):
    return hook_script_reference(
        {"type": "command", "command": command, **fields}, host=host,
        workspace_path="/checkout", plugin_root=plugin_root,
    )


@pytest.mark.parametrize("command,fields", [
    ('"${CLAUDE_PROJECT_DIR}/scripts/guard.sh"', {}),
    ('"${CLAUDE_PROJECT_DIR}/scripts/guard.sh" --check', {}),
    # The documented shell spellings (#702 review): quoted alone, quoted with
    # the tail, and unquoted with a plain tail, braced or not.
    ('"${CLAUDE_PROJECT_DIR}"/scripts/guard.sh', {}),
    ('"$CLAUDE_PROJECT_DIR"/scripts/guard.sh --check', {}),
    ('"$CLAUDE_PROJECT_DIR/scripts/guard.sh"', {}),
    ("${CLAUDE_PROJECT_DIR}/scripts/guard.sh", {}),
    ("$CLAUDE_PROJECT_DIR/scripts/guard.sh --check", {}),
    ("${CLAUDE_PROJECT_DIR}/scripts/guard.sh", {"args": []}),
    ("${CLAUDE_PROJECT_DIR}/scripts/guard.sh", {"args": ["--check"]}),
])
def test_explicit_project_anchor_is_independent_of_caller_environment(command, fields, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/outside/another-project")
    result = reference(command, **fields)
    assert (result.path, result.basis, result.limit) == (
        "scripts/guard.sh", "project_root_placeholder", None,
    )


@pytest.mark.parametrize("root", ["plugins/demo", ""])
@pytest.mark.parametrize("command", [
    '"${CLAUDE_PLUGIN_ROOT}/scripts/guard.sh"', '"${CLAUDE_PLUGIN_ROOT}"/scripts/guard.sh',
    '"$CLAUDE_PLUGIN_ROOT"/scripts/guard.sh', "$CLAUDE_PLUGIN_ROOT/scripts/guard.sh",
])
def test_plugin_anchor_requires_selection_evidence(root, command):
    assert reference(command).limit == "plugin_root_not_established"
    result = reference(command, plugin_root=root)
    assert result.path == (root + "/" if root else "") + "scripts/guard.sh"
    assert result.basis == "plugin_root_placeholder"


@pytest.mark.parametrize("command,limit", [
    # Unquoted tails with shell metacharacters, another variable, a partial
    # name, literal quoting and a concatenation the grammar does not read.
    ("$CLAUDE_PROJECT_DIR/scripts/*.sh", "unsupported_shell_command"),
    ("${CLAUDE_PROJECT_DIR}/scripts/guard.sh;true", "unsupported_shell_command"),
    ('"$CLAUDE_PROJECT_DIRX"/scripts/guard.sh', "unsupported_shell_command"),
    ("'$CLAUDE_PROJECT_DIR'/scripts/guard.sh", "unsupported_shell_command"),
    ("'$CLAUDE_PROJECT_DIR/scripts/guard.sh'", "unexpanded_path_placeholder"),
    ('"${CLAUDE_PROJECT_DIR}"/scripts/"guard.sh"', "unsupported_shell_command"),
    ('"$HOME"/scripts/guard.sh', "unsupported_shell_command"),
    ("$HOME/scripts/guard.sh", "unsupported_shell_command"),
    # A wrapper runs a script this grammar does not follow; a bare command
    # with no path- or script-shaped argument names no repository file.
    ('python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/check.py', "interpreter_wrapper"),
    ("bash guard.sh", "interpreter_wrapper"),
    ("npx prettier --write .", "path_lookup"),
    ("true", "path_lookup"),
])
def test_other_spellings_keep_a_precise_limit(command, limit):
    result = reference(command, plugin_root="plugins/demo")
    assert (result.path, result.limit) == (None, limit)


def test_exec_form_substitutes_braced_placeholders_only():
    assert reference("$CLAUDE_PROJECT_DIR/scripts/guard.sh", args=[]).limit == "dynamic_or_conditional_path"
    wrapped = reference("node", args=["${CLAUDE_PLUGIN_ROOT}/scripts/format.js", "--fix"])
    assert wrapped.limit == "interpreter_wrapper"


def test_every_reference_limit_is_a_published_limit():
    from typing import get_args

    from agents_shipgate.core.hook_script_reference import ReferenceLimit
    from agents_shipgate.schemas.host_grants import HostHookScriptInputV7, HostHookScriptLimitV7

    assert set(get_args(ReferenceLimit)) <= set(get_args(HostHookScriptLimitV7))
    with pytest.raises(ValueError):
        HostHookScriptInputV7(handler=0, limit="an unregistered reason")


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("command", [
    "/checkout/scripts/guard.sh", '"/checkout/scripts/guard.sh" --check',
    "'/checkout/scripts/guard.sh' --check",
])
def test_absolute_workspace_path_uses_original_checkout_identity(host, command):
    result = reference(command, host=host)
    assert (result.path, result.basis, result.limit) == (
        "scripts/guard.sh", "absolute_workspace_path", None,
    )


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("command", [
    "./scripts/guard.sh", "scripts/guard.sh", "/usr/bin/true",
    "/checkout-neighbor/guard.sh", "guard.sh", "python scripts/guard.py",
    "env /checkout/scripts/guard.sh", "sh -c /checkout/scripts/guard.sh",
    "true && /checkout/scripts/guard.sh", "/checkout/scripts/guard.sh | cat",
    "/checkout/scripts/guard.sh > /tmp/out", "/checkout/scripts/guard.sh; true",
    "/checkout/scripts/guard.sh\ntrue", "/checkout/scripts/guard.sh # comment",
    '"$(git rev-parse --show-toplevel)/scripts/guard.sh"',
    '"${CLAUDE_PLUGIN_ROOT:-.}/scripts/guard.sh"',
    '"${UNKNOWN}/scripts/guard.sh"',
    '"/checkout/scripts/guard.sh" "$(helper)"',
    '"/checkout/scripts/guard.sh" "$ARGUMENT"',
    "/checkout/../outside/guard.sh", "/checkout/scripts/../guard.sh",
    '"${CLAUDE_PROJECT_DIR}/../guard.sh"',
    '"${CLAUDE_PROJECT_DIR}/scripts/${SCRIPT}"',
    "'/checkout/scripts/'guard.sh", "'/checkout/scripts/guard.sh",
    "'/checkout/scripts/guard.sh'\\\n",
])
def test_unestablished_paths_never_become_dependencies(host, command):
    result = reference(command, host=host)
    assert result.path is None and result.basis is None and result.limit


def test_a_shell_literal_is_not_a_variable_expansion():
    assert reference("'${CLAUDE_PROJECT_DIR}/scripts/guard.sh'").limit == "unexpanded_path_placeholder"


@pytest.mark.parametrize("fields", [
    {"shell": "powershell"}, {"commandWindows": "C:/other.exe"},
    {"command_windows": "C:/other.exe"}, {"args": None}, {"args": [0]},
])
def test_unknown_execution_form_keeps_its_limit(fields):
    assert reference("/checkout/scripts/guard.sh", **fields).limit


def test_codex_does_not_inherit_claude_placeholders_or_exec_form():
    assert reference('"${CLAUDE_PROJECT_DIR}/guard.sh"', host="codex").limit == "unsupported_path_placeholder"
    assert reference("/checkout/guard.sh", host="codex", args=[]).limit == "unsupported_exec_form"


def test_no_filesystem_read_occurs_during_reference_selection(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("reference selection must not read or resolve paths")
    monkeypatch.setattr(Path, "open", unexpected)
    monkeypatch.setattr(Path, "resolve", unexpected)
    assert reference('"${CLAUDE_PROJECT_DIR}/guard.sh"').path == "guard.sh"


@pytest.mark.parametrize("command", [None, [], "", "/checkout/" + "a" * 8192, "/checkout/" + "a" * 1025])
def test_bounds_and_invalid_commands_are_named(command):
    assert reference(command).limit
