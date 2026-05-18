"""Convert baseline integrity issues into Finding objects.

This module is the bridge between :mod:`agents_shipgate.core.baseline`
(which produces typed :class:`BaselineIntegrityIssue` records) and the
report findings stream. It lives under ``checks/`` because the engine
treats the resulting findings just like any other check output — they
flow through ``assign_finding_ids`` and ``annotate_remediation`` before
hitting the report.

Unlike checks in ``BUILTIN_CHECKS``, this module is not invoked by
``run_checks``. It is called directly from :mod:`agents_shipgate.cli.scan`
after ``apply_baseline`` because the integrity check needs the loaded
baseline and audit log paths, not the tool context.

Design notes
------------

- Integrity findings are **not subject to manifest suppressions**. The
  whole point of the integrity check is to detect baseline tampering;
  letting users silence it via ``checks.ignore`` would reopen the trust
  hole that M2 closes.
- Severity is fixed at ``BaselineIntegrityIssue.default_severity``.
  Manifest-level severity overrides (``checks.severity_overrides``) are
  intentionally bypassed here. A user who wants to tune individual
  integrity-finding severities should reach for M1's ``floor_severity``
  + acknowledgement mechanism instead. (Out of scope for M2.)
- In ``integrity_mode="strict"``, the
  :data:`SHIP-BASELINE-INTEGRITY-MISMATCH` finding carries
  ``blocks_release=True`` so the release decision blocks the gate even
  for findings whose severity might otherwise be tunable. Lower-severity
  integrity findings stay advisory in strict mode.
"""

from __future__ import annotations

from typing import Literal

from agents_shipgate.core.baseline import BaselineIntegrityIssue
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.common import SourceReference, parse_severity
from agents_shipgate.schemas.report import Finding

IntegrityMode = Literal["off", "warn", "strict"]

# Map each issue kind to its check ID. SHIP-BASELINE-INTEGRITY-MISMATCH
# covers the file-level concerns; SHIP-BASELINE-ENTRY-EXPIRED handles
# review-window expiry; SHIP-BASELINE-ENTRY-STALE collects the
# scan-aware and deprecated-id cases.
_KIND_TO_CHECK_ID: dict[str, str] = {
    "hash_mismatch": "SHIP-BASELINE-INTEGRITY-MISMATCH",
    "missing_audit_log": "SHIP-BASELINE-INTEGRITY-MISMATCH",
    "invalid_audit_log": "SHIP-BASELINE-INTEGRITY-MISMATCH",
    "entry_no_audit": "SHIP-BASELINE-INTEGRITY-MISMATCH",
    "legacy_no_provenance": "SHIP-BASELINE-INTEGRITY-MISMATCH",
    "entry_expired": "SHIP-BASELINE-ENTRY-EXPIRED",
    "deprecated_check_id": "SHIP-BASELINE-ENTRY-STALE",
    "resolved_not_pruned": "SHIP-BASELINE-ENTRY-STALE",
}

# Per-check recommendation surfaced in the Finding. Specific text lives
# in CheckMetadata.recommendation; we duplicate a short version here so
# every emitted Finding carries useful inline guidance for reviewers
# reading report.md without bouncing through the check catalog.
_KIND_TO_RECOMMENDATION: dict[str, str] = {
    "hash_mismatch": (
        "Run `agents-shipgate baseline verify` for the full report and "
        "re-run `agents-shipgate baseline save` once the baseline is "
        "reviewed."
    ),
    "missing_audit_log": (
        "Re-run `agents-shipgate baseline save` to regenerate the "
        "audit log alongside the baseline file."
    ),
    "invalid_audit_log": (
        "Inspect the malformed audit log, then re-run "
        "`agents-shipgate baseline save` once the baseline state is "
        "reviewed."
    ),
    "entry_no_audit": (
        "Run `agents-shipgate baseline save` to record a new audit "
        "entry for the current baseline state."
    ),
    "legacy_no_provenance": (
        "Re-run `agents-shipgate baseline save` to upgrade the legacy "
        "baseline to v0.5 with provenance stamps."
    ),
    "entry_expired": (
        "Re-review the accepted debt and either fix the underlying "
        "finding, remove the baseline entry, or extend "
        "`provenance.expires` with a new reason."
    ),
    "deprecated_check_id": (
        "Update the baseline entry to use one of the listed "
        "replacement check IDs; re-running `baseline save` does not "
        "rewrite check IDs."
    ),
    "resolved_not_pruned": (
        "Re-run `agents-shipgate baseline save` to drop the resolved "
        "entry from the baseline."
    ),
}


def build_findings(
    issues: list[BaselineIntegrityIssue],
    *,
    context: ScanContext,
    integrity_mode: IntegrityMode,
) -> list[Finding]:
    """Convert integrity issues to Finding objects.

    Returns an empty list when ``integrity_mode == "off"`` so callers
    can call this unconditionally without branching on the manifest
    flag themselves.

    ``blocks_release`` is set to ``True`` on
    ``SHIP-BASELINE-INTEGRITY-MISMATCH`` findings when
    ``integrity_mode == "strict"``. All other integrity findings stay
    at ``blocks_release=False`` regardless of mode — they're advisory
    by design. Strict-mode gating focuses on the single signal
    "baseline file has been edited", which is the trust property the
    audit log defends.
    """
    if integrity_mode == "off":
        return []
    findings: list[Finding] = []
    for issue in issues:
        check_id = _KIND_TO_CHECK_ID[issue.kind]
        blocks_release = (
            integrity_mode == "strict"
            and check_id == "SHIP-BASELINE-INTEGRITY-MISMATCH"
        )
        findings.append(
            Finding(
                check_id=check_id,
                title=issue.title,
                severity=parse_severity(issue.default_severity),
                category="baseline",
                tool_id=None,
                tool_name=issue.tool_name,
                agent_id=context.agent.id,
                evidence=dict(issue.evidence),
                confidence="high",
                provenance_kind="static_declaration",
                source=SourceReference(
                    type="baseline",
                    ref=str(issue.evidence.get("baseline_path"))
                    if "baseline_path" in issue.evidence
                    else None,
                ),
                recommendation=_KIND_TO_RECOMMENDATION[issue.kind],
                blocks_release=blocks_release,
            )
        )
    return findings


def has_hash_mismatch(issues: list[BaselineIntegrityIssue]) -> bool:
    """Return True iff any issue maps to SHIP-BASELINE-INTEGRITY-MISMATCH.

    The standalone ``agents-shipgate baseline verify`` command uses this
    to decide whether to exit with code 6 (in strict mode) or 0 (warn).
    """
    return any(
        _KIND_TO_CHECK_ID[issue.kind] == "SHIP-BASELINE-INTEGRITY-MISMATCH"
        for issue in issues
    )
