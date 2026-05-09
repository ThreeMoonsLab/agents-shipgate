"""Pin the v0.12 ``agents-shipgate explain-finding`` CLI surface.

Covers:
- Happy path: a fingerprint from a real scan resolves to a payload
  with the canonical keys and a non-empty templated explanation.
- Bad fingerprint: exit code 2 + structured agent-mode error with a
  close-match suggestion when one exists.
- Missing/malformed report path: exit code 3 + structured error.
- Determinism: identical fingerprint + identical report → identical
  payload (so callers can cache without surprises).
- Templated explanation always names the affected tool (when one
  exists), severity, recommendation, and an action-aware sentence
  that matches the finding's `agent_action`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.explain_finding import (
    FingerprintNotFound,
    _render_explanation,
    explain_finding_payload,
)
from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.models import Finding

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_MANIFEST = REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"

CANONICAL_PAYLOAD_KEYS = frozenset(
    {
        "fingerprint",
        "id",
        "check_id",
        "title",
        "severity",
        "category",
        "tool_name",
        "tool_id",
        "evidence",
        "recommendation",
        "agent_action",
        "autofix_safe",
        "requires_human_review",
        "suggested_patch_kind",
        "docs_url",
        "suppressed",
        "suppression_reason",
        "baseline_status",
        "metadata",
        "explanation",
        "source_report",
    }
)


def _scan_into(tmp_path: Path) -> tuple[Path, list[dict]]:
    """Run a real scan against the support_refund sample and return
    (report_path, findings_list)."""
    out = tmp_path / "reports"
    run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    payload = json.loads((out / "report.json").read_text("utf-8"))
    return out / "report.json", payload["findings"]


def test_happy_path_payload_shape(tmp_path):
    """A real scan + fingerprint produces a payload with exactly the
    documented keys and a non-empty explanation."""
    report_path, findings = _scan_into(tmp_path)
    fp = next(f["fingerprint"] for f in findings if not f["suppressed"])
    payload = explain_finding_payload(fingerprint=fp, report_path=report_path)

    assert set(payload) == CANONICAL_PAYLOAD_KEYS, (
        f"explain-finding payload diverged from documented keys.\n"
        f"  expected: {sorted(CANONICAL_PAYLOAD_KEYS)}\n"
        f"  got:      {sorted(payload)}"
    )
    assert payload["fingerprint"] == fp
    assert payload["explanation"], "Explanation must be non-empty."
    assert payload["check_id"]
    assert payload["source_report"] == str(report_path)


def test_metadata_populated_for_known_check_ids(tmp_path):
    """For every finding whose check_id is in the catalog, the
    `metadata` field is a dict (not None) with the canonical
    CheckMetadata keys. Catches a regression where the catalog lookup
    silently drops to None."""
    report_path, findings = _scan_into(tmp_path)
    for raw in findings:
        if not raw["fingerprint"]:
            continue
        payload = explain_finding_payload(
            fingerprint=raw["fingerprint"], report_path=report_path
        )
        # Every check_id in the support_refund sample is in the
        # built-in catalog, so metadata should always populate.
        assert payload["metadata"] is not None, (
            f"Metadata missing for {payload['check_id']!r} "
            f"(fingerprint {payload['fingerprint']!r})."
        )
        assert payload["metadata"]["id"] == payload["check_id"]


def test_payload_is_deterministic(tmp_path):
    """Two calls with the same inputs return byte-identical payloads.
    Cached / repeated lookups must not drift due to dict iteration
    order or non-deterministic catalog initialization."""
    report_path, findings = _scan_into(tmp_path)
    fp = next(f["fingerprint"] for f in findings if not f["suppressed"])

    a = explain_finding_payload(fingerprint=fp, report_path=report_path)
    b = explain_finding_payload(fingerprint=fp, report_path=report_path)

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), (
        "explain_finding_payload is not deterministic across calls."
    )


def test_unknown_fingerprint_raises_with_suggestion(tmp_path):
    """A close-but-wrong fingerprint must raise ``FingerprintNotFound``
    carrying a suggested correction so the agent can recover without
    walking findings[] itself."""
    report_path, findings = _scan_into(tmp_path)
    real_fp = next(f["fingerprint"] for f in findings if f["fingerprint"])
    # Mutate one character — close enough for difflib to suggest the original.
    mutated = real_fp[:-1] + ("0" if real_fp[-1] != "0" else "1")

    with pytest.raises(FingerprintNotFound) as exc_info:
        explain_finding_payload(fingerprint=mutated, report_path=report_path)

    assert exc_info.value.suggestion == real_fp, (
        f"Expected suggestion={real_fp!r}; got {exc_info.value.suggestion!r}."
    )


def test_unknown_fingerprint_with_no_close_match(tmp_path):
    """Completely-unrelated fingerprint string yields suggestion=None."""
    report_path, _findings = _scan_into(tmp_path)
    with pytest.raises(FingerprintNotFound) as exc_info:
        explain_finding_payload(
            fingerprint="fp_xxxxxxxxxxxxxxxx", report_path=report_path
        )
    assert exc_info.value.suggestion is None


def test_missing_report_raises_value_error(tmp_path):
    """A non-existent report path raises ValueError so the CLI maps
    it to exit 3 (input_parse_error)."""
    with pytest.raises(ValueError, match="report file not found"):
        explain_finding_payload(
            fingerprint="fp_anything",
            report_path=tmp_path / "nope.json",
        )


def test_malformed_report_raises_value_error(tmp_path):
    """A path that exists but isn't valid JSON raises ValueError."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        explain_finding_payload(fingerprint="fp_anything", report_path=bad)


def test_cli_exit_codes_and_json_shape(tmp_path):
    """End-to-end: the typer CLI returns exit 0 for a happy path,
    2 for unknown fingerprint, 3 for unreadable report. The JSON
    output round-trips through json.loads."""
    report_path, findings = _scan_into(tmp_path)
    fp = next(f["fingerprint"] for f in findings if not f["suppressed"])
    runner = CliRunner()

    happy = runner.invoke(
        app,
        ["explain-finding", fp, "--from", str(report_path), "--json"],
    )
    assert happy.exit_code == 0, happy.output
    parsed = json.loads(happy.stdout)
    assert parsed["fingerprint"] == fp

    bad_fp = runner.invoke(
        app,
        ["explain-finding", "fp_xxxxxxxxxxxxxxxx", "--from", str(report_path)],
    )
    assert bad_fp.exit_code == 2

    missing = runner.invoke(
        app,
        ["explain-finding", fp, "--from", str(tmp_path / "missing.json")],
    )
    assert missing.exit_code == 3


def test_explanation_names_tool_severity_and_action():
    """The templated explanation must reliably name the affected tool,
    the severity, the check_id, and an action-aware closing sentence.
    Pinned so a refactor doesn't drop one of those signals — the
    prompt expects all of them when it asks the agent to summarize a
    finding."""
    finding = Finding(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        title="High-risk tool lacks a declared approval policy.",
        severity="critical",
        category="policy",
        tool_name="stripe.create_refund",
        recommendation="Declare an approval policy or remove the tool.",
        agent_action="escalate_to_human",
        suppressed=False,
        evidence={"risk_tags": ["financial_action", "destructive"]},
        autofix_safe=False,
        requires_human_review=True,
    )
    text = _render_explanation(finding, metadata=None)

    assert "stripe.create_refund" in text
    assert "critical" in text
    assert "SHIP-POLICY-APPROVAL-MISSING" in text
    assert "approval policy" in text  # from recommendation
    # Action-aware closing sentence
    assert "human judgment" in text or "human review" in text or "no machine" in text.lower()


def test_explanation_handles_suppressed_findings():
    """Suppressed findings still get a coherent explanation and have
    the suppression status spelled out."""
    finding = Finding(
        check_id="SHIP-DOC-MISSING-DESCRIPTION",
        title="Tool description is missing or too short.",
        severity="medium",
        category="documentation",
        tool_name="legacy_search",
        recommendation="Add a clear capability description.",
        agent_action="informational",
        suppressed=True,
        suppression_reason="tool deprecated 2026-Q2",
        evidence={"description_length": 0},
    )
    text = _render_explanation(finding, metadata=None)
    assert "suppressed" in text.lower()
    assert "tool deprecated 2026-Q2" in text
