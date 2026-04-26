
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
permissions:
  scopes:
    - support:read
checks:
  ignore:
    - check_id: SHIP-DOC-MISSING-DESCRIPTION
      tool: support.lookup
      reason: "covered by internal docs"
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
