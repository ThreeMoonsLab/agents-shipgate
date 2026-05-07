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
from agents_shipgate.core.disclaimers import HITL_RUNTIME_CONTROL_DISCLAIMER
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
    assert packet.packet_schema_version == "0.3"


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


def test_packet_has_reviewer_sections(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    payload = serialize_packet_json(packet)
    for section in (
        "release_decision",
        "capability_intent",
        "high_risk_surface",
        "tool_surface_diff",
        "approval_coverage",
        "idempotency_risk",
        "scope_coverage",
        "memory_isolation",
        "human_in_the_loop",
        "dynamic_scenarios",
        "not_proven",
    ):
        assert section in payload, f"missing section: {section}"
    assert payload["tool_surface_diff"]["status"] == "not_declared"


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
    assert section.runtime_control_disclaimer == HITL_RUNTIME_CONTROL_DISCLAIMER
    assert section.provenance_mode == "fresh_scan"


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


def test_load_packet_json_upgrades_v02_hitl_fields(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    payload = serialize_packet_json(packet)
    payload["packet_schema_version"] = "0.2"
    hitl = payload["human_in_the_loop"]
    hitl.pop("runtime_control_disclaimer", None)
    hitl.pop("source_provenance", None)
    hitl.pop("provenance_mode", None)

    upgraded = load_packet_json(payload)

    assert upgraded.packet_schema_version == "0.3"
    assert upgraded.human_in_the_loop.runtime_control_disclaimer == (
        HITL_RUNTIME_CONTROL_DISCLAIMER
    )
    assert upgraded.human_in_the_loop.source_provenance == []
    assert upgraded.human_in_the_loop.provenance_mode == "unavailable"


def test_load_packet_json_upgrades_v01_to_v03(tmp_path):
    _, packet = _scan_with_packet(tmp_path)
    payload = serialize_packet_json(packet)
    payload["packet_schema_version"] = "0.1"
    payload.pop("tool_surface_diff")
    hitl = payload["human_in_the_loop"]
    hitl.pop("runtime_control_disclaimer", None)
    hitl.pop("source_provenance", None)
    hitl.pop("provenance_mode", None)

    upgraded = load_packet_json(payload)

    assert upgraded.packet_schema_version == "0.3"
    assert upgraded.tool_surface_diff.status == "not_declared"
    assert upgraded.tool_surface_diff.enabled is False
    assert upgraded.human_in_the_loop.runtime_control_disclaimer == (
        HITL_RUNTIME_CONTROL_DISCLAIMER
    )
    assert upgraded.human_in_the_loop.source_provenance == []
    assert upgraded.human_in_the_loop.provenance_mode == "unavailable"


def test_evidence_packet_cli_accepts_report_json(tmp_path):
    """Regression for PR #43 review: a CI-archived ``report.json`` must
    produce a (degraded) packet via ``evidence-packet --from``. The
    primary use case is regenerating the packet when only the report is
    on hand and the source workspace is no longer available."""

    out, _ = _scan_with_packet(tmp_path)
    target = tmp_path / "rebuilt"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(out / "report.json"),
            "--out",
            str(target),
            "--format",
            "md,html",
        ],
    )
    assert result.exit_code == 0, result.output
    rebuilt_md = (target / "packet.md").read_text(encoding="utf-8")
    # Same sections render with the same verdict source.
    assert "## §1 Release decision — BLOCKED" in rebuilt_md
    # And the rebuilt-from-report note shows up in §10 so reviewers know
    # the declared coverage is incomplete.
    assert "rebuilt from report.json" in rebuilt_md.lower()


def test_evidence_packet_from_report_marks_degradation_in_json(tmp_path):
    """The degraded path must surface a residual in
    ``packet.json.not_proven.additional_residuals`` so machine consumers
    (CI bots, dashboards) can detect the reduced fidelity without
    parsing prose."""

    out, _ = _scan_with_packet(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(out / "report.json"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    residuals = payload["not_proven"]["additional_residuals"]
    assert any("rebuilt from report.json" in note.lower() for note in residuals)
    # Verdict must still come from release_decision.decision (the
    # degradation does not affect §1).
    assert payload["release_decision"]["decision"] == "blocked"
    assert payload["release_decision"]["verdict"] == "BLOCKED"


def test_evidence_packet_from_report_rebuilds_hitl_gap_provenance(tmp_path):
    scan_out = tmp_path / "scan"
    run_scan(
        config_path=Path("samples/hitl_evidence_agent/shipgate.yaml"),
        output_dir=scan_out,
        formats=["json"],
        ci_mode="advisory",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(scan_out / "report.json"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    hitl = payload["human_in_the_loop"]
    assert hitl["provenance_mode"] == "rebuilt_from_findings"
    assert any(
        item["status"] == "expected_but_absent"
        for item in hitl["source_provenance"]
    )


def test_evidence_packet_from_report_marks_covered_hitl_provenance_unavailable(tmp_path):
    scan_out = tmp_path / "scan"
    run_scan(
        config_path=Path("samples/hitl_evidence_covered_agent/shipgate.yaml"),
        output_dir=scan_out,
        formats=["json"],
        ci_mode="advisory",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(scan_out / "report.json"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    hitl = payload["human_in_the_loop"]
    assert hitl["provenance_mode"] == "unavailable"
    assert hitl["source_provenance"] == []


def test_evidence_packet_from_clean_report_does_not_invent_scope_gaps(tmp_path):
    """Regression for PR #43 review: rebuilding a packet from
    ``samples/clean_read_only_agent``'s ``report.json`` must not invent
    ``missing_declared`` scopes. The original scan emits §6 as covered
    with no SHIP-AUTH-SCOPE-COVERAGE-MISSING findings; the rebuilt path
    has a stub manifest with empty ``permissions.scopes``, so deriving
    gaps from manifest comparison would falsely report every tool
    scope as missing. The fix is to derive missing/unused from finding
    evidence — authoritative regardless of whether the manifest is
    real or stubbed."""

    clean_config = Path("samples/clean_read_only_agent/shipgate.yaml")
    scan_out = tmp_path / "scan"
    fresh_report, _ = run_scan(
        config_path=clean_config,
        output_dir=scan_out,
        formats=["json"],
        ci_mode="advisory",
        packet_generated_at=GENERATED_AT,
    )

    # Sanity: the original scan does not flag any scope-coverage gaps
    # for this fixture.
    scope_findings = [
        f
        for f in fresh_report.findings
        if f.check_id
        in {"SHIP-AUTH-SCOPE-COVERAGE-MISSING", "SHIP-MANIFEST-UNUSED-SCOPE"}
    ]
    assert scope_findings == [], (
        f"clean fixture unexpectedly produced scope findings: "
        f"{[f.check_id for f in scope_findings]}"
    )

    # Compare the freshly-scanned packet's §6 to the rebuilt-from-report
    # packet's §6. They must agree on missing/unused.
    fresh_packet = load_packet_json((scan_out / "packet.json").read_text(encoding="utf-8"))
    runner = CliRunner()
    rebuilt_dir = tmp_path / "rebuilt"
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(scan_out / "report.json"),
            "--out",
            str(rebuilt_dir),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    rebuilt_packet = load_packet_json(
        (rebuilt_dir / "packet.json").read_text(encoding="utf-8")
    )

    assert fresh_packet.scope_coverage.missing_declared == []
    assert rebuilt_packet.scope_coverage.missing_declared == []
    assert rebuilt_packet.scope_coverage.unused_declared == []
    # Status should not regress to "missing" just because the manifest
    # was stubbed.
    assert rebuilt_packet.scope_coverage.status != "missing"


def test_evidence_packet_writes_packet_json_when_format_includes_json(tmp_path):
    """Regression for PR #43 review: ``--format json`` must write
    ``packet.json`` to ``--out``. Previously ``json`` was rejected and
    the only way to emit packet JSON was via ``--json`` (stdout only).
    A user rebuilding from ``report.json`` could not produce the
    standard local artifact set."""

    out, _ = _scan_with_packet(tmp_path / "scan")
    target = tmp_path / "rebuilt"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(out / "report.json"),
            "--out",
            str(target),
            "--format",
            "md,json,html",
        ],
    )
    assert result.exit_code == 0, result.output
    for name in ("packet.md", "packet.json", "packet.html"):
        assert (target / name).exists(), f"{name} not written"
    # The written packet.json must round-trip.
    payload = (target / "packet.json").read_text(encoding="utf-8")
    reloaded = load_packet_json(payload)
    assert reloaded.packet_schema_version == "0.3"


def test_evidence_packet_pdf_only_exits_zero_when_weasyprint_missing(
    tmp_path, monkeypatch
):
    """Regression for PR #43 review: ``--format pdf`` is the only requested
    output and WeasyPrint is unavailable. The packet contract treats PDF
    as a documented graceful-skip (matches the scan path), so the CLI
    must exit 0 — not 2 — even though no file was written."""

    out, _ = _scan_with_packet(tmp_path / "scan")
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    target = tmp_path / "rebuilt"
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
            "pdf",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "skipped" in (result.output + result.stderr).lower()
    assert not (target / "packet.pdf").exists()


def test_evidence_packet_pdf_skip_does_not_block_other_outputs(
    tmp_path, monkeypatch
):
    """``--format md,pdf`` with WeasyPrint missing must still emit
    packet.md and exit 0. The PDF skip is informational; the rest of
    the format set is unaffected."""

    out, _ = _scan_with_packet(tmp_path / "scan")
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    target = tmp_path / "rebuilt"
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
            "md,pdf",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (target / "packet.md").exists()
    assert not (target / "packet.pdf").exists()


def test_evidence_packet_unknown_format_error_lists_json(tmp_path):
    """Regression for PR #43 review: ``json`` is in ``_VALID_FORMATS``
    so the validation error must list it. Otherwise users hitting an
    invalid format see a misleading expected-set."""

    out, _ = _scan_with_packet(tmp_path / "scan")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(out / "packet.json"),
            "--format",
            "bogus",
        ],
    )
    assert result.exit_code == 2
    combined = (result.output + result.stderr).lower()
    assert "json" in combined
    assert "html" in combined
    assert "pdf" in combined
    assert "md" in combined


def test_evidence_packet_default_format_includes_json(tmp_path):
    """Default ``--format`` is now ``md,json,html``: rebuilding without
    specifying ``--format`` should produce all three artifacts."""

    out, _ = _scan_with_packet(tmp_path / "scan")
    target = tmp_path / "rebuilt"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-packet",
            "--from",
            str(out / "report.json"),
            "--out",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    for name in ("packet.md", "packet.json", "packet.html"):
        assert (target / name).exists(), f"{name} not written"


def test_evidence_packet_rejects_unrecognised_json(tmp_path):
    """Inputs that are JSON but not packet.json or report.json must
    fail cleanly (exit 2) rather than producing partial output."""

    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["evidence-packet", "--from", str(bogus)],
    )
    assert result.exit_code == 2
    assert "packet.json or report.json" in (result.output + result.stderr).lower()


def test_evidence_packet_cli_round_trips(tmp_path):
    out, packet = _scan_with_packet(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["evidence-packet", "--from", str(out / "packet.json"), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["packet_schema_version"] == "0.3"
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


def test_manifest_packet_disabled_is_honored(tmp_path):
    """Regression for PR #43 review: when ``shipgate.yaml`` sets
    ``output.packet.enabled: false`` and the CLI does not pass
    ``--packet``/``--no-packet``, the packet must not be written. The
    bug was that the CLI flag's ``True`` default overwrote the
    manifest setting via ``run_scan``."""

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "docs.lookup",
                        "description": "Read internal docs.",
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: opt-out
agent:
  name: opt-out-agent
  declared_purpose:
    - look up documentation
environment:
  target: local
tool_sources:
  - id: docs
    type: mcp
    path: tools.json
output:
  packet:
    enabled: false
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "reports"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    # report.* should be present, packet.* should NOT.
    assert (out_dir / "report.md").exists()
    for name in ("packet.md", "packet.json", "packet.html", "packet.pdf"):
        assert not (out_dir / name).exists(), (
            f"manifest disabled the packet, but {name} was emitted"
        )


def test_cli_packet_flag_overrides_manifest_disabled(tmp_path):
    """When the manifest sets ``output.packet.enabled: false`` but the
    user passes ``--packet`` on the command line, the explicit CLI flag
    wins (tri-state: ``None`` defers to manifest, ``True``/``False``
    override it)."""

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "docs.lookup",
                        "description": "Read internal docs.",
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: opt-out
agent:
  name: opt-out-agent
  declared_purpose:
    - look up documentation
environment:
  target: local
tool_sources:
  - id: docs
    type: mcp
    path: tools.json
output:
  packet:
    enabled: false
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "reports"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--out",
            str(out_dir),
            "--packet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "packet.md").exists()


def test_scope_coverage_honors_wildcard_declarations(tmp_path):
    """Regression for PR #43 review: a manifest scope of ``stripe:*`` must
    cover ``stripe:refunds:write`` so the packet does not contradict the
    SHIP-AUTH-SCOPE-COVERAGE-MISSING auth checks (which already treat
    wildcards as coverage)."""

    _, packet = _scan_with_packet(tmp_path)
    section = packet.scope_coverage
    # stripe:* in manifest covers stripe.create_refund's stripe:refunds:write,
    # so neither should appear as a gap.
    assert "stripe:*" not in section.unused_declared
    assert "stripe:refunds:write" not in section.missing_declared
    # The row for stripe:refunds:write should be marked declared via the
    # wildcard match.
    by_scope = {row.scope: row for row in section.rows}
    assert by_scope["stripe:refunds:write"].declared is True


def test_approval_coverage_skips_unflagged_high_risk_tools(tmp_path):
    """Regression for PR #43 review: gmail.send_customer_email is
    high-risk on the support_refund_agent fixture but Shipgate does not
    flag it as missing approval (only confirmation). It must not appear
    as an approval gap row."""

    _, packet = _scan_with_packet(tmp_path)
    by_tool = {row.tool: row for row in packet.approval_coverage.rows}
    assert "gmail.send_customer_email" not in by_tool, (
        "approval-coverage row emitted for tool with no SHIP-POLICY-APPROVAL-MISSING finding"
    )
    # The tool that IS flagged must still surface.
    assert "stripe.create_refund" in by_tool


def test_packet_json_is_byte_reproducible_for_identical_inputs(tmp_path):
    """Two scans of the same workspace must produce byte-identical
    ``packet.json``. ``generated_at`` is intentionally not auto-filled
    by ``build_packet`` to preserve this contract."""

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    out_a.mkdir()
    out_b.mkdir()
    for out in (out_a, out_b):
        run_scan(
            config_path=SAMPLE_CONFIG,
            output_dir=out,
            formats=["json"],
            ci_mode="advisory",
        )
    a = (out_a / "packet.json").read_text(encoding="utf-8")
    b = (out_b / "packet.json").read_text(encoding="utf-8")
    assert a == b


def test_pdf_unavailable_warning_does_not_pollute_source_warnings(tmp_path, monkeypatch):
    """Regression for PR #43 review: a missing WeasyPrint install must
    log a warning, not feed into ``report.source_warnings`` /
    ``evidence_coverage.source_warning_count``. Otherwise a clean scan
    with ``--packet-format ...,pdf`` would falsely tell reviewers to
    rerun after fixing source loaders."""

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "docs.lookup",
                        "description": "Read internal docs.",
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: clean-pdf-skip
agent:
  name: clean-agent
  declared_purpose:
    - look up documentation
environment:
  target: local
tool_sources:
  - id: docs
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )

    # Force WeasyPrint to be unavailable.
    monkeypatch.setitem(sys.modules, "weasyprint", None)

    report, _ = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_formats=["md", "json", "html", "pdf"],
        packet_generated_at=GENERATED_AT,
    )

    # No source-loader warnings on this clean fixture.
    assert report.source_warnings == []
    assert report.release_decision is not None
    assert report.release_decision.evidence_coverage.source_warning_count == 0
    # Packet still emits the other formats; PDF is silently skipped.
    out = tmp_path / "reports"
    assert (out / "packet.md").exists()
    assert not (out / "packet.pdf").exists()
    # And §10 of the packet does not list any source warnings either.
    payload = json.loads((out / "packet.json").read_text(encoding="utf-8"))
    assert payload["not_proven"]["source_warnings"] == []


def test_packet_markdown_escapes_user_controlled_strings(tmp_path):
    """Regression for PR #43 review: project/agent names, tool names,
    and finding titles must be Markdown-escaped before reaching
    ``packet.md``. ``[Click here](evil)`` in a tool name must not
    render as a clickable link in a Markdown viewer."""

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Injection
  version: "1.0"
paths:
  /records:
    post:
      operationId: "[Click here](https://evil.example)"
      summary: "Update [records](https://evil.example)"
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                updates:
                  type: object
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: "**bold** _team_ <tag>"
agent:
  name: "markdown-agent"
  declared_purpose:
    - "update [records](https://evil.example)"
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
policies:
  require_approval_for_tools:
    - "[Click here](https://evil.example)"
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "reports"
    run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=out_dir,
        formats=["json"],
        ci_mode="advisory",
        packet_generated_at=GENERATED_AT,
    )
    md = (out_dir / "packet.md").read_text(encoding="utf-8")

    # Raw injection patterns must not appear unescaped in the rendered
    # Markdown — they would otherwise be interpreted by a Markdown viewer.
    assert "[Click here](https://evil.example)" not in md
    assert "**bold** _team_ <tag>" not in md
    # Their escaped forms must appear.
    assert "\\[Click here\\]\\(https://evil.example\\)" in md
    assert "\\*\\*bold\\*\\* \\_team\\_ \\<tag\\>" in md


def test_packet_omits_generated_at_when_unset(tmp_path):
    """When ``packet_generated_at`` is not supplied, ``packet.json`` must
    not include a ``generated_at`` key. Sub-section optional fields stay
    in the JSON so the contract shape is otherwise stable."""

    run_scan(
        config_path=SAMPLE_CONFIG,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = json.loads((tmp_path / "packet.json").read_text(encoding="utf-8"))
    assert "generated_at" not in payload
    # Sanity: optional sub-fields are still present (e.g. ApprovalCoverageRow.source
    # may be null) so consumers can rely on a stable shape.
    assert "approval_coverage" in payload
