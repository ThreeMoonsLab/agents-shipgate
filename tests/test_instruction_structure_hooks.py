"""Generated hook uses the same real preflight parser and never invents approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from test_current_control import repo as repo

from agents_shipgate.cli.install_hooks import _hook_script_text


def _hook():
    namespace = {"__name__": "hook_under_test"}
    exec(compile(_hook_script_text(), "generated-hook.py", "exec"), namespace)
    namespace["_cli"] = lambda: [str(Path(__file__).resolve().parents[1] / "shipgate")]
    return namespace


def _args(config="shipgate.yaml"):
    return argparse.Namespace(config=config, base="origin/main", head="", ci_mode="advisory")


def _event(repo, path, old, new, mode="default"):
    return {"tool_name": "Edit", "session_id": "fixture-session", "permission_mode": mode,
            "cwd": str(repo), "tool_input": {
                "file_path": str(repo / path), "old_string": old, "new_string": new,
            }}


@pytest.mark.parametrize("mode", ["default", "acceptEdits"])
def test_prose_pre_post_then_permission_expansion_never_reuses_approval(repo, capsys, mode):
    hook = _hook()
    path = ".claude/skills/demo/SKILL.md"
    target = repo / path
    target.parent.mkdir(parents=True)
    before = "---\nname: demo\ndescription: Fixture\nallowed-tools: Read\n---\nOld prose.\n"
    after = before.replace("Old prose.", "New prose.")
    target.write_text(before)
    args = _args()
    event = _event(repo, path, "Old prose.", "New prose.", mode)
    assert hook["_pretooluse"](event, repo, args) == 0
    assert capsys.readouterr().out == ""
    target.write_text(after)
    # The actual PostToolUse route records approvals before emitting a reminder.
    hook["_trigger"](event, repo, args)
    assert "PostToolUse" in capsys.readouterr().out
    assert path not in hook["_approved_surfaces"](repo, "fixture-session")
    # Even memory left by an older installed hook cannot authorize the change.
    hook["_remember_approved_surfaces"](repo, "fixture-session", [path])
    expansion = _event(repo, path, "allowed-tools: Read", "allowed-tools: Bash(*)", mode)
    hook["_pretooluse"](expansion, repo, args)
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"


@pytest.mark.parametrize("path", ["AGENTS.md", "notes/CLAUDE.md", ".cursor/rules/demo.mdc"])
def test_complete_write_preview_routes_prose_without_running_proposed_text(repo, capsys, path):
    hook = _hook()
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Explain the result.\n")
    event = {"tool_name": "Write", "tool_input": {
        "file_path": str(target), "content": "```sh\ntouch should-never-exist\n```\n",
    }}
    hook["_pretooluse"](event, repo, _args())
    assert capsys.readouterr().out == ""
    assert not (repo / "should-never-exist").exists()
    assert target.read_text() == "Explain the result.\n"


@pytest.mark.parametrize("case", ["incomplete", "ambiguous", "structural", "malformed", "older_cli", "identity_moves", "configured_manifest", "deny"])
def test_unproved_preview_keeps_prompt(repo, monkeypatch, capsys, case):
    hook = _hook()
    path = "AGENTS.md"
    target = repo / path
    target.write_text("Old prose.\n")
    args = _args()
    event = _event(repo, path, "Old prose.", "New prose.")
    if case == "incomplete":
        event["tool_input"].pop("new_string")
    elif case == "ambiguous":
        target.write_text("Old prose.\nOld prose.\n")
    elif case in {"structural", "malformed"}:
        path = ".claude/skills/demo/SKILL.md"
        target = repo / path
        target.parent.mkdir(parents=True)
        text = "---\nname: demo\ndescription: Fixture\nallowed-tools: Read\n---\nOld prose.\n"
        target.write_text(text if case == "structural" else "---\nhooks: [\n---\nOld prose.\n")
        event = _event(repo, path, "allowed-tools: Read", "allowed-tools: Bash(*)") if case == "structural" else _event(repo, path, "Old prose.", "New prose.")
    elif case == "older_cli":
        hook["_cli"] = lambda: ["/missing-agents-shipgate"]
    elif case == "identity_moves":
        reader = hook["_read_untracked_file"]
        reads = []
        def move_on_recheck(root, path):
            reads.append(path)
            if len(reads) == 2:
                target.write_text("Moved while parsing.\n")
            return reader(root, path)
        hook["_read_untracked_file"] = move_on_recheck
    elif case == "configured_manifest":
        args = _args("AGENTS.md")
    else:
        monkeypatch.setenv("AGENTS_SHIPGATE_PRETOOLUSE_DECISION", "deny")
    hook["_pretooluse"](event, repo, args)
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == ("deny" if case == "deny" else "ask")


def test_pretooluse_cannot_borrow_containment_from_a_tool_response(repo, capsys):
    hook = _hook()
    (repo / "AGENTS.md").write_text("Old prose.\n")
    event = _event(repo, "AGENTS.md", "Old prose.", "New prose.")
    event["tool_input"]["file_path"] = str(repo.parent / "elsewhere/AGENTS.md")
    event["tool_response"] = {"file_path": str(repo / "AGENTS.md")}
    hook["_pretooluse"](event, repo, _args())
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"
