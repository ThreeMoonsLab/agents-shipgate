"""Tests for `Finding.provenance_kind` (v0.15).

Locks the public enum surface, asserts emitted reports always carry a
real value (catching forgotten helper-callsite kwargs), and pins the
fingerprint-stability invariant from the implementation plan.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from jsonschema import ValidationError, validate

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.findings import (
    PROVENANCE_KIND_ORDER,
    finding_fingerprint,
    provenance_kind_counts,
)
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.common import ProvenanceKind
from agents_shipgate.schemas.report import Finding, ReadinessReport

SAMPLES = [
    Path("samples/support_refund_agent/shipgate.yaml"),
    Path("samples/simple_openai_api_agent/shipgate.yaml"),
    Path("samples/simple_langchain_agent/shipgate.yaml"),
    Path("samples/simple_crewai_agent/shipgate.yaml"),
]

CURRENT_SCHEMA = Path(
    "docs/report-schema.v"
    f"{ReadinessReport.model_fields['report_schema_version'].default}.json"
)


def test_provenance_kind_enum_values():
    """Public surface lock. Mirror of test_agent_action_enum_values."""
    assert set(get_args(ProvenanceKind)) == {
        "static_declaration",
        "ast_extraction",
        "keyword_heuristic",
        "regex_heuristic",
        "policy_pack",
        "runtime_trace",
    }


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda p: p.parent.name)
def test_emitted_findings_never_have_none_provenance(tmp_path, sample):
    """No emitted finding may carry `provenance_kind=None`. The model
    default is None for legacy report back-compat, but every built-in
    check must specify the value via the required kwarg on
    `tool_finding`/`agent_finding`."""
    report, _ = run_scan(
        config_path=sample,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    for finding in report.findings:
        assert finding.provenance_kind is not None, (
            f"Finding {finding.check_id} on {sample.parent.name} "
            f"emitted with provenance_kind=None — the helper callsite "
            f"likely forgot the kwarg."
        )


def test_bundled_fixture_findings_exclude_heuristic_policy_matches(tmp_path):
    """Heuristic applicability is represented as evidence gaps, not Findings."""
    kinds: set[str] = set()
    policy_gap_count = 0
    for sample in SAMPLES:
        out = tmp_path / sample.parent.name
        out.mkdir()
        report, _ = run_scan(
            config_path=sample,
            output_dir=out,
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        for finding in report.findings:
            if finding.provenance_kind is not None:
                kinds.add(finding.provenance_kind)
        policy_gap_count += len(report.policy_evidence_gaps)
    assert "static_declaration" in kinds
    assert "keyword_heuristic" not in kinds
    assert "regex_heuristic" not in kinds
    assert policy_gap_count > 0


def test_each_provenance_value_constructs_cleanly():
    """Per-category snapshot: every literal value must be a valid
    Pydantic input. Catches typos in the enum at the model boundary."""
    for value in get_args(ProvenanceKind):
        finding = Finding(
            check_id="SHIP-TEST",
            title="t",
            severity="info",
            category="test",
            recommendation="r",
            provenance_kind=value,
        )
        assert finding.provenance_kind == value


def test_provenance_kind_counts_cover_all_values_and_suppression():
    findings = [
        Finding(
            check_id=f"SHIP-TEST-{index}",
            title=f"t{index}",
            severity="info",
            category="test",
            recommendation="r",
            provenance_kind=kind,
            suppressed=index == 2,
        )
        for index, kind in enumerate(PROVENANCE_KIND_ORDER)
    ]

    active_counts = provenance_kind_counts(findings)
    all_counts = provenance_kind_counts(findings, include_suppressed=True)

    assert set(active_counts) == set(PROVENANCE_KIND_ORDER)
    assert active_counts["keyword_heuristic"] == 0
    assert all_counts["keyword_heuristic"] == 1
    for kind in (
        "static_declaration",
        "ast_extraction",
        "regex_heuristic",
        "policy_pack",
        "runtime_trace",
    ):
        assert active_counts[kind] == 1
        assert all_counts[kind] == 1


def test_provenance_kind_counts_treat_legacy_none_as_static():
    finding = Finding(
        check_id="SHIP-TEST",
        title="t",
        severity="info",
        category="test",
        recommendation="r",
    )

    counts = provenance_kind_counts([finding])

    assert counts["static_declaration"] == 1


def test_provenance_kind_not_in_fingerprint():
    """Fingerprint stability invariant. Two findings that differ only
    by provenance_kind MUST share a fingerprint — otherwise every
    existing baseline file in the wild would be invalidated."""
    base_kwargs = dict(
        check_id="SHIP-TEST",
        title="t",
        severity="info",
        category="test",
        tool_name="tool_a",
        evidence={"foo": "bar"},
        recommendation="r",
    )
    a = Finding(**base_kwargs, provenance_kind="static_declaration")
    b = Finding(**base_kwargs, provenance_kind="regex_heuristic")
    assert finding_fingerprint(a) == finding_fingerprint(b)


def test_wire_schema_rejects_missing_provenance_kind(tmp_path):
    """v0.15 wire contract: provenance_kind is required + non-nullable.
    A payload missing the field MUST fail jsonschema validation."""
    report, _ = run_scan(
        config_path=SAMPLES[0],
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    payload = report_json_payload(report)
    schema = json.loads(CURRENT_SCHEMA.read_text(encoding="utf-8"))

    # Sanity check: the unmodified payload validates.
    validate(instance=payload, schema=schema)

    # Drop provenance_kind from the first finding and re-validate.
    assert payload["findings"], "fixture must emit at least one finding"
    payload["findings"][0].pop("provenance_kind", None)
    with pytest.raises(ValidationError):
        validate(instance=payload, schema=schema)


def test_wire_schema_rejects_unknown_provenance_kind(tmp_path):
    """The wire enum is closed — unknown values must fail validation."""
    report, _ = run_scan(
        config_path=SAMPLES[0],
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    payload = report_json_payload(report)
    schema = json.loads(CURRENT_SCHEMA.read_text(encoding="utf-8"))

    payload["findings"][0]["provenance_kind"] = "made_up_value"
    with pytest.raises(ValidationError):
        validate(instance=payload, schema=schema)


def test_annotate_remediation_coerces_plugin_finding_provenance():
    """Third-party plugin checks may emit `Finding(...)` directly
    without the new v0.15 kwarg. `annotate_remediation` must coerce
    `provenance_kind=None` to `"static_declaration"` so the wire
    schema's required + non-nullable enum is satisfied. Without this
    fallback, any pre-v0.15 plugin would produce schema-invalid
    `report.json`."""
    from agents_shipgate.core.findings import annotate_remediation

    plugin_finding = Finding(
        check_id="ORG-PLUGIN-TEST",
        title="t",
        severity="info",
        category="test",
        recommendation="r",
    )
    assert plugin_finding.provenance_kind is None
    annotate_remediation([plugin_finding], check_metadata_lookup={})
    assert plugin_finding.provenance_kind == "static_declaration"


def test_annotate_remediation_preserves_explicit_provenance():
    """The coercion in `annotate_remediation` must NOT overwrite a
    provenance value that a check explicitly set."""
    from agents_shipgate.core.findings import annotate_remediation

    explicit = Finding(
        check_id="ORG-PLUGIN-TEST",
        title="t",
        severity="info",
        category="test",
        recommendation="r",
        provenance_kind="policy_pack",
    )
    annotate_remediation([explicit], check_metadata_lookup={})
    assert explicit.provenance_kind == "policy_pack"
