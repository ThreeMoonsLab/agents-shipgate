from agents_shipgate.config.schema import SuppressionConfig
from agents_shipgate.core.findings import (
    apply_severity_overrides,
    apply_suppressions,
    assign_finding_ids,
    finding_fingerprint,
    summarize_findings,
)
from agents_shipgate.core.models import Finding, Tool


def test_apply_suppressions_matches_tool_name_and_preserves_reason():
    finding = Finding(
        check_id="SHIP-SCHEMA-BROAD-FREE-TEXT",
        title="Broad input",
        severity="high",
        category="schema",
        tool_id="tool:support.search_kb",
        tool_name="support.search_kb",
        recommendation="Constrain input.",
    )

    apply_suppressions(
        [finding],
        [
            SuppressionConfig(
                check_id="SHIP-SCHEMA-BROAD-FREE-TEXT",
                tool="support.search_kb",
                reason="Intentional free-form search.",
            )
        ],
    )

    assert finding.suppressed is True
    assert finding.suppression_reason == "Intentional free-form search."


def test_finding_ids_are_stable_across_ordering():
    first = Finding(
        check_id="SHIP-A",
        title="A",
        severity="high",
        category="test",
        tool_name="tool_a",
        evidence={"field": "x"},
        recommendation="Fix A.",
    )
    second = Finding(
        check_id="SHIP-B",
        title="B",
        severity="high",
        category="test",
        tool_name="tool_b",
        evidence={"field": "y"},
        recommendation="Fix B.",
    )

    assign_finding_ids([first, second])
    first_id = first.id
    second_id = second.id
    assign_finding_ids([second, first])

    assert first.id == first_id
    assert second.id == second_id


def test_colliding_finding_ids_use_stable_discriminator():
    first = Finding(
        check_id="SHIP-A",
        title="A",
        severity="high",
        category="test",
        tool_name="tool_a",
        evidence={"field": "same"},
        recommendation="Fix A.",
    )
    second = Finding(
        check_id="SHIP-A",
        title="B",
        severity="high",
        category="test",
        tool_name="tool_a",
        evidence={"field": "same"},
        recommendation="Fix B.",
    )

    assign_finding_ids([first, second])
    first_id = first.id
    second_id = second.id
    assign_finding_ids([second, first])

    assert first.fingerprint == second.fingerprint
    assert first.id == first_id
    assert second.id == second_id
    assert first.id != second.id


def test_finding_fingerprint_is_stable_across_severity_override():
    finding = Finding(
        check_id="SHIP-DOC-MISSING-DESCRIPTION",
        title="Missing description",
        severity="medium",
        category="documentation",
        tool_name="docs.short",
        evidence={"description_length": 5},
        recommendation="Add a description.",
    )

    assign_finding_ids([finding])
    fingerprint = finding.fingerprint
    apply_severity_overrides([finding], {"SHIP-DOC-MISSING-DESCRIPTION": "critical"})

    assert finding.severity == "critical"
    assert finding.fingerprint == fingerprint
    assert finding.evidence["default_severity"] == "medium"


def test_finding_fingerprint_ignores_default_severity_evidence():
    finding = Finding(
        check_id="SHIP-DOC-MISSING-DESCRIPTION",
        title="Missing description",
        severity="medium",
        category="documentation",
        tool_name="docs.short",
        evidence={"description_length": 5},
        recommendation="Add a description.",
    )
    fingerprint = finding_fingerprint(finding)

    apply_severity_overrides([finding], {"SHIP-DOC-MISSING-DESCRIPTION": "critical"})

    assert finding_fingerprint(finding) == fingerprint


def test_finding_fingerprint_sorts_list_evidence_values():
    first = Finding(
        check_id="SHIP-SCOPE-PROHIBITED-TOOL-PRESENT",
        title="Tool overlaps prohibited action",
        severity="high",
        category="scope",
        tool_name="tool_a",
        evidence={"risk_tags": ["write", "financial_action"]},
        recommendation="Fix A.",
    )
    second = Finding(
        check_id="SHIP-SCOPE-PROHIBITED-TOOL-PRESENT",
        title="Tool overlaps prohibited action",
        severity="high",
        category="scope",
        tool_name="tool_a",
        evidence={"risk_tags": ["financial_action", "write"]},
        recommendation="Fix A.",
    )

    assign_finding_ids([first])
    assign_finding_ids([second])

    assert first.fingerprint == second.fingerprint


def test_evidence_coverage_ignores_suppression_count():
    finding = Finding(
        check_id="SHIP-A",
        title="A",
        severity="critical",
        category="test",
        tool_name="tool_a",
        recommendation="Fix A.",
        suppressed=True,
    )
    tool = Tool(id="tool:tool_a", name="tool_a", source_type="openapi", extraction={"confidence": "high"})

    summary = summarize_findings([finding], [tool])

    assert summary.critical_count == 0
    assert summary.suppressed_count == 1
    assert summary.evidence_coverage == "static"


def test_tool_confidence_defaults_to_low_when_extraction_omits_contract():
    tool = Tool(id="tool:plugin_tool", name="plugin_tool", source_type="plugin")

    assert tool.extraction_confidence == "low"
    assert tool.extraction["confidence"] == "low"
