"""Tests for `Finding.provenance_kind` (v0.14).

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
from agents_shipgate.core.findings import finding_fingerprint
from agents_shipgate.core.models import Finding, ProvenanceKind
from agents_shipgate.report.json_report import report_json_payload

SAMPLES = [
    Path("samples/support_refund_agent/shipgate.yaml"),
    Path("samples/simple_openai_api_agent/shipgate.yaml"),
    Path("samples/simple_langchain_agent/shipgate.yaml"),
    Path("samples/simple_crewai_agent/shipgate.yaml"),
]

V14_SCHEMA = Path("docs/report-schema.v0.14.json")


def test_provenance_kind_enum_values():
    """Public surface lock. Mirror of test_agent_action_enum_values."""
    assert set(get_args(ProvenanceKind)) == {
        "static_declaration",
        "ast_extraction",
        "keyword_heuristic",
        "regex_heuristic",
        "policy_pack",
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


def test_bundled_fixture_distribution(tmp_path):
    """Across the bundled samples, both static_declaration and
    keyword_heuristic must be represented. Confirms the multi-axis
    classification is real, not collapsed to one bucket. (The other
    three values are covered by per-category construction tests below;
    the bundled samples don't trigger them today.)"""
    kinds: set[str] = set()
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
    assert "static_declaration" in kinds
    assert "keyword_heuristic" in kinds


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
    """v0.14 wire contract: provenance_kind is required + non-nullable.
    A payload missing the field MUST fail jsonschema validation."""
    report, _ = run_scan(
        config_path=SAMPLES[0],
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    payload = report_json_payload(report)
    schema = json.loads(V14_SCHEMA.read_text(encoding="utf-8"))

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
    schema = json.loads(V14_SCHEMA.read_text(encoding="utf-8"))

    payload["findings"][0]["provenance_kind"] = "made_up_value"
    with pytest.raises(ValidationError):
        validate(instance=payload, schema=schema)
