import json
from pathlib import Path

import pytest
from jsonschema import validate

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.report.sarif import SarifSourcePaths, render_sarif_report
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import (
    EvidenceGap,
    EvidenceGapAction,
    Finding,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)

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
SUPPORT_REFUND_SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


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


def test_sarif_tags_action_surface_release_blockers():
    report = _report_with_findings(
        [
            Finding(
                check_id="SHIP-ACTION-POLICY-VIOLATION",
                title="Action policy failed",
                severity="medium",
                category="action_surface",
                tool_name="send_customer_email",
                evidence={"policy_id": "require-audit"},
                recommendation="Declare audit evidence.",
                blocks_release=True,
            )
        ]
    )

    payload = render_sarif_report(report)
    rule = payload["runs"][0]["tool"]["driver"]["rules"][0]
    result = payload["runs"][0]["results"][0]

    assert rule["properties"]["tags"] == ["action_surface", "release_blocker"]
    assert result["properties"]["tags"] == ["action_surface", "release_blocker"]


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


def test_sarif_output_stays_findings_only_for_capability_diff_reports(tmp_path):
    run_scan(
        config_path=SUPPORT_REFUND_SAMPLE,
        output_dir=tmp_path,
        formats=["sarif"],
        ci_mode="advisory",
    )

    sarif_text = (tmp_path / "report.sarif").read_text(encoding="utf-8")

    assert "capability_facts" not in sarif_text
    assert "declared_intentions" not in sarif_text
    assert "misalignments" not in sarif_text
    assert "release_consequence" not in sarif_text
    assert "suggested_scenarios" not in sarif_text
    assert "tool_surface_facts" not in sarif_text
    assert "tool_surface_diff" not in sarif_text
    assert "samples/support_refund_agent/specs/support-tools.openapi.yaml" in sarif_text


@pytest.mark.parametrize(("source_path", "uri", "encoding"), [
    (".github/workflows/ci.yml", ".github/workflows/ci.yml", None),
    (".github/workflows/ci%20draft.yml", ".github/workflows/ci%2520draft.yml", "percent"),
    ("https://example.test/ci%20draft.yml", "https://example.test/ci%20draft.yml", None),
])
def test_sarif_keeps_repository_relative_changed_files(
    tmp_path: Path, source_path: str, uri: str, encoding: str | None,
) -> None:
    report = _report_with_findings(
        [
            Finding(
                check_id="SHIP-VERIFY-TRUST-ROOT-CHANGED",
                title="Trust root changed",
                severity="high",
                category="verify",
                recommendation="Review the change.",
                source=SourceReference(type="changed_file", path=source_path),
            )
        ]
    )
    paths = SarifSourcePaths("nested/shipgate.yaml", tmp_path)

    payload = render_sarif_report(report, source_paths=paths)

    location = payload["runs"][0]["results"][0]["locations"][0]
    assert location["physicalLocation"]["artifactLocation"]["uri"] == uri
    assert location["properties"].get("shipgateArtifactUriEncoding") == encoding


@pytest.mark.parametrize(
    ("source_ref", "expected_uri", "expected_line"),
    [
        ("tools.py:7", "nested/tools.py", 7),
        ("tools.py:²", "nested/shipgate.yaml", None),
        ("tools.py:0", "nested/shipgate.yaml", None),
        ("tools # draft.py", "nested/tools%20%23%20draft.py", None),
        ("tools # draft.py:7#selector", "nested/tools%20%23%20draft.py", 7),
    ],
)
def test_sarif_semantic_gap_links_to_local_source_and_preserves_line(
    tmp_path: Path, source_ref: str, expected_uri: str, expected_line: int | None
):
    for name in ("tools.py", "tools # draft.py"):
        (tmp_path / name).write_text("# source\n", encoding="utf-8")
    paths = SarifSourcePaths("nested/shipgate.yaml", tmp_path.resolve())
    gap = EvidenceGap(
        kind="missing_effect_evidence",
        subject="tool",
        source_ref=source_ref,
        why="Effect evidence is missing.",
        next_action=EvidenceGapAction(
            kind="declare_action_effect", why="Effect evidence is missing.", expects="Review effect."
        ),
    )

    source = paths.gap_location(gap)

    assert paths.artifact_uri(source.path, source.type) == expected_uri
    assert source.start_line == expected_line


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
