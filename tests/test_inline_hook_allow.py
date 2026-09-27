"""#826: literal inline allow declarations, with conservative abstention."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _hooks_grants,
    compared_grant,
    host_grant_expansion_signals,
)
from agents_shipgate.core.inline_hook_allow import (
    broad_tool_matcher,
    inline_allow_facts,
    literal_permission_decision,
)

OUTPUT = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}})
ECHO = "echo " + shlex.quote(OUTPUT)
EMITTERS = [ECHO, "printf '%s\\n' " + shlex.quote(OUTPUT), "printf '%s' " + shlex.quote(OUTPUT), "printf " + shlex.quote(OUTPUT), "cat <<'EOF'\n" + OUTPUT + "\nEOF", 'cat <<"EOF"\n' + OUTPUT + "\nEOF", "cat <<EOF\n" + OUTPUT + "\nEOF"]


@pytest.mark.parametrize("command", EMITTERS)
@pytest.mark.parametrize("matcher", ["*", "", "Bash", "Bash|Read", "^(Bash|Read)$", None])
def test_unconditional_inline_allow_forms(command, matcher):
    group = {"hooks": []}
    if matcher is not None:
        group["matcher"] = matcher
    assert literal_permission_decision(command) == "allow"
    assert inline_allow_facts(group, {"type": "command", "command": command}) == {"inline_allow": True, "decision_limit": None}


@pytest.mark.parametrize("matcher", ["Read", "NotBash", "BashTool", "Bash(rm *)", "Bash(?=foo)", "Bash[", ["Bash"], 0])
def test_matcher_is_not_a_substring_guess(matcher):
    assert broad_tool_matcher(matcher) is False


@pytest.mark.parametrize("command", [
    "./guard.sh", "python guard.py", "if true; then " + ECHO + "; fi",
    "test -f approved && " + ECHO, "false || " + ECHO, ECHO + " | cat",
    ECHO + "; true", ECHO + " > result.json", "env " + ECHO,
    "sh -c " + shlex.quote(ECHO), ECHO + "\nexit 0", ECHO + " # comment",
    "echo -e " + shlex.quote(OUTPUT), "echo " + OUTPUT,
    "echo\n" + shlex.quote(OUTPUT), "echo " + OUTPUT.replace(chr(34), chr(92) + chr(34)),
    "printf '%b' " + shlex.quote(OUTPUT), "printf '%s' " + shlex.quote(OUTPUT) + " extra",
    "cat <<EOF\n" + OUTPUT + "\nEOF\necho next", "cat <<EOF\n$OUTPUT\nEOF",
    "cat <<EOF\n" + OUTPUT + "\nWRONG", "cat file.json", "echo '$OUTPUT'",
    "echo `helper`", "echo $(helper)", "echo 'unterminated", "echo " + "x" * 8192,
    None, [ECHO],
])
def test_guarded_indirect_dynamic_and_malformed_commands_abstain(command):
    assert literal_permission_decision(command) is None
    assert inline_allow_facts({"matcher": "*"}, {"type": "command", "command": command}) == {"inline_allow": False, "decision_limit": "script_or_command_behavior_not_read"}


@pytest.mark.parametrize("extra", [{"if": "Bash(ls *)"}, {"if": ""}, {"async": True}, {"async": False}, {"args": []}, {"enabled": False}, {"once": True}])
def test_handler_conditions_and_unknown_execution_options_abstain(extra):
    assert inline_allow_facts({"matcher": "*"}, {"type": "command", "command": ECHO, **extra})["inline_allow"] is False


@pytest.mark.parametrize("obj", [
    {"hookSpecificOutput": {"hookEventName": "PostToolUse", "permissionDecision": "allow"}},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}, "continue": False},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {}}},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": 4}},
])
def test_conflicting_or_unsupported_output_fields_abstain(obj):
    assert literal_permission_decision("echo " + shlex.quote(json.dumps(obj))) is None


def test_duplicate_decision_keys_are_not_evidence():
    assert literal_permission_decision("echo " + shlex.quote(OUTPUT.replace('"allow"', '"deny", "permissionDecision": "allow"'))) is None


@pytest.mark.parametrize("decision", ["deny", "ask"])
def test_non_allow_literals_are_not_notes(decision):
    facts = inline_allow_facts({"matcher": "*"}, {"type": "command", "command": ECHO.replace('"allow"', f'"{decision}"')})
    assert facts == {"inline_allow": False, "decision_limit": None}


def grant(command=ECHO, *, host="claude-code", event="PreToolUse", matcher="*", basis="host_configuration"):
    return _hooks_grants({"hooks": {event: [{"matcher": matcher, "hooks": [{"type": "command", "command": command}]}]}}, host=host, scope="repository", source=".claude/settings.json", basis=basis)[0]


@pytest.mark.parametrize("kwargs,noted", [({}, True), ({"matcher": "Read"}, False), ({"host": "codex"}, False), ({"event": "PostToolUse"}, False), ({"command": "./guard.sh"}, False), ({"basis": "declared_only"}, False), ({"basis": "plugin_selected"}, False)])
def test_notes_are_bounded_and_do_not_change_direction_or_risk(kwargs, noted):
    current = grant(**kwargs)
    # Loading basis is derived from source plus access/risk for plugin files.
    if "basis" in kwargs:
        current["source"] = "plugins/demo/hooks.json"
    change = {"baseline": None, "current": current}
    signals = host_grant_expansion_signals([change])
    (row,) = capability_diff_rows({"changes": [change], "expansion_signals": signals})
    bare = {**current, "handlers": [{k: v for k, v in h.items() if k not in {"inline_allow", "decision_limit"}} for h in current["handlers"]]}
    original_change = {"baseline": None, "current": bare}
    (original,) = capability_diff_rows({"changes": [original_change], "expansion_signals": signals})
    assert {k: v for k, v in row.as_dict().items() if k != "why"} == {k: v for k, v in original.as_dict().items() if k != "why"}
    assert compared_grant(current) == compared_grant(bare)
    assert signals == host_grant_expansion_signals([original_change])
    assert ("auto-approves" in row.why) is noted


def test_removed_inline_allow_is_not_described_as_current_approval():
    (row,) = capability_diff_rows({"changes": [{"baseline": grant(), "current": None}], "expansion_signals": []})
    assert "auto-approves" not in row.why


@pytest.mark.parametrize("matcher", ["*", "", "Bash"])
def test_cli_routes_agree_and_controls_do_not_change(tmp_path, matcher, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], env=env, check=True, capture_output=True)
    def invoke(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(repo)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout
    git("init", "-q", "-b", "main")
    settings = repo / ".claude/settings.json"
    settings.write_text("{}")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [{"type": "command", "command": ECHO}]}]}}))
    git("add", "-A")
    git("commit", "-qm", "head")
    diff = json.loads(invoke("diff", "--base", "main", "--json"))
    text = invoke("diff", "--base", "main")
    verify = json.loads(invoke("verify", "--base", "main", "--head", "HEAD", "--json"))
    assert diff["rows"] == verify["host_comparison"]["rows"]
    note = "inline allow auto-approves matched tool calls without a prompt"
    assert note in text and note in diff["rows"][0]["why"]
    assert note in (repo / "agents-shipgate-reports/pr-comment.md").read_text()
    check = json.loads(invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    monkeypatch.setattr("agents_shipgate.core.host_grants.inline_allow_facts", lambda group, handler: {})
    old = json.loads(invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    assert all(check[k] == old[k] for k in ("decision", "control", "violations"))


def test_script_behavior_is_a_named_limit_without_opening_the_script(tmp_path, monkeypatch):
    script = tmp_path / "guard.sh"
    script.write_text(ECHO)
    original = Path.open
    def guarded_open(path, *args, **kwargs):
        assert path != script, "hook script contents must remain unread"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    item = grant(command=str(script))
    assert item["handlers"][0]["decision_limit"] == "script_or_command_behavior_not_read"
    assert item["handlers"][0]["inline_allow"] is False
