"""One vocabulary for the two release-policy reason codes.

``SHIP-VERIFY-POLICY-WEAKENED`` makes a base-relative claim: the head gate is
weaker than the base gate. ``SHIP-VERIFY-POLICY-BASE-ABSENT`` makes the
opposite kind of statement: there was no base gate to compare against, so no
claim about direction is available at all.

Four consumers read those findings back as facts — reviewer routing, the
verifier summary, the fix task, and the GitHub Action outputs — and each needs
the same two answers about a finding:

``counts_as_weakened``
    The **fail-closed routing** answer. An unprovable direction stays treated
    as weakened, because otherwise breaking the base scan would be a way to
    clear the gate-bypass alarm. Only a git-proven first adoption clears it:
    a base that carries no manifest at all cannot have had one weakened.

``weakening_is_proven``
    The **honest copy** answer. It is true only for a finding that actually
    compared two policies and found the head weaker. Routing and copy are
    deliberately different questions: reporting "this PR weakens the release
    policy" for a change whose direction was never established states a fact
    the run does not have, and the reader cannot tell it apart from a real
    weakening.

Both predicates accept the pre-split id alongside the current one. Reports
written before the split carry ``SHIP-VERIFY-POLICY-WEAKENED`` with a
``manifest_introduced`` or ``base_snapshot_unavailable`` evidence kind, and
those artifacts are still read — by ``--diff-from``, by the PR-comment and
handoff renderers, and by anything reprojecting a stored ``report.json``. They
must keep projecting to what they meant when they were written. Nothing here
causes the old id to be *emitted* again.
"""

from __future__ import annotations

POLICY_WEAKENED_CHECK_ID = "SHIP-VERIFY-POLICY-WEAKENED"
POLICY_BASE_ABSENT_CHECK_ID = "SHIP-VERIFY-POLICY-BASE-ABSENT"

#: Every reason code that reports on the release-policy surface, current and
#: pre-split. Read-side only: the emitter picks exactly one of the two.
POLICY_REASON_CODES: frozenset[str] = frozenset(
    {POLICY_WEAKENED_CHECK_ID, POLICY_BASE_ABSENT_CHECK_ID}
)

#: The base carries no Shipgate manifest under any name — this diff adopts the
#: gate. Proven from git by ``cli/verify/orchestrator._manifest_introduced``.
ADOPTION_EVIDENCE_KIND = "manifest_introduced"
#: No base effective-policy snapshot was obtainable, so the direction of the
#: change could not be established either way.
NO_BASE_EVIDENCE_KIND = "base_snapshot_unavailable"

#: Evidence kinds that mean "no comparison happened". Everything else on a
#: policy reason code is the result of an actual base-vs-head comparison.
_UNCOMPARED_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {ADOPTION_EVIDENCE_KIND, NO_BASE_EVIDENCE_KIND}
)


def _evidence_kind(evidence: object) -> str:
    if isinstance(evidence, dict):
        return str(evidence.get("kind") or "")
    return str(getattr(evidence, "kind", "") or "")


def is_policy_reason_code(check_id: str) -> bool:
    """Whether ``check_id`` reports on the release-policy surface."""

    return check_id in POLICY_REASON_CODES


def counts_as_weakened(check_id: str, evidence: object = None) -> bool:
    """Fail-closed: does this finding keep the gate-weakened flag raised?"""

    if not is_policy_reason_code(check_id):
        return False
    return _evidence_kind(evidence) != ADOPTION_EVIDENCE_KIND


def weakening_is_proven(check_id: str, evidence: object = None) -> bool:
    """Honest copy: did this finding actually compare two policies?"""

    if not is_policy_reason_code(check_id):
        return False
    return _evidence_kind(evidence) not in _UNCOMPARED_EVIDENCE_KINDS


def is_adoption_evidence(check_id: str, evidence: object = None) -> bool:
    """Whether this finding is the git-proven first-adoption fail-safe."""

    return is_policy_reason_code(check_id) and (
        _evidence_kind(evidence) == ADOPTION_EVIDENCE_KIND
    )


__all__ = [
    "ADOPTION_EVIDENCE_KIND",
    "NO_BASE_EVIDENCE_KIND",
    "POLICY_BASE_ABSENT_CHECK_ID",
    "POLICY_REASON_CODES",
    "POLICY_WEAKENED_CHECK_ID",
    "counts_as_weakened",
    "is_adoption_evidence",
    "is_policy_reason_code",
    "weakening_is_proven",
]
