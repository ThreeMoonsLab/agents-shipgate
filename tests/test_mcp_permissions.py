from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.checks import mcp_permissions
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core import risk_hints
from agents_shipgate.core.adopter_text import internal_vocabulary
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, AuthInfo, Tool
from agents_shipgate.core.lenses.action_surface import build_action_surface_facts
from agents_shipgate.core.lenses.tool_surface import (
    ToolSurfaceDiffReference,
    build_tool_surface_facts,
    tool_annotation_hash,
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
    description: str | None = None,
    annotations: dict[str, object] | None = None,
    auth_scopes: list[str] | None = None,
) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        description=description,
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
        configured_source_ids=["mcp-tools"],
    )


def _write_mcp_workspace(
    tmp_path: Path,
    *,
    tool: dict[str, object],
    trust: str | None = None,
) -> Path:
    tool_payload = dict(tool)
    tool_payload.setdefault(
        "inputSchema",
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    trust_line = f"    trust: {trust}\n" if trust is not None else ""
    config = tmp_path / "shipgate.yaml"
    config.write_text(
        """version: "0.1"
project:
  name: mcp-annotation-test
agent:
  name: mcp-agent
  declared_purpose:
    - publish and review an MCP tool surface
environment:
  target: local
tool_sources:
  - id: mcp-tools
    type: mcp
    path: tools.json
    binding:
      complete: true
      reason: Reviewed test fixture binding for the complete published MCP surface.
"""
        + trust_line
        + """ci:
  mode: advisory
""",
        encoding="utf-8",
    )
    (tmp_path / "tools.json").write_text(
        json.dumps({"tools": [tool_payload]}),
        encoding="utf-8",
    )
    return config


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
    assert finding.evidence["annotation_surface_changed"] is None
    assert finding.evidence["independent_evidence_unchanged"] is None
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


def test_scan_keeps_keyword_only_annotation_contradiction_visible_and_non_gating(
    tmp_path: Path,
) -> None:
    config = _write_mcp_workspace(
        tmp_path,
        tool={
            "name": "delete_account",
            "description": "Remove a customer account.",
            "annotations": {"readOnlyHint": True},
        },
    )

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert exit_code == 0
    [finding] = [
        item
        for item in report.findings
        if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"
    ]
    assert finding.evidence["published_annotations"] == {"readOnlyHint": True}
    assert finding.evidence["independent_evidence"][0]["basis"] == "inferred_keyword"
    assert finding.evidence["independent_evidence"][0]["source"] == "risk_hint:keyword"
    assert "MCP clients" in finding.evidence["client_consequence"]
    assert finding.support is not None
    assert finding.support.policy_eligible is False
    assert finding.blocks_release is False
    assert finding.requires_human_review is False
    assert finding.suggested_patch_kind == "none"
    assert finding.agent_action == "informational"
    assert report.release_decision is not None
    assert not any(
        item.check_id == finding.check_id
        for item in [
            *report.release_decision.blockers,
            *report.release_decision.review_items,
        ]
    )
    rule = next(
        item
        for item in report.release_decision.contribution_rules
        if item.check_id == finding.check_id
    )
    assert (rule.category, rule.rule) == ("excluded", "unsupported_evidence")
    assert any(
        gap.kind == "inferred_policy_applicability"
        and finding.check_id in gap.why
        for gap in report.policy_evidence_gaps
    )
    assert report.agent_summary is not None
    assert report.agent_summary.needs_human_review == 0


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


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("get_account", "Fetch an account record before you delete it."),
        ("search_audit_log", "Search the audit log for remove events."),
    ],
)
def test_structural_read_evidence_outweighs_inferred_destructive_keyword(
    name: str,
    description: str,
) -> None:
    tool = _assessed(
        _mcp_tool(
            name=name,
            description=description,
            annotations={"readOnlyHint": True, "httpMethod": "GET"},
        )
    )

    claims = {
        (claim.value, claim.confidence, claim.basis, claim.source)
        for claim in tool.semantic_assessment.effect.claims
    }
    assert ("read", "high", "protocol_structure", "openapi_method") in claims
    assert (
        "destructive",
        "medium",
        "inferred_keyword",
        "risk_hint:keyword",
    ) in claims

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {finding.check_id for finding in findings}


def test_structural_read_evidence_supports_explicit_non_destructive_hint() -> None:
    tool = _assessed(
        _mcp_tool(
            name="search_accounts",
            description="Search account records before you remove one.",
            annotations={"destructiveHint": False, "httpMethod": "GET"},
        )
    )

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {
        finding.check_id for finding in findings
    }


def test_structural_side_effect_still_contradicts_structural_read_evidence() -> None:
    tool = _assessed(
        _mcp_tool(
            name="get_account",
            description="Fetch an account record before you delete it.",
            annotations={"readOnlyHint": True, "httpMethod": "GET"},
            auth_scopes=["accounts:delete"],
        )
    )

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    [finding] = [
        item
        for item in findings
        if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"
    ]
    assert any(
        row["effect"] == "destructive"
        and row["confidence"] == "high"
        and row["basis"] == "structural_scope"
        and row["source"] == "auth_scope"
        for row in finding.evidence["independent_evidence"]
    )
    assert (
        "against structural server auth scope evidence and inferred keyword evidence"
        in finding.recommendation
    )
    assert "declared scope evidence" not in finding.recommendation


@pytest.mark.parametrize("trust", [None, "internal", "external", "untrusted"])
def test_scan_source_trust_does_not_silence_annotation_contradiction(
    tmp_path: Path,
    trust: str | None,
) -> None:
    config = _write_mcp_workspace(
        tmp_path,
        trust=trust,
        tool={
            "name": "archive_account",
            "annotations": {"readOnlyHint": True},
            "auth": {"scopes": ["accounts:delete"]},
        },
    )

    report, _exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    [finding] = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"
    ]
    assert finding.source is not None
    assert finding.source.ref == "tools.json"
    assert any(
        fact.source_id == "mcp-tools" for fact in report.tool_surface_facts.tools
    )


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


def test_reviewed_effect_declaration_is_not_independent_annotation_evidence() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "perform_operation", "effect": "destructive"}
    )
    tool = _assessed(
        _mcp_tool(
            name="perform_operation",
            annotations={"readOnlyHint": True},
        ),
        declaration=declaration,
    )

    findings = mcp_permissions.run(_context(diff_reference=None, tool=tool))

    assert "SHIP-MCP-ANNOTATION-CONTRADICTION" not in {
        finding.check_id for finding in findings
    }


def test_same_source_annotation_conflict_stays_in_semantic_evidence_gap() -> None:
    tool = _assessed(
        _mcp_tool(
            name="perform_operation",
            annotations={"readOnlyHint": True, "destructiveHint": True},
        )
    )

    assert tool.semantic_assessment.effect.status == "conflicting"
    assert "conflicting_effect_evidence" in {
        issue.kind for issue in tool.semantic_assessment.effect.issues
    }
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
    assert finding.evidence["annotation_surface_changed"] is True
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
    assert finding.evidence["annotation_surface_changed"] is True
    assert finding.evidence["independent_evidence_unchanged"] is True


def test_cochanged_annotation_reports_unchanged_evidence_without_exact_delta() -> None:
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
                "title": "Update account",
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
    assert finding.evidence["annotation_surface_changed"] is True
    assert finding.evidence["independent_evidence_unchanged"] is True


def test_changed_independent_evidence_is_not_reported_as_unchanged() -> None:
    manifest = _manifest()
    base_tool = _assessed(
        _mcp_tool(
            name="perform_operation",
            auth_scopes=["accounts:write"],
        ),
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
            name="perform_operation",
            annotations={"readOnlyHint": True},
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

    [finding] = [
        item
        for item in findings
        if item.check_id == "SHIP-MCP-ANNOTATION-CONTRADICTION"
    ]
    assert finding.evidence["form"] == "static"
    assert finding.evidence["annotation_changes"] == []
    assert finding.evidence["annotation_surface_changed"] is True
    assert finding.evidence["independent_evidence_unchanged"] is False


def test_annotation_hash_preserves_client_visible_keys_and_list_order() -> None:
    assert tool_annotation_hash({"observed": "a"}) != tool_annotation_hash(
        {"observed": "b"}
    )
    assert tool_annotation_hash(
        {"audience": ["user", "assistant"]}
    ) != tool_annotation_hash(
        {"audience": ["assistant", "user"]}
    )
