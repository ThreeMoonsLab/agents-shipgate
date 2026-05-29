"""Normalized effective-policy snapshot (roadmap §5.3).

``build_effective_policy_snapshot`` projects the manifest into a
deterministic, semantic snapshot of the release policy surface — the CI
gate strength, the suppression/waiver set, severity overrides, and
baseline posture. The snapshot is what the SHIP-VERIFY-* weakening and
expansion classifiers compare base-vs-head, so it must be a pure,
order-independent function of its inputs (no wall-clock, no set-iteration
leakage).

It is intentionally NOT a text diff: comparing snapshots answers "did the
gate move toward less review / less blocking / broader waivers?" robustly
across reformatting and key reordering in ``shipgate.yaml``.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents_shipgate.schemas.capability_change import EffectivePolicy
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def build_effective_policy_snapshot(
    manifest: AgentsShipgateManifest,
    *,
    baseline_fingerprints: Iterable[str] = (),
    ci_gate_present: bool = True,
) -> EffectivePolicy:
    """Build the normalized effective-policy snapshot from the manifest.

    ``baseline_fingerprints`` is the accepted-debt set (findings whose
    ``baseline_status == "matched"``); callers that have the built report
    pass it for legibility, while the in-check head snapshot omits it
    (findings are not baseline-matched at check time). ``ci_gate_present``
    is supplied by the caller because CI-gate presence is a filesystem
    fact, not a manifest fact — plain ``scan`` cannot see it, so it
    defaults to ``True`` and is only flipped by verify orchestration.

    The ``EffectivePolicy`` model validator sorts every list/dict, so the
    output is byte-stable regardless of manifest key order.
    """
    # Every manifest sub-config is optional and may be None / absent; coalesce
    # defensively so a sparse shipgate.yaml yields the empty snapshot rather
    # than raising. ``getattr`` guards keep this robust to schema evolution.
    ci = manifest.ci
    checks = manifest.checks
    baseline = manifest.baseline
    ignore = getattr(checks, "ignore", None) or []
    suppressed_check_ids = [s.check_id for s in ignore]
    waiver_scopes = [f"{s.check_id}:{s.tool or '*'}" for s in ignore]
    overrides = getattr(checks, "severity_overrides", None) or {}
    severity_overrides = {
        check_id: entry.severity for check_id, entry in overrides.items()
    }
    return EffectivePolicy(
        ci_mode=getattr(ci, "mode", None),
        fail_on=list(getattr(ci, "fail_on", None) or []),
        suppressed_check_ids=suppressed_check_ids,
        waiver_scopes=waiver_scopes,
        severity_overrides=severity_overrides,
        baseline_integrity_mode=getattr(baseline, "integrity_mode", None),
        baseline_fingerprints=list(baseline_fingerprints),
        ci_gate_present=ci_gate_present,
    )


def accepted_debt_fingerprints(findings: Iterable) -> list[str]:
    """Sorted unique fingerprints of accepted-debt (baseline-matched) findings.

    These are the findings the baseline currently absorbs; a base-vs-head
    growth of this set is a baseline expansion (surfaced in the snapshot).
    """
    out: set[str] = set()
    for finding in findings:
        if getattr(finding, "baseline_status", None) == "matched":
            fingerprint = getattr(finding, "fingerprint", None) or getattr(
                finding, "id", None
            )
            if fingerprint:
                out.add(fingerprint)
    return sorted(out)


__all__ = [
    "build_effective_policy_snapshot",
    "accepted_debt_fingerprints",
]
