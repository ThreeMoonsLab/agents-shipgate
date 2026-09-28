"""#702: selected script bytes are evidence, never script semantics."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.hook_script_capture import MAX_HOOK_SCRIPT_BYTES, capture_hook_script
from agents_shipgate.core.host_grants import (
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    diff_host_grants,
    host_grant_expansion_signals,
    inventory_is_complete,
)
from agents_shipgate.core.trust_roots import IdentityBoundReadSession, IdentityReadBudgetExceeded


def reader(root, *, budget=2 * MAX_HOOK_SCRIPT_BYTES):
    return IdentityBoundReadSession(root, max_entries=1000, max_total_bytes=budget)


def workspace(root, *, host="claude-code", command=None, source=None):
    root.mkdir(exist_ok=True)
    path = root / (source or (".claude/settings.json" if host == "claude-code" else ".codex/hooks.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    command = command or ('"${CLAUDE_PROJECT_DIR}/guard.sh"' if host == "claude-code" else f'"{root}/guard.sh"')
    path.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}))
    (root / "guard.sh").write_bytes(b"#!/bin/sh\nprintf first\n")
    return root


def inventory(root):
    return build_host_boundary_snapshot(root).inventory


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_script_only_change_is_one_non_widening_hook_row(tmp_path, host):
    root = workspace(tmp_path / "repo", host=host)
    before = inventory(root)
    (root / "guard.sh").write_bytes(b"#!/bin/sh\nprintf changed\n")
    after = inventory(root)
    changes = diff_host_grants(before, after)
    assert len(changes) == 1
    assert changes[0]["baseline"]["config_sha256"] == changes[0]["current"]["config_sha256"]
    assert host_grant_expansion_signals(changes) == []
    (row,) = capability_diff_rows({"changes": changes, "expansion_signals": []})
    assert row.direction == "changed" and row.expands is False
    assert "guard.sh" in row.why and "declaration unchanged" in row.why
    assert "host_configuration" in row.why and "not permissions or runtime behavior" in row.why
    assert row.before == row.after == "SessionStart"


def test_unchanged_and_unrelated_files_are_quiet(tmp_path):
    root = workspace(tmp_path / "repo")
    before = inventory(root)
    (root / "unrelated.sh").write_text("unrelated")
    assert diff_host_grants(before, inventory(root)) == []


def test_baseline_preserves_script_digest_and_historical_absence_is_not_empty(tmp_path):
    root = workspace(tmp_path / "repo")
    current = inventory(root)
    baseline = build_host_grants_baseline(current)
    (grant,) = baseline["inventory"]["grants"]
    assert "handlers" not in grant
    assert grant["script_inputs"][0]["sha256"] == hashlib.sha256((root / "guard.sh").read_bytes()).hexdigest()
    grant.pop("script_inputs")
    result = build_host_drift_payload(baseline=baseline, inventory=current, baseline_file="old.json")
    assert "baseline_hook_script_inputs_unavailable" in result["incomparable_reasons"]


def test_script_bytes_are_not_decoded_or_interpreted(tmp_path):
    payload = b"\xff\x00permissionDecision allow; rm -rf /\n"
    (tmp_path / "guard").write_bytes(payload)
    session = reader(tmp_path)
    assert capture_hook_script(session, "guard") == {
        "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload), "limit": None,
    }
    session.finish()


@pytest.mark.parametrize("shape,limit", [
    ("missing", "missing_input"), ("directory", "non_regular_input"),
    ("symlink", "symlink_input"), ("parent_symlink", "symlink_input"),
    ("hardlink", "unsafe_or_unreadable_input"), ("oversize", "oversized_input"),
])
def test_unsafe_inputs_are_precise_limits(tmp_path, shape, limit):
    path = "guard"
    if shape == "directory":
        (tmp_path / path).mkdir()
    elif shape in {"symlink", "parent_symlink"}:
        (tmp_path / "target").write_text("outside")
        (tmp_path / path).symlink_to("target")
        if shape == "parent_symlink":
            path += "/child"
    elif shape == "hardlink":
        (tmp_path / "target").write_text("same inode")
        os.link(tmp_path / "target", tmp_path / path)
    elif shape == "oversize":
        (tmp_path / path).write_bytes(b"x" * (MAX_HOOK_SCRIPT_BYTES + 1))
    session = reader(tmp_path)
    result = capture_hook_script(session, path)
    assert result["limit"] == limit and result["sha256"] is None


def test_unreadable_input_and_budget_failure_are_not_empty_evidence(tmp_path, monkeypatch):
    (tmp_path / "guard").write_bytes(b"some bytes")
    with pytest.raises(IdentityReadBudgetExceeded):
        capture_hook_script(reader(tmp_path, budget=1), "guard")
    session = reader(tmp_path)
    def fail(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(session, "read_bytes", fail)
    assert capture_hook_script(session, "guard")["limit"] == "unreadable_input"


def test_capture_is_invalidated_when_script_changes_before_finish(tmp_path):
    path = tmp_path / "guard"
    path.write_text("before")
    session = reader(tmp_path)
    assert capture_hook_script(session, "guard")["sha256"]
    path.write_text("after and different")
    with pytest.raises(ValueError, match="changed"):
        session.finish()


def test_missing_selected_script_refuses_complete_inventory(tmp_path):
    root = workspace(tmp_path / "repo")
    (root / "guard.sh").unlink()
    result = inventory(root)
    assert not inventory_is_complete(result)
    assert any("guard.sh" in item["message"] and "missing_input" in item["message"] for item in result["issues"])


def test_unselected_hook_and_fallback_never_read_a_script(tmp_path, monkeypatch):
    root = workspace(tmp_path / "repo", source=".claude/hooks/hooks.json")
    def fail(*args, **kwargs):
        pytest.fail("unselected or unresolved script must remain unread")
    monkeypatch.setattr("agents_shipgate.core.host_grants.capture_hook_script", fail)
    result = inventory(root)
    assert result["grants"][0]["script_inputs"][0]["limit"] == "hook_selection_not_established"
    workspace(root, command='"${CLAUDE_PLUGIN_ROOT:-.}/guard.sh"')
    result = inventory(root)
    assert inventory_is_complete(result)
    assert any(item["limit"] == "dynamic_or_conditional_path" for grant in result["grants"] for item in grant["script_inputs"])


def test_handler_bound_across_groups_is_not_silently_complete(tmp_path):
    from agents_shipgate.core.hook_script_reference import MAX_HOOK_SCRIPT_HANDLERS
    root = workspace(tmp_path / "repo")
    handler = {"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/guard.sh"'}
    (root / ".claude/settings.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [handler] * MAX_HOOK_SCRIPT_HANDLERS}, {"hooks": [handler]},
    ]}}))
    result = inventory(root)
    assert not inventory_is_complete(result)
    assert result["grants"][0]["script_inputs"][-1]["limit"] == "handler_bound_exceeded"


@pytest.mark.parametrize("enabled", [True, False])
def test_plugin_root_is_bound_only_for_an_enabled_selected_plugin(tmp_path, enabled):
    root = tmp_path / "repo"
    files = {
        ".claude/settings.json": {
            "extraKnownMarketplaces": {"market": {"source": {"source": "directory", "path": "./"}}},
            "enabledPlugins": {"demo@market": enabled},
        },
        ".claude-plugin/marketplace.json": {
            "name": "market", "owner": {"name": "owner"},
            "plugins": [{"name": "demo", "source": "./plugins/demo"}],
        },
        "plugins/demo/.claude-plugin/plugin.json": {"name": "demo"},
        "plugins/demo/hooks/hooks.json": {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/guard.sh"'},
        ]}]}},
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    script = root / "plugins/demo/guard.sh"
    script.write_text("before")
    before = inventory(root)
    script.write_text("after")
    after = inventory(root)
    changes = diff_host_grants(before, after)
    if enabled:
        assert len(changes) == 1
        entry = changes[0]["current"]["script_inputs"][0]
        assert entry["path"] == "plugins/demo/guard.sh"
        assert entry["basis"] == "plugin_root_placeholder"
        assert host_grant_expansion_signals(changes) == []
    else:
        assert changes == []


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_live_and_committed_routes_read_the_same_script_bytes(tmp_path, host):
    root = workspace(tmp_path / "repo", host=host)
    def git(*args):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
            capture_output=True, check=True,
        )
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    (root / "guard.sh").write_text("changed bytes, not executable analysis")
    git("add", "guard.sh")
    git("commit", "-qm", "script change")
    def run(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(root)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout
    diff = json.loads(run("diff", "--base", "main", "--json"))
    assert len(diff["rows"]) == 1, diff
    assert "guard.sh" in diff["rows"][0]["why"]
    assert not diff["rows"][0]["expands"]
    script_coverage = [item for item in diff["coverage"]["items"] if item["source"] == "guard.sh"]
    assert len(script_coverage) == 1
    assert script_coverage[0]["status"] == "changed_without_rows"
    assert script_coverage[0]["rows"] == 0  # The one row belongs to the hook declaration.
    assert script_coverage[0]["hosts"] == [host]
    assert diff["coverage"]["read_sources_only"] is True
    checked = json.loads(run("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    assert checked["control"]["state"] != "complete"
    assert host in checked["affected_hosts"]
    assert any(
        row["path"] == "guard.sh" and row["evidence"].get("hook_script_hosts") == [host]
        for row in checked["violations"]
    )
    text = run("diff", "--base", "main")
    assert "guard.sh" in text and "SessionStart" in text
    committed = json.loads(run("verify", "--base", "main", "--head", "HEAD", "--json"))
    assert committed["host_comparison"]["rows"] == diff["rows"]
    assert "guard.sh" in (root / "agents-shipgate-reports/pr-comment.md").read_text()
    live = json.loads(run("verify", "--base", "main", "--json"))
    assert live["host_comparison"]["rows"] == diff["rows"]


def test_current_control_rejects_a_changed_ignored_script(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("guard.sh\nagents-shipgate-reports/\n")
    def git(*args):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
            capture_output=True, check=True,
        )
    git("init", "-q", "-b", "main")
    git("add", ".gitignore")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    workspace(root)
    verify = CliRunner().invoke(app, ["verify", "--workspace", str(root), "--base", "main", "--json"])
    assert verify.exit_code in (0, 10, 20), verify.output
    command = ["agent", "control", "--workspace", str(root)]
    before = CliRunner().invoke(app, command)
    assert before.exit_code == 0, before.output
    (root / "guard.sh").write_text("changed after verification")
    after = CliRunner().invoke(app, command)
    assert after.exit_code != 0, after.output
    assert "script" in after.output.lower() or "dependency" in after.output.lower()


def test_script_change_correction_and_recurrence(tmp_path):
    root = workspace(tmp_path / "repo")
    script = root / "guard.sh"
    original = script.read_bytes()
    baseline = inventory(root)
    changed = original + b"# constructed dependency change\n"
    script.write_bytes(changed)
    first = diff_host_grants(baseline, inventory(root))
    assert len(first) == 1
    script.write_bytes(original)
    assert diff_host_grants(baseline, inventory(root)) == []
    script.write_bytes(changed)
    assert diff_host_grants(baseline, inventory(root)) == first


@pytest.mark.parametrize("groups", [{"unexpected": []}, [{"unexpected": []}]])
def test_unsupported_hook_shapes_record_a_limit_without_inventing_dependencies(tmp_path, groups):
    root = workspace(tmp_path / "repo")
    (root / ".claude/settings.json").write_text(json.dumps({"hooks": {"SessionStart": groups}}))
    result = inventory(root)
    assert inventory_is_complete(result)
    assert result["grants"][0]["script_inputs"] == [{
        "handler": 0, "limit": "unsupported_hook_shape", "path": None,
        "basis": None, "sha256": None, "size_bytes": None,
    }]


def test_dependency_after_display_limit_is_still_compared(tmp_path):
    from agents_shipgate.core.host_grants import MAX_HOOK_HANDLERS

    root = workspace(tmp_path / "repo")
    handlers = [{"type": "command", "command": "echo display-only"}] * MAX_HOOK_HANDLERS
    handlers.append({"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/guard.sh"'})
    (root / ".claude/settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": handlers}]}}))
    before = inventory(root)
    assert inventory_is_complete(before)
    (root / "guard.sh").write_text("dependency beyond displayed handlers changed")
    after = inventory(root)
    changes = diff_host_grants(before, after)
    assert len(changes) == 1
    assert changes[0]["current"]["script_inputs"][-1]["handler"] == MAX_HOOK_HANDLERS
    assert changes[0]["current"]["script_inputs"][-1]["path"] == "guard.sh"
    assert host_grant_expansion_signals(changes) == []
