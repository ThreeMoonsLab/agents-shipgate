from __future__ import annotations

import json

import pytest

from agents_shipgate.cli._helpers import _print_cli_summary
from agents_shipgate.packet.html import render_packet_html
from agents_shipgate.packet.json_packet import load_packet_json
from agents_shipgate.packet.markdown import render_packet_markdown
from agents_shipgate.report.human_order import cold_reader_lead, surface_lead
from agents_shipgate.report.markdown import render_markdown_report
from tests.test_cold_reader_order import _cold_scan


def test_real_mixed_surface_distinguishes_provisional_effects_from_static_evidence(tmp_path, monkeypatch):
    _, report, _, _ = _cold_scan(tmp_path, monkeypatch)
    before = report.model_dump_json()
    text = "\n".join(surface_lead(report).text_lines())
    assert "Conservative effect projections:" in text
    assert "Effect evidence:" in text
    assert "assemble_case_timeline (write) [provisional: unknown effect" in text
    assert "ops.queue_backfill (write) [provisional: protocol default" in text
    assert "issue_goodwill_refund (financial write) [provisional: inference" in text
    assert "record_case_outcome (write) [reviewed declaration" in text
    assert "not pass-eligible" in text
    assert "Provisional signals still require attention" in text
    assert "runtime behavior" in text
    assert report.model_dump_json() == before


def test_existing_human_projections_and_json_keep_the_same_evidence(tmp_path, monkeypatch, capsys):
    repo, report, context, github = _cold_scan(tmp_path, monkeypatch)
    _print_cli_summary(report, "advisory", 0, human_context=context)
    packet = load_packet_json((repo / "reports" / "packet.json").read_text())
    outputs = [capsys.readouterr().out, github,
               render_markdown_report(report, human_context=context),
               render_packet_markdown(packet, human_context=context, cold_lead=cold_reader_lead(report)),
               render_packet_html(packet, human_context=context, cold_lead=cold_reader_lead(report))]
    for text in outputs:
        assert "Conservative effect projections:" in text
        assert "Effect evidence:" in text
        assert "provisional: unknown effect" in text
        assert "Provisional signals still require attention" in text
    wire = json.loads((repo / "reports" / "report.json").read_text())
    action = next(row for row in wire["action_surface_facts"]["actions"]
                  if row["tool_name"] == "assemble_case_timeline")
    assert action["effect"] == "write"
    assert action["semantic_assessment"]["effect"]["status"] == "unknown"
    assert action["semantic_assessment"]["pass_eligible"] is False
    assert wire["release_decision"]["decision"] == "insufficient_evidence"


@pytest.mark.parametrize("state,label", [
    ("declared", "reviewed declaration"), ("structural", "structural evidence"),
    ("inferred", "provisional: inference"), ("protocol_default", "provisional: protocol default"),
    ("unknown", "provisional: unknown effect"), ("conflicting", "conflicting effect evidence"),
    (None, "provisional: evidence unavailable"),
])
def test_every_existing_effect_state_has_explicit_copy(tmp_path, monkeypatch, state, label):
    _, report, _, _ = _cold_scan(tmp_path, monkeypatch)
    action = next(row for row in report.action_surface_facts.actions if row.tool_name == "assemble_case_timeline")
    if state is None:
        action.semantic_assessment = None
    else:
        assessment = action.semantic_assessment
        action.semantic_assessment = assessment.model_copy(update={
            "effect": assessment.effect.model_copy(update={"status": state}),
        })
    text = "\n".join(surface_lead(report).text_lines())
    assert f"assemble_case_timeline (write) [{label}; not pass-eligible]" in text


def test_severity_and_rendering_do_not_upgrade_evidence(tmp_path, monkeypatch):
    _, report, context, _ = _cold_scan(tmp_path, monkeypatch)
    for finding in report.findings:
        finding.severity = "critical"
    before = report.model_dump_json()
    render_markdown_report(report, human_context=context)
    surface_lead(report).text_lines()
    assert report.model_dump_json() == before
    action = next(row for row in report.action_surface_facts.actions if row.tool_name == "assemble_case_timeline")
    assert action.semantic_assessment.effect.status == "unknown"
    assert not action.semantic_assessment.pass_eligible
