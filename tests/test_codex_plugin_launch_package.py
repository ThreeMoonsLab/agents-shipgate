from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

from agents_shipgate.cli.discovery.agent_instructions.renderers import render_codex_skill_files
from agents_shipgate.cli.scan import run_scan

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "agents-shipgate"
MARKETPLACE_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"


def test_agents_shipgate_codex_plugin_manifest_is_skill_only() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "agents-shipgate"
    assert manifest["version"] == "0.13.0"
    assert manifest["skills"] == "./skills/"
    assert "apps" not in manifest
    assert "mcpServers" not in manifest
    assert "hooks" not in manifest

    interface = manifest["interface"]
    assert interface["displayName"] == "Agents Shipgate"
    assert interface["defaultPrompt"].startswith("Use $agents-shipgate")
    assert "scanner runs through the agents-shipgate CLI" in interface["longDescription"]
    assert interface["privacyPolicyURL"].endswith("/docs/privacy.md")
    assert interface["termsOfServiceURL"].endswith("/docs/terms.md")


def test_agents_shipgate_codex_plugin_marketplace_entry_is_installable() -> None:
    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))

    assert marketplace["name"] == "agents-shipgate"
    assert marketplace["interface"]["displayName"] == "Agents Shipgate"
    [entry] = marketplace["plugins"]
    assert entry == {
        "name": "agents-shipgate",
        "source": {
            "source": "local",
            "path": "./plugins/agents-shipgate",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Developer Tools",
    }


def test_agents_shipgate_codex_plugin_skill_matches_canonical_renderer() -> None:
    rendered = render_codex_skill_files()

    for repo_rel, expected in rendered.items():
        plugin_rel = repo_rel.replace(
            ".agents/skills/agents-shipgate/",
            "plugins/agents-shipgate/skills/agents-shipgate/",
            1,
        )
        assert (REPO_ROOT / plugin_rel).read_text(encoding="utf-8") == expected


def test_agents_shipgate_codex_plugin_scans_without_plugin_findings(
    tmp_path: Path,
) -> None:
    shutil.copytree(PLUGIN_ROOT, tmp_path / "plugins" / "agents-shipgate")
    (tmp_path / ".agents" / "plugins").mkdir(parents=True)
    shutil.copyfile(MARKETPLACE_PATH, tmp_path / ".agents" / "plugins" / "marketplace.json")
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: agents-shipgate-codex-plugin
            agent:
              name: agents-shipgate-codex-plugin
              declared_purpose:
                - let Codex run Agents Shipgate Tool-Use Readiness workflows
            environment:
              target: local
            tool_sources:
              - id: agents_shipgate_codex_plugin
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

    report, exit_code = run_scan(config_path=manifest)

    assert exit_code == 0
    assert report.release_decision.decision == "passed"
    assert report.codex_plugin_surface is not None
    assert report.codex_plugin_surface.plugin_count == 1
    assert report.codex_plugin_surface.marketplace_count == 1
    assert report.codex_plugin_surface.skill_count == 1
    assert report.codex_plugin_surface.app_count == 0
    assert report.codex_plugin_surface.mcp_server_stub_count == 0
    assert report.codex_plugin_surface.hook_stub_count == 0
    assert report.tool_inventory == []
    assert {
        finding.check_id
        for finding in report.findings
        if finding.check_id.startswith("SHIP-CODEX-PLUGIN-")
    } == set()
