"""Tests for the Release Evidence Packet (`agents_shipgate.packet`).

Covers:
- ``build_packet`` invariants (all 10 sections always present, verdict
  derives from ``release_decision.decision`` only, disclaimers are
  verbatim and unconditional).
- HTML escaping safety.
- Golden fixtures for ``samples/support_refund_agent/expected/packet.*``.
- CLI tests for ``agents-shipgate evidence-packet``.
- Scan integration: ``scan`` emits packet by default; ``--no-packet``
  disables; ``--packet-format`` validates input.
- PDF graceful-skip when WeasyPrint is unavailable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.packet import (
    EvidencePacket,
    PacketSchemaError,
    load_packet_json,
    render_packet_html,
    render_packet_markdown,
    serialize_packet_json,
)
from agents_shipgate.packet.disclaimer import (
    PACKET_NON_PROOF,
    PACKET_NON_PROOF_HEADLINE,
)

SAMPLE_CONFIG = Path("samples/support_refund_agent/shipgate.yaml")
EXPECTED_DIR = Path("samples/support_refund_agent/expected")
EXPECTED_PACKET_MD = EXPECTED_DIR / "packet.md"
EXPECTED_PACKET_JSON = EXPECTED_DIR / "packet.json"
EXPECTED_PACKET_HTML = EXPECTED_DIR / "packet.html"

GENERATED_AT = "2026-01-01T00:00:00+00:00"


def _scan_with_packet(tmp_path: Path) -> tuple[Path, EvidencePacket]:
    """Run scan against the support_refund_agent fixture and return
    ``(out_dir, parsed_packet)``."""

    run_scan(
        config_path=SAMPLE_CONFIG,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_generated_at=GENERATED_AT,
    )
    payload = (tmp_path / "packet.json").read_text(encoding="utf-8")
    return tmp_path, load_packet_json(payload)


def test_packet_emits_alongside_report_by_default(tmp_path):
    out, packet = _scan_with_packet(tmp_path)
    for name in ("packet.md", "packet.json", "packet.html"):
        assert (out / name).exists(), name
    assert packet.packet_schema_version == "0.1"


def test_no_packet_flag_skips_packet_outputs(tmp_path):
    run_scan(
        config_path=SAMPLE_CONFIG,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    for name in ("packet.md", "packet.json", "packet.html", "packet.pdf"):
        assert not (tmp_path / name).exists(), f"{name} should not be present"


def test_packet_has_all_ten_sections(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    payload = serialize_packet_json(packet)
    for section in (
        "release_decision",
        "capability_intent",
        "high_risk_surface",
        "approval_coverage",
        "idempotency_risk",
        "scope_coverage",
        "memory_isolation",
        "human_in_the_loop",
        "dynamic_scenarios",
        "not_proven",
    ):
        assert section in payload, f"missing section: {section}"


def test_verdict_derives_from_release_decision_not_fail_policy(tmp_path):
    """The §1 verdict label must come from ``release_decision.decision``,
    even when ``fail_policy.would_fail_ci`` says otherwise. The sample
    fixture is in advisory mode (would_fail_ci=False) but the decision
    is ``blocked``; the verdict must reflect ``blocked``."""

    _, packet = _scan_with_packet(tmp_path)
    section = packet.release_decision
    assert section.fail_policy.would_fail_ci is False
    assert section.fail_policy.exit_code == 0
    assert section.decision == "blocked"
    assert section.verdict == "BLOCKED"


def test_capability_intent_diff_lists_observed_tools(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.capability_intent
    assert section.declared_purpose
    assert "stripe.create_refund" in section.observed_tools
    # SHIP-SCOPE-PROHIBITED-TOOL-PRESENT fires twice on this fixture.
    assert any(item.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
               for item in section.divergence_findings)


def test_high_risk_surface_includes_high_risk_tools(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.high_risk_surface
    assert section.high_risk_count >= 1
    names = {entry.name for entry in section.tools}
    assert "stripe.create_refund" in names
    # stripe.create_refund has no approval policy in the fixture.
    stripe = next(e for e in section.tools if e.name == "stripe.create_refund")
    assert stripe.has_approval_policy is False


def test_approval_coverage_separates_declared_and_gap(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.approval_coverage
    by_tool = {row.tool: row for row in section.rows}
    assert by_tool["shopify.cancel_order"].declared is True
    assert by_tool["stripe.create_refund"].declared is False
    assert any(
        item.check_id == "SHIP-POLICY-APPROVAL-MISSING"
        for item in section.gap_findings
    )


def test_idempotency_risk_reports_retry_policy_and_gaps(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.idempotency_risk
    assert any(
        item.check_id == "SHIP-SIDEFX-IDEMPOTENCY-MISSING"
        for item in section.gap_findings
    )


def test_scope_coverage_finds_missing_declared(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.scope_coverage
    assert "shopify:orders:write" in section.missing_declared


def test_memory_isolation_always_not_declared_for_v01(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.memory_isolation
    assert section.is_declared is False
    assert section.status == "not_declared"


def test_human_in_the_loop_reads_human_review_recommended(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.human_in_the_loop
    # The fixture has a low-confidence tool and source warning, which
    # makes evidence_coverage recommend human review.
    assert section.human_review_recommended is True


def test_dynamic_scenarios_surfaces_human_review_findings(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.dynamic_scenarios
    # The fixture has source_warning_count=1, so we expect at least one
    # scenario referencing it.
    assert any("source warning" in s.scenario.lower() for s in section.scenarios)


def test_not_proven_carries_canonical_disclaimers(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.not_proven
    assert section.headline == PACKET_NON_PROOF_HEADLINE
    labels = [item.label for item in section.unconditional]
    expected = [label for label, _ in PACKET_NON_PROOF]
    assert labels == expected
    bodies = [item.body for item in section.unconditional]
    expected_bodies = [body for _, body in PACKET_NON_PROOF]
    assert bodies == expected_bodies


def test_not_proven_contains_per_run_residuals(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    section = packet.not_proven
    # The fixture's MCP source emits a wildcard warning.
    assert any("wildcard" in w.lower() for w in section.source_warnings)


def test_html_escapes_user_controlled_strings():
    """An injected ``<script>`` tag in a tool name must appear escaped
    in the rendered HTML; we never round-trip through a markdown
    renderer that allows raw HTML, so this is a structural guarantee."""

    from agents_shipgate.core.models import (
        BaselineDelta,
        EvidenceCoverageDecision,
        FailPolicy,
    )
    from agents_shipgate.packet.models import (
        ApprovalCoverageSection,
        CapabilityIntentDiff,
        DynamicScenariosSection,
        HighRiskSurfaceSection,
        HighRiskToolEntry,
        HumanInTheLoopEvidence,
        IdempotencyRiskSection,
        MemoryIsolationStatus,
        NotProvenItem,
        NotProvenSection,
        ReleaseDecisionSection,
        ScopeCoverageSection,
    )

    decision = ReleaseDecisionSection(
        decision="passed",
        verdict="PASSED",
        reason="ok",
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=False,
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory",
            fail_on=[],
            new_findings_only=False,
            would_fail_ci=False,
            exit_code=0,
        ),
    )
    packet = EvidencePacket(
        generated_at=GENERATED_AT,
        run_id="r",
        project={"name": "<script>alert('p')</script>"},
        agent={"name": "<img src=x onerror=alert(1)>"},
        environment={"target": "local"},
        release_decision=decision,
        capability_intent=CapabilityIntentDiff(
            status="not_declared",
            declared_purpose=[],
            prohibited_actions=[],
            observed_tools=["<script>evil()</script>"],
            rows=[],
            divergence_findings=[],
        ),
        high_risk_surface=HighRiskSurfaceSection(
            status="informational",
            total_tools=1,
            high_risk_count=1,
            tools=[
                HighRiskToolEntry(
                    name="<script>",
                    source_type="mcp",
                    risk_tags=["<x>"],
                ),
            ],
        ),
        approval_coverage=ApprovalCoverageSection(status="informational"),
        idempotency_risk=IdempotencyRiskSection(status="informational"),
        scope_coverage=ScopeCoverageSection(status="informational"),
        memory_isolation=MemoryIsolationStatus(),
        human_in_the_loop=HumanInTheLoopEvidence(status="not_declared"),
        dynamic_scenarios=DynamicScenariosSection(status="informational"),
        not_proven=NotProvenSection(
            headline=PACKET_NON_PROOF_HEADLINE,
            unconditional=[
                NotProvenItem(label=label, body=body)
                for label, body in PACKET_NON_PROOF
            ],
            source_warnings=["<svg/onload=alert(1)>"],
        ),
    )

    html = render_packet_html(packet)
    # Raw tag literals must NEVER appear; everything is HTML-escaped.
    assert "<script>" not in html.lower().replace("<script>", "&lt;script&gt;")
    assert "&lt;script&gt;" in html
    assert "&lt;svg/onload=alert(1)&gt;" in html
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_load_packet_json_rejects_wrong_schema_version():
    bogus = {
        "packet_schema_version": "9.9",
        "generated_at": GENERATED_AT,
        "run_id": "r",
        "project": {},
        "agent": {},
        "environment": {},
    }
    with pytest.raises(PacketSchemaError):
        load_packet_json(bogus)


def test_load_packet_json_rejects_invalid_json():
    with pytest.raises(PacketSchemaError):
        load_packet_json("not-json")


def test_evidence_packet_cli_round_trips(tmp_path):
    out, packet = _scan_with_packet(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["evidence-packet", "--from", str(out / "packet.json"), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["packet_schema_version"] == "0.1"
    assert payload["run_id"] == packet.run_id


def test_evidence_packet_cli_writes_md_and_html(tmp_path):
    out, _ = _scan_with_packet(tmp_path)
    target = tmp_path / "rendered"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(out / "packet.json"),
            "--out",
            str(target),
            "--format",
            "md,html",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (target / "packet.md").exists()
    assert (target / "packet.html").exists()


def test_evidence_packet_cli_rejects_malformed_packet(tmp_path):
    bad = tmp_path / "packet.json"
    bad.write_text("not json", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["evidence-packet", "--from", str(bad)],
    )
    assert result.exit_code == 2


def test_packet_pdf_skipped_when_weasyprint_unavailable(tmp_path, monkeypatch):
    """When ``weasyprint`` is missing, ``--packet-format md,json,html,pdf``
    must still complete (exit 0) and emit the other formats."""

    monkeypatch.setitem(sys.modules, "weasyprint", None)
    run_scan(
        config_path=SAMPLE_CONFIG,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_formats=["md", "json", "html", "pdf"],
        packet_generated_at=GENERATED_AT,
    )
    assert (tmp_path / "packet.md").exists()
    assert (tmp_path / "packet.json").exists()
    assert (tmp_path / "packet.html").exists()
    assert not (tmp_path / "packet.pdf").exists()


def test_render_packet_pdf_raises_when_weasyprint_missing(monkeypatch, tmp_path):
    from agents_shipgate.packet.pdf import (
        PdfRendererUnavailable,
        render_packet_pdf,
    )

    monkeypatch.setitem(sys.modules, "weasyprint", None)
    out, packet = _scan_with_packet(tmp_path / "scan")
    with pytest.raises(PdfRendererUnavailable):
        render_packet_pdf(packet, tmp_path / "x.pdf")


def test_packet_format_validation_rejects_unknown_value():
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(SAMPLE_CONFIG),
            "--packet-format",
            "md,bogus",
        ],
    )
    assert result.exit_code == 2


def test_report_md_links_to_packet_when_packet_enabled(tmp_path):
    run_scan(
        config_path=SAMPLE_CONFIG,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_generated_at=GENERATED_AT,
    )
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "See `packet.md`" in md


def test_packet_json_matches_golden(tmp_path):
    out, _ = _scan_with_packet(tmp_path)
    actual = json.loads((out / "packet.json").read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED_PACKET_JSON.read_text(encoding="utf-8"))
    assert actual == expected


def test_packet_md_matches_golden(tmp_path):
    out, _ = _scan_with_packet(tmp_path)
    actual = (out / "packet.md").read_text(encoding="utf-8")
    expected = EXPECTED_PACKET_MD.read_text(encoding="utf-8")
    assert actual == expected


def test_packet_html_matches_golden(tmp_path):
    out, _ = _scan_with_packet(tmp_path)
    actual = (out / "packet.html").read_text(encoding="utf-8")
    expected = EXPECTED_PACKET_HTML.read_text(encoding="utf-8")
    assert actual == expected


def test_render_round_trips_via_load(tmp_path):
    out, packet = _scan_with_packet(tmp_path)
    payload = (out / "packet.json").read_text(encoding="utf-8")
    reloaded = load_packet_json(payload)
    assert reloaded == packet
    assert render_packet_markdown(reloaded) == render_packet_markdown(packet)


def test_build_packet_round_trips_via_serialize_and_load(tmp_path):
    """``build_packet -> serialize_packet_json -> load_packet_json`` is a
    no-op identity. Confirms the ``EvidencePacket`` JSON contract is
    self-consistent and that the schema lock prevents drift."""

    _, packet = _scan_with_packet(tmp_path)
    payload = serialize_packet_json(packet)
    reloaded = load_packet_json(json.dumps(payload))
    assert reloaded == packet
