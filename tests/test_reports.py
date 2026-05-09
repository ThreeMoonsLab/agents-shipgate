import json
from pathlib import Path

import pytest
from jsonschema import validate

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.report.markdown import _safe_markdown_text, render_markdown_report

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")
EXPECTED_MARKDOWN = Path("samples/support_refund_agent/expected/report.md")
OPENAI_API_SAMPLE = Path("samples/simple_openai_api_agent/shipgate.yaml")
OPENAI_API_EXPECTED_MARKDOWN = Path("samples/simple_openai_api_agent/expected/report.md")
LANGCHAIN_SAMPLE = Path("samples/simple_langchain_agent/shipgate.yaml")
LANGCHAIN_EXPECTED_MARKDOWN = Path("samples/simple_langchain_agent/expected/report.md")
CREWAI_SAMPLE = Path("samples/simple_crewai_agent/shipgate.yaml")
CREWAI_EXPECTED_MARKDOWN = Path("samples/simple_crewai_agent/expected/report.md")
REPORT_SCHEMA = Path("docs/report-schema.v0.1.json")
REPORT_SCHEMA_V02 = Path("docs/report-schema.v0.2.json")
REPORT_SCHEMA_V04 = Path("docs/report-schema.v0.4.json")
REPORT_SCHEMA_V06 = Path("docs/report-schema.v0.6.json")
REPORT_SCHEMA_V07 = Path("docs/report-schema.v0.7.json")
REPORT_SCHEMA_V08 = Path("docs/report-schema.v0.8.json")
REPORT_SCHEMA_V09 = Path("docs/report-schema.v0.9.json")
REPORT_SCHEMA_V10 = Path("docs/report-schema.v0.10.json")
REPORT_SCHEMA_V11 = Path("docs/report-schema.v0.11.json")


def test_sample_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    actual = actual.replace(str(Path.cwd()), "<REPO>")
    expected = EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_openai_api_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = OPENAI_API_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_langchain_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=LANGCHAIN_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = LANGCHAIN_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_crewai_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=CREWAI_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = CREWAI_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_json_report_contains_integration_contract_keys(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report.model_dump(mode="json")

    assert payload["summary"]["status"] == "release_blockers_detected"
    assert "critical_count" in payload["summary"]
    assert "title" in payload["findings"][0]
    assert "severity" in payload["findings"][0]
    assert "fingerprint" in payload["findings"][0]
    assert "tool_inventory" in payload
    assert "loaded_plugins" in payload
    assert payload["loaded_plugins"] == []
    assert payload["schema_version"] == "0.1"
    assert payload["report_schema_version"] == "0.11"
    assert "release_decision" in payload
    assert payload["release_decision"]["decision"] in {
        "blocked",
        "review_required",
        "passed",
    }
    assert "frameworks" in payload
    assert "loaded_policy_packs" in payload
    for key in (
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
        "tool_surface_facts",
        "tool_surface_diff",
    ):
        assert key in payload


def test_markdown_release_summary_is_derived_from_json_contract(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)
    decision = payload["release_decision"]
    fail_policy = decision["fail_policy"]
    markdown = render_markdown_report(report)

    assert f"Decision: {decision['decision']}" in markdown
    assert f"Blockers ({len(decision['blockers'])}):" in markdown
    assert f"Review items ({len(decision['review_items'])}):" in markdown
    fail_on_text = (
        ", ".join(fail_policy["fail_on"]) if fail_policy["fail_on"] else "none"
    )
    assert (
        f"Fail policy: ci_mode={fail_policy['ci_mode']}, fail_on=[{fail_on_text}], "
        f"new_findings_only={str(fail_policy['new_findings_only']).lower()}, "
        f"would_fail_ci={str(fail_policy['would_fail_ci']).lower()} "
        f"(exit {fail_policy['exit_code']})"
    ) in markdown


def test_capability_intent_diff_support_refund_fixture(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)

    stripe = next(
        fact
        for fact in payload["capability_facts"]
        if fact["tool_name"] == "stripe.create_refund"
    )
    assert {"financial_action", "external_write"} <= set(stripe["risk_tags"])
    assert stripe["included_reason"] == "referenced_by_critical_finding"
    assert stripe["control_status"] == "missing"
    assert not any(
        fact["tool_name"] == "refund_status_lookup"
        for fact in payload["capability_facts"]
    )

    refund_intentions = [
        intention
        for intention in payload["declared_intentions"]
        if "refund" in intention["text"]
    ]
    assert refund_intentions
    assert any("financial_action" in item["intent_tags"] for item in refund_intentions)
    update_ticket_intention = next(
        intention
        for intention in payload["declared_intentions"]
        if intention["text"] == "update support ticket notes"
    )

    approval_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-POLICY-APPROVAL-MISSING"
        and finding["tool_name"] == "stripe.create_refund"
    )
    idempotency_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-SIDEFX-IDEMPOTENCY-MISSING"
        and finding["tool_name"] == "stripe.create_refund"
    )
    unused_scope_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-MANIFEST-UNUSED-SCOPE"
    )
    assert approval_finding["fingerprint"] == "fp_f092940f62fbb012"
    assert idempotency_finding["fingerprint"] == "fp_dac8011e14c53777"

    assert any(
        item["kind"] == "policy_gap"
        and approval_finding["id"] in item["finding_refs"]
        for item in payload["misalignments"]
    )
    assert all(
        update_ticket_intention["id"] not in item["intention_refs"]
        for item in payload["misalignments"]
        if item["tool_name"] == "stripe.create_refund"
    )
    assert any(
        item["kind"] == "control_missing"
        and idempotency_finding["id"] in item["finding_refs"]
        for item in payload["misalignments"]
    )
    assert any(
        item["kind"] == "scope_drift"
        and unused_scope_finding["id"] in item["finding_refs"]
        for item in payload["misalignments"]
    )
    scenario_types = {item["scenario_type"] for item in payload["suggested_scenarios"]}
    assert {"approval", "idempotency_retry"} <= scenario_types
    least_privilege = next(
        item
        for item in payload["suggested_scenarios"]
        if item["scenario_type"] == "least_privilege_scope"
    )
    assert unused_scope_finding["id"] in least_privilege["source_findings"]


def test_capability_intent_markdown_pins_prohibited_actions(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "- prohibited\\_action: issue refund without approval" in markdown
    assert "- prohibited\\_action: send external email without preview" in markdown
    assert (
        "- prohibited\\_action: cancel order without explicit confirmation"
        in markdown
    )


def test_capability_intent_markdown_collapses_multiline_instruction_preview(tmp_path):
    report, _ = run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "assistant.\n\nYou should only advise" not in markdown
    assert (
        "- instruction\\_preview: You are a support refund assistant. "
        "You should only advise the support representative"
    ) in markdown


def test_capability_diff_intent_tags_are_alias_based_and_negation_aware():
    from agents_shipgate.report.capability_diff import _intent_tags

    tags = _intent_tags("send external email without preview")

    assert {"external_write", "customer_communication"} <= set(tags)
    assert "write" not in tags
    assert "read_only" not in tags
    assert "financial_action" not in _intent_tags("do not refund customer")
    assert "destructive" not in _intent_tags("never delete records")
    assert _intent_tags("update support ticket notes") == []
    assert _intent_tags("answer ticket status") == []
    assert "financial_action" in _intent_tags("process reimbursements")


def test_capability_diff_includes_framework_release_blocker_categories():
    from agents_shipgate.core.models import (
        Finding,
        ReadinessReport,
        ReportSummary,
        ToolSurfaceSummary,
    )
    from agents_shipgate.report.capability_diff import apply_capability_diff

    report = ReadinessReport(
        run_id="test",
        project={"name": "framework-test"},
        agent={"name": "framework-agent", "declared_purpose": ["use framework tools"]},
        environment={"target": "test"},
        summary=ReportSummary(status="release_review_required", high_count=1),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=[
            Finding(
                id="finding-langchain-dynamic",
                fingerprint="fp_langchain_dynamic",
                check_id="SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
                title="LangChain tool surface cannot be statically enumerated",
                severity="high",
                category="langchain",
                confidence="medium",
                recommendation="Provide explicit inventory metadata.",
            )
        ],
    )

    apply_capability_diff(report, [])

    assert report.misalignments
    assert report.misalignments[0].kind == "control_missing"
    assert report.misalignments[0].finding_refs == ["finding-langchain-dynamic"]
    assert report.suggested_scenarios[0].scenario_type == "wildcard_inventory"


def test_capability_diff_release_consequence_mirrors_release_decision(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)

    assert payload["release_consequence"]["decision"] == payload["release_decision"]["decision"]
    assert (
        payload["release_consequence"]["fail_policy"]
        == payload["release_decision"]["fail_policy"]
    )
    assert payload["release_consequence"]["blocker_misalignment_count"] >= 1
    assert payload["release_decision"]["blockers"] == [
        {
            "id": "fp_f092940f62fbb012",
            "fingerprint": "fp_f092940f62fbb012",
            "check_id": "SHIP-POLICY-APPROVAL-MISSING",
            "severity": "critical",
            "title": "stripe.create_refund lacks a declared approval policy",
            "baseline_status": None,
        },
        {
            "id": "fp_dac8011e14c53777",
            "fingerprint": "fp_dac8011e14c53777",
            "check_id": "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
            "severity": "critical",
            "title": "stripe.create_refund lacks idempotency evidence",
            "baseline_status": None,
        },
    ]


def test_release_consequence_counts_distinct_release_decision_findings(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)

    assert len(payload["misalignments"]) > len(payload["release_decision"]["review_items"])
    assert payload["release_consequence"]["blocker_misalignment_count"] == len(
        payload["release_decision"]["blockers"]
    )
    assert payload["release_consequence"]["review_misalignment_count"] == len(
        payload["release_decision"]["review_items"]
    )


def test_capability_diff_blocks_are_reproducible(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    first_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="advisory",
    )
    second_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "second",
        formats=["json"],
        ci_mode="advisory",
    )
    first = report_json_payload(first_report)
    second = report_json_payload(second_report)
    for key in (
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
    ):
        assert first[key] == second[key]


def test_report_paths_use_absolute_path_when_output_escapes_manifest_base(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert str(report.generated_reports["json"]).startswith(str(tmp_path))
    assert not str(report.generated_reports["json"]).startswith("..")


def test_json_report_is_reproducible_for_same_inputs(tmp_path):
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    first = (tmp_path / "report.json").read_text(encoding="utf-8")
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    second = (tmp_path / "report.json").read_text(encoding="utf-8")

    assert first == second


def test_json_schema_is_published():
    text = REPORT_SCHEMA.read_text(encoding="utf-8")
    schema = json.loads(text)

    assert "Agents Shipgate Readiness Report v0.1" in text
    assert '"schema_version"' in text
    inventory_item = schema["properties"]["tool_inventory"]["items"]
    assert {"name", "source_type", "risk_tags", "confidence"} <= set(
        inventory_item["required"]
    )
    api_surface = schema["properties"]["api_surface"]["anyOf"][0]
    assert {
        "prompt_file_count",
        "tool_file_count",
        "response_format_count",
        "model_config_present",
    } <= set(api_surface["required"])


def test_json_report_validates_against_v11_schema(tmp_path):
    """v0.11 schema adds optional source provenance keys on
    findings[].source. Emitted reports must validate against the v0.11
    schema."""
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    schema = json.loads(REPORT_SCHEMA_V11.read_text(encoding="utf-8"))

    validate(instance=report_json_payload(report), schema=schema)


def test_v07_schema_file_is_frozen():
    """v0.7 schema file stays parseable and pinned to const "0.7".
    Catches accidental edits or regeneration overwrites of frozen
    schemas."""
    schema = json.loads(REPORT_SCHEMA_V07.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.7"}
    assert "release_decision" not in schema.get("required", [])


def test_v08_schema_file_is_frozen():
    """v0.8 schema file stays parseable and excludes v0.9 additive fields."""
    schema = json.loads(REPORT_SCHEMA_V08.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.8"}
    for field in (
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
    ):
        assert field not in schema.get("required", [])


def test_v09_schema_file_is_frozen():
    """v0.9 schema file stays parseable and excludes v0.10 additive fields."""
    schema = json.loads(REPORT_SCHEMA_V09.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.9"}
    for field in ("tool_surface_facts", "tool_surface_diff"):
        assert field not in schema.get("required", [])


def test_v10_schema_file_is_frozen():
    """v0.10 schema file stays parseable and pinned to const "0.10"."""
    schema = json.loads(REPORT_SCHEMA_V10.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.10"}


def test_v07_schema_preserves_nested_required_lists():
    """Top-level required fields plus nested required lists for Finding,
    tool_inventory[], loaded_plugins[], LoadedPolicyPack, and per-framework
    surfaces must mirror the v0.5 contract. Optional v0.7 additions
    (Finding.patches, manifest_dir, and the four remediation fields)
    are NOT added to required — they remain optional for additive
    consumers.

    Regression for v0.6 reviewer feedback: Pydantic auto-generation
    weakens nested requireds because most fields have defaults.
    """
    schema = json.loads(REPORT_SCHEMA_V07.read_text(encoding="utf-8"))

    finding_required = set(schema["$defs"]["Finding"]["required"])
    assert finding_required >= {
        "id",
        "fingerprint",
        "check_id",
        "title",
        "severity",
        "category",
        "evidence",
        "confidence",
        "recommendation",
        "suppressed",
        "baseline_status",
    }
    # patches and v0.7 additions stay optional (additive).
    assert "patches" not in finding_required
    for new_field in (
        "autofix_safe",
        "requires_human_review",
        "suggested_patch_kind",
        "docs_url",
    ):
        assert new_field not in finding_required, (
            f"v0.7 added {new_field} as optional; must not appear in required"
        )

    tool_inventory_required = set(
        schema["properties"]["tool_inventory"]["items"]["required"]
    )
    assert tool_inventory_required == {
        "name",
        "source_type",
        "risk_tags",
        "auth_scopes",
        "confidence",
    }
    loaded_plugins_required = set(
        schema["properties"]["loaded_plugins"]["items"]["required"]
    )
    assert loaded_plugins_required == {
        "name",
        "value",
        "distribution",
        "version",
        "check_id",
    }
    loaded_pack_required = set(schema["$defs"]["LoadedPolicyPack"]["required"])
    assert loaded_pack_required == {"id", "name", "path", "rule_count"}

    google_adk_required = set(
        schema["properties"]["frameworks"]["properties"]["google_adk"]["required"]
    )
    assert "agent_count" in google_adk_required
    assert "dynamic_toolset_count" in google_adk_required


def test_v08_schema_requires_release_decision():
    """Top-level required must include `release_decision` and the
    ReleaseDecision $def must require all leaf blocks. Catches drift
    between the model and the published v0.8 contract."""
    schema = json.loads(REPORT_SCHEMA_V08.read_text(encoding="utf-8"))
    assert "release_decision" in schema["required"]
    assert schema["properties"]["report_schema_version"] == {"const": "0.8"}
    # The Pydantic model declares `release_decision: ReleaseDecision | None`
    # for test-helper convenience, but the published schema must NOT allow
    # null — every emitted v0.8 report has a populated release_decision.
    assert schema["properties"]["release_decision"] == {
        "$ref": "#/$defs/ReleaseDecision"
    }

    decision_required = set(schema["$defs"]["ReleaseDecision"]["required"])
    assert decision_required == {
        "decision",
        "reason",
        "blockers",
        "review_items",
        "evidence_coverage",
        "baseline_delta",
        "fail_policy",
    }
    fail_policy_required = set(schema["$defs"]["FailPolicy"]["required"])
    assert fail_policy_required == {
        "ci_mode",
        "fail_on",
        "new_findings_only",
        "would_fail_ci",
        "exit_code",
    }
    evidence_required = set(
        schema["$defs"]["EvidenceCoverageDecision"]["required"]
    )
    assert evidence_required == {
        "level",
        "human_review_recommended",
        "source_warning_count",
        "low_confidence_tool_count",
    }
    # STABILITY.md guarantees the full v0.8 contract on each item: id,
    # fingerprint, check_id, severity, title, baseline_status. The
    # nullable ones (id/fingerprint/baseline_status) must still appear
    # as keys so consumers can read them without conditional checks.
    item_required = set(schema["$defs"]["ReleaseDecisionItem"]["required"])
    assert item_required == {
        "id",
        "fingerprint",
        "check_id",
        "severity",
        "title",
        "baseline_status",
    }


def test_v10_schema_requires_release_decision_and_diffs():
    """Top-level required must include release_decision and additive diff fields."""
    schema = json.loads(REPORT_SCHEMA_V10.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.10"}
    assert {
        "release_decision",
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
        "tool_surface_facts",
        "tool_surface_diff",
    } <= set(schema["required"])
    assert schema["properties"]["release_decision"] == {
        "$ref": "#/$defs/ReleaseDecision"
    }
    assert schema["properties"]["release_consequence"] == {
        "$ref": "#/$defs/ReleaseConsequence"
    }

    capability_required = set(schema["$defs"]["CapabilityFact"]["required"])
    assert capability_required == {
        "id",
        "tool_name",
        "source_type",
        "source_ref",
        "capability",
        "risk_tags",
        "auth_scopes",
        "owner",
        "included_reason",
        "control_status",
        "related_findings",
    }
    assert schema["$defs"]["CapabilityFact"]["properties"]["included_reason"]["enum"] == [
        "high_risk_tag",
        "wildcard_exposure",
        "referenced_by_critical_finding",
        "referenced_by_high_finding",
        "referenced_by_medium_finding",
    ]
    assert schema["$defs"]["Misalignment"]["properties"]["kind"]["enum"] == [
        "policy_gap",
        "scope_drift",
        "prohibited_action_present",
        "control_missing",
        "intent_mismatch",
        "undetected_gap",
    ]
    assert schema["$defs"]["SuggestedScenario"]["properties"]["scenario_type"]["enum"] == [
        "approval",
        "confirmation",
        "idempotency_retry",
        "least_privilege_scope",
        "prohibited_action",
        "wildcard_inventory",
        "schema_boundary",
        "prompt_scope_alignment",
        "test_case_coverage",
    ]
    diff_required = set(schema["$defs"]["ToolSurfaceDiff"]["required"])
    assert {
        "enabled",
        "base",
        "summary",
        "tools",
        "high_risk_effects",
        "scopes",
        "controls",
        "metadata_changes",
        "policy_drift",
        "finding_deltas",
        "notes",
    } <= diff_required


def test_v11_schema_rejects_null_release_decision_and_consequence(tmp_path):
    """A v0.11 payload with null release blocks MUST fail validation.
    Regression for the original schema which emitted
    `anyOf: [ReleaseDecision, null]` and silently accepted null."""
    import jsonschema

    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    schema = json.loads(REPORT_SCHEMA_V11.read_text(encoding="utf-8"))
    payload = report_json_payload(report)

    # Sanity: real payload validates.
    validate(instance=payload, schema=schema)

    # Tamper: setting release_decision to null must be rejected.
    payload["release_decision"] = None
    with pytest.raises(jsonschema.ValidationError):
        validate(instance=payload, schema=schema)

    payload = report_json_payload(report)
    payload["release_consequence"] = None
    with pytest.raises(jsonschema.ValidationError):
        validate(instance=payload, schema=schema)


def test_json_report_omits_patches_key_when_not_suggested(tmp_path):
    """Per C4: scan without --suggest-patches must NOT include the
    `patches` key on any finding. Run-id stability for non-opting
    callers depends on this."""
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)
    for finding in payload["findings"]:
        assert "patches" not in finding


def test_markdown_escapes_user_controlled_tool_metadata(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Injection
  version: "1.0"
paths:
  /records:
    post:
      operationId: "[Click here](https://evil.example)"
      summary: "Update [records](https://evil.example)"
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                updates:
                  type: object
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
  name: "**bold** _team_ <tag>"
agent:
  name: markdown-agent
  declared_purpose:
    - update records
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
policies:
  require_approval_for_tools:
    - "[Click here](https://evil.example)"
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "[Click here](https://evil.example)" not in markdown
    assert "\\[Click here\\]\\(https://evil.example\\)" in markdown
    assert "**bold** _team_ <tag>" not in markdown
    assert "\\*\\*bold\\*\\* \\_team\\_ \\<tag\\>" in markdown
    assert _safe_markdown_text("**bold** _underscore_ <tag>") == (
        "\\*\\*bold\\*\\* \\_underscore\\_ \\<tag\\>"
    )


def test_clean_report_has_affirmative_pass_result(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up internal documentation metadata for an existing support article.",
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
  name: clean-test
agent:
  name: clean-agent
  declared_purpose:
    - look up documentation metadata
environment:
  target: local
tool_sources:
  - id: docs
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    # v0.8: the legacy "Result: PASS ..." line was removed in favor of
    # the leading Release Decision block. A clean scan with high-confidence
    # tools yields decision=passed.
    markdown = render_markdown_report(report)
    assert "## Release Decision" in markdown
    assert "Decision: passed" in markdown
    assert "## Capability <-> Intent Diff" in markdown
    assert "No capability/intent misalignments detected from static evidence." in markdown
