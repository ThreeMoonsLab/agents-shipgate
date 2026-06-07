import json
from pathlib import Path

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import write_baseline

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")
GOOGLE_ADK_SAMPLE = Path("samples/google_adk_agent/shipgate.yaml")
OPENAI_API_SAMPLE = Path("samples/simple_openai_api_agent/shipgate.yaml")
ANTHROPIC_SAMPLE = Path("samples/simple_anthropic_agent/shipgate.yaml")
OPENAI_SDK_SAMPLE = Path("samples/openai_agents_sdk_agent/shipgate.yaml")


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


def test_openai_agents_sdk_directory_fixture_scans_static_tools(tmp_path):
    report, exit_code = run_scan(
        config_path=OPENAI_SDK_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert exit_code == 0
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    inventory = {entry["name"]: entry for entry in report.tool_inventory}
    assert set(inventory) == {"support.lookup_case", "support.render_reply"}
    assert inventory["support.lookup_case"]["source_ref"] == "agents/case_tools.py"
    assert inventory["support.render_reply"]["source_ref"] == "agents/reply_tools.py"
    assert {action.tool_name for action in report.action_surface_facts.actions} == {
        "support.lookup_case",
        "support.render_reply",
    }


def test_artifact_registry_refactor_preserves_framework_json_shape(tmp_path):
    run_scan(
        config_path=GOOGLE_ADK_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_formats=["json"],
    )

    report_payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    packet_payload = json.loads((tmp_path / "packet.json").read_text(encoding="utf-8"))

    assert "framework_artifacts" not in report_payload
    assert {"api_surface", "anthropic_surface", "frameworks", "codex_plugin_surface"} <= set(
        report_payload
    )
    assert report_payload["frameworks"]["google_adk"]["agent_count"] == 1
    assert "framework_artifacts" not in packet_payload
    assert "packet_schema_version" in packet_payload


def test_artifact_registry_refactor_preserves_api_surface_json_shape(tmp_path):
    run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path / "openai",
        formats=["json"],
        ci_mode="advisory",
        packet_formats=["json"],
    )
    run_scan(
        config_path=ANTHROPIC_SAMPLE,
        output_dir=tmp_path / "anthropic",
        formats=["json"],
        ci_mode="advisory",
        packet_formats=["json"],
    )

    openai_payload = json.loads(
        (tmp_path / "openai" / "report.json").read_text(encoding="utf-8")
    )
    anthropic_payload = json.loads(
        (tmp_path / "anthropic" / "report.json").read_text(encoding="utf-8")
    )

    assert openai_payload["api_surface"]["tool_file_count"] == 1
    assert openai_payload["anthropic_surface"] is None
    assert anthropic_payload["api_surface"] is None
    assert anthropic_payload["anthropic_surface"]["tool_file_count"] == 1
    assert "framework_artifacts" not in openai_payload
    assert "framework_artifacts" not in anthropic_payload


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


def test_agent_finding_does_not_emit_duplicate_policy_evidence_source(tmp_path):
    """v0.19 reviewer-grade provenance: agent-level findings have a
    single citation site (the manifest pointer IS the primary
    ``source``). Setting ``policy_evidence_source`` to the same
    pointer would force every downstream renderer (packet markdown,
    SARIF, scenario YAML) to dedupe and would otherwise produce
    ``... — shipgate.yaml:N — shipgate.yaml:N`` style output.

    Regression: an early v0.19 draft set both source AND
    policy_evidence_source from the same manifest pointer.
    """
    import json as _json
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        '{"tools": [{"name": "docs.read", "description": "read", '
        '"annotations": {"readOnlyHint": true}}]}',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project: {name: dedupe-test}
agent:
  name: t
  declared_purpose: [read]
environment: {target: local}
tool_sources:
  - {id: tools, type: mcp, path: tools.json}
permissions:
  scopes: ["*"]
ci: {mode: advisory}
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "out",
        formats=["json", "sarif"],
        ci_mode="advisory",
    )
    broad = next(
        f for f in report.findings if f.check_id == "SHIP-AUTH-MANIFEST-BROAD-SCOPE"
    )
    # Primary source carries the manifest pointer.
    assert broad.source is not None
    assert broad.source.path == "shipgate.yaml"
    assert broad.source.pointer == "/permissions/scopes"
    # Secondary is None — the agent-level finding has only one site.
    assert broad.policy_evidence_source is None
    # SARIF emits exactly one location, not two duplicates.
    sarif = _json.loads(
        (tmp_path / "out" / "report.sarif").read_text(encoding="utf-8")
    )
    matches = [
        r for r in sarif["runs"][0]["results"]
        if r["ruleId"] == "SHIP-AUTH-MANIFEST-BROAD-SCOPE"
    ]
    assert matches and len(matches[0].get("locations", [])) == 1


def test_policy_evidence_source_threads_manifest_pointer(tmp_path):
    """High-risk policy/idempotency findings must carry both source
    pointers: the tool location (in ``Finding.source``) AND the manifest
    evidence pointer (in ``Finding.policy_evidence_source``) so a
    reviewer can jump to the manifest line where the missing mitigation
    should be declared without grep.
    """
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
  name: dual-source-test
agent:
  name: dual-source-agent
  declared_purpose:
    - update records
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
ci:
  mode: advisory
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    approval = next(
        f for f in report.findings if f.check_id == "SHIP-POLICY-APPROVAL-MISSING"
    )
    assert approval.source is not None
    # Tool source intact: the MCP loader sets source.type to the tool
    # source type so the reviewer can jump to the tool definition.
    assert approval.source.type == "mcp"
    # Manifest evidence pointer added: structured fields point at the
    # exact YAML line where the missing-policy declaration belongs.
    assert approval.policy_evidence_source is not None
    assert approval.policy_evidence_source.type == "manifest"
    assert approval.policy_evidence_source.pointer == (
        "/policies/require_approval_for_tools"
    )
    assert approval.policy_evidence_source.path == "shipgate.yaml"
    # The pointer doesn't resolve (the manifest doesn't declare the
    # block), so ``start_line`` is None — the reviewer still gets the
    # pointer string and the manifest filename for orientation.
    assert approval.policy_evidence_source.start_line is None
    assert approval.capability_refs
    assert approval.capability_policy_evidence is not None
    assert approval.capability_policy_evidence.capability_id in approval.capability_refs
    assert approval.capability_policy_evidence.identity["tool_name"] == "dangerous.write"
    assert approval.capability_policy_evidence.matched_predicates[
        "missing_approval_policy"
    ] is True


def test_mixed_read_write_tool_still_requires_policy_controls(tmp_path):
    """A read verb plus a mutating verb must not become effectively read-only.

    Capability-native policy matching delegates to the canonical risk helper so
    medium-confidence read-only keyword hints do not mask destructive/write
    hints. This preserves the old built-in approval/confirmation findings for
    common mixed names like ``get_or_delete_record``.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Mixed Tool API
  version: "1.0"
paths:
  /records/{id}:
    get:
      operationId: get_or_delete_record
      summary: Get or delete a customer record.
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: mixed-read-write
agent:
  name: mixed-agent
  declared_purpose:
    - manage records
environment:
  target: production_like
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
ci:
  mode: advisory
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    findings = {
        finding.check_id: finding
        for finding in report.findings
        if finding.tool_name == "get_or_delete_record"
    }
    assert "SHIP-POLICY-APPROVAL-MISSING" in findings
    assert "SHIP-POLICY-CONFIRMATION-MISSING" in findings
    assert findings["SHIP-POLICY-APPROVAL-MISSING"].capability_refs
    assert findings["SHIP-POLICY-CONFIRMATION-MISSING"].capability_refs


def test_policy_evidence_source_resolves_existing_pointer_line(tmp_path):
    """When the manifest declares the block the policy_evidence_source
    points at (even if the tool isn't in the list), the YAML position
    index must resolve the pointer to a concrete line number."""
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
        "properties": {"updates": {"type": "object"}}
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
  name: dual-source-test
agent:
  name: dual-source-agent
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
    - some.other.tool
ci:
  mode: advisory
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    approval = next(
        f for f in report.findings if f.check_id == "SHIP-POLICY-APPROVAL-MISSING"
    )
    assert approval.policy_evidence_source is not None
    assert approval.policy_evidence_source.pointer == (
        "/policies/require_approval_for_tools"
    )
    # Block IS declared, so the position index resolves a line number.
    assert approval.policy_evidence_source.start_line is not None
    assert approval.policy_evidence_source.start_line > 0


def test_fingerprint_stable_with_policy_evidence_source(tmp_path):
    """Threading the manifest pointer must not invalidate existing
    baselines. ``finding_fingerprint`` hashes ``check_id + tool_name +
    evidence``; ``policy_evidence_source`` is provenance metadata and
    must stay out of the identity hash."""
    from agents_shipgate.core.findings import finding_fingerprint
    from agents_shipgate.schemas.common import SourceReference
    from agents_shipgate.schemas.report import CapabilityPolicyEvidence, Finding

    base = Finding(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        title="t",
        severity="critical",
        category="policy",
        tool_name="stripe.create_refund",
        evidence={"risk_tags": ["financial_action"]},
        confidence="high",
        recommendation="r",
    )
    enriched = Finding(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        title="t",
        severity="critical",
        category="policy",
        tool_name="stripe.create_refund",
        evidence={"risk_tags": ["financial_action"]},
        confidence="high",
        recommendation="r",
        policy_evidence_source=SourceReference(
            type="manifest",
            ref="shipgate.yaml#/policies/require_approval_for_tools",
            path="shipgate.yaml",
            start_line=42,
            pointer="/policies/require_approval_for_tools",
        ),
        capability_refs=["cap_123"],
        capability_policy_evidence=CapabilityPolicyEvidence(
            capability_id="cap_123",
            identity={"tool_name": "stripe.create_refund"},
            effect={"effect": "financial_write"},
            authority={"scopes": []},
            controls={"approval_required": False},
            hashes={"identity_hash": "123"},
            matched_predicates={"missing_approval_policy": True},
            source=SourceReference(type="mcp", ref="tools.json"),
        ),
    )
    assert finding_fingerprint(base) == finding_fingerprint(enriched)


def test_change_me_placeholders_route_to_review_required(tmp_path):
    """Unresolved CHANGE_ME placeholders in shipgate.yaml must surface as
    source_warnings so the existing
    ``source_warning_count > 0 → review_required`` branch in
    release_decision.evidence_coverage trips. Without that, a scan
    against a manifest still carrying stub values would emit a release
    packet that looks like real evidence.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up documentation metadata.",
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
  name: CHANGE_ME
agent:
  name: docs-agent
  declared_purpose:
    - look up documentation
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
ci:
  mode: advisory
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    placeholder_warnings = [
        warning for warning in report.source_warnings if "CHANGE_ME" in warning
    ]
    assert placeholder_warnings, report.source_warnings
    assert any("shipgate.yaml:" in warning for warning in placeholder_warnings)
    assert report.release_decision is not None
    assert report.release_decision.evidence_coverage.source_warning_count >= 1
    assert report.release_decision.decision == "review_required"
    # advisory mode does not fail CI, but the gate above is still routed.
    assert exit_code == 0


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

    assert baseline.schema_version == "0.5"
    assert baseline.tool_surface_facts is not None
    assert baseline.action_surface_facts is not None
    assert first_report.run_id == second_report.run_id
    assert exit_code == 0
    assert second_report.baseline is not None
    assert second_report.baseline.matched_count > 0
    assert second_report.baseline.new_count == 0
    assert second_report.tool_surface_diff.enabled is True
    assert second_report.tool_surface_diff.base.kind == "baseline"
    assert all(
        finding.baseline_status in {None, "matched"}
        for finding in second_report.findings
    )


def test_baseline_save_is_idempotent_for_unchanged_findings(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
    )

    write_baseline(report, baseline_path)
    first = baseline_path.read_text(encoding="utf-8")
    write_baseline(report, baseline_path)
    second = baseline_path.read_text(encoding="utf-8")

    assert first == second


def test_scan_diff_from_prior_report_does_not_change_release_gate(tmp_path):
    first_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="strict",
    )
    baseline_path = tmp_path / "baseline.json"
    write_baseline(first_report, baseline_path)

    without_diff, without_diff_exit = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "without",
        formats=["json"],
        ci_mode="strict",
        baseline_path=baseline_path,
    )
    with_diff, with_diff_exit = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "with",
        formats=["json"],
        ci_mode="strict",
        baseline_path=baseline_path,
        diff_from_path=tmp_path / "base" / "report.json",
    )

    assert with_diff_exit == without_diff_exit
    assert with_diff.release_decision is not None
    assert without_diff.release_decision is not None
    assert with_diff.release_decision.decision == without_diff.release_decision.decision
    assert with_diff.tool_surface_diff.enabled is True
    assert with_diff.tool_surface_diff.base.kind == "report"


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
      "annotations": {"readOnlyHint": true},
      "auth": {
        "type": "oauth2",
        "scopes": ["shared:read"]
      },
      "owner": "support-platform"
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
    assert report.tool_inventory[0]["auth_scopes"] == ["shared:read"]
    assert report.tool_inventory[0]["owner"] == "support-platform"
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

    # The scanner must not have executed agent.py (which would write imported.txt).
    assert not (project / "imported.txt").exists()


def test_source_warnings_ordering_duplicate_before_policy_pack(tmp_path):
    """Regression test for P3 (v0.19 decomp): duplicate-tool warnings must
    appear *before* policy-pack and artifact warnings in
    ``report.source_warnings``.

    Pre-decomp ``run_scan`` assembled warnings in the order:
        source → duplicate → artifact → placeholder → policy_pack → dedup

    The initial decomp accidentally moved duplicate warnings to the end
    (source → artifact → placeholder → policy_pack → duplicate). That changed
    ``report.source_warnings`` for any fixture with both a duplicate-tool name
    and a policy-pack or artifact warning — a STABILITY regression.

    This test reproduces the exact ordering property: given one duplicate-tool
    warning and one optional-missing-policy-pack warning, the duplicate warning
    must have a lower index in ``report.source_warnings``.
    """
    project = tmp_path / "project"
    project.mkdir()

    # Two tool sources with the same operationId → triggers duplicate_warning
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Primary API
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
    # Optional missing policy pack → triggers policy_pack_warning
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: ordering-test
agent:
  name: ordering-agent
  declared_purpose:
    - test warning ordering
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
  - id: mcp
    type: mcp
    path: mcp.json
checks:
  policy_packs:
    - path: missing-pack.yaml
      optional: true
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    warnings = report.source_warnings
    dup_idx = next(
        (i for i, w in enumerate(warnings) if "Duplicate tool name 'shared.lookup'" in w),
        None,
    )
    pp_idx = next(
        (i for i, w in enumerate(warnings) if "missing-pack.yaml" in w and "failed to load" in w),
        None,
    )

    assert dup_idx is not None, (
        f"Expected a duplicate-tool warning in source_warnings; got: {warnings}"
    )
    assert pp_idx is not None, (
        f"Expected an optional-policy-pack warning in source_warnings; got: {warnings}"
    )
    assert dup_idx < pp_idx, (
        f"Duplicate-tool warning (index {dup_idx}) must appear before "
        f"policy-pack warning (index {pp_idx}) in report.source_warnings. "
        f"Full list: {warnings}"
    )
