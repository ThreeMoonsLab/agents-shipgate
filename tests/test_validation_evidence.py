from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import validate

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.config.schema import (
    ArtifactPathConfig,
    ValidationConfig,
    ValidationEvidenceConfig,
)
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.findings import finding_fingerprint
from agents_shipgate.core.models import Finding
from agents_shipgate.inputs.validation import load_validation_artifacts
from agents_shipgate.report.json_report import report_json_payload

SAMPLE = Path("samples/hitl_evidence_agent/shipgate.yaml")
COVERED_SAMPLE = Path("samples/hitl_evidence_covered_agent/shipgate.yaml")
REPORT_SOURCE_PROVENANCE_KEYS = {
    "path",
    "start_line",
    "end_line",
    "start_column",
    "pointer",
}


def test_validation_manifest_block_accepts_defaults(tmp_path):
    _write_tools(tmp_path)
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: validation-defaults
agent:
  name: validation-agent
  declared_purpose:
    - review refunds
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
validation:
  mode: human_in_the_loop
""",
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.validation is not None
    assert manifest.validation.target_review_posture == "recommendation_only"
    assert manifest.validation.required_evidence.approval_trace_required is False
    assert manifest.validation.required_evidence.override_reason_required is False
    assert (
        manifest.validation.required_evidence.high_risk_auto_approval_exclusion_required
        is False
    )


def test_validation_manifest_rejects_unknown_keys(tmp_path):
    _write_tools(tmp_path)
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: validation-unknown
agent:
  name: validation-agent
  declared_purpose:
    - review refunds
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
validation:
  mode: human_in_the_loop
  target_review_posture: limited_auto_approval
  evidence_typo: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="evidence_typo"):
        load_manifest(manifest_path)


def test_validation_loader_parses_supported_artifacts(tmp_path):
    validation_dir = tmp_path / "validation"
    validation_dir.mkdir()
    (validation_dir / "approval-traces.jsonl").write_text(
        '{"tool_name":"issue_refund","approved":true,"success":true}\n',
        encoding="utf-8",
    )
    (validation_dir / "override-log.jsonl").write_text(
        '{"tool_name":"issue_refund","action":"override","reason":"manager review"}\n',
        encoding="utf-8",
    )
    (validation_dir / "high-risk-exclusions.yaml").write_text(
        """
high_risk_auto_approval_exclusions:
  - tool: issue_refund
    reason: financial action remains manual
    owner: support-ops
""",
        encoding="utf-8",
    )
    (validation_dir / "promotion-criteria.yaml").write_text(
        """
target_review_posture: limited_auto_approval
required_evidence:
  approval_trace_required: true
  override_reason_required: true
  high_risk_auto_approval_exclusion_required: true
""",
        encoding="utf-8",
    )
    config = ValidationConfig(
        mode="human_in_the_loop",
        target_review_posture="limited_auto_approval",
        evidence=ValidationEvidenceConfig(
            approval_traces=[ArtifactPathConfig(path="validation/approval-traces.jsonl")],
            override_logs=[ArtifactPathConfig(path="validation/override-log.jsonl")],
            high_risk_exclusions=[
                ArtifactPathConfig(path="validation/high-risk-exclusions.yaml")
            ],
            promotion_criteria=[
                ArtifactPathConfig(path="validation/promotion-criteria.yaml")
            ],
        ),
    )

    artifacts = load_validation_artifacts(config, tmp_path)

    assert artifacts is not None
    assert artifacts.approval_traces == [
        {"tool_name": "issue_refund", "approved": True, "success": True}
    ]
    assert artifacts.override_events == [
        {
            "tool_name": "issue_refund",
            "action": "override",
            "reason": "manager review",
        }
    ]
    assert artifacts.high_risk_auto_approval_exclusions[0]["tool"] == "issue_refund"
    assert artifacts.promotion_criteria[0]["target_review_posture"] == (
        "limited_auto_approval"
    )


def test_validation_loader_rejects_wrong_declarative_shape(tmp_path):
    (tmp_path / "exclusions.jsonl").write_text("[]\n", encoding="utf-8")
    config = ValidationConfig(
        mode="human_in_the_loop",
        evidence=ValidationEvidenceConfig(
            high_risk_exclusions=[ArtifactPathConfig(path="exclusions.jsonl")]
        ),
    )

    with pytest.raises(InputParseError, match="high-risk exclusions"):
        load_validation_artifacts(config, tmp_path)


def test_validation_loader_optional_malformed_jsonl_warns(tmp_path):
    (tmp_path / "override-log.jsonl").write_text("{bad-json}\n", encoding="utf-8")
    config = ValidationConfig(
        mode="human_in_the_loop",
        evidence=ValidationEvidenceConfig(
            override_logs=[
                ArtifactPathConfig(path="override-log.jsonl", optional=True)
            ]
        ),
    )

    artifacts = load_validation_artifacts(config, tmp_path)

    assert artifacts is not None
    assert artifacts.override_events == []
    assert any(
        warning.startswith("validation: optional override log")
        for warning in artifacts.warnings
    )
    assert artifacts.source_provenance[0].status == "source_load_failed"
    assert artifacts.source_provenance[0].type == "override_log"


def test_source_provenance_does_not_change_fingerprint():
    base = Finding(
        check_id="SHIP-EVIDENCE-APPROVAL-TRACE-MISSING",
        title="No local approval trace evidence found for issue_refund",
        severity="high",
        category="evidence",
        tool_name="issue_refund",
        evidence={
            "tool_name": "issue_refund",
            "required": "approval_trace_required",
            "reason": "file_missing",
            "trace_files": [],
            "approved_tools": [],
        },
        recommendation="Add local approval trace evidence.",
    )
    with_provenance = base.model_copy(deep=True)
    with_provenance.evidence["source_provenance"] = [
        {
            "type": "approval_trace",
            "ref": "shipgate.yaml",
            "location": "shipgate.yaml#/validation/evidence/approval_traces",
            "status": "expected_but_absent",
            "detail": "no local approval trace source declared",
        }
    ]

    assert finding_fingerprint(with_provenance) == finding_fingerprint(base)


def test_optional_missing_validation_source_adds_load_failed_provenance(tmp_path):
    _write_project(
        tmp_path,
        validation_block="""
validation:
  mode: human_in_the_loop
  required_evidence:
    approval_trace_required: true
  evidence:
    approval_traces:
      - path: validation/missing-approval-traces.jsonl
        optional: true
""",
    )

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    finding = next(
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING"
    )
    assert exit_code == 0
    assert finding.evidence["reason"] == "file_missing"
    assert any("optional approval trace" in warning for warning in report.source_warnings)
    provenance = finding.evidence["source_provenance"]
    assert any(
        item["type"] == "approval_trace"
        and item["status"] == "source_load_failed"
        for item in provenance
    )


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("approval_traces", "../outside.jsonl"),
        ("override_logs", "../outside.jsonl"),
        ("high_risk_exclusions", "../outside.yaml"),
        ("promotion_criteria", "../outside.yaml"),
    ],
)
def test_validation_loader_containment_for_each_evidence_path(tmp_path, field, path):
    project = tmp_path / "project"
    project.mkdir()
    config = ValidationConfig(
        mode="human_in_the_loop",
        evidence=ValidationEvidenceConfig(**{field: [ArtifactPathConfig(path=path)]}),
    )

    with pytest.raises(InputParseError, match="resolves outside manifest directory"):
        load_validation_artifacts(config, project)


def test_empty_stream_evidence_reports_distinct_reasons(tmp_path):
    _write_project(
        tmp_path,
        validation_block="""
validation:
  mode: human_in_the_loop
  required_evidence:
    approval_trace_required: true
    override_reason_required: true
  evidence:
    approval_traces:
      - path: validation/approval-traces.jsonl
    override_logs:
      - path: validation/override-log.jsonl
""",
    )
    validation_dir = tmp_path / "validation"
    validation_dir.mkdir()
    (validation_dir / "approval-traces.jsonl").write_text("", encoding="utf-8")
    (validation_dir / "override-log.jsonl").write_text("", encoding="utf-8")

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    approval = next(
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING"
    )
    override = next(
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING"
    )
    assert exit_code == 0
    assert approval.evidence["reason"] == "no_trace_events"
    assert (
        approval.title
        == "Loaded local approval trace evidence has no recorded events for issue_refund"
    )
    assert approval.evidence["trace_files"] == ["validation/approval-traces.jsonl"]
    assert override.evidence["reason"] == "no_override_events"
    assert override.title == "Loaded local override evidence has no recorded events"
    assert override.evidence["events_missing_reason"] == []


def test_validation_evidence_covered_case_has_no_evidence_findings(tmp_path):
    _write_project(
        tmp_path,
        validation_block="""
validation:
  mode: human_in_the_loop
  target_review_posture: limited_auto_approval
  required_evidence:
    approval_trace_required: true
    override_reason_required: true
    high_risk_auto_approval_exclusion_required: true
  evidence:
    approval_traces:
      - path: validation/approval-traces.jsonl
    override_logs:
      - path: validation/override-log.jsonl
    high_risk_exclusions:
      - path: validation/high-risk-exclusions.yaml
    promotion_criteria:
      - path: validation/promotion-criteria.yaml
""",
    )
    validation_dir = tmp_path / "validation"
    validation_dir.mkdir()
    (validation_dir / "approval-traces.jsonl").write_text(
        '{"tool_name":"issue_refund","approved":true}\n',
        encoding="utf-8",
    )
    (validation_dir / "override-log.jsonl").write_text(
        '{"tool_name":"issue_refund","action":"override","reason":"ops review"}\n',
        encoding="utf-8",
    )
    (validation_dir / "high-risk-exclusions.yaml").write_text(
        """
high_risk_auto_approval_exclusions:
  - tool: issue_refund
    reason: financial action remains manual
""",
        encoding="utf-8",
    )
    (validation_dir / "promotion-criteria.yaml").write_text(
        """
target_review_posture: limited_auto_approval
required_evidence:
  approval_trace_required: true
  override_reason_required: true
  high_risk_auto_approval_exclusion_required: true
""",
        encoding="utf-8",
    )

    reports_dir = tmp_path / "reports"
    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=reports_dir,
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    assert not any(finding.check_id.startswith("SHIP-EVIDENCE-") for finding in report.findings)
    packet = json.loads((reports_dir / "packet.json").read_text(encoding="utf-8"))
    assert packet["human_in_the_loop"]["status"] == "covered"


def test_high_risk_exclusion_does_not_duplicate_missing_approval_policy(tmp_path):
    _write_project(
        tmp_path,
        validation_block="""
validation:
  mode: human_in_the_loop
  required_evidence:
    high_risk_auto_approval_exclusion_required: true
""",
        include_approval_policy=False,
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    check_ids = {finding.check_id for finding in report.findings}

    assert "SHIP-POLICY-APPROVAL-MISSING" in check_ids
    assert "SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING" not in check_ids


def test_hitl_evidence_sample_reports_expected_findings_and_packet(tmp_path):
    report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json", "sarif"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)
    schema = json.loads(Path("docs/report-schema.v0.11.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["summary"]["critical_count"] == 0
    assert payload["release_decision"]["decision"] == "review_required"
    assert {
        "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING",
        "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING",
        "SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING",
        "SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING",
    } <= {finding["check_id"] for finding in payload["findings"]}
    validate(instance=payload, schema=schema)

    packet = json.loads((tmp_path / "packet.json").read_text(encoding="utf-8"))
    hitl_checks = {item["check_id"] for item in packet["human_in_the_loop"]["trace_findings"]}
    assert "SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING" in hitl_checks
    assert "HITL evidence gaps" in (tmp_path / "packet.md").read_text(encoding="utf-8")
    override_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING"
    )
    assert override_finding["evidence"]["events_missing_reason"] == []
    assert all(
        REPORT_SOURCE_PROVENANCE_KEYS.isdisjoint(item)
        for finding in payload["findings"]
        for item in finding["evidence"].get("source_provenance", [])
    )
    assert all(
        REPORT_SOURCE_PROVENANCE_KEYS.isdisjoint(item)
        for item in packet["human_in_the_loop"]["source_provenance"]
    )

    sarif = json.loads((tmp_path / "report.sarif").read_text(encoding="utf-8"))
    rule_ids = {
        rule["id"]
        for run in sarif["runs"]
        for rule in run["tool"]["driver"]["rules"]
    }
    assert "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING" in rule_ids


def test_hitl_evidence_covered_sample_reports_provenance(tmp_path):
    report, exit_code = run_scan(
        config_path=COVERED_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    assert not any(finding.check_id.startswith("SHIP-EVIDENCE-") for finding in report.findings)
    packet = json.loads((tmp_path / "packet.json").read_text(encoding="utf-8"))
    hitl = packet["human_in_the_loop"]
    assert hitl["status"] == "covered"
    assert hitl["provenance_mode"] == "fresh_scan"
    assert hitl["runtime_control_disclaimer"].startswith("HITL evidence is local")
    provenance = hitl["source_provenance"]
    assert {
        "approval_trace",
        "override_log",
        "high_risk_exclusion",
        "promotion_criteria",
        "manifest_requirement",
    } <= {item["type"] for item in provenance}
    assert all(not item["ref"].startswith("/") for item in provenance)
    assert all(REPORT_SOURCE_PROVENANCE_KEYS.isdisjoint(item) for item in provenance)


def test_hitl_evidence_sample_outputs_are_deterministic(tmp_path):
    out = tmp_path / "reports"
    run_scan(
        config_path=SAMPLE,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
    )
    first_report = (out / "report.json").read_text(encoding="utf-8")
    first_packet = (out / "packet.json").read_text(encoding="utf-8")
    first_packet_md = (out / "packet.md").read_text(encoding="utf-8")
    first_packet_html = (out / "packet.html").read_text(encoding="utf-8")

    run_scan(
        config_path=SAMPLE,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
    )

    assert first_report == (out / "report.json").read_text(encoding="utf-8")
    assert first_packet == (out / "packet.json").read_text(encoding="utf-8")
    assert first_packet_md == (out / "packet.md").read_text(encoding="utf-8")
    assert first_packet_html == (out / "packet.html").read_text(encoding="utf-8")


def test_hitl_evidence_covered_packet_ignores_source_mtime(tmp_path):
    out = tmp_path / "reports"
    run_scan(
        config_path=COVERED_SAMPLE,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
    )
    first_packet = (out / "packet.json").read_text(encoding="utf-8")
    first_packet_md = (out / "packet.md").read_text(encoding="utf-8")
    first_packet_html = (out / "packet.html").read_text(encoding="utf-8")

    source = COVERED_SAMPLE.parent / "validation" / "approval-traces.jsonl"
    stat = source.stat()
    try:
        os.utime(source, (stat.st_atime + 10, stat.st_mtime + 10))
        run_scan(
            config_path=COVERED_SAMPLE,
            output_dir=out,
            formats=["json"],
            ci_mode="advisory",
        )
    finally:
        os.utime(source, (stat.st_atime, stat.st_mtime))

    assert first_packet == (out / "packet.json").read_text(encoding="utf-8")
    assert first_packet_md == (out / "packet.md").read_text(encoding="utf-8")
    assert first_packet_html == (out / "packet.html").read_text(encoding="utf-8")


def _write_tools(path: Path) -> None:
    (path / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "issue_refund",
                        "description": "Issue a customer refund after support review.",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "case_id": {"type": "string"},
                                "amount": {"type": "number", "maximum": 500},
                                "idempotency_key": {"type": "string"},
                            },
                            "required": ["case_id", "amount", "idempotency_key"],
                        },
                        "auth": {"scopes": ["refunds:write"]},
                        "owner": "support-ops",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_project(
    path: Path,
    *,
    validation_block: str,
    include_approval_policy: bool = True,
) -> None:
    _write_tools(path)
    approval_policy = (
        """
  require_approval_for_tools:
    - issue_refund
"""
        if include_approval_policy
        else ""
    )
    (path / "shipgate.yaml").write_text(
        f"""
version: "0.1"
project:
  name: validation-project
agent:
  name: validation-agent
  declared_purpose:
    - prepare refunds for support review
environment:
  target: production_like
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
{validation_block}
permissions:
  scopes:
    - refunds:write
policies:{approval_policy}
  require_idempotency_for_tools:
    - issue_refund
risk_overrides:
  tools:
    issue_refund:
      tags: [financial_action, write]
      owner: support-ops
      reason: refund issuance is a financial write action
""",
        encoding="utf-8",
    )
