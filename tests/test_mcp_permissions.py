from __future__ import annotations

from pathlib import Path

from agents_shipgate.checks import mcp_permissions
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, Tool
from agents_shipgate.core.lenses.action_surface import build_action_surface_facts
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.surfaces import ActionSurfaceFacts


def _manifest() -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "mcp-permissions-test"},
            "agent": {"name": "mcp-agent"},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "codex", "type": "codex_config", "path": "."}],
        }
    )


def _read_only_docs_tool() -> Tool:
    return Tool(
        id="tool:read_docs",
        name="read_docs",
        source_type="codex_config_mcp",
        source_id="codex_config_mcp:docs",
        annotations={
            "mcp_server": True,
            "mcp_local_documentation": True,
            "readOnlyHint": True,
        },
        extraction_confidence="high",
    )


def _context(*, diff_reference: ToolSurfaceDiffReference | None) -> ScanContext:
    manifest = _manifest()
    tool = _read_only_docs_tool()
    action_facts = build_action_surface_facts(
        manifest,
        agent_id="agent:mcp",
        tools=[tool],
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:mcp", name="mcp-agent"),
        tools=[tool],
        config_path=Path("shipgate.yaml"),
        action_surface_facts=action_facts,
        diff_reference=diff_reference,
    )


def test_read_only_server_added_is_quiet_without_diff_reference() -> None:
    findings = mcp_permissions.run(_context(diff_reference=None))

    assert [finding.check_id for finding in findings] == []


def test_read_only_server_added_warns_against_empty_diff_reference() -> None:
    findings = mcp_permissions.run(
        _context(
            diff_reference=ToolSurfaceDiffReference(
                kind="report",
                facts=None,
                action_facts=ActionSurfaceFacts(),
            )
        )
    )

    assert [finding.check_id for finding in findings] == [
        "SHIP-MCP-READONLY-SERVER-ADDED"
    ]
