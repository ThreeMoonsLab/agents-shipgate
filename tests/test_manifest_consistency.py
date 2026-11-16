
from agents_shipgate.cli.scan import run_scan


def test_manifest_consistency_flags_stale_entries_and_unused_scopes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "billing.create_refund",
      "description": "Create a customer refund.",
      "inputSchema": {
        "type": "object",
        "properties": {"amount": {"type": "number"}}
      },
      "annotations": {"destructiveHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    config = project / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: manifest-consistency
agent:
  name: manifest-agent
  declared_purpose:
    - create refunds
environment:
  target: production_like
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: billing.create_refund, source_id: tools}]
      handoffs: []
      reason: reviewed manifest-consistency fixture binding
permissions:
  scopes:
    - billing:*
policies:
  require_approval_for_tools:
    - missing.tool
risk_overrides:
  tools:
    missing.tool:
      tags: ["read_only"]
      reason: "stale"
checks:
  ignore:
    - check_id: SHIP-DOES-NOT-EXIST
      reason: "stale"
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    check_ids = {finding.check_id for finding in report.findings}

    assert "SHIP-MANIFEST-STALE-SUPPRESSION" in check_ids
    assert "SHIP-MANIFEST-STALE-POLICY" in check_ids
    assert "SHIP-MANIFEST-STALE-RISK-OVERRIDE" in check_ids
    assert "SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING" in check_ids
    assert "SHIP-MANIFEST-UNUSED-SCOPE" in check_ids


def test_manifest_consistency_has_no_false_positive_for_current_entries(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "support.lookup",
      "description": "Look up support metadata.",
      "annotations": {"readOnlyHint": true},
      "auth": {"scopes": ["support:read"]}
    }
  ]
}
""",
        encoding="utf-8",
    )
    config = project / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: manifest-consistency-clean
agent:
  name: manifest-agent
  declared_purpose:
    - look up support metadata
environment:
  target: production
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: support.lookup, source_id: tools}]
      handoffs: []
      reason: reviewed manifest-consistency fixture binding
permissions:
  scopes:
    - support:read
checks:
  ignore:
    - check_id: SHIP-DOC-MISSING-DESCRIPTION
      tool: support.lookup
      reason: "covered by internal docs"
    - check_id: SHIP-API-OPERATIONAL-READINESS
      reason: "legacy v0.3 compatibility alias"
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    check_ids = {finding.check_id for finding in report.findings}

    assert "SHIP-MANIFEST-STALE-SUPPRESSION" not in check_ids
    assert "SHIP-MANIFEST-STALE-POLICY" not in check_ids
    assert "SHIP-MANIFEST-STALE-RISK-OVERRIDE" not in check_ids
    assert "SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING" not in check_ids
    assert "SHIP-MANIFEST-UNUSED-SCOPE" not in check_ids


def test_a_catalog_tool_without_an_established_binding_is_not_a_missing_tool(tmp_path):
    """#1035: compare with the catalog, and never propose removing the entry."""

    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "billing.lookup_refund",
      "description": "Look up a refund.",
      "annotations": {"readOnlyHint": true}
    },
    {
      "name": "billing.void_refund",
      "description": "Void a pending refund.",
      "annotations": {"destructiveHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    config = project / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: manifest-consistency-catalog
agent:
  name: manifest-agent
  declared_purpose:
    - look up refunds
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: billing.lookup_refund, source_id: tools}]
      handoffs: []
      reason: reviewed fixture binding; void_refund is not bound to this agent
policies:
  require_approval_for_tools:
    - billing.void_refund
    - missing.tool
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    stale = {
        finding.evidence["tool"]: finding
        for finding in report.findings
        if finding.check_id == "SHIP-MANIFEST-STALE-POLICY"
    }

    assert set(stale) == {"billing.void_refund", "missing.tool"}
    unbound = stale["billing.void_refund"]
    assert unbound.title == (
        "approval policy references billing.void_refund, whose binding to the "
        "agent is not established"
    )
    assert unbound.evidence["issues"] == ["binding_not_established"]
    assert [patch.kind for patch in unbound.patches] == ["manual"]
    missing = stale["missing.tool"]
    assert missing.title == "approval policy references missing tool missing.tool"
    assert "issues" not in missing.evidence
    assert [(patch.kind, patch.confidence) for patch in missing.patches] == [
        ("remove_pointer", "high")
    ]
