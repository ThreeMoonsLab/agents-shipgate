from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
    CAPABILITY_LOCK_SCHEMA_VERSION,
)
from agents_shipgate.schemas.governance_benchmark import (
    GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION,
    GovernanceBenchmarkCatalogV1,
)
from agents_shipgate.schemas.report import ReadinessReport
from scripts.run_governance_benchmark import (
    GovernanceBenchmarkOptions,
    benchmark_exit_code,
    load_governance_benchmark_catalog,
    render_governance_benchmark_result_json,
    run_governance_benchmark,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "benchmark/agent-pr-governance/cases.yaml"


def _catalog() -> GovernanceBenchmarkCatalogV1:
    return load_governance_benchmark_catalog(CATALOG_PATH)


def test_catalog_v02_validates_and_sorts_cases() -> None:
    catalog = _catalog()

    assert catalog.schema_version == "0.2"
    assert len(catalog.cases) == 50
    assert [case.id for case in catalog.cases] == sorted(case.id for case in catalog.cases)
    status_counts = {status: 0 for status in ("executable", "catalog_only", "external_evidence")}
    for case in catalog.cases:
        status_counts[case.status] += 1
    assert status_counts == {
        "executable": 10,
        "catalog_only": 27,
        "external_evidence": 13,
    }
    executable = {case.id: case for case in catalog.cases if case.status == "executable"}
    assert executable["mcp-refund-tool-added"].capability_expectations[0].tool_name == (
        "stripe.create_refund"
    )
    assert executable["benign-narrow-scope"].capability_expectations[0].semantic_direction == (
        "narrowed"
    )


def test_runner_executes_selected_case_deterministically(tmp_path: Path) -> None:
    catalog = _catalog()
    options = GovernanceBenchmarkOptions(case_ids=frozenset({"mcp-safe-read-tool-added"}))

    first = run_governance_benchmark(
        catalog,
        catalog_path=CATALOG_PATH,
        options=options,
        work_root=tmp_path / "first",
    )
    second = run_governance_benchmark(
        catalog,
        catalog_path=CATALOG_PATH,
        options=options,
        work_root=tmp_path / "second",
    )

    assert render_governance_benchmark_result_json(first) == (
        render_governance_benchmark_result_json(second)
    )
    assert first.summary.selected_cases == 1
    assert first.summary.passed_cases == 1
    assert first.cases[0].actual is not None
    assert first.cases[0].actual.merge_verdict == "human_review_required"
    assert first.cases[0].capability_diff is not None
    assert first.cases[0].capability_diff.added == 1


def test_full_executable_governance_benchmark_passes(tmp_path: Path) -> None:
    result = run_governance_benchmark(
        _catalog(),
        catalog_path=CATALOG_PATH,
        work_root=tmp_path / "run",
    )

    assert benchmark_exit_code(result) == 0
    assert result.summary.total_cases == 50
    assert result.summary.executable_cases == 10
    assert result.summary.selected_cases == 10
    assert result.summary.failed_cases == 0
    assert {metric.metric: metric.total for metric in result.metrics} == {
        "authority_routing": 8,
        "capability_semantic_fidelity": 6,
        "explanation_usefulness": 10,
        "remediation_boundary": 6,
        "safe_pass_rate": 2,
        "unsafe_merge_prevention": 4,
    }


def test_include_catalog_only_rows_are_skipped_unless_strict(tmp_path: Path) -> None:
    catalog = _catalog()
    options = GovernanceBenchmarkOptions(
        case_ids=frozenset({"dep-registry-changed"}),
        include_catalog_only=True,
    )
    result = run_governance_benchmark(
        catalog,
        catalog_path=CATALOG_PATH,
        options=options,
        work_root=tmp_path / "non-strict",
    )
    strict_result = run_governance_benchmark(
        catalog,
        catalog_path=CATALOG_PATH,
        options=GovernanceBenchmarkOptions(
            case_ids=frozenset({"dep-registry-changed"}),
            include_catalog_only=True,
            strict=True,
        ),
        work_root=tmp_path / "strict",
    )

    assert result.summary.skipped_cases == 1
    assert result.summary.failed_cases == 0
    assert result.cases[0].status == "external_evidence"
    assert strict_result.summary.failed_cases == 1
    assert benchmark_exit_code(strict_result) == 1


def test_explicit_non_executable_case_selection_is_not_silently_empty(
    tmp_path: Path,
) -> None:
    result = run_governance_benchmark(
        _catalog(),
        catalog_path=CATALOG_PATH,
        options=GovernanceBenchmarkOptions(case_ids=frozenset({"dep-registry-changed"})),
        work_root=tmp_path / "explicit-non-executable",
    )

    assert result.summary.selected_cases == 1
    assert result.summary.skipped_cases == 1
    assert result.summary.failed_cases == 0
    assert result.cases[0].id == "dep-registry-changed"
    assert result.cases[0].status == "external_evidence"


def test_unknown_case_id_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown governance benchmark case id"):
        run_governance_benchmark(
            _catalog(),
            options=GovernanceBenchmarkOptions(case_ids=frozenset({"no-such-case"})),
            work_root=tmp_path / "unknown",
        )


def test_capability_expectation_failure_exits_one(tmp_path: Path) -> None:
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    data["cases"] = [case for case in data["cases"] if case["id"] == "mcp-safe-read-tool-added"]
    data["cases"][0]["capability_expectations"][0]["tool_name"] = "support.nope"
    catalog = GovernanceBenchmarkCatalogV1.model_validate(data)

    result = run_governance_benchmark(
        catalog,
        options=GovernanceBenchmarkOptions(case_ids=frozenset({"mcp-safe-read-tool-added"})),
        work_root=tmp_path / "failure",
    )

    assert benchmark_exit_code(result) == 1
    assert result.summary.failed_cases == 1
    assert any("capability expectation did not match" in item for item in result.cases[0].failures)


def test_malformed_catalog_raises_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad-cases.yaml"
    bad.write_text(
        """
schema_version: "0.2"
name: bad
description: bad
metrics: [safe_pass_rate]
cases:
  - id: missing-required-fields
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Invalid governance benchmark catalog"):
        load_governance_benchmark_catalog(bad)


def test_governance_benchmark_schemas_validate_catalog_and_result(tmp_path: Path) -> None:
    catalog = _catalog()
    result = run_governance_benchmark(
        catalog,
        catalog_path=CATALOG_PATH,
        options=GovernanceBenchmarkOptions(case_ids=frozenset({"benign-docs-opted-in"})),
        work_root=tmp_path / "schema",
    )
    catalog_schema = json.loads(
        (REPO_ROOT / "docs/governance-benchmark-catalog-schema.v0.2.json").read_text(
            encoding="utf-8"
        )
    )
    result_schema = json.loads(
        (REPO_ROOT / "docs/governance-benchmark-result-schema.v0.2.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator.check_schema(catalog_schema)
    Draft202012Validator.check_schema(result_schema)
    Draft202012Validator(catalog_schema).validate(catalog.model_dump(mode="json"))
    Draft202012Validator(result_schema).validate(result.model_dump(mode="json"))
    assert result.governance_benchmark_result_schema_version == "0.2"
    assert result.experimental is False


def test_governance_benchmark_preserves_public_schema_boundaries() -> None:
    assert ReadinessReport.model_fields["report_schema_version"].default == "0.41"
    assert CAPABILITY_LOCK_SCHEMA_VERSION == "0.8"
    assert CAPABILITY_LOCK_DIFF_SCHEMA_VERSION == "0.9"
    assert GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION == "0.2"
