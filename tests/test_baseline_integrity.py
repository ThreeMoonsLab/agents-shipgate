"""Tests for the v0.5 baseline integrity surface (M2).

Layered:
- unit tests against ``core.baseline_audit`` (audit log primitives).
- unit tests against ``core.baseline.verify_baseline`` and
  ``baseline_resolved_fingerprints`` using synthetic ``BaselineFile`` and
  ``ReadinessReport`` instances.
- unit tests against ``checks.baseline_integrity.build_findings``.
- integration tests that drive a real scan with ``run_scan`` and a
  tampered baseline, asserting integrity findings appear in the report.
- CLI tests for ``agents-shipgate baseline verify``.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.checks.baseline_integrity import (
    build_findings as build_integrity_findings,
)
from agents_shipgate.checks.baseline_integrity import (
    has_hash_mismatch,
)
from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import (
    BaselineIntegrityIssue,
    baseline_from_report,
    baseline_resolved_fingerprints,
    verify_baseline,
    write_baseline,
)
from agents_shipgate.core.baseline_audit import (
    BaselineAuditEntry,
    append_audit_entry,
    compute_baseline_hash,
    compute_baseline_hash_from_file,
    latest_audit_entry,
    read_audit_log,
    utc_now_isoformat,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
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

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


# --- audit log primitives -------------------------------------------------


def test_compute_baseline_hash_is_deterministic():
    canonical = '{"schema_version":"0.5"}\n'
    assert compute_baseline_hash(canonical) == compute_baseline_hash(canonical)
    assert compute_baseline_hash(canonical).startswith("sha256:")
    assert compute_baseline_hash(canonical) != compute_baseline_hash(
        canonical + " "
    )


def test_audit_log_append_and_read_round_trip(tmp_path):
    log_path = tmp_path / "baseline-audit.log"
    entry = BaselineAuditEntry(
        timestamp=utc_now_isoformat(),
        run_id="run_1",
        scanner_version="0.10.0",
        baseline_path=str(tmp_path / "baseline.json"),
        hash_before=None,
        hash_after="sha256:abc",
        added_fingerprints=["fp_a", "fp_b"],
    )
    append_audit_entry(log_path, entry)
    entry2 = BaselineAuditEntry(
        timestamp=utc_now_isoformat(),
        run_id="run_2",
        scanner_version="0.10.0",
        baseline_path=str(tmp_path / "baseline.json"),
        hash_before="sha256:abc",
        hash_after="sha256:def",
        added_fingerprints=["fp_c"],
        removed_fingerprints=["fp_a"],
    )
    append_audit_entry(log_path, entry2)
    rows = read_audit_log(log_path)
    assert [r.run_id for r in rows] == ["run_1", "run_2"]
    assert latest_audit_entry(log_path).run_id == "run_2"


def test_audit_log_missing_returns_empty(tmp_path):
    assert read_audit_log(tmp_path / "missing.log") == []
    assert latest_audit_entry(tmp_path / "missing.log") is None


# --- baseline_from_report provenance behavior -----------------------------


def _stub_report(*finding_pairs: tuple[str, str]) -> ReadinessReport:
    """Build a minimal ReadinessReport with the given (check_id, tool_name) findings.

    Fingerprints are assigned deterministically here so tests don't have to
    re-run the full scan pipeline.
    """
    findings = []
    for index, (check_id, tool_name) in enumerate(finding_pairs):
        finding = Finding(
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
        findings.append(finding)
    return ReadinessReport(
        run_id="run_stub",
        project={"name": "stub"},
        agent={"name": "test", "id": "agent:test"},
        environment={"target": "staging"},
        summary=ReportSummary(status="advisory_pass"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=findings,
    )


def test_baseline_from_report_stamps_provenance_on_new_entries():
    report = _stub_report(("SHIP-X", "tool_a"), ("SHIP-Y", "tool_b"))
    baseline = baseline_from_report(
        report, scanner_version="9.9.9", now="2026-01-01T00:00:00Z"
    )
    assert baseline.schema_version == "0.5"
    assert all(entry.provenance is not None for entry in baseline.findings)
    p = baseline.findings[0].provenance
    assert p.scanner_version == "9.9.9"
    assert p.run_id == "run_stub"
    assert p.recorded_at == "2026-01-01T00:00:00Z"


def test_baseline_from_report_preserves_prior_provenance():
    """Re-saves must not rotate provenance.recorded_at for existing fingerprints.

    The audit-log-vs-baseline integrity story depends on stable
    ``provenance.run_id`` per fingerprint; rotating it on every save
    would defeat the entire mechanism.
    """
    report = _stub_report(("SHIP-X", "tool_a"))
    first = baseline_from_report(
        report, scanner_version="1.0.0", now="2026-01-01T00:00:00Z"
    )
    # Append a new finding for the second save
    report2 = _stub_report(("SHIP-X", "tool_a"), ("SHIP-Y", "tool_b"))
    second = baseline_from_report(
        report2,
        scanner_version="1.0.1",
        prior_baseline=first,
        now="2026-06-01T00:00:00Z",
    )
    by_fp = {entry.fingerprint: entry for entry in second.findings}
    # Existing entry: provenance preserved
    assert by_fp["fp_test_0000"].provenance.scanner_version == "1.0.0"
    assert by_fp["fp_test_0000"].provenance.recorded_at == "2026-01-01T00:00:00Z"
    # New entry: fresh stamp
    assert by_fp["fp_test_0001"].provenance.scanner_version == "1.0.1"
    assert by_fp["fp_test_0001"].provenance.recorded_at == "2026-06-01T00:00:00Z"


def test_baseline_from_report_upgrades_legacy_entries_without_provenance():
    """Re-saving a v0.4 baseline (no provenance) stamps provenance fresh."""
    report = _stub_report(("SHIP-X", "tool_a"))
    legacy_baseline = BaselineFile(
        schema_version="0.4",
        created_at="2025-12-01T00:00:00Z",
        source_report_run_id="run_legacy",
        findings=[
            BaselineFinding(
                fingerprint="fp_test_0000",
                check_id="SHIP-X",
                tool_name="tool_a",
                severity="high",
                title="legacy",
                provenance=None,
            )
        ],
    )
    upgraded = baseline_from_report(
        report,
        scanner_version="1.0.0",
        prior_baseline=legacy_baseline,
        now="2026-01-01T00:00:00Z",
    )
    assert upgraded.findings[0].provenance is not None
    assert upgraded.findings[0].provenance.recorded_at == "2026-01-01T00:00:00Z"


# --- write_baseline + audit log integration -------------------------------


def test_write_baseline_creates_audit_log_first_save(tmp_path):
    baseline_path = tmp_path / ".agents-shipgate" / "baseline.json"
    audit_path = tmp_path / ".agents-shipgate" / "baseline-audit.log"
    report = _stub_report(("SHIP-X", "tool_a"), ("SHIP-Y", "tool_b"))
    write_baseline(report, baseline_path)
    assert baseline_path.exists()
    assert audit_path.exists()
    rows = read_audit_log(audit_path)
    assert len(rows) == 1
    row = rows[0]
    assert row.hash_before is None
    assert row.hash_after.startswith("sha256:")
    assert row.run_id == "run_stub"
    assert set(row.added_fingerprints) == {"fp_test_0000", "fp_test_0001"}
    assert row.removed_fingerprints == []


def test_write_baseline_appends_audit_row_on_resave(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    audit_path = tmp_path / "baseline-audit.log"
    write_baseline(_stub_report(("SHIP-X", "tool_a")), baseline_path)
    initial_hash = compute_baseline_hash_from_file(baseline_path)
    write_baseline(
        _stub_report(("SHIP-X", "tool_a"), ("SHIP-Y", "tool_b")), baseline_path
    )
    rows = read_audit_log(audit_path)
    assert len(rows) == 2
    assert rows[1].hash_before == initial_hash
    assert rows[1].hash_after == compute_baseline_hash_from_file(baseline_path)
    assert rows[1].added_fingerprints == ["fp_test_0001"]
    assert rows[1].removed_fingerprints == []


def test_write_baseline_audit_log_path_override(tmp_path):
    baseline_path = tmp_path / "b.json"
    audit_path = tmp_path / "elsewhere" / "audit.log"
    write_baseline(
        _stub_report(("SHIP-X", "t")),
        baseline_path,
        audit_log_path=audit_path,
    )
    assert audit_path.exists()
    assert not (tmp_path / "baseline-audit.log").exists()


# --- verify_baseline ------------------------------------------------------


def _bootstrap_baseline(tmp_path: Path) -> tuple[Path, Path]:
    """Create a clean baseline + audit log pair for use in verify tests."""
    baseline_path = tmp_path / "baseline.json"
    write_baseline(
        _stub_report(("SHIP-X", "tool_a"), ("SHIP-Y", "tool_b")),
        baseline_path,
    )
    return baseline_path, tmp_path / "baseline-audit.log"


def test_verify_baseline_clean(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    issues = verify_baseline(baseline_path, audit_path)
    assert issues == []


def test_verify_baseline_detects_hand_edit(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    # Hand-edit: change `created_at` value (a single byte change)
    data = baseline_path.read_text(encoding="utf-8")
    tampered = data.replace('"created_at"', '"created_at" ')  # add a space
    baseline_path.write_text(tampered, encoding="utf-8")
    issues = verify_baseline(baseline_path, audit_path)
    kinds = [issue.kind for issue in issues]
    assert "hash_mismatch" in kinds
    assert has_hash_mismatch(issues)


def test_verify_baseline_missing_audit_log_with_entries(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    audit_path.unlink()
    issues = verify_baseline(baseline_path, audit_path)
    kinds = [issue.kind for issue in issues]
    assert "missing_audit_log" in kinds


def test_verify_baseline_malformed_audit_log_is_integrity_issue(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    audit_path.write_text("not-json\n", encoding="utf-8")
    issues = verify_baseline(baseline_path, audit_path)
    kinds = [issue.kind for issue in issues]
    assert "invalid_audit_log" in kinds
    assert has_hash_mismatch(issues)


def test_verify_baseline_missing_audit_log_empty_baseline_ok(tmp_path):
    """An empty baseline with no audit log is not an integrity violation.

    Fresh / unsaved state — nothing to verify against yet.
    """
    baseline_path = tmp_path / "baseline.json"
    BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run_empty",
    ).model_dump_json(indent=2)  # construct only
    baseline_path.write_text(
        BaselineFile(
            created_at="2026-01-01T00:00:00Z",
            source_report_run_id="run_empty",
        ).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    issues = verify_baseline(baseline_path, tmp_path / "audit.log")
    assert issues == []


def test_verify_baseline_legacy_no_provenance(tmp_path):
    """Loading a v0.4 baseline that lacks provenance produces an integrity flag."""
    baseline_path = tmp_path / "baseline.json"
    legacy = BaselineFile(
        schema_version="0.4",
        created_at="2025-12-01T00:00:00Z",
        source_report_run_id="run_legacy",
        findings=[
            BaselineFinding(
                fingerprint="fp_legacy",
                check_id="SHIP-X",
                tool_name="t",
                severity="high",
                title="legacy",
                provenance=None,
            )
        ],
    )
    baseline_path.write_text(legacy.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # Audit log exists but does not cover this entry
    audit_path = tmp_path / "baseline-audit.log"
    append_audit_entry(
        audit_path,
        BaselineAuditEntry(
            timestamp=utc_now_isoformat(),
            run_id="run_legacy",
            scanner_version="0.10.0",
            baseline_path=str(baseline_path),
            hash_before=None,
            hash_after=compute_baseline_hash_from_file(baseline_path),
        ),
    )
    issues = verify_baseline(baseline_path, audit_path)
    kinds = {issue.kind for issue in issues}
    assert "legacy_no_provenance" in kinds


def test_verify_baseline_entry_no_audit(tmp_path):
    """An entry whose provenance.run_id is not in the audit log fires."""
    baseline_path = tmp_path / "baseline.json"
    baseline = BaselineFile(
        schema_version="0.5",
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run_unknown",
        findings=[
            BaselineFinding(
                fingerprint="fp_x",
                check_id="SHIP-X",
                tool_name="t",
                severity="high",
                title="x",
                provenance=BaselineProvenance(
                    scanner_version="1.0.0",
                    run_id="run_unknown",  # not in audit log below
                    recorded_at="2026-01-01T00:00:00Z",
                ),
            )
        ],
    )
    baseline_path.write_text(
        baseline.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    audit_path = tmp_path / "baseline-audit.log"
    append_audit_entry(
        audit_path,
        BaselineAuditEntry(
            timestamp=utc_now_isoformat(),
            run_id="run_other",  # mismatch
            scanner_version="1.0.0",
            baseline_path=str(baseline_path),
            hash_before=None,
            hash_after=compute_baseline_hash_from_file(baseline_path),
        ),
    )
    issues = verify_baseline(baseline_path, audit_path)
    kinds = {issue.kind for issue in issues}
    assert "entry_no_audit" in kinds


def test_verify_baseline_entry_expired(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    yesterday = date.today() - timedelta(days=5)
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run_x",
        findings=[
            BaselineFinding(
                fingerprint="fp_exp",
                check_id="SHIP-X",
                tool_name="t",
                severity="high",
                title="x",
                provenance=BaselineProvenance(
                    scanner_version="1.0.0",
                    run_id="run_x",
                    recorded_at="2026-01-01T00:00:00Z",
                    expires=yesterday,
                ),
            )
        ],
    )
    baseline_path.write_text(
        baseline.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    audit_path = tmp_path / "baseline-audit.log"
    append_audit_entry(
        audit_path,
        BaselineAuditEntry(
            timestamp=utc_now_isoformat(),
            run_id="run_x",
            scanner_version="1.0.0",
            baseline_path=str(baseline_path),
            hash_before=None,
            hash_after=compute_baseline_hash_from_file(baseline_path),
        ),
    )
    issues = verify_baseline(baseline_path, audit_path, today=date.today())
    kinds = {issue.kind for issue in issues}
    assert "entry_expired" in kinds


def test_verify_baseline_deprecated_check_id(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run_x",
        findings=[
            BaselineFinding(
                fingerprint="fp_dep",
                check_id="SHIP-API-OPERATIONAL-READINESS",  # in LEGACY_CHECK_ID_ALIASES
                tool_name="t",
                severity="high",
                title="legacy",
                provenance=BaselineProvenance(
                    scanner_version="1.0.0",
                    run_id="run_x",
                    recorded_at="2026-01-01T00:00:00Z",
                ),
            )
        ],
    )
    baseline_path.write_text(
        baseline.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    audit_path = tmp_path / "baseline-audit.log"
    append_audit_entry(
        audit_path,
        BaselineAuditEntry(
            timestamp=utc_now_isoformat(),
            run_id="run_x",
            scanner_version="1.0.0",
            baseline_path=str(baseline_path),
            hash_before=None,
            hash_after=compute_baseline_hash_from_file(baseline_path),
        ),
    )
    issues = verify_baseline(baseline_path, audit_path)
    kinds = {issue.kind for issue in issues}
    assert "deprecated_check_id" in kinds


# --- baseline_resolved_fingerprints --------------------------------------


def test_resolved_fingerprints_flags_entries_with_no_current_match():
    """A baseline entry whose check no longer fires is `resolved_not_pruned`."""
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run_x",
        findings=[
            BaselineFinding(
                fingerprint="fp_resolved",
                check_id="SHIP-X",
                tool_name="tool_gone",
                severity="high",
                title="gone",
                provenance=BaselineProvenance(
                    scanner_version="1.0.0",
                    run_id="run_x",
                    recorded_at="2026-01-01T00:00:00Z",
                ),
            )
        ],
    )
    # Current scan has no matching finding
    current_findings: list[Finding] = []
    issues = baseline_resolved_fingerprints(current_findings, baseline)
    assert len(issues) == 1
    assert issues[0].kind == "resolved_not_pruned"
    assert issues[0].fingerprint == "fp_resolved"


# --- build_findings (check module) ---------------------------------------


def _stub_context() -> ScanContext:
    from agents_shipgate.config.loader import load_manifest

    return ScanContext(
        manifest=load_manifest(SAMPLE),
        agent=Agent(id="agent:test", name="test"),
        tools=[],
        config_path=SAMPLE,
    )


def _issue(kind: str, severity: str = "critical") -> BaselineIntegrityIssue:
    return BaselineIntegrityIssue(
        kind=kind,
        default_severity=severity,
        title=f"{kind} title",
        evidence={"kind": kind},
        fingerprint="fp_test",
        check_id="SHIP-X",
        tool_name="tool_a",
    )


def test_build_findings_off_returns_empty():
    context = _stub_context()
    issues = [_issue("hash_mismatch")]
    assert build_integrity_findings(issues, context=context, integrity_mode="off") == []


def test_build_findings_warn_does_not_block():
    context = _stub_context()
    issues = [_issue("hash_mismatch"), _issue("entry_expired", "high")]
    findings = build_integrity_findings(
        issues, context=context, integrity_mode="warn"
    )
    assert len(findings) == 2
    assert all(not f.blocks_release for f in findings)


def test_build_findings_strict_blocks_only_mismatch():
    context = _stub_context()
    issues = [
        _issue("hash_mismatch"),
        _issue("entry_expired", "high"),
        _issue("deprecated_check_id", "low"),
    ]
    findings = build_integrity_findings(
        issues, context=context, integrity_mode="strict"
    )
    by_check_id = {f.check_id: f for f in findings}
    assert by_check_id["SHIP-BASELINE-INTEGRITY-MISMATCH"].blocks_release is True
    assert by_check_id["SHIP-BASELINE-ENTRY-EXPIRED"].blocks_release is False
    assert by_check_id["SHIP-BASELINE-ENTRY-STALE"].blocks_release is False


# --- scan-pipeline integration -------------------------------------------


def _save_baseline_via_scan(tmp_path: Path) -> tuple[Path, Path]:
    """Run a real scan against the bundled sample and save its baseline."""
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
    )
    baseline_path = tmp_path / "baseline.json"
    write_baseline(report, baseline_path)
    return baseline_path, tmp_path / "baseline-audit.log"


def test_scan_emits_integrity_finding_on_tampered_baseline(tmp_path):
    baseline_path, _ = _save_baseline_via_scan(tmp_path)
    # Hand-edit the baseline (adds a stray space; hash changes)
    data = baseline_path.read_text(encoding="utf-8")
    baseline_path.write_text(data.replace("\n", " \n", 1), encoding="utf-8")
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "out2",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
    )
    integrity_findings = [
        f for f in report.findings if f.check_id == "SHIP-BASELINE-INTEGRITY-MISMATCH"
    ]
    assert integrity_findings, "expected at least one integrity-mismatch finding"
    # warn mode (default) → does not block
    assert all(not f.blocks_release for f in integrity_findings)


def test_scan_integrity_off_emits_no_integrity_findings(tmp_path, monkeypatch):
    baseline_path, _ = _save_baseline_via_scan(tmp_path)
    # Tamper to ensure verify would fire
    data = baseline_path.read_text(encoding="utf-8")
    baseline_path.write_text(data.replace("\n", " \n", 1), encoding="utf-8")
    # Monkeypatch the manifest loader to force integrity_mode=off without
    # editing the bundled sample's shipgate.yaml. We splice in the desired
    # value on the loaded model right after load_manifest returns.
    from agents_shipgate.config import loader as loader_mod

    original_load = loader_mod.load_manifest

    def patched(path):
        manifest = original_load(path)
        manifest.baseline.integrity_mode = "off"
        return manifest

    monkeypatch.setattr(loader_mod, "load_manifest", patched)
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "out2",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
    )
    integrity_findings = [
        f for f in report.findings if f.check_id.startswith("SHIP-BASELINE-")
    ]
    assert integrity_findings == []


def test_scan_strict_integrity_sets_blocks_release(tmp_path, monkeypatch):
    baseline_path, _ = _save_baseline_via_scan(tmp_path)
    data = baseline_path.read_text(encoding="utf-8")
    baseline_path.write_text(data.replace("\n", " \n", 1), encoding="utf-8")
    from agents_shipgate.config import loader as loader_mod

    original_load = loader_mod.load_manifest

    def patched(path):
        manifest = original_load(path)
        manifest.baseline.integrity_mode = "strict"
        return manifest

    monkeypatch.setattr(loader_mod, "load_manifest", patched)
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "out2",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
    )
    mismatches = [
        f
        for f in report.findings
        if f.check_id == "SHIP-BASELINE-INTEGRITY-MISMATCH"
    ]
    assert mismatches
    assert all(f.blocks_release for f in mismatches)


def test_scan_strict_integrity_malformed_audit_log_blocks_release(
    tmp_path, monkeypatch
):
    baseline_path, audit_path = _save_baseline_via_scan(tmp_path)
    audit_path.write_text("not-json\n", encoding="utf-8")
    from agents_shipgate.config import loader as loader_mod

    original_load = loader_mod.load_manifest

    def patched(path):
        manifest = original_load(path)
        manifest.baseline.integrity_mode = "strict"
        return manifest

    monkeypatch.setattr(loader_mod, "load_manifest", patched)
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "out2",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
    )
    mismatches = [
        f
        for f in report.findings
        if f.check_id == "SHIP-BASELINE-INTEGRITY-MISMATCH"
    ]
    assert [(f.evidence.get("kind"), f.blocks_release) for f in mismatches] == [
        ("invalid_audit_log", True)
    ]


# --- CLI: baseline verify -------------------------------------------------


def test_cli_baseline_verify_clean(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "baseline",
            "verify",
            "--baseline",
            str(baseline_path),
            "--audit-log",
            str(audit_path),
        ],
    )
    assert result.exit_code == 0
    assert "Baseline OK" in result.stdout


def test_cli_baseline_verify_strict_exits_6_on_mismatch(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    # Tamper
    data = baseline_path.read_text(encoding="utf-8")
    baseline_path.write_text(data.replace("\n", " \n", 1), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "baseline",
            "verify",
            "--baseline",
            str(baseline_path),
            "--audit-log",
            str(audit_path),
            "--strict",
        ],
    )
    assert result.exit_code == 6


def test_cli_baseline_verify_strict_exits_6_on_malformed_audit_log(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    audit_path.write_text("not-json\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "baseline",
            "verify",
            "--baseline",
            str(baseline_path),
            "--audit-log",
            str(audit_path),
            "--strict",
        ],
    )
    assert result.exit_code == 6
    assert "invalid_audit_log" in result.stdout


def test_cli_baseline_verify_json(tmp_path):
    baseline_path, audit_path = _bootstrap_baseline(tmp_path)
    data = baseline_path.read_text(encoding="utf-8")
    baseline_path.write_text(data.replace("\n", " \n", 1), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "baseline",
            "verify",
            "--baseline",
            str(baseline_path),
            "--audit-log",
            str(audit_path),
            "--json",
        ],
    )
    assert result.exit_code == 0  # no --strict, so still 0
    payload = json.loads(result.stdout)
    assert payload["issue_count"] >= 1
    assert any(
        issue["kind"] == "hash_mismatch" for issue in payload["issues"]
    )


def test_cli_baseline_verify_missing_baseline_exits_3(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "baseline",
            "verify",
            "--baseline",
            str(tmp_path / "nope.json"),
        ],
    )
    assert result.exit_code == 3


def test_cli_baseline_save_honors_manifest_audit_log_override(tmp_path):
    sample_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE.parent, sample_dir)
    config = sample_dir / "shipgate.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\nbaseline:\n  integrity_mode: strict\n  audit_log: custom/audit.log\n",
        encoding="utf-8",
    )
    baseline_path = sample_dir / ".agents-shipgate" / "baseline.json"
    custom_audit = sample_dir / ".agents-shipgate" / "custom" / "audit.log"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "baseline",
            "save",
            "--config",
            str(config),
            "--out",
            str(baseline_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert custom_audit.exists()
    assert not (sample_dir / ".agents-shipgate" / "baseline-audit.log").exists()
    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
    )
    assert [
        f for f in report.findings if f.check_id.startswith("SHIP-BASELINE-")
    ] == []
