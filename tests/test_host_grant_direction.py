"""#820: a changed grant is not automatically an expansion."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import DIRECTION_UNKNOWN, capability_diff_rows
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
    # An undocumented predecessor is not a host default: full access arriving
    # widens as it does from nothing (#820 review).
    ("sandbox_mode", "custom", "danger-full-access", True),
    ("sandbox_mode", "workspace_write", "danger-full-access", True),
    ("approval_policy", "on-request", "never", False),
    # `cached` has no external web access; `live` is unrestricted retrieval.
    ("web_search", "cached", "live", True),
    ("web_search", "live", "cached", False),
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


def test_ambiguous_setting_predecessors_fall_back_to_a_new_declaration():
    """Two values left, so neither is the one replaced: the arrival is read as
    a new declaration, and `bypassPermissions` declared anew widens (#820 review)."""

    old = _claude_grants({"permissions": {"defaultMode": "default"}}, scope="repository", source=SETTINGS)
    old += _claude_grants({"permissions": {"defaultMode": "plan"}}, scope="repository", source=SETTINGS)
    new = _claude_grants({"permissions": {"defaultMode": "bypassPermissions"}}, scope="repository", source=SETTINGS)
    changes = diff_host_grants({"grants": old}, {"grants": new})
    assert host_grant_expansion_signals(changes) == [f"permission_mode_added: claude-code:{SETTINGS}"]


@pytest.mark.parametrize("value", ["False", "false", 0, None])
def test_sandbox_disable_requires_a_boolean_not_a_lookalike(value):
    _, signals, rows = _compare(_claude_grants, {"sandbox": {"enabled": True}}, {"sandbox": {"enabled": value}})
    assert rows and not signals and not any(row.expands for row in rows)


def _cursor(data, **kwargs):
    from agents_shipgate.core.host_grants import _cursor_grants

    return _cursor_grants(data, **kwargs)


def _vscode(data, **kwargs):
    from agents_shipgate.core.host_grants import _vscode_mcp_extras

    return _mcp_grants(data, host="vscode", **kwargs) + _vscode_mcp_extras(data, **kwargs)[0]


def _handlers(*groups):
    return {"hooks": {"PostToolUse": [
        {"matcher": matcher, "hooks": [{"type": "command", "command": command}]} for matcher, command in groups
    ]}}


@pytest.mark.parametrize("before,after,expands", [
    # Hook grants are one per event, so a hook added to an event that already
    # had one is a `changed` grant; one more declared handler is still an
    # added hook (#820 review).
    (_handlers(("Edit", "bin/lint.sh")), _handlers(("Edit", "bin/lint.sh"), ("*", "bin/x.sh")), True),
    (_handlers(("Edit", "bin/lint.sh"), ("*", "bin/x.sh")), _handlers(("Edit", "bin/lint.sh")), False),
    (_handlers(("Bash", "bin/guard.sh")), _handlers(("Bash", "bin/approve-everything.sh")), False),
    (_handlers(("Edit", "bin/lint.sh")), _handlers(("*", "bin/lint.sh")), False),
])
def test_an_added_handler_on_an_existing_event_widens(before, after, expands):
    changes, signals, rows = _compare(_claude_grants, before, after)
    assert [change["current"]["kind"] for change in changes] == ["hook"]
    assert bool(signals) is expands
    [row] = rows
    assert (row.direction, row.expands) == (("widened", True) if expands else ("changed", False))
    # Neither a removed handler nor an edit is assumed to narrow.
    assert row.why.endswith(DIRECTION_UNKNOWN) is not expands


_TEAM = {"source": {"source": "github", "repo": "acme/plugins"}}
_FORK = {"source": {"source": "github", "repo": "attacker/plugins"}}


@pytest.mark.parametrize("before,after,direction", [
    ({}, {"extraKnownMarketplaces": {"acme": _TEAM}}, "added"),
    (
        {"enabledPlugins": {"p@acme": True}, "extraKnownMarketplaces": {"acme": _TEAM}},
        {"enabledPlugins": {"p@acme": True}, "extraKnownMarketplaces": {"acme": _FORK}},
        "widened",
    ),
])
def test_a_marketplace_added_or_repointed_still_expands(before, after, direction):
    """#720 shipped this: what the enabled plugins install and run changes."""

    _, signals, rows = _compare(_claude_grants, before, after)
    assert signals
    assert [(row.direction, row.expands, row.why) for row in rows] == [
        (direction, True, "changes a plugin_or_app grant")
    ]


@pytest.mark.parametrize("app,expands", [
    ({"default_tools_enabled": True}, True),  # `enabled` defaults to true.
    ({"enabled": True}, True),
    ({"enabled": False}, False),
])
def test_a_codex_app_declared_without_enabled_is_enabled(app, expands):
    _, signals, rows = _compare(_codex_grants, {}, {"apps": {"docs": app}}, ".codex/config.toml")
    assert bool(signals) is expands
    assert [row.expands for row in rows] == [expands]


def test_rereading_an_app_whose_enablement_was_not_recorded_does_not_widen():
    """A baseline saved before `enabled` defaulted to true re-reads as enabled."""

    current = _codex_grants(
        {"apps": {"docs": {"default_tools_enabled": True}}}, scope="repository", source=".codex/config.toml",
    )
    saved = [{**grant, "enabled": None} for grant in current]
    changes = diff_host_grants({"grants": saved}, {"grants": current})
    assert changes and host_grant_expansion_signals(changes) == []
    assert not any(row.why.endswith(DIRECTION_UNKNOWN) for row in capability_diff_rows({"changes": changes}))


@pytest.mark.parametrize("reader,source,before,after,expands", [
    # Claude Code sandboxing reference: commands run outside the sandbox,
    # prompt-free sandboxed Bash, and a nested sandbox that weakens security.
    (_claude_grants, SETTINGS, {"sandbox": {"enabled": True}},
     {"sandbox": {"enabled": True, "excludedCommands": ["docker"]}}, True),
    (_claude_grants, SETTINGS, {"sandbox": {"excludedCommands": ["docker"]}},
     {"sandbox": {"excludedCommands": ["docker", "curl"]}}, True),
    (_claude_grants, SETTINGS, {"sandbox": {"excludedCommands": ["docker", "curl"]}},
     {"sandbox": {"excludedCommands": ["docker"]}}, False),
    (_claude_grants, SETTINGS, {"sandbox": {"enabled": True}},
     {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}, True),
    (_claude_grants, SETTINGS, {"sandbox": {"autoAllowBashIfSandboxed": True}},
     {"sandbox": {"autoAllowBashIfSandboxed": False}}, False),
    (_claude_grants, SETTINGS, {"sandbox": {"enabled": True}},
     {"sandbox": {"enabled": True, "enableWeakerNestedSandbox": True}}, True),
    (_claude_grants, SETTINGS, {"sandbox": {"network": {"allowedDomains": ["a.example"]}}},
     {"sandbox": {"network": {"allowedDomains": ["*"]}}}, True),
    (_claude_grants, SETTINGS, {"sandbox": {"network": {"allowedDomains": ["a.example", "b.example"]}}},
     {"sandbox": {"network": {"allowedDomains": ["a.example"]}}}, False),
    # Codex config reference: `writable_roots` adds writable directories.
    (_codex_grants, ".codex/config.toml", {}, {"sandbox_workspace_write": {"writable_roots": ["/"]}}, True),
    (_codex_grants, ".codex/config.toml", {"sandbox_workspace_write": {"writable_roots": ["/tmp/a", "/tmp/b"]}},
     {"sandbox_workspace_write": {"writable_roots": ["/tmp/a"]}}, False),
    # VS Code's MCP sandbox takes the same `network.allowedDomains`.
    (_vscode, ".vscode/mcp.json", {"servers": {}, "sandbox": {"network": {"allowedDomains": ["a.example"]}}},
     {"servers": {}, "sandbox": {"network": {"allowedDomains": ["*"]}}}, True),
])
def test_documented_sandbox_loosenings_widen_and_their_reverse_is_settled(reader, source, before, after, expands):
    _, signals, rows = _compare(reader, before, after, source)
    assert bool(signals) is expands
    assert any(row.expands for row in rows) is expands
    assert not any(row.why.endswith(DIRECTION_UNKNOWN) for row in rows)


@pytest.mark.parametrize("before", [
    {"permissions": {"defaultMode": "delegate"}},
    {"permissions": {"defaultMode": "Default"}},
])
def test_an_undocumented_mode_does_not_hide_bypass_permissions(before):
    _, signals, rows = _compare(_claude_grants, before, {"permissions": {"defaultMode": "bypassPermissions"}})
    assert signals == [f"permission_mode_added: claude-code:{SETTINGS}"]
    assert [row.after for row in rows if row.expands] == ["defaultMode: bypassPermissions"]


@pytest.mark.parametrize("reader,source,before,after", [
    (_codex_grants, ".codex/config.toml", {"approval_policy": "untrusted"}, {"approval_policy": "on-request"}),
    (_codex_grants, ".codex/config.toml", {}, {"sandbox_mode": "workspace-write"}),
    (_cursor, ".cursor/cli.json", {"sandbox": True}, {"sandbox": False}),
    (_claude_grants, SETTINGS, {"disableAllHooks": True}, {"disableAllHooks": False}),
    (_claude_grants, SETTINGS, {"permissions": {"defaultMode": "acceptEdits"}}, {"permissions": {"defaultMode": "auto"}}),
    (_claude_grants, SETTINGS, {"sandbox": {"enabled": True}}, {"sandbox": {"enabled": "false"}}),
    (_vscode, ".vscode/mcp.json", {"servers": {}, "sandbox": {"network": {"allowUnixSockets": ["/a"]}}},
     {"servers": {}, "sandbox": {"network": {"allowUnixSockets": ["/b"]}}}),
    (_claude_grants, SETTINGS, {"enabledPlugins": {"p@m": True}}, {"enabledPlugins": {"p@m": "yes"}}),
])
def test_an_edit_no_rule_orders_is_named_as_unknown(reader, source, before, after):
    """#820 acceptance: not a widening, still a row, and named as such."""

    _, signals, rows = _compare(reader, before, after, source)
    assert not signals and not any(row.expands for row in rows)
    arrived = [row for row in rows if row.direction != "removed"]
    assert arrived and all(row.why.endswith(f"; {DIRECTION_UNKNOWN}") for row in arrived)
    assert not any(row.why.endswith(DIRECTION_UNKNOWN) for row in rows if row.direction == "removed")


@pytest.mark.parametrize("before,after", [
    ({"permissions": {"defaultMode": "default"}}, {"permissions": {"defaultMode": "dontAsk"}}),
    ({"permissions": {"defaultMode": "bypassPermissions"}}, {"permissions": {"defaultMode": "default"}}),
    ({"sandbox": {"enabled": False}}, {"sandbox": {"enabled": True}}),
    ({"enabledPlugins": {"p@m": True}}, {"enabledPlugins": {"p@m": False}}),
    ({}, {"permissions": {"disableBypassPermissionsMode": "disable"}}),
])
def test_a_settled_tightening_is_neither_a_widening_nor_unknown(before, after):
    _, signals, rows = _compare(_claude_grants, before, after)
    assert not signals
    assert not any(row.expands or row.why.endswith(DIRECTION_UNKNOWN) for row in rows)
