from __future__ import annotations

from pathlib import Path

from agents_shipgate.checks import mcp_permissions
from agents_shipgate.core import risk_hints
from agents_shipgate.core.adopter_text import internal_vocabulary
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, AuthInfo, Tool
from agents_shipgate.core.lenses.action_surface import build_action_surface_facts
from agents_shipgate.core.lenses.tool_surface import (
    ToolSurfaceDiffReference,
    build_tool_surface_facts,
)
from agents_shipgate.core.semantic_assessment import assess_tool_semantics
from agents_shipgate.schemas.manifest import ActionDeclarationConfig, AgentsShipgateManifest
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


def _context(
    *,
    diff_reference: ToolSurfaceDiffReference | None,
    tool: Tool | None = None,
    manifest: AgentsShipgateManifest | None = None,
) -> ScanContext:
    manifest = manifest or _manifest()
    tool = tool or _read_only_docs_tool()
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


def _mcp_tool(
    *,
    name: str = "delete_account",
    annotations: dict[str, object] | None = None,
    auth_scopes: list[str] | None = None,
) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        source_type="mcp",
        source_id="mcp-tools",
        source_ref="tools.json",
        source_path="tools.json",
        source_start_line=2,
        source_pointer="/tools/0",
        annotations=annotations or {},
        auth=AuthInfo(scopes=auth_scopes or []),
        extraction_confidence="high",
        extraction={"surface": "enumerated"},
    )


def _assessed(
    tool: Tool,
    manifest: AgentsShipgateManifest | None = None,
    declaration: ActionDeclarationConfig | None = None,
) -> Tool:
    [enriched] = risk_hints.enrich_tools_with_risk_hints(manifest or _manifest(), [tool])
    enriched.semantic_assessment = assess_tool_semantics(enriched, declaration)
    return enriched


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

    assert [finding.check_id for finding in findings] == ["SHIP-MCP-READONLY-SERVER-ADDED"]


def test_read_only_hint_contradicting_inferred_destructive_evidence_is_reported() -> None:
    tool = _assessed(_mcp_tool(annotations={"readOnlyHint": True}))

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    [finding] = [item for item in findings if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"]
    assert finding.severity == "high"
    assert finding.confidence == "medium"
    assert finding.provenance_kind == "keyword_heuristic"
    assert finding.source.path == "tools.json"
    assert finding.source.pointer == "/tools/0"
    assert finding.evidence["form"] == "static"
    assert finding.evidence["published_annotations"] == {"readOnlyHint": True}
    assert finding.evidence["independent_evidence"] == [
        {
            "effect": "destructive",
            "confidence": "medium",
            "basis": "inferred_keyword",
            "source": "risk_hint:keyword",
            "source_pointer": "/tools/0",
            "details": {"tag": "destructive", "hint_source": "keyword"},
        }
    ]
    assert "inferred keyword evidence" in finding.recommendation
    assert "MCP clients" in finding.recommendation
    assert internal_vocabulary(f"{finding.title} {finding.recommendation}") == ()


def test_missing_annotations_are_not_a_contradiction() -> None:
    tool = _assessed(_mcp_tool())

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {finding.check_id for finding in findings}


def test_explicit_destructive_false_contradicts_structural_delete_evidence() -> None:
    tool = _assessed(
        _mcp_tool(
            name="archive_account",
            annotations={"destructiveHint": False},
            auth_scopes=["accounts:delete"],
        )
    )

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    [finding] = [item for item in findings if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"]
    assert finding.confidence == "high"
    assert finding.provenance_kind == "static_declaration"
    assert finding.evidence["published_annotations"] == {"destructiveHint": False}
    assert {row["basis"] for row in finding.evidence["independent_evidence"]} == {
        "structural_scope",
    }


def test_absent_destructive_hint_is_not_a_contradiction() -> None:
    tool = _assessed(_mcp_tool(name="archive_account", auth_scopes=["accounts:delete"]))

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {finding.check_id for finding in findings}


def test_read_only_hint_with_read_only_evidence_is_not_a_contradiction() -> None:
    tool = _assessed(
        _mcp_tool(
            name="get_account",
            annotations={"readOnlyHint": True, "httpMethod": "GET"},
        )
    )

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {finding.check_id for finding in findings}


def test_internal_source_trust_does_not_silence_published_contradiction() -> None:
    manifest = _manifest()
    manifest.tool_sources[0].trust = "internal"
    tool = _assessed(_mcp_tool(annotations={"readOnlyHint": True}), manifest=manifest)

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool, manifest=manifest))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" in {finding.check_id for finding in findings}


def test_reviewed_override_silences_only_the_inferred_evidence_it_answers() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "delete_account",
            "effect": "read",
            "override": {
                "evidence": "The handler only searches archived account records.",
                "reason": "The historical name no longer describes a delete operation.",
            },
        }
    )
    tool = _assessed(
        _mcp_tool(annotations={"readOnlyHint": True}),
        declaration=declaration,
    )

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {
        finding.check_id for finding in findings
    }


def test_hint_only_delta_names_flip_and_unchanged_independent_evidence() -> None:
    manifest = _manifest()
    base_tool = _assessed(_mcp_tool(), manifest=manifest)
    base_facts = build_action_surface_facts(
        manifest,
        agent_id="agent:mcp",
        tools=[base_tool],
    )
    base_tool_facts = build_tool_surface_facts(
        manifest,
        [base_tool],
        [],
        None,
        None,
    )
    head_tool = _assessed(_mcp_tool(annotations={"readOnlyHint": True}), manifest=manifest)

    findings = mcp_permissions.run(
        _context(
            tool=head_tool,
            manifest=manifest,
            diff_reference=ToolSurfaceDiffReference(
                kind="report",
                facts=base_tool_facts,
                action_facts=base_facts,
            ),
        )
    )

    [finding] = [item for item in findings if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"]
    assert finding.evidence["form"] == "delta"
    assert finding.evidence["annotation_changes"] == [
        {
            "annotation": "readOnlyHint",
            "before": "absent",
            "after": True,
        }
    ]
    assert finding.evidence["independent_evidence_unchanged"] is True


def test_destructive_hint_only_delta_names_absent_to_false_flip() -> None:
    manifest = _manifest()
    base_tool = _assessed(
        _mcp_tool(name="archive_account", auth_scopes=["accounts:delete"]),
        manifest=manifest,
    )
    base_action_facts = build_action_surface_facts(
        manifest,
        agent_id="agent:mcp",
        tools=[base_tool],
    )
    base_tool_facts = build_tool_surface_facts(
        manifest,
        [base_tool],
        [],
        None,
        None,
    )
    head_tool = _assessed(
        _mcp_tool(
            name="archive_account",
            annotations={"destructiveHint": False},
            auth_scopes=["accounts:delete"],
        ),
        manifest=manifest,
    )

    findings = mcp_permissions.run(
        _context(
            tool=head_tool,
            manifest=manifest,
            diff_reference=ToolSurfaceDiffReference(
                kind="report",
                facts=base_tool_facts,
                action_facts=base_action_facts,
            ),
        )
    )

    [finding] = [item for item in findings if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"]
    assert finding.evidence["form"] == "delta"
    assert finding.evidence["annotation_changes"] == [
        {
            "annotation": "destructiveHint",
            "before": "absent",
            "after": False,
        }
    ]


def test_delta_requires_only_the_contradicting_hint_to_change() -> None:
    manifest = _manifest()
    base_tool = _assessed(
        _mcp_tool(name="update_account", annotations={"httpMethod": "POST"}),
        manifest=manifest,
    )
    base_action_facts = build_action_surface_facts(
        manifest,
        agent_id="agent:mcp",
        tools=[base_tool],
    )
    base_tool_facts = build_tool_surface_facts(
        manifest,
        [base_tool],
        [],
        None,
        None,
    )
    head_tool = _assessed(
        _mcp_tool(
            name="update_account",
            annotations={
                "httpMethod": "POST",
                "readOnlyHint": True,
                "destructiveHint": False,
            },
        ),
        manifest=manifest,
    )

    findings = mcp_permissions.run(
        _context(
            tool=head_tool,
            manifest=manifest,
            diff_reference=ToolSurfaceDiffReference(
                kind="report",
                facts=base_tool_facts,
                action_facts=base_action_facts,
            ),
        )
    )

    [finding] = [item for item in findings if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"]
    assert finding.evidence["published_annotations"] == {"readOnlyHint": True}
    assert finding.evidence["form"] == "static"
    assert finding.evidence["annotation_changes"] == []
    assert finding.evidence["independent_evidence_unchanged"] is False
