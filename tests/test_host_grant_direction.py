"""#820: a changed grant is not automatically an expansion."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _claude_grants,
    _codex_grants,
    _mcp_grants,
    diff_host_grants,
    host_grant_expansion_signals,
)
from agents_shipgate.core.preflight import signals_for_host_grant_drift

SETTINGS = ".claude/settings.json"


def _hook(command):
    return {"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": command}]}]}}


CASES = [
    ({"enabledPlugins": {"demo@local": True}}, {"enabledPlugins": {"demo@local": False}}, False),
    ({}, {"enabledPlugins": {"demo@local": False}}, False),
    ({"enabledPlugins": {"demo@local": False}}, {"enabledPlugins": {"demo@local": True}}, True),
    ({}, {"enabledPlugins": {"demo@local": True}}, True),
    ({"permissions": {"defaultMode": "bypassPermissions"}}, {"permissions": {"defaultMode": "default"}}, False),
    ({"permissions": {"defaultMode": "default"}}, {"permissions": {"defaultMode": "bypassPermissions"}}, True),
    ({"permissions": {"defaultMode": "bypassPermissions"}}, {"permissions": {"defaultMode": "acceptEdits"}}, False),
    ({"permissions": {"defaultMode": "acceptEdits"}}, {"permissions": {"defaultMode": "auto"}}, False),
    ({"sandbox": {"enabled": False}}, {"sandbox": {"enabled": True}}, False),
    ({"sandbox": {"enabled": True}}, {"sandbox": {"enabled": False}}, True),
    (_hook("bin/lint.sh"), _hook("[ -f .skip-lint ] && exit 0; bin/lint.sh"), False),
    ({}, _hook("bin/lint.sh"), True),
]


def _compare(reader, before, after, source=SETTINGS):
    changes = diff_host_grants(
        {"grants": reader(before, scope="repository", source=source)},
        {"grants": reader(after, scope="repository", source=source)},
    )
    signals = host_grant_expansion_signals(changes)
    return changes, signals, capability_diff_rows({"changes": changes, "expansion_signals": signals})


@pytest.mark.parametrize("before,after,expands", CASES)
def test_claude_direction_from_facts(before, after, expands):
    changes, signals, rows = _compare(_claude_grants, before, after)
    assert changes and rows
    assert bool(signals) is expands
    assert any(row.expands for row in rows) is expands
    if not expands:
        assert all(row.direction != "widened" for row in rows)


@pytest.mark.parametrize("host", ["claude-code", "codex", "cursor", "vscode"])
def test_mcp_argument_change_is_not_inferred_as_widening_or_narrowing(host):
    before = {"mcpServers": {"docs": {"command": "npx", "args": ["example-server@1.2.3"]}}}
    after = {"mcpServers": {"docs": {"command": "npx", "args": ["example-server@1.2.3", "--read-only"]}}}
    def reader(data, **kwargs):
        return _mcp_grants(data, host=host, **kwargs)
    _, signals, rows = _compare(reader, before, after, ".mcp.json")
    assert signals == []
    assert [(row.direction, row.expands) for row in rows] == [("changed", False)]
    assert "authority direction is unknown" in rows[0].why
    _, signals, rows = _compare(reader, {}, after, ".mcp.json")
    assert signals and rows[0].expands


@pytest.mark.parametrize("setting,before,after,expands", [
    ("sandbox_mode", "danger-full-access", "workspace-write", False),
    ("sandbox_mode", "workspace-write", "danger-full-access", True),
    ("sandbox_mode", "read-only", "workspace-write", True),
    ("sandbox_mode", "custom", "danger-full-access", False),
    ("approval_policy", "on-request", "never", False),
    ("web_search", "cached", "live", False),
])
def test_codex_settings_are_not_ranked_by_risk(setting, before, after, expands):
    _, signals, rows = _compare(_codex_grants, {setting: before}, {setting: after}, ".codex/config.toml")
    assert bool(signals) is expands
    assert any(row.expands for row in rows) is expands


def test_one_setting_expansion_does_not_mark_a_tightening_in_the_same_file():
    before = {"permissions": {"defaultMode": "bypassPermissions"}, "enableAllProjectMcpServers": False}
    after = {"permissions": {"defaultMode": "default"}, "enableAllProjectMcpServers": True}
    _, signals, rows = _compare(_claude_grants, before, after)
    assert signals == [f"permission_mode_added: claude-code:{SETTINGS}"]
    assert [row.after for row in rows if row.expands] == ["enableAllProjectMcpServers: true"]


def test_named_mcp_approvals_are_not_paired_as_scalar_replacements():
    _, signals, rows = _compare(
        _claude_grants, {"enabledMcpjsonServers": ["old"]}, {"enabledMcpjsonServers": ["new"]},
    )
    assert signals
    assert [row.after for row in rows if row.expands] == ["enabledMcpjsonServers: new"]


@pytest.mark.parametrize("value", ["false", 1, [], {}])
def test_plugin_enablement_is_not_boolean_coercion(value):
    _, signals, rows = _compare(_claude_grants, {}, {"enabledPlugins": {"demo@local": value}})
    assert rows and not signals and not any(row.expands for row in rows)


@pytest.mark.parametrize("path,before,after,expands", [
    (SETTINGS, *case) for case in CASES
] + [
    (
        ".mcp.json",
        {"mcpServers": {"docs": {"command": "npx", "args": ["server@1"]}}},
        {"mcpServers": {"docs": {"command": "npx", "args": ["server@1", "--read-only"]}}},
        False,
    ),
    (".mcp.json", {}, {"mcpServers": {"docs": {"command": "server"}}}, True),
])
def test_every_route_retains_changes_and_agrees_on_expansion(tmp_path, path, before, after, expands):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args], env=env, check=True, capture_output=True, text=True)
    def invoke(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(repo)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout
    git("init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n")
    (repo / path).write_text(json.dumps(before))
    git("add", "-A")
    git("commit", "-qm", "base")
    invoke("audit", "--host", "--save-baseline", "--json")
    git("checkout", "-qb", "change")
    (repo / path).write_text(json.dumps(after))
    git("add", "-A")
    git("commit", "-qm", "head")
    diff = json.loads(invoke("diff", "--base", "main", "--json"))
    text = invoke("diff", "--base", "main")
    check = json.loads(invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    verifier = json.loads(invoke("verify", "--base", "main", "--head", "HEAD", "--json"))
    drift = json.loads(invoke("audit", "--host", "--drift", "--json"))
    preflight = json.loads(invoke("preflight", "--json"))
    assert diff["rows"] and diff["comparison_status"] == "comparable"
    assert diff["rows"] == verifier["host_comparison"]["rows"]
    def shape(rows):
        return sorted((row["direction"], row["expands"]) for row in rows)
    assert shape(diff["rows"]) == shape(check["rows"])
    assert any(row["expands"] for row in diff["rows"]) is expands
    assert bool(drift["expansion_signals"]) is expands
    assert ("⚠" in text) is expands
    comment = (repo / "agents-shipgate-reports/pr-comment.md").read_text()
    assert ("⚠" in comment) is expands
    reasons = signals_for_host_grant_drift(drift)
    assert ("Expansion signals" in " ".join(reason.reason for reason in reasons)) is expands
    assert preflight["host_grant_drift"]["expansion_signals"] == drift["expansion_signals"]
    assert ("Expansion signals" in " ".join(signal["reason"] for signal in preflight["signals"])) is expands


@pytest.mark.parametrize("server,expands", [
    ({"command": "server"}, True),
    ({"type": "http", "url": "https://example.invalid/mcp"}, False),
])
def test_vscode_sandbox_direction_requires_the_documented_stdio_transport(server, expands):
    from agents_shipgate.core.host_grants import _vscode_mcp_extras

    def reader(data, **kwargs):
        return _vscode_mcp_extras(data, **kwargs)[0]
    _, signals, rows = _compare(
        reader,
        {"servers": {"docs": {**server, "sandboxEnabled": True}}},
        {"servers": {"docs": {**server, "sandboxEnabled": False}}},
        ".vscode/mcp.json",
    )
    assert bool(signals) is expands
    assert any(row.expands for row in rows) is expands


def test_ambiguous_setting_predecessors_do_not_invent_a_default():
    old = _claude_grants({"permissions": {"defaultMode": "default"}}, scope="repository", source=SETTINGS)
    old += _claude_grants({"permissions": {"defaultMode": "plan"}}, scope="repository", source=SETTINGS)
    new = _claude_grants({"permissions": {"defaultMode": "bypassPermissions"}}, scope="repository", source=SETTINGS)
    changes = diff_host_grants({"grants": old}, {"grants": new})
    assert host_grant_expansion_signals(changes) == []


@pytest.mark.parametrize("value", ["False", "false", 0, None])
def test_sandbox_disable_requires_a_boolean_not_a_lookalike(value):
    _, signals, rows = _compare(_claude_grants, {"sandbox": {"enabled": True}}, {"sandbox": {"enabled": value}})
    assert rows and not signals and not any(row.expands for row in rows)
