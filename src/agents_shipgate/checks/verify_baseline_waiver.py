"""SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED (§5.1 Tier B / §5.3).

Detects when this PR broadens what the gate forgives — a classic
reward-hacking move: instead of fixing a finding, suppress it or fold it
into the baseline.

Compares the base report's effective-policy snapshot (via ``--diff-from``)
against the head manifest and emits a finding when ANY of these grew
(head is a proper superset of base):

- ``suppressed_check_ids`` — a check newly added to ``checks.ignore``.
- ``waiver_scopes`` — a ``check_id:tool`` waiver scope newly added or
  broadened (e.g. a specific tool widened to ``*``).
- ``baseline_fingerprints`` — accepted-debt fingerprints grew (the
  baseline now absorbs more findings).

No base snapshot -> emits nothing here: touching the baseline/waiver files
is already surfaced by SHIP-VERIFY-TRUST-ROOT-TOUCHED, and proving an
*expansion* specifically requires the base. (SHIP-VERIFY-POLICY-BASE-ABSENT
owns the fail-safe review-required signal for missing-base policy edits.)
"""

from __future__ import annotations

from agents_shipgate.checks._verify_common import (
    base_effective_policy,
    head_effective_policy,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED"


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    base = base_effective_policy(context)
    if base is None:
        return []
    head = head_effective_policy(context)
    findings: list[Finding] = []

    new_suppressions = sorted(
        set(head.suppressed_check_ids) - set(base.suppressed_check_ids)
    )
    if new_suppressions:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title="Suppression set expanded",
                severity="high",
                evidence={
                    "kind": "suppression_expanded",
                    "added_check_ids": new_suppressions,
                },
                recommendation=(
                    "This PR adds findings to checks.ignore so they no longer "
                    "gate release. A human must confirm each suppression is "
                    "justified; do not suppress findings to make CI pass."
                ),
            )
        )

    new_waivers = sorted(set(head.waiver_scopes) - set(base.waiver_scopes))
    # Only report waiver-scope growth that is not already fully described by
    # the check-id-level suppression growth above (avoid double-reporting a
    # brand-new suppression as both a new check_id and a new scope).
    new_waivers = [
        scope
        for scope in new_waivers
        if scope.split(":", 1)[0] not in new_suppressions
    ]
    if new_waivers:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title="Waiver scope expanded",
                severity="high",
                evidence={
                    "kind": "waiver_expanded",
                    "added_scopes": new_waivers,
                },
                recommendation=(
                    "This PR broadens a waiver scope (e.g. a single tool "
                    "widened to all tools). A human must approve the wider "
                    "waiver."
                ),
            )
        )

    findings.extend(
        baseline_expansion_findings(
            context,
            base,
            head_baseline_fingerprints=head.baseline_fingerprints,
        )
    )

    return findings


def baseline_expansion_findings(
    context: ScanContext,
    base,
    *,
    head_baseline_fingerprints: list[str],
) -> list[Finding]:
    """Build baseline-expansion findings after head baseline matching.

    The ordinary check pass runs before ``apply_baseline`` has assigned
    ``Finding.baseline_status``. The scan pipeline calls this helper after
    baseline matching with the head accepted-debt fingerprints so the
    comparison is real base-vs-head accepted debt rather than an empty
    pre-match placeholder.
    """
    if not verification_active(context) or base is None:
        return []
    new_baseline = sorted(
        set(head_baseline_fingerprints) - set(base.baseline_fingerprints)
    )
    if new_baseline:
        return [
            verify_finding(
                context,
                check_id=CHECK_ID,
                title="Baseline expanded",
                severity="high",
                evidence={
                    "kind": "baseline_expanded",
                    "added_fingerprints": new_baseline,
                },
                recommendation=(
                    "This PR grows the accepted-debt baseline, so more "
                    "findings are forgiven. A human must confirm the new "
                    "baseline entries are intentional."
                ),
            )
        ]
    return []
