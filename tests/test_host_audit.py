from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.host_audit import (
    host_audit_inventory,
    render_host_audit_markdown,
)
from agents_shipgate.cli.main import app

runner = CliRunner()


def _seed_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {"GITHUB_TOKEN": "secret-token-value"},
                    },
                    "remote": {"url": "https://mcp.example.test/sse"},
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
                    "allow": ["Bash(npm test:*)", "Bash(*)"],
                    "deny": ["WebFetch"],
                },
                "hooks": {"PreToolUse": []},
            }
        ),
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


def test_inventory_collects_all_grant_kinds(tmp_path: Path) -> None:
    inventory = host_audit_inventory(_seed_workspace(tmp_path))

    servers = {item["server"]: item for item in inventory["mcp_servers"]}
    assert set(servers) == {"github", "remote"}
    assert servers["github"]["env_keys"] == ["GITHUB_TOKEN"]
    assert servers["remote"]["transport"] == "url"

    rules = {item["rule"]: item for item in inventory["permission_rules"]}
    assert rules["Bash(*)"]["wildcard"] is True
    assert rules["Bash(npm test:*)"]["wildcard"] is False
    assert rules["WebFetch"]["kind"] == "deny"

    assert inventory["hooks"] == [
        {"file": ".claude/settings.json", "event": "PreToolUse"}
    ]

    workflow = inventory["workflows"][0]
    assert workflow["pull_request_target"] is True
    assert workflow["write_all"] is True
    assert any("contents" in scope for scope in workflow["write_scopes"])


def test_inventory_never_includes_env_values(tmp_path: Path) -> None:
    inventory = host_audit_inventory(_seed_workspace(tmp_path))
    assert "secret-token-value" not in json.dumps(inventory)
    assert "secret-token-value" not in render_host_audit_markdown(inventory)


def test_markdown_flags_wildcard_and_risky_workflows(tmp_path: Path) -> None:
    markdown = render_host_audit_markdown(
        host_audit_inventory(_seed_workspace(tmp_path))
    )
    assert "# Host Capability Audit" in markdown
    assert "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW" in markdown
    assert "**pull_request_target**" in markdown
    assert "**write-all**" in markdown
    assert "verify --preview" in markdown


def test_cli_audit_host_json(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    result = runner.invoke(
        app, ["audit", "--host", "--workspace", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mcp_servers"]


def test_cli_audit_without_host_flag_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", "--workspace", str(tmp_path)])
    assert result.exit_code == 2


def test_audit_on_empty_workspace_is_clean(tmp_path: Path) -> None:
    inventory = host_audit_inventory(tmp_path)
    assert inventory["mcp_servers"] == []
    markdown = render_host_audit_markdown(inventory)
    assert "None declared." in markdown


def test_inventory_is_deterministic(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    one = json.dumps(host_audit_inventory(tmp_path), sort_keys=True)
    two = json.dumps(host_audit_inventory(tmp_path), sort_keys=True)
    assert one == two
