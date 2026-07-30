from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest
import typer
from pydantic import ValidationError
from typer.testing import CliRunner

import agents_shipgate.cli.host_audit as host_audit_cli
import agents_shipgate.core.trust_roots as trust_roots_module
from agents_shipgate.cli.host_audit import (
    HOST_GRANTS_SCHEMA_VERSION,
    _atomic_write_baseline,
    _refuse_invalid_baseline_overwrite,
    host_audit_inventory,
    host_grants_sha256,
    render_host_audit_markdown,
)
from agents_shipgate.cli.main import app
from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    load_host_grants_baseline,
)
from agents_shipgate.schemas.host_grants import (
    HostGrantsBaselineV2,
    HostGrantsDriftV2,
    HostGrantsInventoryArtifactV2,
    HostGrantsInventoryV2,
)

runner = CliRunner()


def _agent_mode_error(output: str) -> dict:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "error" in payload:
            return payload
    raise AssertionError(f"No structured agent-mode error in output:\n{output}")


def _seed_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {
                            "GITHUB_TOKEN": "secret-token-value",
                            "READ_ONLY": "true",
                        },
                    },
                    "remote": {
                        "url": (
                            "https://user:pass@mcp.example.test/services/"
                            "WEBHOOK-PATH-TOP-SECRET?token=secret"
                        ),
                        "headers": {"Authorization": "Bearer secret"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "Bash(npm test:*)",
                        "Bash(*)",
                        "Bash(curl -H 'Authorization: INLINE-TOP-SECRET' https://example.test)",
                    ],
                    "deny": ["WebFetch"],
                    "additionalDirectories": ["../shared"],
                },
                "sandbox": {"enabled": True},
                "enabledPlugins": {"reviewer@example": True},
                "hooks": {"PreToolUse": []},
            }
        ),
        encoding="utf-8",
    )
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        """
approval_policy = "never"
sandbox_mode = "danger-full-access"

[mcp_servers.docs]
url = "https://docs.example.test/mcp?api_key=secret"
""",
        encoding="utf-8",
    )
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "cli.json").write_text(
        json.dumps({"permissions": {"allow": ["Shell(git status)"]}}),
        encoding="utf-8",
    )
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"browser": {"command": "browser-mcp"}}}),
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "release.yml").write_text(
        """
name: release
on:
  pull_request_target:
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: write-all
    steps:
      - run: echo hi
""",
        encoding="utf-8",
    )
    return tmp_path


def _grants(inventory: dict, kind: str) -> list[dict]:
    return [grant for grant in inventory["grants"] if grant["kind"] == kind]


def _save_baseline(tmp_path: Path, *, scope: str = "repository") -> Path:
    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--scope",
            scope,
            "--save-baseline",
        ],
    )
    assert result.exit_code == 0, result.output
    return tmp_path / ".agents-shipgate" / "host-grants.json"


def _drift_json(tmp_path: Path, *extra: str) -> tuple[int, dict]:
    result = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--drift", "--json", *extra],
    )
    return result.exit_code, json.loads(result.output)


def test_inventory_v02_collects_typed_multi_host_grants(tmp_path: Path) -> None:
    inventory = host_audit_inventory(_seed_workspace(tmp_path))
    assert inventory["host_grants_inventory_schema_version"] == "0.2"
    HostGrantsInventoryV2.model_validate(inventory)
    assert inventory["scope"] == "repository"
    assert inventory["static_analysis_only"] is True
    assert inventory["runtime_session_verified"] is False
    assert {item["host"] for item in inventory["host_coverage"]} == {
        "codex",
        "claude-code",
        "cursor",
        "vscode",
        "github",
    }
    assert all(item["status"] == "complete" for item in inventory["host_coverage"])
    assert {grant["kind"] for grant in inventory["grants"]} >= {
        "mcp_server",
        "permission_rule",
        "permission_mode",
        "sandbox",
        "additional_path",
        "plugin_or_app",
        "hook",
        "workflow",
    }
    assert {grant["host"] for grant in _grants(inventory, "mcp_server")} >= {
        "codex",
        "claude-code",
        "cursor",
    }


def test_inventory_redacts_env_headers_urls_and_userinfo(tmp_path: Path) -> None:
    inventory = host_audit_inventory(_seed_workspace(tmp_path))
    rendered = json.dumps(inventory, sort_keys=True)
    for secret in (
        "secret-token-value",
        "Bearer secret",
        "user:pass",
        "api_key=secret",
        "INLINE-TOP-SECRET",
        "WEBHOOK-PATH-TOP-SECRET",
    ):
        assert secret not in rendered
        assert secret not in render_host_audit_markdown(inventory)
    remote = next(grant for grant in _grants(inventory, "mcp_server") if grant["server"] == "remote")
    assert remote["header_keys"] == ["Authorization"]
    assert remote["endpoint"] == "https://mcp.example.test/<redacted-path>"
    assert len(remote["config_sha256"]) == 64


def test_setting_grants_redact_values_in_inventory_baseline_and_drift(
    tmp_path: Path,
) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    settings = claude / "settings.json"
    secrets = (
        "hunter2secret",
        "AKIARAWSECRETVALUE",
        "tokenXYZ123",
    )
    settings.write_text(
        json.dumps(
            {
                "permissions": {
                    "defaultMode": "http://user:tokenXYZ123@evil.example/path"
                },
                "sandbox": {
                    "httpProxy": "http://svc:hunter2secret@proxy.corp:3128",
                    "extraEnv": {
                        "AWS_SECRET_ACCESS_KEY": "AKIARAWSECRETVALUE"
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    settings.write_text(
        json.dumps(
            {
                "permissions": {
                    "defaultMode": "http://user:rotated@evil.example/path",
                    "skipDangerousModePermissionPrompt": True,
                },
                "sandbox": {
                    "httpProxy": "http://svc:rotated@proxy.corp:3128",
                    "extraEnv": {"AWS_SECRET_ACCESS_KEY": "ROTATEDSECRET"},
                },
            }
        ),
        encoding="utf-8",
    )
    drift = build_host_drift_payload(
        baseline=baseline,
        inventory=host_audit_inventory(tmp_path),
        baseline_file=".agents-shipgate/host-grants.json",
    )

    rendered = json.dumps(
        {"inventory": inventory, "baseline": baseline, "drift": drift},
        sort_keys=True,
    )
    for secret in (*secrets, "rotated", "ROTATEDSECRET", "user:", "svc:"):
        assert secret not in rendered
    setting_values = {
        grant["setting"]: grant["value"]
        for grant in inventory["grants"]
        if grant["kind"] in {"permission_mode", "sandbox"}
    }
    assert setting_values["sandbox.extraEnv"] == (
        '{"AWS_SECRET_ACCESS_KEY":"<redacted>"}'
    )
    assert setting_values["sandbox.httpProxy"] == "http://proxy.corp:3128"
    assert setting_values["defaultMode"] == "http://evil.example/<redacted-path>"


def test_local_static_reads_only_current_claude_project_and_user_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".cursor").mkdir()
    (home / ".codex").mkdir()
    (home / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8"
    )
    (home / ".cursor/mcp.json").write_text(
        json.dumps({"mcpServers": {"user": {"command": "user-mcp"}}}), encoding="utf-8"
    )
    (home / ".codex/config.toml").write_text('approval_policy = "on-request"\n', encoding="utf-8")
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "unrelated_secret": "must-not-leak",
                "projects": {
                    str(workspace.resolve()): {
                        "mcpServers": {"local": {"url": "https://local.example/mcp"}}
                    },
                    "/other/project": {"mcpServers": {"hidden": {"command": "hidden-secret"}}},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    inventory = host_audit_inventory(workspace, scope="local_static")
    assert inventory["scope"] == "local_static"
    rendered = json.dumps(inventory)
    assert "local" in rendered and "user" in rendered
    assert "must-not-leak" not in rendered
    assert "hidden-secret" not in rendered
    assert "~/.claude.json#current-workspace" in rendered

    first_artifact = next(
        item
        for item in inventory["artifacts"]
        if item["path"] == "~/.claude.json#current-workspace"
    )
    claude_state = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    claude_state["unrelated_secret"] = "changed-but-still-unrelated"
    claude_state["projects"]["/other/project"]["mcpServers"]["hidden"]["command"] = (
        "another-hidden-secret"
    )
    (home / ".claude.json").write_text(json.dumps(claude_state), encoding="utf-8")
    second = host_audit_inventory(workspace, scope="local_static")
    second_artifact = next(
        item
        for item in second["artifacts"]
        if item["path"] == "~/.claude.json#current-workspace"
    )
    assert first_artifact["redacted_sha256"] == second_artifact["redacted_sha256"]
    assert inventory["grants"] == second["grants"]


def test_policy_helper_is_never_run_and_makes_coverage_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    marker = tmp_path / "must-not-exist"
    (home / ".claude/settings.json").write_text(
        json.dumps({"policyHelper": f"touch {marker}"}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    inventory = host_audit_inventory(workspace, scope="local_static")
    assert not marker.exists()
    issue = next(item for item in inventory["issues"] if item["source"] == "~/.claude/settings.json")
    assert issue["kind"] == "unsupported"
    assert issue["blocking"] is True
    coverage = next(item for item in inventory["host_coverage"] if item["host"] == "claude-code")
    assert coverage["status"] == "partial"
    assert f"touch {marker}" not in json.dumps(inventory)


def test_local_static_overlapping_layers_are_partial_until_precedence_is_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Bash(*)"]}}), encoding="utf-8"
    )
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))

    inventory = host_audit_inventory(workspace, scope="local_static")

    issue = next(
        item for item in inventory["issues"] if item["kind"] == "unresolved_precedence"
    )
    assert issue["host"] == "claude-code"
    coverage = next(
        item for item in inventory["host_coverage"] if item["host"] == "claude-code"
    )
    assert coverage["status"] == "partial"
    with pytest.raises(ValueError, match="cannot acknowledge missing evidence"):
        build_host_grants_baseline(inventory)


def test_invalid_config_is_structured_partial_coverage_and_cannot_baseline(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{not-json", encoding="utf-8")
    inventory = host_audit_inventory(tmp_path)
    [issue] = inventory["issues"]
    assert issue["kind"] == "parse_failed"
    assert issue["blocking"] is True
    claude = next(item for item in inventory["host_coverage"] if item["host"] == "claude-code")
    assert claude["status"] == "partial"
    result = runner.invoke(
        app, ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline"]
    )
    assert result.exit_code == 2
    assert "cannot acknowledge missing evidence" in result.output


def test_incomplete_inventory_save_routes_to_coverage_review_without_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    (tmp_path / ".mcp.json").write_text("{not-json", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--save-baseline",
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result.output)
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert "claude-code=partial" in action["why"]
    assert "claude-code:.mcp.json" in action["why"]
    assert "--save-baseline" not in result.output
    assert not (tmp_path / ".agents-shipgate/host-grants.json").exists()


def test_repository_intermediate_symlink_is_rejected_without_secret_leak(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    secret = "OUTSIDE-PERMISSION-TOP-SECRET"
    (outside / "settings.json").write_text(
        json.dumps({"permissions": {"allow": [f"Bash(echo {secret})"]}}),
        encoding="utf-8",
    )
    (tmp_path / ".claude").symlink_to(outside, target_is_directory=True)
    inventory = host_audit_inventory(tmp_path)
    assert secret not in json.dumps(inventory)
    issue = next(item for item in inventory["issues"] if item["source"] == ".claude/settings.json")
    assert issue["kind"] == "unreadable"
    assert "symbolic links" in issue["message"]
    coverage = next(item for item in inventory["host_coverage"] if item["host"] == "claude-code")
    assert coverage["status"] == "partial"


def test_repository_globstar_boundary_fails_closed_on_symlink_ancestor(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-globstar"
    outside.mkdir()
    secret = "OUTSIDE-INSTRUCTION-TOP-SECRET"
    (outside / "CLAUDE.md").write_text(secret, encoding="utf-8")
    (tmp_path / "vendor").symlink_to(outside, target_is_directory=True)

    inventory = host_audit_inventory(tmp_path)

    assert secret not in json.dumps(inventory)
    issues = [
        item
        for item in inventory["issues"]
        if item["source"] == "vendor" and item["host"] == "claude-code"
    ]
    assert issues
    assert all(item["kind"] == "unreadable" for item in issues)
    assert all(item["blocking"] is True for item in issues)
    coverage = next(
        item
        for item in inventory["host_coverage"]
        if item["host"] == "claude-code"
    )
    assert coverage["status"] == "partial"


def test_vscode_present_is_experimental_and_cannot_baseline(tmp_path: Path) -> None:
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "mcp.json").write_text(
        json.dumps({"servers": {"docs": {"command": "docs"}}}), encoding="utf-8"
    )
    inventory = host_audit_inventory(tmp_path)
    coverage = next(item for item in inventory["host_coverage"] if item["host"] == "vscode")
    assert coverage["status"] == "experimental"
    result = runner.invoke(
        app, ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline"]
    )
    assert result.exit_code == 2


def test_cli_scope_and_json_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_workspace(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    out = tmp_path / "reports/host-grants.json"
    result = runner.invoke(
        app,
        [
            "audit", "--host", "--workspace", str(tmp_path), "--scope", "local-static",
            "--json", "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(result.output)["scope"] == "local_static"
    invalid = runner.invoke(app, ["audit", "--host", "--scope", "runtime"])
    assert invalid.exit_code == 2


def test_cli_without_host_flag_errors(tmp_path: Path) -> None:
    assert runner.invoke(app, ["audit", "--workspace", str(tmp_path)]).exit_code == 2


def test_empty_repository_scope_is_complete_and_explicitly_excludes_runtime(tmp_path: Path) -> None:
    inventory = host_audit_inventory(tmp_path)
    assert inventory["grants"] == []
    assert all(item["status"] == "complete" for item in inventory["host_coverage"])
    assert any("runtime" in item for item in inventory["excluded_scopes"])
    assert "No statically declared grants" in render_host_audit_markdown(inventory)


def test_inventory_is_byte_deterministic(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    one = json.dumps(host_audit_inventory(tmp_path), sort_keys=True)
    two = json.dumps(host_audit_inventory(tmp_path), sort_keys=True)
    assert one == two


def test_inventory_coverage_paths_are_owned_by_central_boundary_registry(tmp_path: Path) -> None:
    inventory = host_audit_inventory(tmp_path)
    registered = {
        path
        for adapter in BOUNDARY_ADAPTERS
        for path in (*adapter.exact_paths, *adapter.globs)
    }
    reported = {
        path
        for coverage in inventory["host_coverage"]
        for path in coverage["sources_expected"]
    }
    assert reported == registered


def test_shared_snapshot_reads_and_parses_each_file_once(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use least privilege.\n", encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text("version: 1\n", encoding="utf-8")
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{}", encoding="utf-8")
    cache = HostStaticParseCache()
    snapshot = build_host_boundary_snapshot(tmp_path, cache=cache)
    agents_path = str((tmp_path / "AGENTS.md").absolute())
    manifest_path = str((tmp_path / "shipgate.yaml").absolute())
    settings_path = str((claude / "settings.json").absolute())
    assert cache.read_counts[agents_path] == 1
    assert cache.read_counts[manifest_path] == 1
    assert cache.parse_counts[settings_path] == 1
    assert agents_path not in cache.parse_counts
    assert manifest_path not in cache.parse_counts
    before_reads = dict(cache.read_counts)
    before_parses = dict(cache.parse_counts)
    projected = host_audit_inventory(tmp_path, snapshot=snapshot)
    assert projected == snapshot.inventory
    assert cache.read_counts == before_reads
    assert cache.parse_counts == before_parses


def test_host_static_entry_budget_is_aggregate_across_inventory_and_reads(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("Use least privilege.\n", encoding="utf-8")
    cache = HostStaticParseCache(
        max_entries=1,
        max_total_bytes=1024 * 1024,
    )

    snapshot = build_host_boundary_snapshot(tmp_path, cache=cache)

    assert cache.resource_bound_error is not None
    assert any(
        issue["kind"] == "unreadable"
        and "aggregate" in issue["message"]
        and issue["blocking"]
        for issue in snapshot.inventory["issues"]
    )
    assert all(
        coverage["status"] != "complete"
        for coverage in snapshot.inventory["host_coverage"]
    )


def test_host_static_byte_budget_is_aggregate_across_unique_sources(
    tmp_path: Path,
) -> None:
    agents = tmp_path / "AGENTS.md"
    claude = tmp_path / "CLAUDE.md"
    agents.write_text("A" * 40, encoding="utf-8")
    claude.write_text("C" * 40, encoding="utf-8")
    cache = HostStaticParseCache(
        max_entries=10_000,
        max_total_bytes=40,
    )

    snapshot = build_host_boundary_snapshot(tmp_path, cache=cache)

    assert cache.resource_bound_error is not None
    assert any(
        issue["kind"] == "unreadable"
        and "aggregate" in issue["message"]
        and issue["blocking"]
        for issue in snapshot.inventory["issues"]
    )
    assert all(
        coverage["status"] != "complete"
        for coverage in snapshot.inventory["host_coverage"]
    )


def test_host_static_reader_scans_each_source_directory_constant_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    for index in range(50):
        (rules / f"rule-{index:02d}.mdc").write_text(
            f"rule {index}\n",
            encoding="utf-8",
        )
    real_scandir = os.scandir
    calls: dict[str, int] = {}

    def counting_scandir(path: os.PathLike[str] | str | int):
        if not isinstance(path, int):
            key = os.path.abspath(os.fspath(path))
            calls[key] = calls.get(key, 0) + 1
        return real_scandir(path)

    monkeypatch.setattr(trust_roots_module.os, "scandir", counting_scandir)

    inventory = host_audit_inventory(tmp_path)

    assert not inventory["issues"]
    assert calls[os.path.abspath(os.fspath(rules))] == 2


def test_bounded_host_static_reader_keeps_hardlinks_fail_closed(
    tmp_path: Path,
) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    settings = claude / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    alias = tmp_path / "settings-alias.json"
    alias.hardlink_to(settings)

    inventory = host_audit_inventory(tmp_path)

    issue = next(
        item
        for item in inventory["issues"]
        if item["source"] == ".claude/settings.json"
    )
    assert issue["kind"] == "unreadable"
    assert "singly-linked" in issue["message"]
    assert issue["blocking"] is True
    assert all(
        grant["source"] != ".claude/settings.json"
        for grant in inventory["grants"]
    )


def test_host_inventory_rejects_protected_addition_after_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    added = docs / "AGENTS.md"
    real_finish = trust_roots_module.IdentityBoundReadSession.finish
    injected = False

    def finish_after_addition(
        reader: trust_roots_module.IdentityBoundReadSession,
    ) -> None:
        nonlocal injected
        if reader.root == tmp_path and not injected:
            injected = True
            added.write_text("late host instruction\n", encoding="utf-8")
        real_finish(reader)

    monkeypatch.setattr(
        trust_roots_module.IdentityBoundReadSession,
        "finish",
        finish_after_addition,
    )

    inventory = host_audit_inventory(tmp_path)

    assert inventory["artifacts"] == []
    assert inventory["grants"] == []
    assert any(
        issue["kind"] == "unreadable"
        and "changed identity" in issue["message"]
        and issue["blocking"]
        for issue in inventory["issues"]
    )
    assert all(
        coverage["status"] == "partial"
        for coverage in inventory["host_coverage"]
    )


def test_snapshot_projection_rejects_scope_or_workspace_mismatch(tmp_path: Path) -> None:
    snapshot = build_host_boundary_snapshot(tmp_path)
    with pytest.raises(ValueError, match="scope"):
        host_audit_inventory(tmp_path, scope="local_static", snapshot=snapshot)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="different workspace"):
        host_audit_inventory(other, snapshot=snapshot)


def test_v02_baseline_is_typed_portable_redacted_and_idempotent(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    baseline_path = _save_baseline(tmp_path)
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    HostGrantsBaselineV2.model_validate(payload)
    assert payload["host_grants_schema_version"] == "0.2"
    assert payload["scope"] == "repository"
    assert "workspace" not in payload["inventory"]
    assert payload["inventory"]["artifacts"]
    assert payload["inventory"]["host_coverage"]
    assert payload["inventory_sha256"] == host_grants_sha256(payload["inventory"])
    assert "secret-token-value" not in baseline_path.read_text(encoding="utf-8")
    first = baseline_path.read_bytes()
    second = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline", "--json"],
    )
    assert second.exit_code == 0
    assert json.loads(second.output)["status"] == "unchanged"
    assert baseline_path.read_bytes() == first


def test_valid_baseline_update_uses_atomic_replacement(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    baseline = _save_baseline(tmp_path)
    original_inode = baseline.stat().st_ino
    config = tmp_path / ".mcp.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["mcpServers"]["github"]["args"].append("--new-scope")
    config.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "updated"
    assert baseline.stat().st_ino != original_inode
    assert not list(baseline.parent.glob(f".{baseline.name}.*.tmp"))


def test_save_refuses_valid_symlink_and_preserves_external_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    external = _save_baseline(source)
    before = external.read_bytes()
    requested = tmp_path / "requested"
    baseline = requested / ".agents-shipgate" / "host-grants.json"
    baseline.parent.mkdir(parents=True)
    baseline.symlink_to(external)

    result = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(requested), "--save-baseline"],
    )

    assert result.exit_code == 2
    assert baseline.is_symlink()
    assert external.read_bytes() == before


def test_save_refuses_symlinked_parent_and_preserves_external_directory(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    requested = tmp_path / "requested"
    requested.mkdir()
    (requested / ".agents-shipgate").symlink_to(
        external,
        target_is_directory=True,
    )

    result = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(requested), "--save-baseline"],
    )

    assert result.exit_code == 2
    assert not (external / "host-grants.json").exists()


def test_save_refuses_hardlink_and_preserves_external_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    external = _save_baseline(source)
    before = external.read_bytes()
    requested = tmp_path / "requested"
    baseline = requested / ".agents-shipgate" / "host-grants.json"
    baseline.parent.mkdir(parents=True)
    baseline.hardlink_to(external)

    result = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(requested), "--save-baseline"],
    )

    assert result.exit_code == 2
    assert external.read_bytes() == before
    assert baseline.read_bytes() == before
    assert external.stat().st_nlink == 2


def test_baseline_read_rejects_a_symlink_swap_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    baseline = _save_baseline(source)
    external_root = tmp_path / "external"
    external_root.mkdir()
    external = _save_baseline(external_root)
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == baseline and not swapped:
            swapped = True
            baseline.unlink()
            baseline.symlink_to(external)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ValueError, match="readable host-grants baseline"):
        load_host_grants_baseline(baseline)
    assert swapped is True
    assert baseline.is_symlink()


def test_save_allows_explicit_absolute_regular_target_but_not_relative_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    absolute = tmp_path / "explicit-baseline.json"
    created = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(workspace),
            "--save-baseline",
            "--baseline-file",
            str(absolute),
        ],
    )
    assert created.exit_code == 0, created.output
    assert absolute.is_file()

    escaped = tmp_path / "escaped-baseline.json"
    refused = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(workspace),
            "--save-baseline",
            "--baseline-file",
            "../escaped-baseline.json",
        ],
    )
    assert refused.exit_code == 2
    assert not escaped.exists()


def test_atomic_save_fails_closed_if_target_appears(tmp_path: Path) -> None:
    target = tmp_path / "appeared.json"
    target.write_text("external evidence", encoding="utf-8")

    with pytest.raises(typer.Exit) as raised:
        _atomic_write_baseline(target, "{}\n", expected=None)

    assert raised.value.exit_code == 2
    assert target.read_text(encoding="utf-8") == "external evidence"


def test_save_reports_symlink_loop_without_a_traceback(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to("loop")

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--save-baseline",
            "--baseline-file",
            str(loop / "host-grants.json"),
        ],
    )

    assert result.exit_code == 2
    assert "Refusing to write host-grants baseline" in result.output
    assert "Traceback" not in result.output


def test_clean_and_changed_v02_drift(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _save_baseline(tmp_path)
    code, clean = _drift_json(tmp_path)
    assert code == 0
    HostGrantsDriftV2.model_validate(clean)
    assert clean["comparison_status"] == "comparable"
    assert clean["has_drift"] is False
    assert clean["baseline_sha256"] == clean["current_sha256"]

    mcp_path = tmp_path / ".mcp.json"
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    data["mcpServers"]["github"]["args"].append("--unrestricted")
    mcp_path.write_text(json.dumps(data), encoding="utf-8")
    code, changed = _drift_json(tmp_path)
    assert code == 0
    assert changed["has_drift"] is True
    assert changed["changes"]
    assert "mcp_server_changed: claude-code:github" in changed["expansion_signals"]


def test_zero_grant_recognized_artifact_is_bound_into_drift(tmp_path: Path) -> None:
    codex = tmp_path / ".codex"
    codex.mkdir()
    hooks = codex / "hooks.json"
    hooks.write_text("{}", encoding="utf-8")
    initial = host_audit_inventory(tmp_path)
    assert not [grant for grant in initial["grants"] if grant["source"] == ".codex/hooks.json"]
    _save_baseline(tmp_path)

    hooks.write_text(json.dumps({"metadata": "changed"}), encoding="utf-8")
    code, payload = _drift_json(tmp_path)
    assert code == 0
    assert payload["comparison_status"] == "comparable"
    assert payload["has_drift"] is True
    assert payload["changes"] == []
    assert payload["artifact_changes"]


def test_coverage_metadata_is_bound_into_baseline_and_drift(tmp_path: Path) -> None:
    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    changed = json.loads(json.dumps(inventory))
    codex = next(item for item in changed["host_coverage"] if item["host"] == "codex")
    codex["sources_expected"].append(".codex/future-static-surface.toml")
    payload = build_host_drift_payload(
        baseline=baseline,
        inventory=changed,
        baseline_file=".agents-shipgate/host-grants.json",
    )
    assert payload["comparison_status"] == "comparable"
    assert payload["has_drift"] is True
    assert payload["coverage_changes"] == [
        {
            "host": "codex",
            "baseline": next(
                item
                for item in baseline["inventory"]["host_coverage"]
                if item["host"] == "codex"
            ),
            "current": codex,
        }
    ]


def test_codex_requirements_emit_typed_grants_and_drift(tmp_path: Path) -> None:
    codex = tmp_path / ".codex"
    codex.mkdir()
    requirements = codex / "requirements.toml"
    requirements.write_text(
        'allowed_sandbox_modes = ["workspace-write"]\napi_key = "REQUIREMENT-TOP-SECRET"\n',
        encoding="utf-8",
    )
    inventory = host_audit_inventory(tmp_path)
    requirements_by_name = {
        grant["requirement"]: grant
        for grant in inventory["grants"]
        if grant["kind"] == "requirement"
    }
    requirement = requirements_by_name["allowed_sandbox_modes"]
    assert requirements_by_name["api_key"]["value"] == "<redacted>"
    assert "REQUIREMENT-TOP-SECRET" not in json.dumps(inventory)
    assert {
        grant["requirement"]
        for grant in inventory["grants"]
        if grant["kind"] == "requirement"
    } == {
        "allowed_sandbox_modes",
        "api_key",
    }
    matching = [
        grant for grant in inventory["grants"] if grant["kind"] == "requirement"
    ]
    assert len(matching) == 2
    assert requirement["requirement"] == "allowed_sandbox_modes"
    assert requirement["value"] == '["workspace-write"]'
    _save_baseline(tmp_path)

    requirements.write_text(
        'allowed_sandbox_modes = ["workspace-write"]\n'
        'api_key = "ROTATED-REQUIREMENT-TOP-SECRET"\n',
        encoding="utf-8",
    )
    rotated = _drift_json(tmp_path)[1]
    assert rotated["has_drift"] is False
    assert "ROTATED-REQUIREMENT-TOP-SECRET" not in json.dumps(rotated)

    requirements.write_text(
        'allowed_sandbox_modes = ["workspace-write", "danger-full-access"]\n'
        'api_key = "ROTATED-REQUIREMENT-TOP-SECRET"\n',
        encoding="utf-8",
    )
    payload = _drift_json(tmp_path)[1]
    assert payload["has_drift"] is True
    assert any(
        change["current"] and change["current"]["kind"] == "requirement"
        for change in payload["changes"]
    )


def test_codex_selected_profile_is_resolved_or_coverage_is_partial(tmp_path: Path) -> None:
    codex = tmp_path / ".codex"
    codex.mkdir()
    config = codex / "config.toml"
    config.write_text(
        'profile = "reviewed"\n[profiles.reviewed]\nsandbox_mode = "workspace-write"\n',
        encoding="utf-8",
    )
    inventory = host_audit_inventory(tmp_path)
    [profile] = [grant for grant in inventory["grants"] if grant["kind"] == "profile"]
    assert profile["profile"] == "reviewed"
    assert profile["resolved"] is True
    assert not inventory["issues"]

    config.write_text('profile = "missing"\n', encoding="utf-8")
    unresolved = host_audit_inventory(tmp_path)
    [profile] = [grant for grant in unresolved["grants"] if grant["kind"] == "profile"]
    assert profile["resolved"] is False
    assert any(
        issue["kind"] == "unsupported" and issue["host"] == "codex"
        for issue in unresolved["issues"]
    )
    codex_coverage = next(
        item for item in unresolved["host_coverage"] if item["host"] == "codex"
    )
    assert codex_coverage["status"] == "partial"


def test_drift_all_env_header_value_rotation_quiet_but_key_addition_fires(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _save_baseline(tmp_path)
    path = tmp_path / ".mcp.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mcpServers"]["github"]["env"]["GITHUB_TOKEN"] = "rotated"
    data["mcpServers"]["github"]["env"]["READ_ONLY"] = "false"
    data["mcpServers"]["remote"]["headers"]["Authorization"] = "Bearer rotated"
    data["mcpServers"]["remote"]["url"] = (
        "https://mcp.example.test/services/ROTATED-WEBHOOK-PATH-TOP-SECRET"
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    rotated = _drift_json(tmp_path)[1]
    assert rotated["has_drift"] is False
    assert "ROTATED-WEBHOOK-PATH-TOP-SECRET" not in json.dumps(rotated)

    data["mcpServers"]["github"]["env"]["NEW_GRANT_SHAPING_KEY"] = "any-value"
    data["mcpServers"]["remote"]["headers"]["X-Scope"] = "admin"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _drift_json(tmp_path)[1]["has_drift"] is True


def test_inline_permission_secret_rotation_is_redacted_and_hash_invariant(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _save_baseline(tmp_path)
    path = tmp_path / ".claude/settings.json"
    text = path.read_text(encoding="utf-8").replace(
        "INLINE-TOP-SECRET", "ROTATED-INLINE-TOP-SECRET"
    )
    path.write_text(text, encoding="utf-8")
    code, payload = _drift_json(tmp_path)
    assert code == 0
    assert payload["has_drift"] is False
    assert "ROTATED-INLINE-TOP-SECRET" not in json.dumps(payload)


def test_parse_error_never_echoes_secret_file_content(tmp_path: Path) -> None:
    secret = "PARSER-SENTINEL-TOP-SECRET"
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"x":{"env":{"TOKEN":"' + secret + '"}}}',
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["audit", "--host", "--workspace", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0
    assert secret not in result.output


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        (".codex/config.toml", 'approval_policy = "TOML-PARSER-TOP-SECRET\n'),
        (
            ".github/workflows/release.yml",
            "permissions: [YAML-PARSER-TOP-SECRET\n",
        ),
    ],
)
def test_toml_yaml_parser_errors_never_echo_source_lines(
    tmp_path: Path, relative: str, content: str
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    inventory = host_audit_inventory(tmp_path)
    rendered = json.dumps(inventory)
    assert "TOP-SECRET" not in rendered
    assert any(issue["kind"] == "parse_failed" for issue in inventory["issues"])


def test_deny_removal_and_hook_change_are_expansion_signals(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _save_baseline(tmp_path)
    path = tmp_path / ".claude/settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["permissions"]["deny"] = []
    data["hooks"]["PreToolUse"] = [{"hooks": [{"command": "echo changed"}]}]
    path.write_text(json.dumps(data), encoding="utf-8")
    drift = _drift_json(tmp_path)[1]
    assert "deny_rule_removed: claude-code:WebFetch" in drift["expansion_signals"]
    assert "hook_changed: claude-code:.claude/settings.json" in drift["expansion_signals"]


def test_legacy_v01_baseline_is_incomparable_advisory_and_strict_20(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    baseline = tmp_path / ".agents-shipgate/host-grants.json"
    baseline.parent.mkdir()
    legacy = {
        "host_grants_schema_version": "0.1",
        "inventory_sha256": "legacy",
        "inventory": {"mcp_servers": []},
    }
    baseline.write_text(
        json.dumps(legacy),
        encoding="utf-8",
    )
    shared = build_host_drift_payload(
        baseline=legacy,
        inventory=host_audit_inventory(tmp_path),
        baseline_file=".agents-shipgate/host-grants.json",
    )
    HostGrantsDriftV2.model_validate(shared)
    assert shared["comparison_status"] == "incomparable"
    assert shared["next_action"] is None
    assert "--save-baseline" not in json.dumps(shared)

    code, payload = _drift_json(tmp_path)
    assert code == 0
    HostGrantsDriftV2.model_validate(payload)
    assert payload["comparison_status"] == "incomparable"
    assert payload["has_drift"] is None
    assert "baseline_schema_v0.1" in payload["incomparable_reasons"][0]
    assert payload["next_action"] is None
    assert baseline.exists()

    before = baseline.read_bytes()
    replace = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline"],
    )
    assert replace.exit_code == 2
    assert "--save-baseline" not in replace.output
    assert baseline.read_bytes() == before

    strict = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--drift",
            "--json",
            "--fail-on-drift",
        ],
    )
    assert strict.exit_code == 20


def test_malformed_nested_v02_baseline_is_incomparable_not_a_crash(tmp_path: Path) -> None:
    baseline = tmp_path / ".agents-shipgate/host-grants.json"
    baseline.parent.mkdir()
    baseline.write_text(
        json.dumps(
            {
                "host_grants_schema_version": "0.2",
                "scope": "repository",
                "inventory_sha256": "not-trusted-before-validation",
                "inventory": {
                    "scope": "repository",
                    "artifacts": [],
                    "grants": ["malformed-grant"],
                    "host_coverage": [],
                },
            }
        ),
        encoding="utf-8",
    )
    code, payload = _drift_json(tmp_path)
    assert code == 0
    HostGrantsDriftV2.model_validate(payload)
    assert payload["comparison_status"] == "incomparable"
    assert payload["has_drift"] is None
    assert payload["incomparable_reasons"] == ["malformed_v0.2_baseline"]
    assert payload["next_action"] is None
    assert json.loads(baseline.read_text(encoding="utf-8"))["inventory"]["grants"] == [
        "malformed-grant"
    ]

    markdown = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(tmp_path),
            "--drift",
        ],
    )
    assert markdown.exit_code == 0, markdown.output
    assert "--save-baseline" not in markdown.output
    assert "Review the existing baseline" in markdown.output
    assert "Next: None" not in markdown.output

    before = baseline.read_bytes()
    replace = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline"],
    )
    assert replace.exit_code == 2
    assert "--save-baseline" not in replace.output
    assert baseline.read_bytes() == before

    strict = runner.invoke(
        app,
        [
            "audit", "--host", "--workspace", str(tmp_path), "--drift", "--json",
            "--fail-on-drift",
        ],
    )
    assert strict.exit_code == 20


def test_scope_mismatch_is_incomparable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_workspace(tmp_path)
    _save_baseline(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    code, payload = _drift_json(tmp_path, "--scope", "local-static")
    assert code == 0
    assert payload["comparison_status"] == "incomparable"
    assert "scope_mismatch:repository->local_static" in payload["incomparable_reasons"]
    assert payload["next_action"] is None


def test_fail_on_drift_exits_20_and_advisory_stays_zero(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    _save_baseline(tmp_path)
    path = tmp_path / ".cursor/mcp.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mcpServers"]["extra"] = {"command": "extra"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _drift_json(tmp_path)[0] == 0
    strict = runner.invoke(
        app,
        [
            "audit", "--host", "--workspace", str(tmp_path), "--drift", "--json",
            "--fail-on-drift",
        ],
    )
    assert strict.exit_code == 20


def test_missing_corrupt_unknown_and_tampered_baselines_fail_closed(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    missing = runner.invoke(app, ["audit", "--host", "--workspace", str(tmp_path), "--drift"])
    assert missing.exit_code == 2
    baseline = tmp_path / ".agents-shipgate/host-grants.json"
    baseline.parent.mkdir(exist_ok=True)
    for content in (
        "{not json",
        json.dumps({"host_grants_schema_version": "99.0"}),
    ):
        baseline.write_text(content, encoding="utf-8")
        result = runner.invoke(app, ["audit", "--host", "--workspace", str(tmp_path), "--drift"])
        assert result.exit_code == 2
    baseline.unlink()
    baseline = _save_baseline(tmp_path)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["inventory_sha256"] = "0" * 64
    baseline.write_text(json.dumps(payload), encoding="utf-8")
    tampered = runner.invoke(app, ["audit", "--host", "--workspace", str(tmp_path), "--drift"])
    assert tampered.exit_code == 2
    assert "integrity" in tampered.output


def test_invalid_flag_combinations_are_usage_errors(tmp_path: Path) -> None:
    both = runner.invoke(
        app,
        ["audit", "--host", "--workspace", str(tmp_path), "--save-baseline", "--drift"],
    )
    assert both.exit_code == 2
    no_drift = runner.invoke(
        app, ["audit", "--host", "--workspace", str(tmp_path), "--fail-on-drift"]
    )
    assert no_drift.exit_code == 2


def test_missing_host_recovery_preserves_the_complete_quoted_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    baseline = workspace / "reviewed grants; baseline.json"
    out = workspace / "reports with spaces" / "saved.json"

    result = runner.invoke(
        app,
        [
            "audit",
            "--workspace",
            str(workspace),
            "--scope",
            "local-static",
            "--save-baseline",
            "--baseline-file",
            str(baseline),
            "--json",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 2
    action = _agent_mode_error(result.output)["next_actions"][0]
    assert action["kind"] == "command"
    assert shlex.split(action["command"]) == [
        "agents-shipgate",
        "audit",
        "--host",
        "--workspace",
        str(workspace),
        "--scope",
        "local-static",
        "--save-baseline",
        "--baseline-file",
        str(baseline),
        "--json",
        "--out",
        str(out),
    ]


def test_invalid_scope_with_full_custom_request_requires_a_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "custom workspace"
    baseline = workspace / "custom baseline.json"
    out = workspace / "custom output.json"

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(workspace),
            "--scope",
            "repo-or-local",
            "--save-baseline",
            "--baseline-file",
            str(baseline),
            "--json",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 2
    payload = _agent_mode_error(result.output)
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert "agents-shipgate audit --host" not in payload["next_action"]


def test_save_and_drift_conflict_never_authorizes_a_different_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "custom workspace"
    baseline = workspace / "custom baseline.json"
    out = workspace / "custom output.json"

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(workspace),
            "--scope",
            "local-static",
            "--save-baseline",
            "--drift",
            "--baseline-file",
            str(baseline),
            "--fail-on-drift",
            "--json",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 2
    action = _agent_mode_error(result.output)["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None


def test_missing_baseline_with_output_still_requires_human_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "custom workspace"
    workspace.mkdir()
    baseline = workspace / "missing baseline.json"
    out = workspace / "custom output.json"

    result = runner.invoke(
        app,
        [
            "audit",
            "--host",
            "--workspace",
            str(workspace),
            "--scope",
            "local-static",
            "--drift",
            "--baseline-file",
            str(baseline),
            "--fail-on-drift",
            "--json",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 2
    action = _agent_mode_error(result.output)["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert str(workspace) in action["why"]
    assert str(baseline) in action["why"]
    assert "local-static" in action["why"]
    assert "--save-baseline" not in result.output
    assert not out.exists()


def test_baseline_lstat_failure_is_an_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    baseline = tmp_path / "baseline.json"
    original_lstat = Path.lstat

    def denied_lstat(path: Path) -> object:
        if path == baseline:
            raise PermissionError("inspection denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)

    with pytest.raises(typer.Exit) as raised:
        _refuse_invalid_baseline_overwrite(baseline)

    assert raised.value.exit_code == 4
    payload = _agent_mode_error(capsys.readouterr().err)
    assert payload["error"] == "other_error"
    assert payload["exit_code"] == 4


def test_baseline_read_failure_is_an_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")

    def denied_read(_path: Path) -> tuple[dict, str]:
        try:
            raise PermissionError("read denied")
        except PermissionError as cause:
            raise ValueError("could not read baseline") from cause

    monkeypatch.setattr(
        host_audit_cli,
        "load_host_grants_baseline_with_text",
        denied_read,
    )

    with pytest.raises(typer.Exit) as raised:
        _refuse_invalid_baseline_overwrite(baseline)

    assert raised.value.exit_code == 4
    payload = _agent_mode_error(capsys.readouterr().err)
    assert payload["error"] == "other_error"
    assert payload["exit_code"] == 4


def test_baseline_overwrite_reuses_descriptor_bound_text_without_path_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text
    descriptor_text = '{"host_grants_schema_version":"0.2"}\n'

    monkeypatch.setattr(
        host_audit_cli,
        "load_host_grants_baseline_with_text",
        lambda _path: (
            {"host_grants_schema_version": HOST_GRANTS_SCHEMA_VERSION},
            descriptor_text,
        ),
    )

    def denied_read_text(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> str:
        if path == baseline:
            raise PermissionError("post-validation read denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied_read_text)

    state = _refuse_invalid_baseline_overwrite(baseline)

    assert state is not None
    assert state[1] == descriptor_text


def test_generated_models_reject_unknown_fields_and_invalid_literals(tmp_path: Path) -> None:
    payload = host_audit_inventory(tmp_path)
    with pytest.raises(ValidationError):
        HostGrantsInventoryV2.model_validate({**payload, "legacy_parse_warnings": []})
    with pytest.raises(ValidationError):
        HostGrantsInventoryV2.model_validate({**payload, "scope": "runtime"})


def test_inventory_schema_uses_discriminated_typed_grants() -> None:
    rendered = json.dumps(HostGrantsInventoryArtifactV2.model_json_schema())
    assert '"discriminator"' in rendered
    assert '"propertyName": "kind"' in rendered
    assert '"oneOf"' in rendered
