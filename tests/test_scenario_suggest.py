"""Tests for `agents-shipgate scenario suggest` and `report.scenario_export`.

Covers the contract from the plan: per-(scenario_type, tool) fan-out,
deterministic output, six-category coverage, severity filtering,
suppressed-finding handling, agent-level scenarios, --strict gate, and
the predicate-parity guarantee with the in-report grouping.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.models import (
    Finding,
    Misalignment,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)
from agents_shipgate.report.capability_diff import (
    apply_capability_diff,
    scenario_type_for_finding,
)
from agents_shipgate.report.scenario_export import (
    coverage_gaps,
    derive_yaml_scenarios,
    dump_json,
    dump_yaml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORT_REFUND_REPORT = REPO_ROOT / "samples/support_refund_agent/expected/report.json"
SIMPLE_OPENAI_REPORT = REPO_ROOT / "samples/simple_openai_api_agent/expected/report.json"
GOLDEN_YAML = REPO_ROOT / "tests/fixtures/scenario_suggest/support_refund_agent.expected.yaml"


def _load(path: Path) -> ReadinessReport:
    return ReadinessReport.model_validate_json(path.read_text(encoding="utf-8"))


def _minimal_report(
    *,
    findings: Iterable[Finding] = (),
    misalignments: Iterable[Misalignment] = (),
) -> ReadinessReport:
    """Construct a minimal report for synthetic tests."""
    return ReadinessReport(
        run_id="test",
        project={"name": "scenario-suggest-test"},
        agent={"name": "test-agent"},
        environment={"target": "test"},
        summary=ReportSummary(status="release_review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=list(findings),
        misalignments=list(misalignments),
    )


def _finding(
    *,
    fid: str,
    check_id: str,
    severity: str = "high",
    category: str = "policy",
    tool_name: str | None = None,
    suppressed: bool = False,
) -> Finding:
    return Finding(
        id=fid,
        fingerprint=fid,
        check_id=check_id,
        title=f"{check_id} title",
        severity=severity,  # type: ignore[arg-type]
        category=category,
        tool_name=tool_name,
        recommendation="see check docs",
        suppressed=suppressed,
    )


def _misalignment(
    *,
    mid: str,
    finding_ref: str,
    kind: str = "policy_gap",
    severity: str = "high",
    tool_name: str | None = None,
) -> Misalignment:
    return Misalignment(
        id=mid,
        kind=kind,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        tool_name=tool_name,
        finding_refs=[finding_ref],
        policy_requirement="test",
        gap="test gap",
        release_implication="test impl",
    )


# Test 1 — golden YAML
def test_golden_yaml_matches_support_refund_fixture():
    report = _load(SUPPORT_REFUND_REPORT)
    rendered = dump_yaml(derive_yaml_scenarios(report, min_severity="high"))
    expected = GOLDEN_YAML.read_text(encoding="utf-8")
    assert rendered == expected, (
        "Scenario YAML drifted from the golden fixture. If this is intentional, "
        f"regenerate {GOLDEN_YAML.relative_to(REPO_ROOT)} and review the diff."
    )


# Test 2 — determinism
def test_dump_yaml_is_deterministic_across_runs():
    report = _load(SUPPORT_REFUND_REPORT)
    first = dump_yaml(derive_yaml_scenarios(report, min_severity="high"))
    second = dump_yaml(derive_yaml_scenarios(report, min_severity="high"))
    assert first == second


# Test 3 — six-category coverage
def test_six_required_categories_each_produce_a_scenario():
    cases = [
        # (check_id, category, expected scenario_type)
        ("SHIP-POLICY-APPROVAL-MISSING", "policy", "approval"),
        ("SHIP-POLICY-CONFIRMATION-MISSING", "policy", "confirmation"),
        ("SHIP-SIDEFX-IDEMPOTENCY-MISSING", "side_effects", "idempotency_retry"),
        ("SHIP-SCOPE-PROHIBITED-TOOL-PRESENT", "scope", "prohibited_action"),
        ("SHIP-AUTH-MANIFEST-BROAD-SCOPE", "auth", "least_privilege_scope"),
        ("SHIP-SCHEMA-MISSING-BOUNDS", "schema", "schema_boundary"),
    ]
    findings = [
        _finding(
            fid=f"f{i}",
            check_id=check_id,
            severity="critical",
            category=category,
            tool_name="t",
        )
        for i, (check_id, category, _) in enumerate(cases)
    ]
    misalignments = [
        _misalignment(mid=f"m{i}", finding_ref=f"f{i}", tool_name="t")
        for i in range(len(cases))
    ]
    scenarios = derive_yaml_scenarios(
        _minimal_report(findings=findings, misalignments=misalignments),
        min_severity="critical",
    )
    by_type = {s["id"].split("__", 1)[0]: s for s in scenarios}
    for check_id, _category, expected_type in cases:
        assert expected_type in by_type, (
            f"{check_id} did not produce a {expected_type} scenario"
        )
        assert check_id in by_type[expected_type]["derived_from"]


# Test 4 — severity filter
def test_min_severity_filters_below_threshold():
    finding = _finding(
        fid="f1",
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        severity="medium",
        tool_name="t",
    )
    mis = _misalignment(mid="m1", finding_ref="f1", severity="medium", tool_name="t")
    report = _minimal_report(findings=[finding], misalignments=[mis])

    assert derive_yaml_scenarios(report, min_severity="high") == []
    medium = derive_yaml_scenarios(report, min_severity="medium")
    assert len(medium) == 1
    assert medium[0]["id"] == "approval__t"


# Test 5 — suppressed findings skipped
def test_suppressed_findings_excluded_from_derivation_and_gaps():
    finding = _finding(
        fid="f1",
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        severity="critical",
        tool_name="t",
        suppressed=True,
    )
    mis = _misalignment(mid="m1", finding_ref="f1", severity="critical", tool_name="t")
    report = _minimal_report(findings=[finding], misalignments=[mis])
    scenarios = derive_yaml_scenarios(report, min_severity="critical")
    assert scenarios == []
    assert coverage_gaps(report, scenarios, min_severity="critical") == []


# Test 6 — empty input
def test_empty_report_emits_empty_envelope():
    report = _minimal_report()
    scenarios = derive_yaml_scenarios(report, min_severity="high")
    assert scenarios == []
    rendered = dump_yaml(scenarios)
    assert yaml.safe_load(rendered) == {"scenarios": []}


# Test 7 — CLI smoke (file write + json mode)
def test_cli_writes_yaml_file_and_json_envelope(tmp_path: Path):
    runner = CliRunner()
    out_path = tmp_path / "out.yaml"
    result = runner.invoke(
        app,
        [
            "scenario",
            "suggest",
            "--from",
            str(SUPPORT_REFUND_REPORT),
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    body = yaml.safe_load(out_path.read_text())
    assert "scenarios" in body
    assert isinstance(body["scenarios"], list)
    assert len(body["scenarios"]) > 0

    json_result = runner.invoke(
        app,
        [
            "scenario",
            "suggest",
            "--from",
            str(SUPPORT_REFUND_REPORT),
            "--json",
        ],
    )
    assert json_result.exit_code == 0, json_result.output
    parsed = json.loads(json_result.stdout)
    assert "scenarios" in parsed and len(parsed["scenarios"]) == len(body["scenarios"])


# Test 8 — --strict failure (exit 20)
def test_strict_mode_fails_with_exit_20_when_finding_uncovered(tmp_path: Path):
    finding = _finding(
        fid="fp_uncovered",
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        severity="critical",
        tool_name="t",
    )
    # No misalignment → no derivation path → strict gap
    report = _minimal_report(findings=[finding], misalignments=[])
    report_path = tmp_path / "report.json"
    report_path.write_text(report.model_dump_json())
    out_path = tmp_path / "scenarios.yaml"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scenario",
            "suggest",
            "--from",
            str(report_path),
            "--out",
            str(out_path),
            "--strict",
            "--min-severity",
            "critical",
        ],
    )
    assert result.exit_code == 20, result.output
    body = yaml.safe_load(out_path.read_text())
    assert "coverage_gaps" in body
    assert "fp_uncovered" in body["coverage_gaps"]


# Test 9 — --strict does NOT flag undetected_gap checks
def test_strict_does_not_flag_findings_with_no_scenario_type_predicate(tmp_path: Path):
    # SHIP-API-RETRY-WITHOUT-IDEMPOTENCY is in MISSING_CONTROL_CHECKS but
    # _diff_spec falls through to scenario_type=None — these must NOT be
    # counted as coverage gaps, otherwise --strict over-reports.
    runner = CliRunner()
    out_path = tmp_path / "out.yaml"
    result = runner.invoke(
        app,
        [
            "scenario",
            "suggest",
            "--from",
            str(SIMPLE_OPENAI_REPORT),
            "--out",
            str(out_path),
            "--strict",
        ],
    )
    assert result.exit_code == 0, result.output
    body = yaml.safe_load(out_path.read_text())
    gaps = body.get("coverage_gaps", [])
    # Confirm the predicate excludes the retry-without-idem findings
    report = _load(SIMPLE_OPENAI_REPORT)
    retry_refs = [
        f.id or f.fingerprint
        for f in report.findings
        if f.check_id == "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY"
    ]
    assert retry_refs, "fixture should contain SHIP-API-RETRY-WITHOUT-IDEMPOTENCY findings"
    for ref in retry_refs:
        assert ref not in gaps


# Test 10 — agent-level scenario (tool=None)
def test_agent_level_scenario_uses_agent_suffix_and_null_tool():
    finding = _finding(
        fid="f-agent",
        check_id="SHIP-INVENTORY-WILDCARD-TOOLS",
        severity="critical",
        category="inventory",
        tool_name=None,
    )
    mis = _misalignment(
        mid="m-agent",
        finding_ref="f-agent",
        kind="control_missing",
        severity="critical",
        tool_name=None,
    )
    scenarios = derive_yaml_scenarios(
        _minimal_report(findings=[finding], misalignments=[mis]),
        min_severity="critical",
    )
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s["id"] == "wildcard_inventory__agent"
    assert s["tool"] is None
    rendered = dump_yaml(scenarios)
    assert "tool: null" in rendered


# Test 11 — predicate parity with the in-report grouping
def test_scenario_type_for_finding_matches_in_report_grouping():
    """For every finding referenced by a misalignment in the support fixture,
    the standalone predicate must produce the same scenario_type as the
    misalignment's grouped suggested_scenarios membership."""
    report = _load(SUPPORT_REFUND_REPORT)
    finding_index = {
        (f.id or f.fingerprint): f
        for f in report.findings
        if f.id or f.fingerprint
    }
    # Build {scenario_type: set(misalignment_ids)} from the report itself.
    by_type_in_report: dict[str, set[str]] = {}
    for scenario in report.suggested_scenarios:
        by_type_in_report.setdefault(scenario.scenario_type, set()).update(
            scenario.source_misalignments
        )
    # For each misalignment, every referenced finding's predicate should
    # agree with the type the misalignment was placed under.
    misalignment_index = {m.id: m for m in report.misalignments}
    for scenario_type, mids in by_type_in_report.items():
        for mid in mids:
            mis = misalignment_index[mid]
            for ref in mis.finding_refs:
                f = finding_index.get(ref)
                assert f is not None
                predicate = scenario_type_for_finding(f)
                assert predicate == scenario_type, (
                    f"Drift detected: misalignment {mid} grouped under "
                    f"{scenario_type} but finding {ref} predicate yields {predicate}"
                )


# Bonus — verify dump_json envelope matches dump_yaml shape
def test_json_and_yaml_envelopes_match():
    report = _load(SUPPORT_REFUND_REPORT)
    scenarios = derive_yaml_scenarios(report, min_severity="high")
    gaps = coverage_gaps(report, scenarios, min_severity="high")
    yaml_body = yaml.safe_load(dump_yaml(scenarios, gaps=gaps))
    json_body = json.loads(dump_json(scenarios, gaps=gaps))
    assert yaml_body == json_body


# Bonus — verify scenario_type_for_finding is the public API used by
# apply_capability_diff so refactor protection is real.
def test_apply_capability_diff_still_produces_grouped_scenarios():
    finding = _finding(
        fid="fp_refactor_check",
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        severity="critical",
        category="policy",
        tool_name="t",
    )
    report = _minimal_report(findings=[finding])
    apply_capability_diff(report, [])
    types = {s.scenario_type for s in report.suggested_scenarios}
    assert "approval" in types
