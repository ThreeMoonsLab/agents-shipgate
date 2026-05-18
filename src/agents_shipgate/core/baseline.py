from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_shipgate import __version__ as _SCANNER_VERSION
from agents_shipgate.core.baseline_audit import (
    DEFAULT_AUDIT_LOG_PATH,
    BaselineAuditEntry,
    append_audit_entry,
    compute_baseline_hash,
    compute_baseline_hash_from_file,
    latest_audit_entry,
    read_audit_log,
    utc_now_isoformat,
)
from agents_shipgate.core.check_ids import (
    LEGACY_CHECK_ID_ALIASES,
    expands_to_check_id,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import (
    ActionSurfaceFacts,
    BaselineSummary,
    Finding,
    ReadinessReport,
    Severity,
    ToolSurfaceFacts,
)

BASELINE_SCHEMA_VERSION = "0.5"
# v0.5 self-describing entry provenance. Older versions (0.2/0.3/0.4)
# load with `BaselineFinding.provenance = None`; the integrity check
# then flags them as `SHIP-BASELINE-ENTRY-STALE` (kind="legacy_no_provenance")
# in warn/strict modes. Re-saving with `baseline save` upgrades the
# file to 0.5 and stamps provenance on every entry.


class BaselineProvenance(BaseModel):
    """Self-describing record of when and why a baseline entry was added.

    Written by `agents-shipgate baseline save` for every new fingerprint
    in the baseline. Existing fingerprints keep their original provenance
    on re-save; only newly-added ones get a fresh `recorded_at` / `run_id`.

    `expires` is optional and reviewer-controlled: when set, the
    integrity check emits `SHIP-BASELINE-ENTRY-EXPIRED` past that date.
    `reason` is free-form; reviewers should set it when the entry was
    deliberately accepted (not just snapshotted).
    """

    model_config = ConfigDict(extra="forbid")

    scanner_version: str
    run_id: str
    recorded_at: str
    reason: str | None = None
    expires: date | None = None


class BaselineFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    check_id: str
    tool_name: str | None = None
    severity: Severity
    title: str
    # v0.5 additive: when None, the entry pre-dates the v0.5 provenance
    # contract (loaded from 0.2/0.3/0.4) or was constructed by a test
    # helper. Re-saving via `baseline save` populates it.
    provenance: BaselineProvenance | None = None


class BaselineFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.2", "0.3", "0.4", "0.5"] = BASELINE_SCHEMA_VERSION
    project: dict[str, object] = Field(default_factory=dict)
    agent: dict[str, object] = Field(default_factory=dict)
    created_at: str
    source_report_run_id: str
    findings: list[BaselineFinding] = Field(default_factory=list)
    tool_surface_facts: ToolSurfaceFacts | None = None
    action_surface_facts: ActionSurfaceFacts | None = None
    notes: list[str] = Field(default_factory=list)


def baseline_from_report(
    report: ReadinessReport,
    *,
    scanner_version: str | None = None,
    prior_baseline: BaselineFile | None = None,
    now: str | None = None,
) -> BaselineFile:
    """Build a v0.5 baseline file from a scan report.

    Provenance handling:

    - When ``prior_baseline`` is provided and an entry's fingerprint
      already appears there with a populated ``provenance``, the prior
      provenance is preserved verbatim — only newly-added fingerprints
      get a fresh provenance stamp. This keeps re-saves idempotent for
      the audit log and lets reviewer-set ``reason`` / ``expires`` survive
      subsequent saves.
    - When the prior entry has no provenance (loaded from a 0.2/0.3/0.4
      baseline), the entry is upgraded with a fresh provenance stamp.
      This is the migration path: re-saving a legacy baseline stamps
      provenance on every entry as of the next scan.

    ``scanner_version`` and ``now`` are injectable for deterministic
    testing. In production they default to the package version and UTC
    now.
    """
    scanner_version = scanner_version or _SCANNER_VERSION
    recorded_at = now or utc_now_isoformat()
    prior_by_fp: dict[str, BaselineFinding] = {
        entry.fingerprint: entry
        for entry in (prior_baseline.findings if prior_baseline else [])
    }
    findings: list[BaselineFinding] = []
    for finding in _active_findings(report.findings):
        fingerprint = finding.fingerprint or finding.id
        if not fingerprint:
            continue
        prior_entry = prior_by_fp.get(fingerprint)
        if prior_entry is not None and prior_entry.provenance is not None:
            provenance = prior_entry.provenance
        else:
            provenance = BaselineProvenance(
                scanner_version=scanner_version,
                run_id=report.run_id,
                recorded_at=recorded_at,
            )
        findings.append(
            BaselineFinding(
                fingerprint=fingerprint,
                check_id=finding.check_id,
                tool_name=finding.tool_name,
                severity=finding.severity,
                title=finding.title,
                provenance=provenance,
            )
        )
    return BaselineFile(
        project=report.project,
        agent=report.agent,
        created_at=recorded_at,
        source_report_run_id=report.run_id,
        tool_surface_facts=report.tool_surface_facts,
        action_surface_facts=report.action_surface_facts,
        findings=findings,
    )


def write_baseline(
    report: ReadinessReport,
    path: Path,
    *,
    scanner_version: str | None = None,
    audit_log_path: Path | None = None,
) -> BaselineFile:
    """Write a v0.5 baseline + append a row to the audit log.

    The audit log defaults to ``<path.parent>/baseline-audit.log``. Pass
    ``audit_log_path`` explicitly to override (tests use a tmp path).
    The audit row records the SHA-256 of the file before and after the
    write so ``verify_baseline`` can detect hand-edits.

    When no prior baseline exists, ``hash_before`` is ``None``. Re-saves
    that change nothing still produce an audit row with empty
    ``added_fingerprints`` / ``removed_fingerprints`` — that is the
    intended ledger semantics.
    """
    scanner_version = scanner_version or _SCANNER_VERSION
    prior_baseline = _try_load_baseline(path)
    baseline = baseline_from_report(
        report,
        scanner_version=scanner_version,
        prior_baseline=prior_baseline,
    )
    baseline = _preserve_created_at_when_content_matches(baseline, prior_baseline)
    hash_before: str | None = None
    if path.exists():
        try:
            hash_before = compute_baseline_hash_from_file(path)
        except OSError:
            hash_before = None
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = baseline.model_dump_json(indent=2, exclude_none=False) + "\n"
    path.write_text(canonical, encoding="utf-8")
    hash_after = compute_baseline_hash(canonical)
    if audit_log_path is None:
        audit_log_path = path.parent / "baseline-audit.log"
    prior_fps: set[str] = {
        entry.fingerprint
        for entry in (prior_baseline.findings if prior_baseline else [])
    }
    new_fps: set[str] = {entry.fingerprint for entry in baseline.findings}
    audit_entry = BaselineAuditEntry(
        timestamp=utc_now_isoformat(),
        run_id=report.run_id,
        scanner_version=scanner_version,
        baseline_path=str(path),
        hash_before=hash_before,
        hash_after=hash_after,
        added_fingerprints=sorted(new_fps - prior_fps),
        removed_fingerprints=sorted(prior_fps - new_fps),
    )
    append_audit_entry(audit_log_path, audit_entry)
    return baseline


def _try_load_baseline(path: Path) -> BaselineFile | None:
    """Best-effort prior-baseline load; returns ``None`` if absent or invalid.

    A corrupt prior baseline must not block a save — that would brick
    the repo. We treat it as "no prior provenance" and let the new save
    upgrade the file.
    """
    if not path.exists():
        return None
    try:
        return load_baseline(path)
    except InputParseError:
        return None


def load_baseline(path: Path) -> BaselineFile:
    if not path.exists():
        raise InputParseError(f"Baseline file not found: {path}")
    try:
        return BaselineFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise InputParseError(f"Invalid baseline file {path}: {exc}") from exc


def apply_baseline(
    findings: list[Finding],
    baseline: BaselineFile,
    *,
    display_path: str,
    legacy_fingerprints: Sequence[str | None] | None = None,
) -> BaselineSummary:
    baseline_fingerprints = {
        finding.fingerprint for finding in baseline.findings if finding.fingerprint
    }
    current_active_fingerprints: set[str] = set()
    matched_legacy_fingerprints: set[str] = set()
    matched = 0
    new = 0
    for index, finding in enumerate(findings):
        if finding.suppressed:
            continue
        fingerprint = finding.fingerprint or finding.id
        legacy_fingerprint = (
            legacy_fingerprints[index]
            if legacy_fingerprints is not None and index < len(legacy_fingerprints)
            else None
        )
        if not fingerprint:
            continue
        current_active_fingerprints.add(fingerprint)
        if fingerprint in baseline_fingerprints:
            finding.baseline_status = "matched"
            matched += 1
        elif legacy_fingerprint and legacy_fingerprint in baseline_fingerprints:
            finding.baseline_status = "matched"
            matched += 1
            matched_legacy_fingerprints.add(legacy_fingerprint)
        elif legacy_match := _legacy_baseline_match(finding, baseline.findings):
            finding.baseline_status = "matched"
            matched += 1
            matched_legacy_fingerprints.add(legacy_match.fingerprint)
        else:
            finding.baseline_status = "new"
            new += 1
    return BaselineSummary(
        path=display_path,
        matched_count=matched,
        new_count=new,
        resolved_count=len(
            baseline_fingerprints
            - current_active_fingerprints
            - matched_legacy_fingerprints
        ),
    )


def _active_findings(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if not finding.suppressed]


def _legacy_baseline_match(
    finding: Finding, baseline_findings: list[BaselineFinding]
) -> BaselineFinding | None:
    for baseline_finding in baseline_findings:
        if not expands_to_check_id(baseline_finding.check_id, finding.check_id):
            continue
        if (
            baseline_finding.tool_name is not None
            and baseline_finding.tool_name != finding.tool_name
        ):
            continue
        return baseline_finding
    return None


def _preserve_created_at_when_content_matches(
    baseline: BaselineFile, prior_baseline: BaselineFile | None
) -> BaselineFile:
    """Keep the existing ``created_at`` when content is otherwise identical.

    This makes re-saves byte-stable when nothing has changed: identical
    findings, identical provenance, identical surface facts. The audit
    log still records the save, but the on-disk file does not churn.
    """
    if prior_baseline is None:
        return baseline
    if _baseline_content_identity(prior_baseline) != _baseline_content_identity(
        baseline
    ):
        return baseline
    return baseline.model_copy(update={"created_at": prior_baseline.created_at})


def _baseline_content_identity(baseline: BaselineFile) -> dict[str, object]:
    return baseline.model_dump(mode="json", exclude={"created_at"})


# --- Integrity verification ----------------------------------------------
#
# `verify_baseline()` is the static-only integrity check. It reads the
# baseline JSON and the append-only audit log and reports issues that
# can be detected without re-running a scan: hash mismatch, missing
# audit log, entries without audit provenance, legacy-version entries
# without provenance at all, expired entries, deprecated check IDs.
#
# `baseline_resolved_fingerprints()` is the scan-aware companion. Once
# `apply_baseline()` has matched current findings against the baseline,
# any baseline fingerprint that did not match is a candidate for
# pruning (the underlying check no longer fires). The integrity check
# module combines both into the SHIP-BASELINE-ENTRY-STALE finding so
# reviewers see resolved entries alongside the deprecated-id ones.
#
# Neither function emits Findings directly — they return typed
# `BaselineIntegrityIssue` records. The conversion to `Finding` lives
# in `checks/baseline_integrity.py` so the check engine remains the
# sole producer of report findings.

BaselineIntegrityIssueKind = Literal[
    "hash_mismatch",
    "missing_audit_log",
    "invalid_audit_log",
    "entry_no_audit",
    "legacy_no_provenance",
    "entry_expired",
    "deprecated_check_id",
    "resolved_not_pruned",
]


@dataclass(frozen=True)
class BaselineIntegrityIssue:
    """One concern discovered while verifying baseline integrity.

    The integrity check maps each kind to one of the three new check IDs:

    - ``hash_mismatch`` / ``missing_audit_log`` / ``invalid_audit_log`` /
      ``entry_no_audit`` / ``legacy_no_provenance`` →
      ``SHIP-BASELINE-INTEGRITY-MISMATCH``
    - ``entry_expired`` → ``SHIP-BASELINE-ENTRY-EXPIRED``
    - ``deprecated_check_id`` / ``resolved_not_pruned`` →
      ``SHIP-BASELINE-ENTRY-STALE``

    Severity at this layer is the *default* for the kind. The integrity
    check module applies any manifest severity overrides and the
    ``integrity_mode`` flag (warn/strict) before emitting findings.
    """

    kind: BaselineIntegrityIssueKind
    default_severity: Severity
    title: str
    evidence: dict[str, object] = field(default_factory=dict)
    fingerprint: str | None = None
    check_id: str | None = None
    tool_name: str | None = None


def verify_baseline(
    baseline_path: Path,
    audit_log_path: Path | None = None,
    *,
    today: date | None = None,
) -> list[BaselineIntegrityIssue]:
    """Static-only integrity check for a baseline file.

    Returns a list of :class:`BaselineIntegrityIssue` describing every
    integrity concern detected without re-running a scan. Callers that
    also want scan-aware stale detection should additionally consult
    :func:`baseline_resolved_fingerprints`.

    The order of issues is intentional — file-level concerns first
    (hash mismatch, missing audit log), then per-entry concerns ordered
    by baseline-entry order. The CLI surfaces this ordering to humans.

    ``today`` is injectable for deterministic testing of expiry.
    """
    if not baseline_path.exists():
        raise InputParseError(f"Baseline file not found: {baseline_path}")
    baseline = load_baseline(baseline_path)
    log_path = audit_log_path or baseline_path.parent / DEFAULT_AUDIT_LOG_PATH.name
    issues: list[BaselineIntegrityIssue] = []
    audit_issues = _verify_audit_log_alignment(baseline_path, log_path, baseline)
    issues.extend(audit_issues)
    if not any(issue.kind == "invalid_audit_log" for issue in audit_issues):
        issues.extend(_verify_entry_provenance(baseline, log_path))
    issues.extend(_verify_entry_expiry(baseline, today or date.today()))
    issues.extend(_verify_deprecated_check_ids(baseline))
    return issues


def baseline_resolved_fingerprints(
    findings: list[Finding],
    baseline: BaselineFile,
    *,
    legacy_fingerprints: Sequence[str | None] | None = None,
) -> list[BaselineIntegrityIssue]:
    """Scan-aware companion to ``verify_baseline``.

    Identifies baseline entries that did not match any active finding
    in the current scan — i.e. ``baseline.resolved_count`` candidates.
    Each becomes a ``resolved_not_pruned`` issue at low severity so
    reviewers know to clean the baseline up.

    Direct mirror of the resolved-count computation in
    :func:`apply_baseline`; we recompute here rather than threading the
    set out of that function to keep its signature backward-compatible
    with the existing public contract.
    """
    baseline_fingerprints = {
        entry.fingerprint for entry in baseline.findings if entry.fingerprint
    }
    active: set[str] = set()
    legacy_matched: set[str] = set()
    for index, finding in enumerate(findings):
        if finding.suppressed:
            continue
        fingerprint = finding.fingerprint or finding.id
        legacy_fingerprint = (
            legacy_fingerprints[index]
            if legacy_fingerprints is not None and index < len(legacy_fingerprints)
            else None
        )
        if not fingerprint:
            continue
        active.add(fingerprint)
        if fingerprint in baseline_fingerprints:
            continue
        if legacy_fingerprint and legacy_fingerprint in baseline_fingerprints:
            legacy_matched.add(legacy_fingerprint)
            continue
        match = _legacy_baseline_match(finding, baseline.findings)
        if match is not None:
            legacy_matched.add(match.fingerprint)
    resolved = baseline_fingerprints - active - legacy_matched
    if not resolved:
        return []
    entries_by_fp = {entry.fingerprint: entry for entry in baseline.findings}
    issues: list[BaselineIntegrityIssue] = []
    for fingerprint in sorted(resolved):
        entry = entries_by_fp.get(fingerprint)
        check_id = entry.check_id if entry else None
        tool_name = entry.tool_name if entry else None
        issues.append(
            BaselineIntegrityIssue(
                kind="resolved_not_pruned",
                default_severity="low",
                title=(
                    f"Baseline entry for {check_id or 'unknown check'} "
                    f"on {tool_name or 'unknown tool'} no longer fires; "
                    "consider pruning."
                ),
                evidence={
                    "fingerprint": fingerprint,
                    "check_id": check_id,
                    "tool_name": tool_name,
                    "kind": "resolved_not_pruned",
                },
                fingerprint=fingerprint,
                check_id=check_id,
                tool_name=tool_name,
            )
        )
    return issues


def _verify_audit_log_alignment(
    baseline_path: Path,
    log_path: Path,
    baseline: BaselineFile,
) -> list[BaselineIntegrityIssue]:
    """Hash baseline vs. latest audit entry's hash_after; report mismatch.

    A baseline with zero findings and no audit log is a fresh / unsaved
    state — not an integrity violation. Only flag when the baseline
    contains entries (i.e., someone took the trouble to populate it)
    but no audit row exists, or the most recent audit row's
    ``hash_after`` disagrees with the file on disk.
    """
    if not log_path.exists():
        if not baseline.findings:
            return []
        return [
            BaselineIntegrityIssue(
                kind="missing_audit_log",
                default_severity="critical",
                title="Baseline has entries but no audit log",
                evidence={
                    "baseline_path": str(baseline_path),
                    "audit_log_path": str(log_path),
                    "entry_count": len(baseline.findings),
                    "kind": "missing_audit_log",
                },
            )
        ]
    try:
        latest = latest_audit_entry(log_path)
    except InputParseError as exc:
        return [
            BaselineIntegrityIssue(
                kind="invalid_audit_log",
                default_severity="critical",
                title="Baseline audit log is malformed",
                evidence={
                    "baseline_path": str(baseline_path),
                    "audit_log_path": str(log_path),
                    "entry_count": len(baseline.findings),
                    "error": str(exc),
                    "kind": "invalid_audit_log",
                },
            )
        ]
    if latest is None:
        if not baseline.findings:
            return []
        return [
            BaselineIntegrityIssue(
                kind="missing_audit_log",
                default_severity="critical",
                title="Audit log exists but is empty",
                evidence={
                    "baseline_path": str(baseline_path),
                    "audit_log_path": str(log_path),
                    "entry_count": len(baseline.findings),
                    "kind": "missing_audit_log",
                },
            )
        ]
    on_disk_hash = compute_baseline_hash_from_file(baseline_path)
    if on_disk_hash != latest.hash_after:
        return [
            BaselineIntegrityIssue(
                kind="hash_mismatch",
                default_severity="critical",
                title="Baseline file hash does not match latest audit entry",
                evidence={
                    "baseline_path": str(baseline_path),
                    "audit_log_path": str(log_path),
                    "expected_hash": latest.hash_after,
                    "computed_hash": on_disk_hash,
                    "latest_audit_run_id": latest.run_id,
                    "latest_audit_timestamp": latest.timestamp,
                    "kind": "hash_mismatch",
                },
            )
        ]
    return []


def _verify_entry_provenance(
    baseline: BaselineFile, log_path: Path
) -> list[BaselineIntegrityIssue]:
    """Each entry's provenance.run_id should appear in the audit log."""
    issues: list[BaselineIntegrityIssue] = []
    audit_run_ids: set[str] | None = None
    for entry in baseline.findings:
        if entry.provenance is None:
            issues.append(
                BaselineIntegrityIssue(
                    kind="legacy_no_provenance",
                    default_severity="critical",
                    title=(
                        f"Baseline entry for {entry.check_id} lacks v0.5 "
                        "provenance; re-run `agents-shipgate baseline save` "
                        "to migrate."
                    ),
                    evidence={
                        "fingerprint": entry.fingerprint,
                        "check_id": entry.check_id,
                        "tool_name": entry.tool_name,
                        "kind": "legacy_no_provenance",
                    },
                    fingerprint=entry.fingerprint,
                    check_id=entry.check_id,
                    tool_name=entry.tool_name,
                )
            )
            continue
        if audit_run_ids is None:
            audit_run_ids = {row.run_id for row in read_audit_log(log_path)}
        if entry.provenance.run_id not in audit_run_ids:
            issues.append(
                BaselineIntegrityIssue(
                    kind="entry_no_audit",
                    default_severity="critical",
                    title=(
                        f"Baseline entry for {entry.check_id} references "
                        f"run_id {entry.provenance.run_id!r} that is not in "
                        "the audit log."
                    ),
                    evidence={
                        "fingerprint": entry.fingerprint,
                        "check_id": entry.check_id,
                        "tool_name": entry.tool_name,
                        "run_id": entry.provenance.run_id,
                        "kind": "entry_no_audit",
                    },
                    fingerprint=entry.fingerprint,
                    check_id=entry.check_id,
                    tool_name=entry.tool_name,
                )
            )
    return issues


def _verify_entry_expiry(
    baseline: BaselineFile, today: date
) -> list[BaselineIntegrityIssue]:
    issues: list[BaselineIntegrityIssue] = []
    for entry in baseline.findings:
        if entry.provenance is None or entry.provenance.expires is None:
            continue
        if entry.provenance.expires < today:
            days_overdue = (today - entry.provenance.expires).days
            issues.append(
                BaselineIntegrityIssue(
                    kind="entry_expired",
                    default_severity="high",
                    title=(
                        f"Baseline entry for {entry.check_id} expired "
                        f"{days_overdue} day(s) ago "
                        f"({entry.provenance.expires.isoformat()})."
                    ),
                    evidence={
                        "fingerprint": entry.fingerprint,
                        "check_id": entry.check_id,
                        "tool_name": entry.tool_name,
                        "expires": entry.provenance.expires.isoformat(),
                        "days_overdue": days_overdue,
                        "reason": entry.provenance.reason,
                        "kind": "entry_expired",
                    },
                    fingerprint=entry.fingerprint,
                    check_id=entry.check_id,
                    tool_name=entry.tool_name,
                )
            )
    return issues


def _verify_deprecated_check_ids(
    baseline: BaselineFile,
) -> list[BaselineIntegrityIssue]:
    """Entries whose ``check_id`` is a deprecated alias.

    Aliases live in :data:`LEGACY_CHECK_ID_ALIASES`. They still match
    findings at scan time (via :func:`expands_to_check_id`), but new
    baselines should refer to the canonical IDs so reviewers see the
    real check.
    """
    issues: list[BaselineIntegrityIssue] = []
    for entry in baseline.findings:
        if entry.check_id not in LEGACY_CHECK_ID_ALIASES:
            continue
        replacements = sorted(LEGACY_CHECK_ID_ALIASES[entry.check_id])
        issues.append(
            BaselineIntegrityIssue(
                kind="deprecated_check_id",
                default_severity="low",
                title=(
                    f"Baseline entry uses deprecated check_id "
                    f"{entry.check_id!r}; use one of {replacements!r}."
                ),
                evidence={
                    "fingerprint": entry.fingerprint,
                    "check_id": entry.check_id,
                    "tool_name": entry.tool_name,
                    "replacement_check_ids": replacements,
                    "kind": "deprecated_check_id",
                },
                fingerprint=entry.fingerprint,
                check_id=entry.check_id,
                tool_name=entry.tool_name,
            )
        )
    return issues
