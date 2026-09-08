from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from test_safety_qualification import _case, _conforming_cases, _fixture, _run

from agents_shipgate.schemas.safety_qualification import (
    SafetyQualificationResultV1,
    pre_release_safety_requirements,
)
from scripts.run_safety_qualification import run_safety_qualification


def test_actual_ie_keeps_named_gaps_and_unclassified_cases(tmp_path):
    gap = {
        "kind": "source_warning",
        "subject": "remote MCP",
        "source_type": "google_adk",
        "source_ref": "agent.py:12",
        "why": "The remote tool inventory was not supplied.",
        "next_action": {
            "kind": "provide_source",
            "path": "tools.json",
            "why": "Supply the existing tool export.",
            "expects": "A reviewed static tool inventory.",
        },
    }
    paths = _fixture(
        tmp_path,
        actual_overrides={"case-review_required": "insufficient_evidence"},
        evidence_gaps={"case-review_required": [gap]},
    )
    result = _run(paths)
    coverage = result.coverage_misses[0]
    assert coverage.denominator == 4
    assert coverage.count == 2
    assert coverage.rate == 0.5
    assert coverage.unscored_case_ids == []
    misses = {case.case_id: case for case in coverage.cases}
    assert misses["case-review_required"].evidence_gaps[0].source_ref == "agent.py:12"
    assert misses["case-review_required"].evidence_gaps[0].next_action.path == "tools.json"
    assert misses["case-insufficient_evidence"].gap_status == "unclassified"
    assert result.qualified is False
    metric = next(x for x in result.intervals if x.name == "review_exact_rate")
    assert (metric.numerator, metric.denominator, metric.passed) == (0, 1, False)


def test_missing_receipt_is_unscored_not_a_zero_miss(tmp_path):
    paths = _fixture(tmp_path)
    index = json.loads(paths[2].read_text())
    index["receipts"] = [
        x for x in index["receipts"] if x["case_id"] != "case-insufficient_evidence"
    ]
    paths[2].write_text(json.dumps(index))
    result = _run(paths)
    coverage = result.coverage_misses[0]
    assert (coverage.count, coverage.denominator, coverage.rate) == (0, 4, 0.0)
    assert coverage.unscored_case_ids == ["case-insufficient_evidence"]
    assert result.qualified is False


def test_three_label_policy_marks_expected_ie_not_applicable(tmp_path):
    requirements = pre_release_safety_requirements()
    cases = _conforming_cases(requirements)
    paths = _fixture(tmp_path, cases=cases)
    wheel, corpus, receipts, policy = paths
    result = run_safety_qualification(
        wheel_path=wheel,
        corpus_path=corpus,
        receipt_index_path=receipts,
        policy_paths=[policy],
        requirements=requirements,
    )
    metric = next(x for x in result.intervals if x.name == "insufficient_evidence_exact_rate")
    assert (metric.numerator, metric.denominator, metric.passed) == (0, 0, True)
    assert metric.applicability == "not_applicable"
    assert len(result.coverage_misses) == 7
    assert sum(x.denominator for x in result.coverage_misses) == 38
    assert result.qualified is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("count", 0),
        ("denominator", 99),
        ("rate", 0.0),
        ("cases", []),
        ("unscored_case_ids", ["case-passed"]),
    ],
)
def test_miss_accounting_cannot_disagree_with_case_outcomes(tmp_path, field, value):
    payload = _run(_fixture(tmp_path)).model_dump(mode="json")
    payload["coverage_misses"][0][field] = value
    with pytest.raises(ValidationError, match="coverage"):
        SafetyQualificationResultV1.model_validate(payload)


def test_expected_ie_with_a_real_floor_remains_applicable(tmp_path):
    result = _run(_fixture(tmp_path))
    metric = next(x for x in result.intervals if x.name == "insufficient_evidence_exact_rate")
    assert metric.applicability == "applicable"
    payload = result.model_dump(mode="json")
    next(x for x in payload["intervals"] if x["name"] == metric.name)["applicability"] = (
        "not_applicable"
    )
    with pytest.raises(ValidationError, match="applicability"):
        SafetyQualificationResultV1.model_validate(payload)


@pytest.mark.parametrize("version", ["v1", "v2", "v4", "v5"])
def test_older_grammar_preserves_unrecorded_diagnostics(tmp_path, version):
    payload = _run(_fixture(tmp_path)).model_dump(mode="json")
    payload["schema_version"] = f"shipgate.safety_qualification/{version}"
    with pytest.raises(ValidationError, match="require the v6 envelope"):
        SafetyQualificationResultV1.model_validate(payload)
    payload.pop("coverage_misses")
    for metric in payload["intervals"]:
        metric.pop("applicability")
    result = SafetyQualificationResultV1.model_validate(payload)
    assert result.schema_version == "shipgate.safety_qualification/v5"
    assert result.coverage_misses is None
    assert all(metric.applicability is None for metric in result.intervals)
    encoded = result.model_dump(mode="json")
    assert "coverage_misses" not in encoded
    assert all("applicability" not in item for item in encoded["intervals"])
    assert SafetyQualificationResultV1.model_validate(encoded) == result


@pytest.mark.parametrize("missing", ["coverage_misses", "applicability"])
def test_v6_diagnostics_cannot_be_absent(tmp_path, missing):
    payload = _run(_fixture(tmp_path)).model_dump(mode="json")
    if missing == "coverage_misses":
        payload.pop(missing)
    else:
        payload["intervals"][0].pop(missing)
    with pytest.raises(ValidationError):
        SafetyQualificationResultV1.model_validate(payload)


def test_zero_denominator_does_not_disable_a_positive_floor(tmp_path):
    paths = _fixture(
        tmp_path,
        cases=[
            _case(f"case-{decision}", decision)
            for decision in ("passed", "blocked", "review_required")
        ],
    )
    result = _run(paths)
    metric = next(x for x in result.intervals if x.name == "insufficient_evidence_exact_rate")
    assert (metric.denominator, metric.applicability, metric.passed) == (0, "applicable", False)
    assert result.qualified is False


@pytest.mark.parametrize("misses,qualified", [(1, True), (2, False)])
def test_actual_ie_keeps_the_approved_safe_pass_tolerance(tmp_path, misses, qualified):
    requirements = pre_release_safety_requirements()
    cases = _conforming_cases(requirements)
    safe_ids = [case.id for case in cases if case.expected_decision == "passed"][:misses]
    wheel, corpus, receipts, policy = _fixture(
        tmp_path,
        cases=cases,
        actual_overrides={case_id: "insufficient_evidence" for case_id in safe_ids},
    )
    result = run_safety_qualification(
        wheel_path=wheel,
        corpus_path=corpus,
        receipt_index_path=receipts,
        policy_paths=[policy],
        requirements=requirements,
    )
    metric = next(x for x in result.intervals if x.name == "safe_pass_rate")
    assert (metric.numerator, metric.denominator) == (14 - misses, 14)
    assert result.qualified is qualified
    assert sum(row.count for row in result.coverage_misses) == misses
