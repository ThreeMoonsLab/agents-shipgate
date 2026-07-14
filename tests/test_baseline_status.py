"""Exception-workflow tests: baseline approval metadata + debt status.

Covers the v0.6 provenance contract (`owner` + CLI-settable
`reason`/`expires`), the fill-only `--apply-to-existing` semantics, and
the `baseline status` aging report with its governance gates.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.baseline import (
    apply_baseline,
    baseline_from_report,
    baseline_status_payload,
    baseline_status_violations,
)
from agents_shipgate.core.policy_evidence import finding_support, predicate_evidence
from agents_shipgate.schemas.baseline import (
    BaselineFile,
    BaselineFinding,
    BaselineProvenance,
)
from agents_shipgate.schemas.report import (
    Finding,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)

runner = CliRunner()


def _stub_report(*finding_pairs: tuple[str, str]) -> ReadinessReport:
    findings = []
    for index, (check_id, tool_name) in enumerate(finding_pairs):
        findings.append(
            Finding(
                check_id=check_id,
                title=f"{check_id} on {tool_name}",
                severity="high",
                category="test",
                tool_name=tool_name,
                evidence={"i": index},
                confidence="high",
                provenance_kind="static_declaration",
                recommendation="stub",
                fingerprint=f"fp_test_{index:04d}",
                id=f"fp_test_{index:04d}",
            )
        )
    return ReadinessReport(
        run_id="run_stub",
        project={"name": "stub"},
        agent={"name": "test", "id": "agent:test"},
        environment={"target": "staging"},
        summary=ReportSummary(status="advisory_pass"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=findings,
    )


def _provenance(**overrides: object) -> BaselineProvenance:
    payload: dict[str, object] = {
        "scanner_version": "1.0.0",
        "run_id": "run_a",
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    payload.update(overrides)
    return BaselineProvenance.model_validate(payload)


def _baseline(*findings: BaselineFinding) -> BaselineFile:
    return BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run_a",
        findings=list(findings),
    )


def _entry(
    fingerprint: str, provenance: BaselineProvenance | None
) -> BaselineFinding:
    return BaselineFinding(
        fingerprint=fingerprint,
        check_id="SHIP-TEST",
        severity="high",
        title="Example",
        provenance=provenance,
    )


# --- provenance stamping ----------------------------------------------------


def test_save_metadata_stamped_on_new_entries() -> None:
    report = _stub_report(("SHIP-X", "tool_a"))
    baseline = baseline_from_report(
        report,
        scanner_version="9.9.9",
        now="2026-06-12T00:00:00Z",
        owner="alice",
        reason="accepted pending Q3 fix",
        expires=date(2026, 9, 30),
    )
    assert baseline.schema_version == "0.8"
    provenance = baseline.findings[0].provenance
    assert provenance.owner == "alice"
    assert provenance.reason == "accepted pending Q3 fix"
    assert provenance.expires == date(2026, 9, 30)


def test_baseline_support_hash_prevents_evidence_laundering() -> None:
    report = _stub_report(("ORG-POLICY", "tool_a"))
    original_support = finding_support(
        [
            predicate_evidence(
                "effect",
                "matched",
                observed="write",
                confidence="high",
                evidence_bases=["protocol_structure"],
                policy_eligible=True,
            )
        ]
    )
    report.findings[0].support = original_support
    baseline = baseline_from_report(
        report,
        scanner_version="9.9.9",
        now="2026-06-12T00:00:00Z",
    )
    assert baseline.findings[0].support_hash == original_support.support_hash

    report.findings[0].support = finding_support(
        [
            predicate_evidence(
                "effect",
                "matched",
                observed="destructive",
                confidence="high",
                evidence_bases=["protocol_structure"],
                policy_eligible=True,
            )
        ]
    )
    summary = apply_baseline(
        report.findings,
        baseline,
        display_path="baseline.json",
    )

    assert report.findings[0].baseline_status == "new"
    assert summary.matched_count == 0


def test_resave_without_flags_preserves_owner() -> None:
    report = _stub_report(("SHIP-X", "tool_a"))
    first = baseline_from_report(
        report, scanner_version="1.0.0", now="2026-01-01T00:00:00Z", owner="alice"
    )
    second = baseline_from_report(
        report, scanner_version="1.0.1", prior_baseline=first, now="2026-06-01T00:00:00Z"
    )
    assert second.findings[0].provenance.owner == "alice"
    assert second.findings[0].provenance.recorded_at == "2026-01-01T00:00:00Z"


def test_apply_to_existing_fills_missing_never_overwrites() -> None:
    report = _stub_report(("SHIP-X", "tool_a"), ("SHIP-Y", "tool_b"))
    first = baseline_from_report(
        report, scanner_version="1.0.0", now="2026-01-01T00:00:00Z"
    )
    # Give entry 0 an owner already; entry 1 stays unowned.
    first.findings[0].provenance.owner = "alice"

    second = baseline_from_report(
        report,
        scanner_version="1.0.1",
        prior_baseline=first,
        now="2026-06-01T00:00:00Z",
        owner="bob",
        reason="quarterly acknowledgement",
        expires=date(2026, 12, 31),
        apply_to_existing=True,
    )
    by_fp = {entry.fingerprint: entry for entry in second.findings}
    # Pre-set owner never overwritten; missing fields filled.
    assert by_fp["fp_test_0000"].provenance.owner == "alice"
    assert by_fp["fp_test_0000"].provenance.reason == "quarterly acknowledgement"
    # Unowned entry gains the new owner; history preserved exactly.
    assert by_fp["fp_test_0001"].provenance.owner == "bob"
    assert by_fp["fp_test_0001"].provenance.recorded_at == "2026-01-01T00:00:00Z"
    assert by_fp["fp_test_0001"].provenance.run_id == "run_stub"
    assert by_fp["fp_test_0001"].provenance.scanner_version == "1.0.0"


def test_legacy_baseline_without_owner_still_loads() -> None:
    legacy = {
        "schema_version": "0.5",
        "created_at": "2026-01-01T00:00:00Z",
        "source_report_run_id": "run-1",
        "findings": [
            {
                "fingerprint": "abc",
                "check_id": "SHIP-TEST",
                "severity": "high",
                "title": "Example",
                "provenance": {
                    "scanner_version": "1.0.0",
                    "run_id": "run-1",
                    "recorded_at": "2026-01-01T00:00:00Z",
                },
            }
        ],
    }
    baseline = BaselineFile.model_validate(legacy)
    assert baseline.findings[0].provenance.owner is None


# --- status payload ---------------------------------------------------------


def test_status_payload_ages_and_expiry() -> None:
    baseline = _baseline(
        _entry("fp_owned", _provenance(owner="alice", expires=date(2026, 7, 1))),
        _entry("fp_expired", _provenance(owner="bob", expires=date(2026, 5, 1))),
        _entry("fp_bare", _provenance()),
        _entry("fp_legacy", None),
    )
    payload = baseline_status_payload(baseline, as_of=date(2026, 6, 12))
    entries = {entry["fingerprint"]: entry for entry in payload["entries"]}

    assert entries["fp_owned"]["age_days"] == 162
    assert entries["fp_owned"]["days_until_expiry"] == 19
    assert entries["fp_owned"]["expired"] is False
    assert entries["fp_expired"]["expired"] is True
    assert entries["fp_bare"]["unowned"] is True
    assert entries["fp_bare"]["no_expiry"] is True
    assert entries["fp_legacy"]["age_days"] is None

    summary = payload["summary"]
    assert summary["total"] == 4
    assert summary["owned"] == 2
    assert summary["unowned"] == 2
    assert summary["expired"] == 1
    assert summary["expiring_within_days"] == 1
    assert summary["no_provenance"] == 1
    assert summary["oldest_age_days"] == 162


def test_status_violations_fail_closed_for_legacy_entries() -> None:
    baseline = _baseline(
        _entry("fp_good", _provenance(owner="alice", expires=date(2026, 12, 31))),
        _entry("fp_bare", _provenance()),
        _entry("fp_legacy", None),
        _entry("fp_expired", _provenance(owner="bob", expires=date(2026, 1, 1))),
    )
    payload = baseline_status_payload(baseline, as_of=date(2026, 6, 12))
    violations = baseline_status_violations(
        payload, require_owner=True, require_expiry=True, max_age_days=30
    )
    kinds = {(v["fingerprint"], v["kind"]) for v in violations}
    assert ("fp_bare", "unowned") in kinds
    assert ("fp_bare", "no_expiry") in kinds
    assert ("fp_legacy", "unowned") in kinds
    assert ("fp_legacy", "age_exceeded") in kinds  # unknown age fails closed
    assert ("fp_expired", "expired") in kinds
    assert ("fp_good", "age_exceeded") in kinds  # recorded 2026-01-01, >30d old
    assert not any(fp == "fp_good" and k != "age_exceeded" for fp, k in kinds)


# --- CLI ---------------------------------------------------------------------


def _write_baseline_file(tmp_path: Path, baseline: BaselineFile) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(
        baseline.model_dump_json(indent=2, exclude_none=False) + "\n",
        encoding="utf-8",
    )
    return path


def test_cli_status_advisory_exit_0_and_json_deterministic(tmp_path: Path) -> None:
    path = _write_baseline_file(
        tmp_path, _baseline(_entry("fp_bare", _provenance()))
    )
    args = [
        "baseline",
        "status",
        "--baseline",
        str(path),
        "--as-of",
        "2026-06-12",
        "--json",
    ]
    one = runner.invoke(app, args)
    two = runner.invoke(app, args)
    assert one.exit_code == 0, one.output  # advisory without gate flags
    assert one.output == two.output
    payload = json.loads(one.output)
    assert payload["debt_status_schema_version"] == "0.1"
    assert payload["violations"] == []


def test_cli_status_gate_exits_20_then_0_after_acknowledgement(tmp_path: Path) -> None:
    path = _write_baseline_file(
        tmp_path, _baseline(_entry("fp_bare", _provenance()))
    )
    gated = [
        "baseline",
        "status",
        "--baseline",
        str(path),
        "--as-of",
        "2026-06-12",
        "--require-owner",
    ]
    result = runner.invoke(app, gated)
    assert result.exit_code == 20
    assert "unowned" in result.output
    assert "--apply-to-existing" in result.output  # re-acknowledge hint

    acknowledged = _baseline(_entry("fp_bare", _provenance(owner="alice")))
    _write_baseline_file(tmp_path, acknowledged)
    result = runner.invoke(app, gated)
    assert result.exit_code == 0, result.output


def test_cli_status_invalid_as_of_exits_2(tmp_path: Path) -> None:
    path = _write_baseline_file(tmp_path, _baseline())
    result = runner.invoke(
        app,
        ["baseline", "status", "--baseline", str(path), "--as-of", "junk"],
    )
    assert result.exit_code == 2


def test_cli_status_missing_baseline_exits_3(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["baseline", "status", "--baseline", str(tmp_path / "missing.json")],
    )
    assert result.exit_code == 3


def test_blank_owner_is_unowned_and_fails_require_owner(tmp_path: Path) -> None:
    # A blank/whitespace owner must never satisfy --require-owner (PR #205
    # review). The CLI rejects blank flags at save time; this covers files
    # produced by any other means.
    baseline = _baseline(
        _entry("fp_empty", _provenance(owner="")),
        _entry("fp_spaces", _provenance(owner="   ")),
    )
    payload = baseline_status_payload(baseline, as_of=date(2026, 6, 12))
    assert all(entry["unowned"] for entry in payload["entries"])
    assert all(entry["owner"] is None for entry in payload["entries"])

    path = _write_baseline_file(tmp_path, baseline)
    result = runner.invoke(
        app,
        [
            "baseline",
            "status",
            "--baseline",
            str(path),
            "--as-of",
            "2026-06-12",
            "--require-owner",
        ],
    )
    assert result.exit_code == 20


def test_apply_to_existing_fills_blank_owner(tmp_path: Path) -> None:
    # Blank metadata counts as missing for the fill-only pass too — same
    # rule as `baseline status` — so the recommended repair path actually
    # clears --require-owner. Whitespace-only is the trap: it is truthy,
    # so a bare `or` keeps it while status treats it as unowned.
    report = _stub_report(("SHIP-X", "tool_a"))
    for hollow in ("", "   "):
        first = baseline_from_report(
            report, scanner_version="1.0.0", now="2026-01-01T00:00:00Z"
        )
        first.findings[0].provenance.owner = hollow

        second = baseline_from_report(
            report,
            scanner_version="1.0.1",
            prior_baseline=first,
            now="2026-06-01T00:00:00Z",
            owner="alice",
            apply_to_existing=True,
        )
        assert second.findings[0].provenance.owner == "alice", repr(hollow)

        payload = baseline_status_payload(second, as_of=date(2026, 6, 12))
        assert baseline_status_violations(payload, require_owner=True) == []


def test_apply_to_existing_keeps_padded_real_owner(tmp_path: Path) -> None:
    # A real value with incidental padding is content, not a hollow
    # approval — fill-only must not overwrite it.
    report = _stub_report(("SHIP-X", "tool_a"))
    first = baseline_from_report(
        report, scanner_version="1.0.0", now="2026-01-01T00:00:00Z"
    )
    first.findings[0].provenance.owner = "  alice  "

    second = baseline_from_report(
        report,
        scanner_version="1.0.1",
        prior_baseline=first,
        now="2026-06-01T00:00:00Z",
        owner="bob",
        apply_to_existing=True,
    )
    assert second.findings[0].provenance.owner == "  alice  "


def test_cli_save_blank_owner_or_reason_exits_2(tmp_path: Path) -> None:
    for flag, value in (("--owner", ""), ("--owner", "   "), ("--reason", " ")):
        result = runner.invoke(
            app,
            ["baseline", "save", "-c", str(tmp_path / "shipgate.yaml"), flag, value],
        )
        assert result.exit_code == 2, (flag, value, result.output)
        assert "cannot be blank" in result.output


def test_cli_save_invalid_expires_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["baseline", "save", "-c", str(tmp_path / "shipgate.yaml"), "--expires", "soon"],
    )
    assert result.exit_code == 2
    assert "ISO date" in result.output


def test_cli_save_apply_to_existing_requires_metadata(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "baseline",
            "save",
            "-c",
            str(tmp_path / "shipgate.yaml"),
            "--apply-to-existing",
        ],
    )
    assert result.exit_code == 2
    assert "--apply-to-existing" in result.output
