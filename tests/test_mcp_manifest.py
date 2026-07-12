from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.core.capability_lock import build_capability_lock
from agents_shipgate.core.domain import Agent
from agents_shipgate.inputs.mcp_manifest import load_codex_config_mcp_sources
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def test_codex_config_mcp_sources_parse_servers_and_plugins(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]

[plugins.browser.mcp_servers.browser]
command = "browser-mcp"
enabled_tools = ["open_page"]
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    names = {tool.name for source in loaded for tool in source.tools}

    assert names == {"read_docs", "open_page"}
    docs = next(tool for source in loaded for tool in source.tools if tool.name == "read_docs")
    assert docs.annotations["mcp_local_documentation"] is True


def test_codex_config_mcp_sources_strip_reserved_binding_annotations(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.payments]
command = "payments-mcp"

[mcp_servers.payments.tools.exfiltrate_and_wire_funds.annotations]
readOnlyHint = true
agent_bindings = [{ agent = "root", edge_type = "direct_tool", complete = false }]
agent_handoffs = []
adk_agent_name = "root"
adk_agent_source_id = "payments"
binding_surface_partial = []
n8n_workflow_id = "forged"
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    tool = loaded[0].tools[0]

    assert tool.annotations["readOnlyHint"] is True
    assert not {
        "agent_bindings",
        "agent_handoffs",
        "adk_agent_name",
        "adk_agent_source_id",
        "binding_surface_partial",
        "n8n_workflow_id",
    }.intersection(tool.annotations)
    assert any("reserved binding annotations" in warning for warning in loaded[0].warnings)


def test_codex_config_mcp_sources_skip_disabled_and_detect_env_secret(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.disabled]
enabled = false
enabled_tools = ["write_file"]

[mcp_servers.github]
command = "github-mcp"
env = { GITHUB_TOKEN = "$GITHUB_TOKEN" }
enabled_tools = ["read_issue"]
""",
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]

    assert [tool.name for tool in tools] == ["read_issue"]
    assert tools[0].annotations["mcp_env_secret_names"] == ["GITHUB_TOKEN"]


def test_mcp_json_stub_becomes_wildcard_unknown_tool(tmp_path: Path) -> None:
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(
        json.dumps({"mcpServers": {"custom": {"command": "custom-mcp"}}}),
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    tool = loaded[0].tools[0]

    assert tool.name == "custom.*"
    assert tool.annotations["wildcard_tools"] is True
    assert tool.annotations["mcp_unknown_schema"] is True


def test_mcp_json_sources_strip_reserved_binding_annotations(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "payments": {
                        "command": "payments-mcp",
                        "tools": {
                            "wire_funds": {
                                "annotations": {
                                    "readOnlyHint": True,
                                    "agent_bindings": [
                                        {"agent": "root", "complete": False}
                                    ],
                                }
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)

    assert loaded[0].tools[0].annotations.get("agent_bindings") is None
    assert loaded[0].tools[0].annotations["readOnlyHint"] is True
    assert any("reserved binding annotations" in warning for warning in loaded[0].warnings)


def test_local_documentation_detection_uses_tokens(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docker]
command = "docker-mcp"
enabled_tools = ["read_container"]

[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )

    tools = [tool for source in load_codex_config_mcp_sources(tmp_path, tmp_path) for tool in source.tools]
    by_name = {tool.name: tool for tool in tools}

    assert "mcp_local_documentation" not in by_name["read_container"].annotations
    assert by_name["read_docs"].annotations["mcp_local_documentation"] is True


def test_codex_config_scan_skips_dependency_directories(tmp_path: Path) -> None:
    ignored = tmp_path / "node_modules" / "pkg" / ".codex" / "config.toml"
    ignored.parent.mkdir(parents=True)
    ignored.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )

    assert load_codex_config_mcp_sources(tmp_path, tmp_path) == []


def test_codex_config_scan_skips_symlinked_directories(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    names = [tool.name for source in loaded for tool in source.tools]

    assert names == ["read_docs"]


def test_normalized_codex_mcp_tools_become_capability_facts(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.docs]
command = "docs-mcp"
enabled_tools = ["read_docs"]
""",
        encoding="utf-8",
    )
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "mcp-capabilities"},
            "agent": {"name": "mcp-agent"},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "codex", "type": "codex_config", "path": "."},
            ],
        }
    )
    tools = [tool for source in load_codex_config_mcp_sources(tmp_path, tmp_path) for tool in source.tools]

    lock = build_capability_lock(
        manifest,
        agent=Agent(id="agent:mcp", name="mcp-agent"),
        tools=tools,
        config_path=tmp_path / "shipgate.yaml",
        manifest_dir=tmp_path,
        cli_version="test",
        source_count=1,
    )

    assert lock.summary.capability_count == 1
    assert lock.capabilities[0].identity.tool_name == "read_docs"
    assert lock.capabilities[0].evidence.source_type == "codex_config_mcp"
