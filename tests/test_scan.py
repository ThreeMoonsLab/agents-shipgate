import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli._artifact_lifecycle import (
    VERIFIER_ROUTE_ARTIFACT_NAMES,
    ArtifactLifecycleError,
)
from agents_shipgate.cli.main import app
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


def test_scan_removes_stale_verifier_route_artifacts(tmp_path: Path) -> None:
    assert set(VERIFIER_ROUTE_ARTIFACT_NAMES) == {
        "verifier.json",
        "agent-handoff.json",
        "pr-comment.md",
        "verify-run.json",
        "verification-plan.json",
        "verification-input.diff",
        "verification-base-report.json",
        "verification-unit-result.json",
        "verification-artifacts.json",
        "verification-receipt.json",
        "human-authorization.json",
    }
    for name in VERIFIER_ROUTE_ARTIFACT_NAMES:
        (tmp_path / name).write_text('{"stale": true}\n', encoding="utf-8")

    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert (tmp_path / "report.json").is_file()
    assert not [
        name for name in VERIFIER_ROUTE_ARTIFACT_NAMES if (tmp_path / name).exists()
    ]


def test_scan_does_not_replace_report_when_stale_route_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_handoff = tmp_path / "agent-handoff.json"
    stale_handoff.write_text('{"stale": true}\n', encoding="utf-8")
    report_path = tmp_path / "report.json"
    prior_report = '{"prior": true}\n'
    report_path.write_text(prior_report, encoding="utf-8")
    real_unlink = Path.unlink

    def deny_stale_handoff_unlink(
        path: Path, missing_ok: bool = False
    ) -> None:
        if path == stale_handoff:
            raise PermissionError("test denied stale handoff cleanup")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_stale_handoff_unlink)

    with pytest.raises(
        ArtifactLifecycleError, match="Could not remove stale verifier artifact"
    ):
        run_scan(
            config_path=SAMPLE,
            output_dir=tmp_path,
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )

    assert stale_handoff.is_file()
    assert report_path.read_text(encoding="utf-8") == prior_report


def test_scan_cleanup_failure_agent_mode_names_exact_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_verifier = tmp_path / "verifier.json"
    stale_verifier.write_text('{"stale": true}\n', encoding="utf-8")
    real_unlink = Path.unlink

    def deny_stale_verifier_unlink(
        path: Path, missing_ok: bool = False
    ) -> None:
        if path == stale_verifier:
            raise PermissionError("test denied stale verifier cleanup")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_stale_verifier_unlink)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--config",
            str(SAMPLE),
            "--out",
            str(tmp_path),
            "--format",
            "json",
        ],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 4, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    expected_prefix = f"Remove {stale_verifier} and re-run scan"
    assert payload["next_action"].startswith(expected_prefix)
    assert payload["next_actions"][0]["kind"] == "edit"
    assert payload["next_actions"][0]["path"] == str(stale_verifier)


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
    assert report.release_decision.decision == "passed"
    assert report.release_decision.evidence_coverage.source_warning_count == 0
    assert report.release_decision.evidence_coverage.semantic_coverage.model_dump() == {
        "total_actions": 2,
        "pass_eligible_actions": 2,
        "gap_count": 0,
        "review_concern_count": 0,
        "reason_counts": {},
    }
    inventory = {entry["name"]: entry for entry in report.tool_inventory}
    assert set(inventory) == {"support.lookup_case", "support.render_reply"}
    assert inventory["support.lookup_case"]["source_ref"] == "inventories/tools.json"
    assert inventory["support.render_reply"]["source_ref"] == "inventories/tools.json"
    assert {entry["source_type"] for entry in inventory.values()} == {"mcp"}
    assert {entry["confidence"] for entry in inventory.values()} == {"high"}
    assert report.source_warnings == []
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

    openai_payload = json.loads((tmp_path / "openai" / "report.json").read_text(encoding="utf-8"))
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
  require_confirmation_for_tools:
    - dangerous.write
action_surface:
  actions:
    - tool: dangerous.write
      effect: destructive
      approval: {required: true}
      safeguards: {rollback: true}
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
    broad = next(f for f in report.findings if f.check_id == "SHIP-AUTH-MANIFEST-BROAD-SCOPE")
    # Primary source carries the manifest pointer.
    assert broad.source is not None
    assert broad.source.path == "shipgate.yaml"
    assert broad.source.pointer == "/permissions/scopes"
    # Secondary is None — the agent-level finding has only one site.
    assert broad.policy_evidence_source is None
    # SARIF emits exactly one location, not two duplicates.
    sarif = _json.loads((tmp_path / "out" / "report.sarif").read_text(encoding="utf-8"))
    matches = [
        r for r in sarif["runs"][0]["results"] if r["ruleId"] == "SHIP-AUTH-MANIFEST-BROAD-SCOPE"
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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: dangerous.write
          source_id: tools
      handoffs: []
      reason: reviewed test binding
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
    approval = next(f for f in report.findings if f.check_id == "SHIP-POLICY-APPROVAL-MISSING")
    assert approval.source is not None
    # Tool source intact: the MCP loader sets source.type to the tool
    # source type so the reviewer can jump to the tool definition.
    assert approval.source.type == "mcp"
    # Manifest evidence pointer added: structured fields point at the
    # exact YAML line where the missing-policy declaration belongs.
    assert approval.policy_evidence_source is not None
    assert approval.policy_evidence_source.type == "manifest"
    assert approval.policy_evidence_source.pointer == ("/policies/require_approval_for_tools")
    assert approval.policy_evidence_source.path == "shipgate.yaml"
    # The pointer doesn't resolve (the manifest doesn't declare the
    # block), so ``start_line`` is None — the reviewer still gets the
    # pointer string and the manifest filename for orientation.
    assert approval.policy_evidence_source.start_line is None
    assert approval.capability_refs
    assert approval.capability_policy_evidence is not None
    assert approval.capability_policy_evidence.capability_id in approval.capability_refs
    assert approval.capability_policy_evidence.identity["tool_name"] == "dangerous.write"
    assert approval.capability_policy_evidence.matched_predicates["missing_approval_policy"] is True


def test_mixed_name_does_not_launder_keyword_effect_into_policy_blocker(tmp_path):
    """A risky name raises uncertainty but cannot create a hard control finding."""

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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: get_or_delete_record
          source_id: api
      handoffs: []
      reason: reviewed test binding
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
    assert "SHIP-POLICY-APPROVAL-MISSING" not in findings
    assert "SHIP-POLICY-CONFIRMATION-MISSING" not in findings
    action = report.action_surface_facts.actions[0]
    assert action.semantic_assessment is not None
    assert action.semantic_assessment.effect.status == "inferred"
    assert action.effect == "destructive"
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    assert any(
        gap.kind == "inferred_effect_only"
        for gap in report.release_decision.evidence_coverage.evidence_gaps
    )


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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: dangerous.write
          source_id: tools
      handoffs: []
      reason: reviewed test binding
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
    approval = next(f for f in report.findings if f.check_id == "SHIP-POLICY-APPROVAL-MISSING")
    assert approval.policy_evidence_source is not None
    assert approval.policy_evidence_source.pointer == ("/policies/require_approval_for_tools")
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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: docs.lookup
          source_id: tools
      handoffs: []
      reason: reviewed test binding
action_surface:
  actions:
    - tool: docs.lookup
      source_id: tools
      effect: read
      authority:
        mode: none
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

    placeholder_warnings = [warning for warning in report.source_warnings if "CHANGE_ME" in warning]
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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: docs.short
          source_id: tools
      handoffs: []
      reason: reviewed test binding
action_surface:
  actions:
    - tool: docs.short
      source_id: tools
      effect: read
      authority:
        mode: none
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

    assert baseline.schema_version == "0.8"
    assert baseline.tool_surface_facts is not None
    assert baseline.action_surface_facts is not None
    assert first_report.run_id == second_report.run_id
    # Baseline matching cannot waive the fixture's intentional semantic gaps.
    assert exit_code == 20
    assert second_report.baseline is not None
    assert second_report.baseline.matched_count > 0
    assert second_report.baseline.new_count == 0
    assert (
        second_report.release_decision.evidence_coverage.semantic_coverage.gap_count > 0
    )
    assert second_report.tool_surface_diff.enabled is True
    assert second_report.tool_surface_diff.base.kind == "baseline"
    assert all(finding.baseline_status in {None, "matched"} for finding in second_report.findings)


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


def test_pre_v029_diff_reference_requires_regeneration_instead_of_effect_deltas(
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "process_order",
                        "description": "Process an order using reviewed inputs.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def write_manifest(effect: str) -> None:
        (project / "shipgate.yaml").write_text(
            f"""version: "0.1"
project: {{name: legacy-diff-compat}}
agent:
  name: legacy-diff-agent
  declared_purpose: [process reviewed orders]
environment: {{target: local}}
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: process_order
          source_id: tools
      handoffs: []
      reason: reviewed test binding
action_surface:
  actions:
    - tool: process_order
      effect: {effect}
      authority:
        mode: none
""",
            encoding="utf-8",
        )

    write_manifest("read")
    base_report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert base_report.release_decision is not None
    assert base_report.release_decision.decision == "passed"

    base_path = tmp_path / "base" / "report.json"
    payload = json.loads(base_path.read_text(encoding="utf-8"))
    payload["report_schema_version"] = "0.28"
    payload["action_surface_facts"]["actions"][0]["effect"] = "read"
    base_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    write_manifest("write")
    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=base_path,
        packet_enabled=False,
    )

    assert exit_code == 0
    assert report.action_surface_facts.actions[0].effect == "write"
    assert report.action_surface_diff.enabled is False
    assert report.action_surface_diff.summary.effect_escalations == 0
    assert report.tool_surface_diff.enabled is False
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    warning = next(
        item for item in report.source_warnings if "not comparable with --diff-from" in item
    )
    assert "uses report schema 0.28" in warning
    assert "agents-shipgate scan -c shipgate.yaml --format json" in warning
    gap = next(
        item
        for item in report.release_decision.evidence_coverage.evidence_gaps
        if item.subject == warning
    )
    assert gap.next_action.kind == "provide_source"
    # No machine-readable command, deliberately. The warning prose above scopes
    # the one-liner to "its source workspace"; `next_action.command` carries no
    # such qualification and reaches `fix_task.allowed_repairs`, where running
    # it here drops --diff-from and clears the very row it was meant to answer
    # (PR #404 review 2). The two steps live in `expects`.
    assert gap.next_action.command is None
    assert gap.next_action.path == "--diff-from"
    assert "base source workspace" in gap.next_action.expects
    assert "without --diff-from" in gap.next_action.expects


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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: support.lookup
          source_id: tools
      handoffs: []
      reason: reviewed test binding
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
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "        - tool: support.lookup\n          source_id: tools\n",
            "        - tool: support.lookup\n"
            "          source_id: tools\n"
            "        - tool: billing.create_refund\n"
            "          source_id: tools\n",
        ),
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
    # `subject` is a display label — `name [provider]` — and identity travels
    # in `subject_id`. This used to assert the label *was* the canonical id, a
    # 64-hex digest that reaches the CLI's `Improve evidence:` line and the
    # GitHub step summary verbatim.
    action = next(
        action
        for action in report.action_surface_facts.actions
        if action.tool_name == "send_email_preview"
    )
    assert any(
        gap.kind == "inferred_policy_applicability"
        and gap.subject == f"{action.tool_name} [{action.provider}]"
        and gap.subject_id == action.tool_id
        for gap in report.policy_evidence_gaps
    )


def test_manual_risk_override_sets_tags_and_owner(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    refund_tool = next(
        item for item in report.tool_inventory if item["name"] == "stripe.create_refund"
    )

    assert refund_tool["owner"] == "payments-platform"
    assert "financial_action" in refund_tool["risk_tags"]
    assert "external_write" in refund_tool["risk_tags"]


def test_same_name_tools_from_different_providers_remain_distinct(tmp_path):
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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: shared.lookup
          source_id: api
        - tool: shared.lookup
          source_id: mcp
      handoffs: []
      reason: reviewed provider-specific test bindings
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.tool_surface.total_tools == 2
    rows = {row["provider"]: row for row in report.tool_inventory}
    assert rows["api"]["auth_scopes"] == []
    assert rows["api"]["owner"] is None
    assert rows["mcp"]["auth_scopes"] == ["shared:read"]
    assert rows["mcp"]["owner"] == "support-platform"
    assert len({row["tool_id"] for row in rows.values()}) == 2
    assert not any("Duplicate tool name" in warning for warning in report.source_warnings)


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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: ticket.create
          source_id: api
      handoffs: []
      reason: reviewed test binding
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

    assert not any(
        finding.check_id == "SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE"
        for finding in report.findings
    )
    assert any(
        gap.kind == "inferred_policy_applicability"
        and gap.why.startswith("SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE:")
        for gap in report.policy_evidence_gaps
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
        '''
from pathlib import Path
Path("imported.txt").write_text("executed")

def function_tool(fn):
    return fn

@function_tool
def harmless(name: str) -> str:
    \"\"\"Return a harmless greeting.\"\"\"
    return name
''',
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
    pp_idx = next(
        (i for i, w in enumerate(warnings) if "missing-pack.yaml" in w and "failed to load" in w),
        None,
    )

    assert not any("Duplicate tool name" in warning for warning in warnings)
    assert pp_idx is not None, (
        f"Expected an optional-policy-pack warning in source_warnings; got: {warnings}"
    )


# --- v0.26 suggested-inventory artifact -------------------------------------

LANGCHAIN_SAMPLE = Path("samples/simple_langchain_agent/shipgate.yaml")


def _write_unreviewed_langchain_project(root: Path) -> Path:
    project = root / "unreviewed-langchain"
    project.mkdir()
    (project / "agent.py").write_text(
        '''
from langchain_core.tools import tool

@tool
def lookup_case(case_id: str) -> dict:
    """Look up read-only support case metadata."""
    return {"case_id": case_id}

agent = create_agent(model=None, tools=[lookup_case])
''',
        encoding="utf-8",
    )
    config = project / "shipgate.yaml"
    config.write_text(
        """
version: "0.1"
project:
  name: unreviewed-langchain
agent:
  name: support-reader
  declared_purpose: [read support metadata]
environment:
  target: local
tool_sources:
  - id: langchain
    type: langchain
    path: agent.py
""",
        encoding="utf-8",
    )
    return config


def test_scan_writes_suggested_inventory_for_low_confidence_tools(tmp_path):
    config = _write_unreviewed_langchain_project(tmp_path)
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    gaps = report.release_decision.evidence_coverage.evidence_gaps
    assert any(gap.kind == "low_confidence_tool" for gap in gaps)

    skeleton_path = tmp_path / "suggested-inventory.json"
    assert skeleton_path.exists()
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    assert "note" in skeleton
    names = [entry["name"] for entry in skeleton["tools"]]
    assert names == sorted(names)
    low_confidence_subjects = {gap.subject for gap in gaps if gap.kind == "low_confidence_tool"}
    assert set(names) == {
        subject.rsplit(" [", 1)[0] for subject in low_confidence_subjects
    }
    # Every entry has at least a name and a non-empty description.
    for entry in skeleton["tools"]:
        assert entry["name"]
        assert entry["description"]


def test_suggested_inventory_loads_as_mcp_inventory(tmp_path):
    """The skeleton must round-trip through the same loader every
    ``tool_inventories`` manifest key uses."""
    from agents_shipgate.inputs.mcp import load_mcp_tools
    from agents_shipgate.schemas.manifest import ToolSourceConfig

    config = _write_unreviewed_langchain_project(tmp_path)
    run_scan(
        config_path=config,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    loaded = load_mcp_tools(
        ToolSourceConfig(id="suggested", type="mcp", path="suggested-inventory.json"),
        tmp_path,
    )
    assert loaded.tools, "skeleton should load as a non-empty inventory"


def test_scan_writes_no_suggested_inventory_when_confidence_is_high(tmp_path):
    run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert not (tmp_path / "suggested-inventory.json").exists()


def test_openapi_action_id_collision_degrades_instead_of_crashing(tmp_path):
    """A valid third-party OpenAPI spec with two operations that normalize
    to the same ``method + path`` must never crash a scan with a hard
    Config error. Regression for the block/goose miner finding: two GET
    operations on ``/sessions/{session_id}`` (one a trailing-slash variant)
    collapsed to one ``action_id`` and hard-failed the duplicate-id guard.
    Same fail-soft principle as the symlink-loop (#212) and MCP-as-tools
    (#214) fixes: degrade to a source_warning, keep both operations.
    """
    project = tmp_path / "project"
    project.mkdir()
    # Two GET operations whose paths normalize identically (the second has
    # a trailing slash), each with a distinct operationId — exactly the
    # shape goose's spec ships.
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Goose-like API
  version: "1.0"
paths:
  /sessions/{session_id}:
    get:
      operationId: get_session
      summary: Get a session.
      parameters:
        - name: session_id
          in: path
          required: true
          schema: {type: string}
      responses:
        "200":
          description: ok
  /sessions/{session_id}/:
    get:
      operationId: get_session_detail
      summary: Get a session detail.
      parameters:
        - name: session_id
          in: path
          required: true
          schema: {type: string}
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
  name: goose-collision
agent:
  name: goose-agent
  declared_purpose:
    - test action_id collision degradation
environment:
  target: local
tool_sources:
  - id: goose-api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: get_session
          source_id: goose-api
        - tool: get_session_detail
          source_id: goose-api
      handoffs: []
      reason: reviewed collision test bindings
""",
        encoding="utf-8",
    )

    # Must not raise ConfigError — the scan completes and returns a report.
    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    # advisory mode never fails the build; in particular it is not the
    # config-error exit code.
    assert exit_code == 0
    # Both operations survive as distinct actions (one disambiguated).
    action_ids = {action.action_id for action in report.action_surface_facts.actions}
    assert len(action_ids) == 2
    assert {a.tool_name for a in report.action_surface_facts.actions} == {
        "get_session",
        "get_session_detail",
    }
    # Provider-scoped v2 identities prevent the former normalized-path
    # collision, so no fail-soft collision warning is needed.
    assert not any(
        "Duplicate action_surface action_id" in warning for warning in report.source_warnings
    )


def test_diff_from_report_with_duplicate_base_action_ids_degrades(tmp_path):
    """A ``--diff-from`` base report whose round-tripped
    ``action_surface_facts`` carry duplicate ``action_id`` values (written
    by a pre-collision-fix engine) must not crash the scan with a hard
    Config error pointing at a manifest that is fine. Same fail-soft
    principle as the build-time collision fix (#226): degrade to a
    source_warning, keep the diff enabled."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Goose-like API
  version: "1.0"
paths:
  /sessions/{session_id}:
    get:
      operationId: get_session
      summary: Get a session.
      parameters:
        - name: session_id
          in: path
          required: true
          schema: {type: string}
      responses:
        "200":
          description: ok
  /sessions/{session_id}/:
    get:
      operationId: get_session_detail
      summary: Get a session detail.
      parameters:
        - name: session_id
          in: path
          required: true
          schema: {type: string}
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
  name: goose-collision
agent:
  name: goose-agent
  declared_purpose:
    - test duplicate base action ids
environment:
  target: local
tool_sources:
  - id: goose-api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: get_session
          source_id: goose-api
        - tool: get_session_detail
          source_id: goose-api
      handoffs: []
      reason: reviewed collision test bindings
""",
        encoding="utf-8",
    )

    _, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "base-reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert exit_code == 0
    base_report_path = tmp_path / "base-reports" / "report.json"
    payload = json.loads(base_report_path.read_text(encoding="utf-8"))
    actions = payload["action_surface_facts"]["actions"]
    assert len(actions) == 2
    # Simulate the pre-fix serialization: both operations collapsed onto
    # the bare collided id.
    bare_id = min((action["action_id"] for action in actions), key=len)
    for action in actions:
        action["action_id"] = bare_id
    stale_base_path = tmp_path / "stale-base-report.json"
    stale_base_path.write_text(json.dumps(payload), encoding="utf-8")

    # Must not raise ConfigError — the scan completes and returns a report.
    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "head-reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
        diff_from_path=stale_base_path,
    )

    assert exit_code == 0
    assert report.action_surface_diff.enabled
    assert any(
        "base reference" in warning and "Duplicate action_surface action_id" in warning
        for warning in report.source_warnings
    ), f"Expected a base-side collision warning; got: {report.source_warnings}"


def test_scan_reports_a_manifest_type_mismatch_as_a_config_error(tmp_path):
    """The reproduction from #387, on the command it was reported against.

    A mapping where ``google_adk.tool_inventories`` expects a list raised
    ``TypeError`` inside a Pydantic validator. Pydantic converts
    ``ValueError`` and ``AssertionError`` into a ``ValidationError`` but lets
    ``TypeError`` propagate, so the manifest typo escaped the config-loading
    boundary and surfaced as ``internal_error`` with "this is a bug — please
    file an issue".
    """

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        'version: "0.1"\n'
        "project:\n  name: repro\n"
        "agent:\n  name: repro-agent\n"
        "environment: dev\n"
        "google_adk:\n  tool_inventories:\n    adk_agent: tool-inventory.json\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["scan", "--config", str(manifest), "--format", "json"],
        env={"AGENTS_SHIPGATE_AGENT_MODE": "1"},
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["error"] == "config_error"
    assert "google_adk.tool_inventories" in payload["message"]
    assert "file an issue" not in json.dumps(payload)
    assert payload["next_actions"][0]["kind"] == "edit"
    assert payload["next_actions"][0]["path"] == str(manifest)
