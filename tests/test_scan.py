from pathlib import Path

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import write_baseline

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


def test_sample_scan_generates_reports(tmp_path):
    report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    assert report.summary.status == "release_blockers_detected"
    assert report.summary.critical_count >= 1
    assert report.tool_surface.total_tools >= 7
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()
    assert "summary" in (tmp_path / "report.json").read_text(encoding="utf-8")


def test_strict_mode_fails_on_critical(tmp_path):
    report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="strict",
    )

    assert report.summary.critical_count >= 1
    assert exit_code == 20


def test_advisory_mode_does_not_fail_with_release_blockers(tmp_path):
    report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.summary.critical_count >= 1
    assert exit_code == 0


def test_fail_on_high_can_fail_ci_without_critical(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "dangerous.write",
      "description": "Update a record.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "updates": {"type": "object"}
        }
      },
      "annotations": {"destructiveHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: fail-on-test
agent:
  name: fail-on-agent
  declared_purpose:
    - update records
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
policies:
  require_approval_for_tools:
    - dangerous.write
ci:
  mode: advisory
  fail_on:
    - high
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
    )

    assert report.summary.critical_count == 0
    assert report.summary.high_count > 0
    assert exit_code == 20


def test_run_id_is_stable_across_verbose_optional_source_warning(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up support documentation metadata.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: run-id-test
agent:
  name: run-id-agent
  declared_purpose:
    - look up support docs
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
  - id: optional_missing
    type: mcp
    path: missing.json
    optional: true
""",
        encoding="utf-8",
    )

    first, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="advisory",
        verbose=False,
    )
    second, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "second",
        formats=["json"],
        ci_mode="advisory",
        verbose=True,
    )

    assert first.run_id == second.run_id


def test_severity_override_reranks_findings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.short",
      "description": "short",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: severity-test
agent:
  name: severity-agent
  declared_purpose:
    - read docs
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
checks:
  severity_overrides:
    SHIP-DOC-MISSING-DESCRIPTION: critical
ci:
  mode: strict
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
    )

    finding = next(
        item for item in report.findings if item.check_id == "SHIP-DOC-MISSING-DESCRIPTION"
    )
    assert finding.severity == "critical"
    assert finding.evidence["default_severity"] == "medium"
    assert exit_code == 20

    baseline = write_baseline(report, tmp_path / "severity-baseline.json")
    saved = next(
        item for item in baseline.findings if item.check_id == "SHIP-DOC-MISSING-DESCRIPTION"
    )
    assert saved.severity == "critical"


def test_read_only_refund_lookup_is_not_critical(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    critical_lookup_findings = [
        finding
        for finding in report.findings
        if finding.tool_name == "refund_status_lookup" and finding.severity == "critical"
    ]
    assert critical_lookup_findings == []
    lookup_inventory = next(
        item for item in report.tool_inventory if item["name"] == "refund_status_lookup"
    )
    assert "read_only" in lookup_inventory["risk_tags"]


def test_baseline_save_and_scan_matches_existing_findings(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    first_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="strict",
    )
    baseline = write_baseline(first_report, baseline_path)

    second_report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "second",
        formats=["json"],
        ci_mode="strict",
        baseline_path=baseline_path,
    )

    assert baseline.schema_version == "0.2"
    assert first_report.run_id == second_report.run_id
    assert exit_code == 0
    assert second_report.baseline is not None
    assert second_report.baseline.matched_count > 0
    assert second_report.baseline.new_count == 0
    assert all(
        finding.baseline_status in {None, "matched"}
        for finding in second_report.findings
    )


def test_baseline_scan_fails_only_on_new_findings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    tools_path = project / "tools.json"
    tools_path.write_text(
        """
{
  "tools": [
    {
      "name": "support.lookup",
      "description": "Look up support metadata.",
      "annotations": {"readOnlyHint": true}
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
  name: baseline-new
agent:
  name: baseline-agent
  declared_purpose:
    - support lookup
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
ci:
  mode: strict
""",
        encoding="utf-8",
    )
    clean_report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "clean",
        formats=["json"],
        ci_mode="strict",
    )
    baseline_path = tmp_path / "baseline.json"
    write_baseline(clean_report, baseline_path)
    tools_path.write_text(
        """
{
  "tools": [
    {
      "name": "support.lookup",
      "description": "Look up support metadata.",
      "annotations": {"readOnlyHint": true}
    },
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

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "changed",
        formats=["json"],
        ci_mode="strict",
        baseline_path=baseline_path,
    )

    assert exit_code == 20
    assert report.baseline is not None
    assert report.baseline.new_count > 0
    assert any(finding.baseline_status == "new" for finding in report.findings)


def test_read_only_kb_search_does_not_render_low_confidence_financial_tag(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    search_inventory = next(
        item for item in report.tool_inventory if item["name"] == "support.search_kb"
    )
    assert search_inventory["risk_tags"] == ["read_only"]


def test_sdk_preview_tool_is_not_treated_as_external_write(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    high_preview_findings = [
        finding
        for finding in report.findings
        if finding.tool_name == "send_email_preview" and finding.severity in {"critical", "high"}
    ]
    assert high_preview_findings == []


def test_manual_risk_override_sets_tags_and_owner(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    refund_tool = next(item for item in report.tool_inventory if item["name"] == "stripe.create_refund")

    assert refund_tool["owner"] == "payments-platform"
    assert "financial_action" in refund_tool["risk_tags"]
    assert "external_write" in refund_tool["risk_tags"]


def test_duplicate_tools_are_deduplicated_with_warning(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Duplicate
  version: "1.0"
paths:
  /lookup:
    get:
      operationId: shared.lookup
      summary: Look up a shared record.
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (project / "mcp.json").write_text(
        """
{
  "tools": [
    {
      "name": "shared.lookup",
      "description": "Look up a shared record from MCP.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: duplicate-test
agent:
  name: duplicate-agent
  declared_purpose:
    - test duplicate handling
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
  - id: mcp
    type: mcp
    path: mcp.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.tool_surface.total_tools == 1
    assert report.tool_inventory[0]["source_type"] == "openapi"
    assert any("Duplicate tool name 'shared.lookup'" in warning for warning in report.source_warnings)


def test_manifest_scope_checks_read_only_purpose_with_write_tool(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Scope Drift
  version: "1.0"
paths:
  /tickets:
    post:
      operationId: ticket.create
      summary: Create a support ticket.
      security:
        - supportOAuth:
            - support:tickets:write
      responses:
        "200":
          description: ok
components:
  securitySchemes:
    supportOAuth:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: https://auth.example.test/token
          scopes:
            support:tickets:write: Write tickets.
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: scope-test
agent:
  name: scope-agent
  declared_purpose:
    - read-only ticket lookups
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
permissions:
  scopes:
    - support:tickets:write
policies:
  require_approval_for_tools:
    - ticket.create
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
    )

    assert any(
        finding.check_id == "SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE"
        for finding in report.findings
    )


def test_run_id_and_source_paths_are_reproducible_without_absolute_source_refs(tmp_path):
    first, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="advisory",
    )
    second, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "second",
        formats=["json"],
        ci_mode="advisory",
    )

    assert first.run_id == second.run_id
    assert all(
        not (finding.source and finding.source.ref and finding.source.ref.startswith("/"))
        for finding in first.findings
    )


def test_default_scan_does_not_import_user_code(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        """
from pathlib import Path
Path("imported.txt").write_text("executed")

def function_tool(fn):
    return fn

@function_tool
def harmless(name: str) -> str:
    \"\"\"Return a harmless greeting.\"\"\"
    return name
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: privacy-test
agent:
  name: privacy-agent
  declared_purpose:
    - test static extraction
environment:
  target: local
tool_sources:
  - id: sdk
    type: openai_agents_sdk
    path: agent.py
    optional: false
""",
        encoding="utf-8",
    )

    run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert not (project / "imported.txt").exists()
