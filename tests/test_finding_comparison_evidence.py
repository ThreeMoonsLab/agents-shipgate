from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan.surface_redaction import _sanitize_diff_reference
from agents_shipgate.core.findings.identity import assign_finding_ids
from agents_shipgate.core.lenses.tool_surface import (
    ToolSurfaceDiffReference,
    _finding_item,
    compute_tool_surface_diff,
    load_tool_surface_diff_reference,
)
from agents_shipgate.core.policy_evidence import finding_support, predicate_evidence
from agents_shipgate.core.privacy import RedactionStats
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.surfaces import ToolSurfaceFacts
from tests.test_policy_packs import _manifest_without_policy_pack, _write_openapi, run_scan


def _finding(*, scope="refunds:read", path="shared/guard.py", eligible=True):
    finding = Finding(
        check_id="ORG-REFUND-GUARD", title="Review refund guard", severity="high",
        category="policy", tool_id="tool_refund", tool_name="refund",
        evidence={"requires_review": True}, recommendation="Review shared/guard.py.",
        source=SourceReference(type="python", path="tools/refund.py", start_line=4),
        policy_evidence_source=SourceReference(type="file", path=path, start_line=2),
        support=finding_support([predicate_evidence(
            "required_scope", "matched", observed=scope, confidence="high",
            evidence_bases=["typed_provider_fact"], policy_eligible=eligible,
        )]),
    )
    return assign_finding_ids([finding])[0]


def _compare(base, head):
    return compute_tool_surface_diff(
        ToolSurfaceFacts(), ToolSurfaceFacts(), head,
        reference=ToolSurfaceDiffReference(
            kind="report", facts=ToolSurfaceFacts(),
            findings=[_finding_item(row) for row in base], finding_evidence=tuple(base),
        ),
    )


@pytest.mark.parametrize("before,after", [
    ({"scope": "refunds:read"}, {"scope": "refunds:*"}),
    ({"scope": "refunds:*"}, {"scope": "refunds:read"}),
    ({"eligible": False}, {"eligible": True}),
    ({"path": "shared/guard.py"}, {"path": "config/guard.yaml"}),
])
def test_same_fingerprint_does_not_hide_changed_support_or_source(before, after):
    base, head = _finding(**before), _finding(**after)
    assert base.fingerprint == head.fingerprint
    originals = base.model_dump_json(), head.model_dump_json()
    diff = _compare([base], [head])
    # The legacy bucket remains an identity comparison, not a safety claim.
    assert diff.summary.unchanged_findings == 1
    assert any("1 matched finding" in note and "changed evidence" in note for note in diff.notes)
    assert any("refund" in note and "tools/refund.py" in note for note in diff.notes)
    assert any("not attribution" in note for note in diff.notes)
    assert originals == (base.model_dump_json(), head.model_dump_json())


def test_standing_weakness_is_not_automatically_safe_or_attributed():
    base, head = _finding(), _finding()
    diff = _compare([base], [head])
    assert diff.summary.unchanged_findings == 1
    assert not any("changed evidence:" in note for note in diff.notes)
    assert any("dependency coverage is not established" in note for note in diff.notes)


def test_a_new_fingerprint_is_not_presented_as_proven_new_authority():
    head = _finding()
    diff = _compare([], [head])
    assert diff.summary.new_findings == 1
    assert any("not attribution" in note for note in diff.notes)


def test_missing_base_evidence_is_explicit_even_when_surface_facts_exist():
    head = _finding()
    reference = ToolSurfaceDiffReference(
        kind="baseline", facts=ToolSurfaceFacts(), findings=[_finding_item(head)],
    )
    diff = compute_tool_surface_diff(ToolSurfaceFacts(), ToolSurfaceFacts(), [head], reference=reference)
    assert any("Base finding evidence is unavailable" in note for note in diff.notes)


def test_missing_support_and_colliding_subjects_cannot_earn_a_complete_comparison():
    missing = _finding().model_copy(update={"support": None})
    diff = _compare([missing], [_finding()])
    assert any("support unavailable" in note for note in diff.notes)
    duplicate = _finding().model_copy(update={"agent_id": "second-agent"})
    diff = _compare([_finding(), duplicate], [_finding(), duplicate])
    assert any("ambiguous finding identities" in note for note in diff.notes)


def test_report_loader_retains_evidence_which_the_legacy_delta_row_drops(tmp_path):
    payload = json.loads(Path("samples/support_refund_agent/expected/report.json").read_text())
    report = ReadinessReport.model_validate(payload)
    finding = _finding()
    report.findings = [finding]
    path = tmp_path / "base.json"
    path.write_text(report.model_dump_json())
    reference = load_tool_surface_diff_reference(path)
    assert reference.finding_evidence == (finding,)
    assert reference.finding_evidence[0].support == finding.support


def test_public_reference_sanitizes_the_retained_evidence_before_rendering():
    secret = "sk-" + "A" * 48
    finding = _finding(path=f"config/{secret}/guard.yaml")
    reference = ToolSurfaceDiffReference(kind="report", facts=ToolSurfaceFacts(), finding_evidence=(finding,))
    public = _sanitize_diff_reference(reference, stats=RedactionStats())
    assert public is not None and public.finding_evidence is not None
    assert secret not in public.finding_evidence[0].model_dump_json()
    assert secret in finding.model_dump_json()


def test_real_policy_scan_keeps_identity_but_exposes_changed_support(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "shipgate.yaml").write_text(_manifest_without_policy_pack() + """
checks:
  policy_packs:
    - path: org-pack.yaml
""")
    pack = tmp_path / "org-pack.yaml"
    policy = """
name: Refund review
rules:
  - id: ORG-REVIEW-REFUND
    title: Review refund capability
    severity: high
    confidence: high
    recommendation: Review create_refund in openapi.yaml.
    match:
      source_types: [openapi]
"""
    pack.write_text(policy)
    base, _ = run_scan(config_path=tmp_path / "shipgate.yaml", output_dir=tmp_path / "base",
                       formats=["json", "markdown"], ci_mode="advisory")
    pack.write_text(policy.replace("confidence: high", "confidence: medium"))
    head, _ = run_scan(config_path=tmp_path / "shipgate.yaml", output_dir=tmp_path / "head",
                       formats=["json", "markdown"], ci_mode="advisory",
                       diff_from_path=tmp_path / "base" / "report.json")
    old = next(row for row in base.findings if row.check_id == "ORG-REVIEW-REFUND")
    new = next(row for row in head.findings if row.check_id == old.check_id)
    assert old.fingerprint == new.fingerprint
    assert old.support != new.support
    notes = head.tool_surface_diff.notes
    assert any("changed evidence" in note and "create_refund" in note and "org-pack.yaml" in note for note in notes)
    assert "Finding identities:" in (tmp_path / "head" / "report.md").read_text()
    assert any(row.fingerprint == new.fingerprint for row in head.tool_surface_diff.finding_deltas.unchanged_findings)
    for name in ("packet.md", "packet.html"):
        text = (tmp_path / "head" / name).read_text()
        assert "Finding identities:" in text and "not attribution" in text
        assert "changed evidence" in text
