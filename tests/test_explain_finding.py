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

# Core fields the payload MUST contain. The payload is now built from
# `Finding.model_dump()` plus three derived fields, so the full set
# expands automatically when Finding gains new fields. Pin the contract
# as a "must contain" subset rather than an exact match — that's what
# downstream agents actually depend on.
REQUIRED_PAYLOAD_KEYS = frozenset(
    {
        # Core finding identity
        "fingerprint",
        "id",
        "check_id",
        "title",
        "severity",
        "category",
        # Tool linkage
        "tool_name",
        "tool_id",
        "agent_id",
        # Evidence and remediation
        "evidence",
        "source",
        "patches",
        "confidence",
        "recommendation",
        "agent_action",
        "autofix_safe",
        "requires_human_review",
        "suggested_patch_kind",
        "docs_url",
        # Suppression / baseline
        "suppressed",
        "suppression_reason",
        "baseline_status",
        # Derived overlay (not in the Finding model)
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
    """A real scan + fingerprint produces a payload that contains every
    documented key and a non-empty explanation. The payload mirrors the
    full `Finding` shape (via `model_dump`) plus the derived overlay
    (`metadata`, `explanation`, `source_report`), so future additive
    Finding fields flow through without a breaking schema change."""
    report_path, findings = _scan_into(tmp_path)
    fp = next(f["fingerprint"] for f in findings if not f["suppressed"])
    payload = explain_finding_payload(fingerprint=fp, report_path=report_path)

    missing = REQUIRED_PAYLOAD_KEYS - set(payload)
    assert not missing, (
        f"explain-finding payload missing required keys: {sorted(missing)}.\n"
        f"  required: {sorted(REQUIRED_PAYLOAD_KEYS)}\n"
        f"  got:      {sorted(payload)}"
    )
    assert payload["fingerprint"] == fp
    assert payload["explanation"], "Explanation must be non-empty."
    assert payload["check_id"]


def test_payload_mirrors_full_finding_shape(tmp_path):
    """The payload must include `source`, `patches`, `confidence`, and
    `agent_id` — fields that earlier hand-picking dropped. The
    explain-finding contract says the payload mirrors canonical Finding
    fields; this pins that promise so a future refactor that
    re-introduces hand-picking trips a regression (#58 review P2.1)."""
    report_path, findings = _scan_into(tmp_path)
    # Pick a finding that actually has patches (the support_refund
    # sample has several when scanned with --suggest-patches).
    fp = next(
        f["fingerprint"] for f in findings if f.get("patches")
    )
    payload = explain_finding_payload(fingerprint=fp, report_path=report_path)

    for field in ("source", "patches", "confidence", "agent_id"):
        assert field in payload, (
            f"Payload must include `{field}` — earlier hand-picking "
            f"dropped this field. Got: {sorted(payload)!r}"
        )
    # `patches` should round-trip from the original finding (non-empty
    # for this sample).
    raw = next(f for f in findings if f["fingerprint"] == fp)
    assert payload["patches"] == raw["patches"]


def test_source_report_is_absolute(tmp_path):
    """`source_report` is documented as an absolute path. A relative
    `--from` (e.g. the canonical default
    `agents-shipgate-reports/report.json`) must resolve to absolute
    before round-tripping into the payload (#58 review P3)."""
    report_path, findings = _scan_into(tmp_path)
    fp = next(f["fingerprint"] for f in findings if f["fingerprint"])

    # Force a relative path that points at the same file.
    import os

    relative = Path(os.path.relpath(report_path, Path.cwd()))
    payload = explain_finding_payload(fingerprint=fp, report_path=relative)

    assert Path(payload["source_report"]).is_absolute(), (
        f"source_report must be absolute; got {payload['source_report']!r}"
    )
    # And it should point at the same file (resolved).
    assert Path(payload["source_report"]).resolve() == report_path.resolve()


def test_pre_v012_report_is_rejected(tmp_path):
    """A v0.11-shaped report (lacking per-finding `agent_action`) must
    be rejected by `explain-finding` — the v0.12 contract says the
    explanation is action-aware, and silently accepting a stale report
    would yield `agent_action: null` and drop that sentence
    (#58 review P2.2)."""
    v11_payload = {
        "schema_version": "0.1",
        "report_schema_version": "0.11",
        "run_id": "r",
        "manifest_dir": "/tmp",
        "project": {},
        "agent": {},
        "environment": {},
        "summary": {
            "status": "review_required",
            "critical_count": 0,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
            "info_count": 0,
            "suppressed_count": 0,
            "human_review_recommended": False,
            "evidence_coverage": "static",
        },
        "tool_surface": {"total_tools": 0, "high_risk_tools": 0},
        "findings": [
            {
                "id": "fp_test",
                "fingerprint": "fp_test",
                "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
                "title": "t",
                "severity": "medium",
                "category": "documentation",
                "evidence": {},
                "confidence": "medium",
                "recommendation": "r",
                "suppressed": False,
            }
        ],
        "recommended_actions": [],
        "generated_reports": {},
    }
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(v11_payload), encoding="utf-8")

    with pytest.raises(ValueError, match=r"requires report_schema_version >= 0\.12"):
        explain_finding_payload(
            fingerprint="fp_test", report_path=stale_path
        )


def test_missing_schema_version_is_rejected(tmp_path):
    """A `report.json` without a `report_schema_version` string fails
    cleanly — preferable to a downstream KeyError or a Pydantic
    validation barrage."""
    bad_payload = {"run_id": "r", "findings": []}
    path = tmp_path / "no-version.json"
    path.write_text(json.dumps(bad_payload), encoding="utf-8")

    with pytest.raises(
        ValueError, match=r"agents-shipgate report\.json"
    ):
        explain_finding_payload(fingerprint="fp_x", report_path=path)


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
