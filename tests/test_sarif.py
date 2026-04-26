import json

from jsonschema import validate

from agents_shipgate.core.models import (
    Finding,
    ReadinessReport,
    ReportSummary,
    SourceReference,
    ToolSurfaceSummary,
)
from agents_shipgate.report.sarif import render_sarif_report

MINIMAL_SARIF_SCHEMA = {
    "type": "object",
    "required": ["version", "runs"],
    "properties": {
        "version": {"const": "2.1.0"},
        "runs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tool", "results"],
                "properties": {
                    "tool": {
                        "type": "object",
                        "required": ["driver"],
                    },
                    "results": {"type": "array"},
                },
            },
        },
    },
}


def test_sarif_uses_canonical_rule_metadata_and_help_uri():
    report = _report_with_findings(
        [
            Finding(
                check_id="SHIP-AUTH-MISSING-SCOPE",
                title="payment.write lacks declared auth scopes",
                severity="high",
                category="auth",
                tool_name="payment.write",
                evidence={"risk_tags": ["write"]},
                recommendation="Declare scopes for payment.write.",
            )
        ]
    )

    payload = render_sarif_report(report)
    rule = payload["runs"][0]["tool"]["driver"]["rules"][0]

    assert rule["id"] == "SHIP-AUTH-MISSING-SCOPE"
    assert rule["shortDescription"]["text"] == "Scope-requiring tool lacks declared auth scopes."
    assert rule["shortDescription"]["text"] != "payment.write lacks declared auth scopes"
    assert rule["helpUri"].endswith("docs/checks.md#ship-auth-missing-scope")


def test_sarif_summarizes_large_evidence_payloads():
    report = _report_with_findings(
        [
            Finding(
                check_id="SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE",
                title="Production target includes low-confidence tool extraction",
                severity="high",
                category="inventory",
                evidence={"tools": [f"tool_{index}" for index in range(50)]},
                recommendation="Declare tools through higher-confidence inputs.",
                source=SourceReference(type="manifest", ref="shipgate.yaml"),
            )
        ]
    )

    payload = render_sarif_report(report)
    evidence = payload["runs"][0]["results"][0]["properties"]["evidence"]

    assert evidence["tools"]["count"] == 50
    assert len(evidence["tools"]["sample"]) == 20
    assert len(json.dumps(payload)) < 10000


def test_sarif_output_matches_minimal_sarif_shape():
    report = _report_with_findings(
        [
            Finding(
                check_id="SHIP-INVENTORY-NOT-ENUMERABLE",
                title="Tool surface cannot be enumerated",
                severity="high",
                category="inventory",
                evidence={"tool_sources": ["empty"]},
                recommendation="Declare a tool source.",
            )
        ]
    )

    validate(instance=render_sarif_report(report), schema=MINIMAL_SARIF_SCHEMA)


def _report_with_findings(findings: list[Finding]) -> ReadinessReport:
    return ReadinessReport(
        run_id="test",
        project={"name": "test"},
        agent={"name": "agent"},
        environment={"target": "local"},
        summary=ReportSummary(status="warnings_detected", high_count=len(findings)),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=findings,
    )
