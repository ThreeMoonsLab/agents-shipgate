from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from agents_shipgate.cli.diagnostics import (
    DIAG_CODEX_PLUGIN_PACKAGE_DETECTED,
    diagnose_detect,
)
from agents_shipgate.cli.discovery.signals import detect_workspace
from agents_shipgate.cli.discovery.template import render_auto_manifest
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import ConfigError


def test_codex_plugin_package_scan_keeps_non_tools_out_of_inventory(
    tmp_path: Path,
) -> None:
    _write_codex_plugin(tmp_path / "plugins" / "browserish", include_app=True)
    manifest = tmp_path / "shipgate.yaml"
    marker = tmp_path / "should-never-run"
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: codex-plugin-test
            agent:
              name: codex-plugin-review
            environment:
              target: local
            tool_sources:
              - id: browserish
                type: codex_plugin
                mode: package
                path: plugins/browserish
            output:
              packet:
                enabled: false
            """
        ),
        encoding="utf-8",
    )

    report, exit_code = run_scan(config_path=manifest)

    assert exit_code == 0
    assert marker.exists() is False
    assert report.codex_plugin_surface is not None
    assert report.codex_plugin_surface.plugin_count == 1
    assert report.codex_plugin_surface.skill_count == 1
    assert report.codex_plugin_surface.app_count == 1
    assert report.codex_plugin_surface.mcp_server_stub_count == 1
    assert report.codex_plugin_surface.hook_stub_count == 1
    assert report.tool_inventory == []
    check_ids = {finding.check_id for finding in report.findings}
    assert "SHIP-INVENTORY-NOT-ENUMERABLE" not in check_ids
    assert "SHIP-CODEX-PLUGIN-MCP-SERVER-NOT-ENUMERABLE" in check_ids
    assert "SHIP-CODEX-PLUGIN-APP-SURFACE-NOT-ENUMERABLE" in check_ids


def test_codex_plugin_mcp_inventory_enumerates_tools(tmp_path: Path) -> None:
    _write_codex_plugin(tmp_path / "plugins" / "browserish", include_app=False)
    inventory = tmp_path / "inventories" / "browser-tools.json"
    inventory.parent.mkdir()
    inventory.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "open_page",
                        "description": "Open a local browser page for inspection.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: codex-plugin-test
            agent:
              name: codex-plugin-review
              declared_purpose:
                - review a plugin package
            environment:
              target: local
            tool_sources:
              - id: browserish
                type: codex_plugin
                mode: package
                path: plugins/browserish
            codex_plugins:
              mcp_tool_inventories:
                - plugin: browserish
                  server: browser
                  path: inventories/browser-tools.json
            output:
              packet:
                enabled: false
            """
        ),
        encoding="utf-8",
    )

    report, _ = run_scan(config_path=manifest)

    assert report.codex_plugin_surface is not None
    assert report.codex_plugin_surface.mcp_inventory_file_count == 1
    assert [tool["name"] for tool in report.tool_inventory] == ["open_page"]
    assert report.tool_inventory[0]["source_type"] == "codex_plugin_mcp_inventory"
    check_ids = {finding.check_id for finding in report.findings}
    assert "SHIP-CODEX-PLUGIN-MCP-SERVER-NOT-ENUMERABLE" not in check_ids


def test_codex_plugin_marketplace_loads_local_plugin(tmp_path: Path) -> None:
    _write_codex_plugin(tmp_path / "plugins" / "browserish", include_app=False)
    _write_marketplace(
        tmp_path,
        plugin_entry={
            "name": "browserish",
            "category": "automation",
            "policy": {"installation": "local", "authentication": "none"},
            "source": {"source": "local", "path": "plugins/browserish"},
        },
    )
    manifest = _write_codex_marketplace_manifest(tmp_path)

    report, _ = run_scan(config_path=manifest)

    assert report.codex_plugin_surface is not None
    assert report.codex_plugin_surface.marketplace_count == 1
    assert report.codex_plugin_surface.marketplaces[0].plugin_count == 1
    assert report.codex_plugin_surface.plugins[0].marketplace == "local-market"
    assert report.codex_plugin_surface.plugins[0].name == "browserish"
    check_ids = {finding.check_id for finding in report.findings}
    assert "SHIP-CODEX-PLUGIN-MARKETPLACE-POLICY-MISSING" not in check_ids


def test_codex_plugin_marketplace_missing_policy_is_finding(tmp_path: Path) -> None:
    _write_codex_plugin(tmp_path / "plugins" / "browserish", include_app=False)
    _write_marketplace(
        tmp_path,
        plugin_entry={
            "name": "browserish",
            "source": {"source": "local", "path": "plugins/browserish"},
        },
    )
    manifest = _write_codex_marketplace_manifest(tmp_path)

    report, _ = run_scan(config_path=manifest)

    findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-CODEX-PLUGIN-MARKETPLACE-POLICY-MISSING"
    ]
    assert len(findings) == 1
    assert findings[0].evidence["plugin"] == "browserish"
    assert findings[0].evidence["missing"] == [
        "policy.installation",
        "policy.authentication",
        "category",
    ]


def test_codex_plugin_manifest_file_path_warning_flows_to_report(
    tmp_path: Path,
) -> None:
    _write_codex_plugin(tmp_path / "plugins" / "browserish", include_app=False)
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: codex-plugin-test
            agent:
              name: codex-plugin-review
              declared_purpose:
                - review a plugin package
            environment:
              target: local
            tool_sources:
              - id: browserish
                type: codex_plugin
                mode: package
                path: plugins/browserish/.codex-plugin/plugin.json
            output:
              packet:
                enabled: false
            """
        ),
        encoding="utf-8",
    )

    report, _ = run_scan(config_path=manifest)

    assert any(
        "prefer the plugin root directory" in warning
        for warning in report.source_warnings
    )


def test_detect_and_init_route_codex_plugin_only_workspace(tmp_path: Path) -> None:
    _write_codex_plugin(tmp_path, include_app=False)

    result = detect_workspace(tmp_path)
    assert result.is_agent_project is False
    assert result.codex_plugin_candidates[0].mode == "package"
    assert result.codex_plugin_candidates[0].path == "."
    diags = diagnose_detect(result, has_manifest=False, workspace=tmp_path)
    assert [diag.id for diag in diags] == [DIAG_CODEX_PLUGIN_PACKAGE_DETECTED]

    rendered = render_auto_manifest(tmp_path, result)
    assert "type: codex_plugin" in rendered
    assert "mode: package" in rendered
    assert "path: ." in rendered
    loaded = load_manifest(_write_manifest(tmp_path, rendered))
    assert loaded.tool_sources[0].type == "codex_plugin"


def test_codex_plugin_invalid_mode_is_manifest_error(tmp_path: Path) -> None:
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: invalid
            agent:
              name: invalid
            environment:
              target: local
            tool_sources:
              - id: plugin
                type: codex_plugin
                mode: runtime
                path: plugin
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="invalid codex_plugin mode"):
        load_manifest(manifest)


def _write_manifest(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "shipgate.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _write_codex_marketplace_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: codex-plugin-test
            agent:
              name: codex-plugin-review
              declared_purpose:
                - review a plugin marketplace
            environment:
              target: local
            tool_sources:
              - id: local_market
                type: codex_plugin
                mode: marketplace
                path: .agents/plugins/marketplace.json
            output:
              packet:
                enabled: false
            """
        ),
        encoding="utf-8",
    )
    return manifest


def _write_marketplace(tmp_path: Path, *, plugin_entry: dict[str, object]) -> None:
    marketplace = tmp_path / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps({"name": "local-market", "plugins": [plugin_entry]}),
        encoding="utf-8",
    )


def _write_codex_plugin(root: Path, *, include_app: bool) -> None:
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "skills" / "browser").mkdir(parents=True)
    plugin: dict[str, object] = {
        "name": root.name,
        "version": "1.0.0",
        "description": "Review browser automation from static Codex plugin files.",
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "hooks": "./hooks.json",
    }
    if include_app:
        plugin["apps"] = "./.app.json"
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(plugin),
        encoding="utf-8",
    )
    (root / "skills" / "browser" / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: browser
            description: Use browser automation for local UI inspection.
            ---

            # Browser
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "browser": {
                        "command": "python",
                        "args": ["-c", "raise SystemExit('must not execute')"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    if include_app:
        (root / ".app.json").write_text(
            json.dumps({"apps": {"browser": {"id": "connector_browser"}}}),
            encoding="utf-8",
        )
    (root / "hooks.json").write_text(
        json.dumps({"preRun": {"command": "touch should-never-run"}}),
        encoding="utf-8",
    )
