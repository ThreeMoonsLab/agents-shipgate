"""SHIP-VERIFY-POLICY-WEAKENED — base-vs-head policy weakening (§5.1 Tier B).

Compares the normalized effective-policy snapshot of the base report
(via ``--diff-from``) against the head manifest and emits one finding per
detected weakening:

- ``ci_mode_weakened`` — CI gate moved strict -> advisory.
- ``fail_on_loosened`` — the fail-on severity set lost a tier
  (head ⊊ base), i.e. fewer severities now fail CI.
- ``severity_override_lowered`` — a check's applied severity dropped
  across a tier boundary versus the base.

If the base snapshot is unavailable (no diff reference, or a pre-v0.22
base) AND the PR touched a policy/manifest trust root, it emits a single
``base_snapshot_unavailable`` review-required finding — a reward-hacker
must not be able to dodge review by breaking the base scan (§5.3: ambiguous
direction -> review_required, never silent pass). When the base is proven
to carry no manifest at all, the same fail-safe emits under evidence kind
``manifest_introduced`` instead: adoption is still a human decision, but
nothing existed to weaken and the finding says so.

Weakening is defined as movement toward less review / less blocking. A
strengthening change (stricter mode, more fail-on severities, raised
override) emits nothing.
"""

from __future__ import annotations

from functools import lru_cache

from agents_shipgate.checks._metadata_loader import load_check_metadata
from agents_shipgate.checks._verify_common import (
    SEVERITY_RANK,
    base_effective_policy,
    changed_files,
    head_effective_policy,
    touched,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.trust_roots import is_context_configured_manifest
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-POLICY-WEAKENED"

# Strength of a CI mode — higher blocks more. Unknown modes rank -1 so an
# unrecognized head mode never reads as "stronger" than a known base.
_CI_MODE_RANK = {"advisory": 0, "warn": 0, "strict": 1, "block": 1}

# Trust roots whose modification, when the base snapshot is missing,
# warrants a fail-safe review-required finding.
_POLICY_SURFACES = (
    "**/shipgate.yaml",
    "**/policies/**",
    "**/.agents-shipgate/**",
)


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    base = base_effective_policy(context)
    head = head_effective_policy(context)
    if base is None:
        return _fail_safe(context)
    return _compare(context, base, head)


def _compare(context: ScanContext, base, head) -> list[Finding]:
    findings: list[Finding] = []

    # 1. ci.mode weakened (strict -> advisory).
    base_rank = _CI_MODE_RANK.get(base.ci_mode or "", 0)
    head_rank = _CI_MODE_RANK.get(head.ci_mode or "", 0)
    if head_rank < base_rank:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title=f"CI mode weakened: {base.ci_mode} -> {head.ci_mode}",
                severity="high",
                evidence={
                    "kind": "ci_mode_weakened",
                    "base_ci_mode": base.ci_mode,
                    "head_ci_mode": head.ci_mode,
                },
                recommendation=(
                    "This PR weakens the CI gate mode. A human must review "
                    "and approve the change; do not weaken the gate to make "
                    "CI pass."
                ),
            )
        )

    # 2. fail_on loosened (head is a proper subset of base).
    base_fail = set(base.fail_on)
    head_fail = set(head.fail_on)
    dropped = base_fail - head_fail
    if dropped:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title="Fail-on severity set loosened",
                severity="high",
                evidence={
                    "kind": "fail_on_loosened",
                    "removed_severities": sorted(
                        dropped, key=lambda s: (SEVERITY_RANK.get(s, -1), s)
                    ),
                    "base_fail_on": list(base.fail_on),
                    "head_fail_on": list(head.fail_on),
                },
                recommendation=(
                    "This PR removes severities from the CI fail-on set, so "
                    "fewer findings block release. A human must approve the "
                    "reduced gate."
                ),
            )
        )

    # 3. effective severity lowered across a tier (per check_id).
    # Compare effective applied severity, not just explicit override text:
    # adding a downgrade (default high -> override medium), lowering an
    # existing override, and removing a hardening override (override critical
    # -> default medium) are all policy weakening.
    defaults = _catalog_default_severities()
    lowered = []
    check_ids = set(base.severity_overrides) | set(head.severity_overrides)
    for check_id in sorted(check_ids):
        base_sev = _effective_severity(
            check_id,
            base.severity_overrides,
            defaults,
        )
        head_sev = _effective_severity(
            check_id,
            head.severity_overrides,
            defaults,
        )
        if base_sev is None or head_sev is None:
            continue
        if SEVERITY_RANK.get(head_sev, -1) < SEVERITY_RANK.get(base_sev, -1):
            lowered.append((check_id, base_sev, head_sev))
    for check_id, base_sev, head_sev in lowered:
        findings.append(
            verify_finding(
                context,
                check_id=CHECK_ID,
                title=f"Severity override lowered for {check_id}",
                severity="high",
                evidence={
                    "kind": "severity_override_lowered",
                    "target_check_id": check_id,
                    "base_severity": base_sev,
                    "head_severity": head_sev,
                },
                recommendation=(
                    f"This PR lowers the severity of {check_id} from "
                    f"{base_sev} to {head_sev}. A human must approve the "
                    "downgrade with a documented reason."
                ),
            )
        )

    return findings


@lru_cache(maxsize=1)
def _catalog_default_severities() -> dict[str, str]:
    return {entry.id: entry.default_severity for entry in load_check_metadata()}


def _effective_severity(
    check_id: str,
    overrides: dict[str, str],
    defaults: dict[str, str],
) -> str | None:
    return overrides.get(check_id) or defaults.get(check_id)


def _touched_policy_surfaces(context: ScanContext) -> list[str]:
    """Changed policy trust roots, including a non-default manifest name.

    ``_POLICY_SURFACES`` only knows ``**/shipgate.yaml``. A repository whose
    gate is ``new-gate.yml`` was therefore invisible to this fail-safe: its
    manifest could be introduced or rewritten with no base snapshot and this
    check emitted nothing at all.
    """

    files = changed_files(context)
    hits = set(touched(_POLICY_SURFACES, files))
    hits.update(path for path in files if is_context_configured_manifest(context, path))
    return sorted(hits)


def _fail_safe(context: ScanContext) -> list[Finding]:
    """No base snapshot: emit review-required iff a policy root was touched."""
    hit = _touched_policy_surfaces(context)
    if not hit:
        return []
    verification = context.verification
    introducing = verification is not None and verification.manifest_introduced
    # An adoption that *also* edits an existing policy pack or baseline is not
    # covered by "nothing existed to weaken": those files were already there.
    # The friendlier wording is only correct when every touched policy surface
    # is the manifest being introduced.
    if introducing and all(
        is_context_configured_manifest(context, path) for path in hit
    ):
        configured_manifest = verification.configured_manifest_path or hit[0]
        # A base with no manifest at all cannot have been weakened. This still
        # emits — at the same check id and severity, so the verdict and every
        # fail-closed consumer are unchanged — because the human decision
        # (adopt this policy) is real. Only the claim about what happened
        # changes. The orchestrator proves the base carries no manifest under
        # any name, so a moved-and-loosened manifest does not reach here.
        return [
            verify_finding(
                context,
                check_id=CHECK_ID,
                title="Initial Shipgate adoption: the base carries no policy",
                severity="medium",
                evidence={
                    "kind": "manifest_introduced",
                    "changed_policy_files": hit,
                },
                recommendation=(
                    "This PR introduces the Shipgate manifest rather than "
                    "changing an existing one, so no prior gate was weakened. "
                    "Adopting a release policy is a human decision: review the "
                    f"configured manifest {configured_manifest!r}; a human "
                    "must decide whether to adopt it."
                ),
            )
        ]
    return [
        verify_finding(
            context,
            check_id=CHECK_ID,
            title="Policy change cannot be proven safe (no base snapshot)",
            severity="medium",
            evidence={
                "kind": "base_snapshot_unavailable",
                "changed_policy_files": hit,
            },
            recommendation=(
                "This PR changes a policy trust root but no base report was "
                "available to prove the change does not weaken the gate. A "
                "human must review the effective-policy change directly."
            ),
        )
    ]
