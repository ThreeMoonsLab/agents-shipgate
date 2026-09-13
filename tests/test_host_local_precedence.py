"""Local Claude Code layers resolve by the documented settings precedence (#657).

`audit --host --scope local-static` used to raise a blocking
`unresolved_precedence` issue whenever two layers existed, so the ordinary
setup — user settings plus a project's — could never save a local baseline.
Each case below is one documented rule: code.claude.com/docs/en/settings
§ Settings precedence and § Lists merge instead of overriding,
code.claude.com/docs/en/hooks, and code.claude.com/docs/en/mcp. Where the
documentation does not settle a case, the inventory still fails closed, and
now names the key and each layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents_shipgate.core.host_grants import (
    _project_claude_precedence,
    build_host_grants_baseline,
    host_audit_inventory,
)

USER = "~/.claude/settings.json"
PROJECT = ".claude/settings.json"
LOCAL = ".claude/settings.local.json"
MANAGED = "/etc/claude-code/managed-settings.json"
WORKSPACE_STATE = "~/.claude.json#current-workspace"


def _audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    scope: str = "local_static",
    user: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    local: dict[str, Any] | None = None,
    mcp: dict[str, Any] | None = None,
    workspace_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = tmp_path / "repo"
    (workspace / ".claude").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    files = {
        home / ".claude/settings.json": user,
        workspace / ".claude/settings.json": project,
        workspace / ".claude/settings.local.json": local,
        workspace / ".mcp.json": {"mcpServers": mcp} if mcp is not None else None,
        home / ".claude.json": (
            {"projects": {str(workspace.resolve()): workspace_state}}
            if workspace_state is not None
            else None
        ),
    }
    for path, content in files.items():
        if content is not None:
            path.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return host_audit_inventory(workspace, scope=scope)


def _claude(inventory: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        grant
        for grant in inventory["grants"]
        if grant["host"] == "claude-code" and grant["kind"] == kind
    ]


def _precedence_issues(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    return [issue for issue in inventory["issues"] if issue["kind"] == "unresolved_precedence"]


def test_user_and_project_permission_rules_merge_and_the_setup_can_be_baselined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _audit(
        tmp_path,
        monkeypatch,
        user={"permissions": {"allow": ["Read"]}},
        project={"permissions": {"deny": ["Bash(*)"], "allow": ["Bash(pytest *)"]}},
    )

    assert not _precedence_issues(inventory)
    assert {
        (grant["source"], grant["disposition"], grant["rule"])
        for grant in _claude(inventory, "permission_rule")
    } == {
        (USER, "allow", "Read"),
        (PROJECT, "deny", "Bash(*)"),
        (PROJECT, "allow", "Bash(pytest *)"),
    }
    claude = next(item for item in inventory["host_coverage"] if item["host"] == "claude-code")
    assert claude["status"] == "complete"
    assert build_host_grants_baseline(inventory)["scope"] == "local_static"


@pytest.mark.parametrize(
    ("layers", "effective"),
    [
        ({"user": "acceptEdits", "project": "plan"}, PROJECT),
        ({"user": "acceptEdits", "project": "plan", "local": "default"}, LOCAL),
        ({"user": "acceptEdits", "local": "plan"}, LOCAL),
        ({"user": "plan"}, USER),
    ],
)
def test_a_scalar_setting_takes_the_highest_layer_that_sets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layers: dict[str, str], effective: str
) -> None:
    inventory = _audit(
        tmp_path,
        monkeypatch,
        **{layer: {"permissions": {"defaultMode": mode}} for layer, mode in layers.items()},
    )

    modes = [grant for grant in _claude(inventory, "permission_mode") if grant["setting"] == "defaultMode"]
    assert [grant["source"] for grant in modes] == [effective]
    assert not _precedence_issues(inventory)


def test_a_mode_its_file_cannot_apply_is_reported_beside_the_lower_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bypassPermissions` does not take effect from project settings on current
    Claude Code, but did before v2.1.257, and a static read cannot see the
    installed version. It must neither shadow the user's mode nor disappear."""

    inventory = _audit(
        tmp_path,
        monkeypatch,
        user={"permissions": {"defaultMode": "acceptEdits"}},
        project={"permissions": {"defaultMode": "bypassPermissions"}},
    )

    modes = {
        (grant["source"], grant["value"])
        for grant in _claude(inventory, "permission_mode")
        if grant["setting"] == "defaultMode"
    }
    assert modes == {(USER, "acceptEdits"), (PROJECT, "bypassPermissions")}
    assert not _precedence_issues(inventory)


def test_hooks_from_every_layer_stay_effective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}
    inventory = _audit(tmp_path, monkeypatch, user={"hooks": hook}, project={"hooks": hook})

    assert {grant["source"] for grant in _claude(inventory, "hook")} == {USER, PROJECT}
    assert not _precedence_issues(inventory)


def test_a_same_named_mcp_server_takes_the_local_scope_entry_and_others_combine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _audit(
        tmp_path,
        monkeypatch,
        mcp={
            "docs": {"command": "npx", "args": ["project-docs"]},
            "shared": {"command": "npx", "args": ["shared"]},
        },
        workspace_state={"mcpServers": {"docs": {"command": "npx", "args": ["local-docs"]}}},
    )

    assert {
        (grant["server"], grant["source"]) for grant in _claude(inventory, "mcp_server")
    } == {("docs", WORKSPACE_STATE), ("shared", ".mcp.json")}
    assert not _precedence_issues(inventory)


@pytest.mark.parametrize(
    ("user", "project", "key"),
    [
        ({"sandbox": {"enabled": True}}, {"sandbox": {"enabled": False}}, "claude-code:sandbox:sandbox.enabled"),
        ({"enabledPlugins": {"tool@market": True}}, {"enabledPlugins": {"tool@market": False}}, "claude-code:plugin_or_app:tool@market"),
    ],
)
def test_an_undocumented_disagreement_fails_closed_and_names_both_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user: dict[str, Any],
    project: dict[str, Any],
    key: str,
) -> None:
    inventory = _audit(tmp_path, monkeypatch, user=user, project=project)

    [issue] = _precedence_issues(inventory)
    assert issue["source"] == key
    assert issue["blocking"] is True
    assert USER in issue["message"] and PROJECT in issue["message"]
    with pytest.raises(ValueError, match="cannot acknowledge missing evidence"):
        build_host_grants_baseline(inventory)


def test_agreeing_layers_of_an_undocumented_kind_resolve_to_the_highest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _audit(
        tmp_path,
        monkeypatch,
        user={"sandbox": {"enabled": True}},
        project={"sandbox": {"enabled": True}},
    )

    assert [grant["source"] for grant in _claude(inventory, "sandbox")] == [PROJECT]
    assert not _precedence_issues(inventory)


def test_other_hosts_still_fail_closed_and_name_their_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor does not document whether its user and project files merge."""

    workspace = tmp_path / "repo"
    (workspace / ".cursor").mkdir(parents=True)
    (workspace / ".cursor/mcp.json").write_text(json.dumps({"mcpServers": {"a": {"command": "a"}}}), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor/mcp.json").write_text(json.dumps({"mcpServers": {"b": {"command": "b"}}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    inventory = host_audit_inventory(workspace, scope="local_static")

    [issue] = _precedence_issues(inventory)
    assert issue["host"] == "cursor"
    assert "~/.cursor/mcp.json" in issue["message"] and ".cursor/mcp.json" in issue["message"]


def test_the_repository_scope_is_not_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PR route reads committed files only and never resolved layers; a
    shared and a local project file both stay visible there."""

    inventory = _audit(
        tmp_path,
        monkeypatch,
        scope="repository",
        project={"permissions": {"defaultMode": "plan"}},
        local={"permissions": {"defaultMode": "default"}},
    )

    assert {grant["source"] for grant in _claude(inventory, "permission_mode")} == {PROJECT, LOCAL}


# --- The managed-only restrictions, on synthetic grants (a managed file is
#     an operating-system path no test may write). ------------------------


def _grant(source: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"grant_id": f"{source}:{kind}:{sorted(fields.items())}", "host": "claude-code", "source": source, "kind": kind, **fields}


def test_managed_rules_only_removes_every_other_layers_rules() -> None:
    grants = [
        _grant(MANAGED, "permission_mode", setting="allowManagedPermissionRulesOnly", value="True"),
        _grant(MANAGED, "permission_rule", disposition="deny", rule="Bash(*)"),
        _grant(USER, "permission_rule", disposition="allow", rule="Bash(*)"),
        _grant(PROJECT, "hook", event="PreToolUse"),
    ]

    kept, issues = _project_claude_precedence(grants)

    assert {(grant["source"], grant["kind"]) for grant in kept} == {
        (MANAGED, "permission_mode"),
        (MANAGED, "permission_rule"),
        (PROJECT, "hook"),
    }
    assert issues == []


def test_managed_hooks_only_removes_every_other_layers_hooks() -> None:
    grants = [
        _grant(MANAGED, "permission_mode", setting="allowManagedHooksOnly", value="True"),
        _grant(MANAGED, "hook", event="Stop"),
        _grant(LOCAL, "hook", event="PreToolUse"),
        _grant(USER, "permission_rule", disposition="allow", rule="Read"),
    ]

    kept, _ = _project_claude_precedence(grants)

    assert {(grant["source"], grant["kind"]) for grant in kept} == {
        (MANAGED, "permission_mode"),
        (MANAGED, "hook"),
        (USER, "permission_rule"),
    }


def test_a_managed_only_setting_outside_managed_settings_restricts_nothing() -> None:
    grants = [
        _grant(PROJECT, "permission_mode", setting="allowManagedPermissionRulesOnly", value="True"),
        _grant(USER, "permission_rule", disposition="allow", rule="Read"),
    ]

    kept, _ = _project_claude_precedence(grants)

    assert [(grant["source"], grant["kind"]) for grant in kept] == [(USER, "permission_rule")]


def test_a_layer_without_a_documented_rank_fails_closed() -> None:
    grants = [
        _grant(WORKSPACE_STATE, "permission_mode", setting="defaultMode", value="plan"),
        _grant(USER, "permission_mode", setting="defaultMode", value="acceptEdits"),
    ]

    kept, [issue] = _project_claude_precedence(grants)

    assert len(kept) == 2
    assert issue["source"] == "claude-code:permission_mode:defaultMode"
    assert WORKSPACE_STATE in issue["message"]


def test_other_hosts_grants_pass_through_untouched() -> None:
    other = {"grant_id": "x", "host": "codex", "source": "~/.codex/config.toml", "kind": "permission_mode", "setting": "approval_policy", "value": "never"}

    kept, issues = _project_claude_precedence([other])

    assert kept == [other] and issues == []
