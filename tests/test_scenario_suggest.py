import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.scenario import scenario_yaml_payload
from agents_shipgate.core.models import (
    Finding,
    Misalignment,
    ReadinessReport,
    ReportSummary,
    SuggestedScenario,
    ToolSurfaceSummary,
)
from agents_shipgate.report.json_report import report_json_payload

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")
ACTIVE_SCENARIO_SEVERITIES = {"critical", "high", "medium"}

runner = CliRunner()


def _sample_report_path(tmp_path: Path) -> Path:
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return tmp_path / "report.json"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_scenario_suggest_writes_yaml_from_report_scenarios(tmp_path):
    report_path = _sample_report_path(tmp_path)
    out_path = tmp_path / "suggested-scenarios.yaml"

    result = runner.invoke(
        app,
        ["scenario", "suggest", "--from", str(report_path), "--out", str(out_path)],
    )

    assert result.exit_code == 0, result.output
    payload = _load_yaml(out_path)
    rows = payload["scenarios"]
    assert rows
    first = rows[0]
    assert {
        "id",
        "scenario_type",
        "derived_from",
        "finding_id",
        "source_scenario_id",
        "source_misalignment_id",
        "tool",
        "adversarial_goal",
        "expected_control",
    } <= set(first)
    assert "Wrote" in result.output


def test_scenario_suggest_covers_reachable_active_scenario_findings(tmp_path):
    report_path = _sample_report_path(tmp_path)
    out_path = tmp_path / "suggested-scenarios.yaml"
    result = runner.invoke(
        app,
        ["scenario", "suggest", "--from", str(report_path), "--out", str(out_path)],
    )
    assert result.exit_code == 0, result.output

    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = {finding["id"]: finding for finding in report["findings"]}
    misalignments = {item["id"]: item for item in report["misalignments"]}
    reachable_active = set()
    for scenario in report["suggested_scenarios"]:
        for misalignment_id in scenario["source_misalignments"]:
            misalignment = misalignments[misalignment_id]
            for finding_id in misalignment["finding_refs"]:
                finding = findings[finding_id]
                if (
                    not finding["suppressed"]
                    and finding["severity"] in ACTIVE_SCENARIO_SEVERITIES
                ):
                    reachable_active.add(finding_id)

    rows = _load_yaml(out_path)["scenarios"]
    row_finding_ids = {row["finding_id"] for row in rows}
    assert reachable_active == row_finding_ids

    wildcard = next(
        finding
        for finding in report["findings"]
        if finding["check_id"] == "SHIP-INVENTORY-WILDCARD-TOOLS"
    )
    owner = next(
        finding
        for finding in report["findings"]
        if finding["check_id"] == "SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING"
    )
    assert wildcard["id"] in row_finding_ids
    assert owner["id"] in row_finding_ids
    assert any(
        findings[finding_id]["severity"] == "medium"
        for finding_id in row_finding_ids
        if finding_id in findings
    )


def test_scenario_suggest_output_is_reproducible(tmp_path):
    report_path = _sample_report_path(tmp_path)
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"

    for out_path in (first, second):
        result = runner.invoke(
            app,
            ["scenario", "suggest", "--from", str(report_path), "--out", str(out_path)],
        )
        assert result.exit_code == 0, result.output

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_scenario_suggest_default_out_and_empty_state(tmp_path):
    clean_report, _ = run_scan(
        config_path=Path("samples/clean_read_only_agent/shipgate.yaml"),
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert not clean_report.suggested_scenarios

    report_path = tmp_path / "report.json"
    result = runner.invoke(app, ["scenario", "suggest", "--from", str(report_path)])

    assert result.exit_code == 0, result.output
    out_path = tmp_path / "suggested-scenarios.yaml"
    assert out_path.read_text(encoding="utf-8") == "scenarios: []\n"


def test_scenario_suggest_omits_suppressed_but_keeps_baseline_matched(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    payload = report_json_payload(report)
    approval_id = None
    idempotency_id = None
    for finding in payload["findings"]:
        if finding["check_id"] == "SHIP-POLICY-APPROVAL-MISSING":
            finding["suppressed"] = True
            approval_id = finding["id"]
        if finding["check_id"] == "SHIP-SIDEFX-IDEMPOTENCY-MISSING" and finding["severity"] == "critical":
            finding["baseline_status"] = "matched"
            idempotency_id = finding["id"]
    assert approval_id
    assert idempotency_id
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    out_path = tmp_path / "suggested-scenarios.yaml"

    result = runner.invoke(
        app,
        ["scenario", "suggest", "--from", str(report_path), "--out", str(out_path)],
    )

    assert result.exit_code == 0, result.output
    finding_ids = {row["finding_id"] for row in _load_yaml(out_path)["scenarios"]}
    assert approval_id not in finding_ids
    assert idempotency_id in finding_ids


def test_scenario_slug_collisions_suffix_all_colliding_rows():
    report = ReadinessReport(
        run_id="run",
        project={"name": "collision"},
        agent={"name": "agent"},
        environment={"target": "test"},
        summary=ReportSummary(status="warnings_detected", high_count=2),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=1),
        tool_inventory=[
            {
                "name": "billing.refund",
                "source_type": "mcp",
                "risk_tags": ["financial_action"],
                "auth_scopes": [],
                "confidence": "high",
            }
        ],
        findings=[
            Finding(
                id="fp_aaaaaaaaaaaaaaaa",
                fingerprint="fp_aaaaaaaaaaaaaaaa",
                check_id="SHIP-POLICY-APPROVAL-MISSING",
                title="billing.refund lacks approval",
                severity="high",
                category="policy",
                tool_name="billing.refund",
                recommendation="Declare approval.",
            ),
            Finding(
                id="fp_bbbbbbbbbbbbbbbb",
                fingerprint="fp_bbbbbbbbbbbbbbbb",
                check_id="SHIP-POLICY-APPROVAL-MISSING",
                title="billing.refund also lacks approval",
                severity="high",
                category="policy",
                tool_name="billing.refund",
                recommendation="Declare approval.",
            ),
        ],
        misalignments=[
            Misalignment(
                id="mis_a",
                kind="policy_gap",
                severity="high",
                tool_name="billing.refund",
                finding_refs=["fp_aaaaaaaaaaaaaaaa"],
                policy_requirement="approval",
                gap="missing",
                release_implication="blocked",
            ),
            Misalignment(
                id="mis_b",
                kind="policy_gap",
                severity="high",
                tool_name="billing.refund",
                finding_refs=["fp_bbbbbbbbbbbbbbbb"],
                policy_requirement="approval",
                gap="missing",
                release_implication="blocked",
            ),
        ],
        suggested_scenarios=[
            SuggestedScenario(
                id="scn_collision",
                scenario_type="approval",
                title="Approval gate",
                given="Exercise billing.refund.",
                expected_control="Approval is required.",
                source_misalignments=["mis_a", "mis_b"],
                source_findings=[
                    "fp_aaaaaaaaaaaaaaaa",
                    "fp_bbbbbbbbbbbbbbbb",
                ],
            )
        ],
    )

    payload = scenario_yaml_payload(report)
    ids = [row["id"] for row in payload["scenarios"]]

    assert ids == [
        "billing_refund_without_approval_aaaaaaaa",
        "billing_refund_without_approval_bbbbbbbb",
    ]


def test_scenario_suggest_rejects_bad_inputs(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    non_report = tmp_path / "non-report.json"
    non_report.write_text('{"hello": "world"}', encoding="utf-8")
    old_report = tmp_path / "old-report.json"
    old_report.write_text('{"report_schema_version": "0.8"}', encoding="utf-8")
    good_report = _sample_report_path(tmp_path / "sample")

    cases = [
        ["scenario", "suggest", "--from", str(tmp_path / "missing.json")],
        ["scenario", "suggest", "--from", str(bad_json)],
        ["scenario", "suggest", "--from", str(non_report)],
        ["scenario", "suggest", "--from", str(old_report)],
        [
            "scenario",
            "suggest",
            "--from",
            str(good_report),
            "--out",
            str(tmp_path),
        ],
    ]
    for args in cases:
        result = runner.invoke(app, args)
        # input_parse_error → exit 3 (docs/errors.json, docs/trust-model.md).
        assert result.exit_code == 3, (args, result.output)


def test_scenario_suggest_accepts_future_minor_report_schema(tmp_path):
    report_path = _sample_report_path(tmp_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["report_schema_version"] = "0.99"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    out_path = tmp_path / "suggested-scenarios.yaml"

    result = runner.invoke(
        app,
        ["scenario", "suggest", "--from", str(report_path), "--out", str(out_path)],
    )

    assert result.exit_code == 0, result.output
    assert _load_yaml(out_path)["scenarios"]


def test_scenario_suggest_agent_mode_error_includes_next_action(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        ["scenario", "suggest", "--from", str(tmp_path / "missing.json")],
    )

    # input_parse_error → exit 3 (docs/errors.json, docs/trust-model.md).
    assert result.exit_code == 3
    json_lines = [
        line for line in (result.output or "").splitlines() if line.startswith("{")
    ]
    assert json_lines
    payload = json.loads(json_lines[-1])
    assert payload["error"] == "input_parse_error"
    assert payload["next_action"] == (
        "Inspect the error message and adjust --from or --out accordingly."
    )
