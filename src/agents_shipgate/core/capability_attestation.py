"""The one projection from capability facts into the published delta attestation.

:mod:`agents_shipgate.schemas.capability_attestation` fixes the envelope. This
module is the only thing that fills it, and it fills it by calling
:func:`agents_shipgate.core.capability_payload.project_capability_delta` — the
same function the reviewer surfaces consume. The delta an attestation carries
and the delta a PR comment renders are therefore one computation, not two
renderings of one value (#433).

It also owns the one derivation of ``analysis_coverage`` from a scan, because
that block is what makes "a tool was added but never bound" visible in the
attestation instead of silently absent (#437). Coverage is not derivable from
capability facts — the subjects it names are exactly the ones that produced no
fact — so it is computed here from the conservation law the exclusion ledger
states:

    observed == analysed ∪ excluded

``observed`` is the tool catalog the scan read; ``analysed`` is the subject set
the capability facts cover. Their difference is what the binding graph could not
reach, and it is named rather than counted (#433).

Nothing here writes a file, adds a command, or reaches the release decision.
``release_decision.decision`` remains the only gate.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agents_shipgate.core.capability_payload import (
    CapabilityPayloadError,
    project_capability_delta,
)
from agents_shipgate.core.surface_exclusions import provider_token
from agents_shipgate.schemas.capabilities import CapabilityFactV1
from agents_shipgate.schemas.capability_attestation import (
    CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION,
    CAPABILITY_DELTA_PREDICATE_TYPE,
    IN_TOTO_STATEMENT_TYPE,
    CapabilityDeltaAttestationSubject,
    CapabilityDeltaAttestationV1,
    CapabilityDeltaPredicateV1,
    CapabilityDeltaSubjectDigest,
    CapabilityDeltaVerificationRef,
)
from agents_shipgate.schemas.capability_payload import (
    CAPABILITY_PAYLOAD_SCHEMA_VERSION,
    CapabilityAnalysisCoverage,
    CapabilitySubjectRef,
    subject_key,
    subject_sort_key,
)


@dataclass(frozen=True)
class ObservedSubject:
    """One subject the scan saw, whether or not it reached the binding graph.

    Deliberately the *catalog* row rather than a capability fact: an unbound
    tool produces no fact, and it is exactly the row this exists to carry.
    """

    tool_id: str
    name: str
    provider: str


@dataclass(frozen=True)
class CapabilityDeltaAttestationInputs:
    """Everything the emitter needs, captured where the two scans still exist.

    Held as a value object because the capability facts and the coverage are
    established while the base and head locks are in hand, and the identities
    the attestation binds to (``input_set_id``, the resolved tree ids) are only
    known later, when the verification plan is built. Carrying the inputs
    forward keeps the projection a single call at the emit site instead of two
    half-projections either side of the plan.
    """

    base_facts: tuple[CapabilityFactV1, ...]
    head_facts: tuple[CapabilityFactV1, ...]
    base_coverage: CapabilityAnalysisCoverage
    head_coverage: CapabilityAnalysisCoverage


def coverage_from_scan(
    *,
    agent_id: str,
    observed: Iterable[ObservedSubject],
    analysed: Iterable[CapabilityFactV1],
) -> CapabilityAnalysisCoverage:
    """Name the observed subjects no capability fact covers.

    ``status`` is ``complete`` because both sides were established: the caller
    only reaches here when a scan produced a catalog *and* a capability set. A
    caller that has one side and not the other must publish ``unavailable``
    instead — see :meth:`CapabilityAnalysisCoverage.not_requested` and the
    payload spec's rule that naming subjects requires having looked.

    Note what this can and cannot see. It names subjects the scan *observed* and
    did not analyse. A subject an adapter could not read at all appears in
    neither set, so it is not nameable here; ``report.surface_exclusions`` is
    where that narrowing is recorded.
    """

    analysed_keys: set[str] = set()
    for fact in analysed:
        if fact.identity.agent_id != agent_id:
            # The observed rows are keyed under ``agent_id``; a fact set under a
            # different agent would share no keys with them, and every analysed
            # tool would be published as outside analysis. Over-reporting is the
            # safe direction, but it is still a false statement about a named
            # subject, so refuse rather than publish one.
            raise CapabilityPayloadError(
                "coverage was asked for agent "
                f"{agent_id!r} against a capability fact under "
                f"{fact.identity.agent_id!r}; the two cannot be compared"
            )
        analysed_keys.add(
            subject_key(
                agent=fact.identity.agent_id,
                provider=fact.identity.provider,
                tool_id=fact.identity.tool_id,
            )
        )
    outside: dict[str, CapabilitySubjectRef] = {}
    for row in observed:
        provider = provider_token(row.provider)
        key = subject_key(agent=agent_id, provider=provider, tool_id=row.tool_id)
        if key in analysed_keys or key in outside:
            continue
        outside[key] = CapabilitySubjectRef(
            key=key,
            name=row.name,
            agent=agent_id,
            provider=provider,
            tool_id=row.tool_id,
        )
    return CapabilityAnalysisCoverage(
        status="complete",
        subjects_outside_analysis=tuple(
            sorted(outside.values(), key=subject_sort_key)
        ),
    )


def project_capability_delta_attestation(
    base_facts: Sequence[CapabilityFactV1],
    head_facts: Sequence[CapabilityFactV1],
    *,
    subject_name: str,
    base_tree_sha: str,
    head_tree_sha: str,
    head_commit_sha: str | None,
    base_analysis_coverage: CapabilityAnalysisCoverage | None = None,
    head_analysis_coverage: CapabilityAnalysisCoverage | None = None,
    verification: CapabilityDeltaVerificationRef | None = None,
) -> CapabilityDeltaAttestationV1:
    """Wrap one projected delta as an in-toto statement about the reviewed tree.

    The delta is not recomputed here: this calls the same
    :func:`project_capability_delta` the reviewer surfaces call, and supplies
    the two tree ids as the payload's ``ref`` labels so the subject and the
    payload name the same two states.
    """

    delta = project_capability_delta(
        base_facts,
        head_facts,
        base_ref=base_tree_sha,
        head_ref=head_tree_sha,
        base_analysis_coverage=base_analysis_coverage,
        head_analysis_coverage=head_analysis_coverage,
    )
    return CapabilityDeltaAttestationV1(
        _type=IN_TOTO_STATEMENT_TYPE,
        subject=(
            CapabilityDeltaAttestationSubject(
                name=subject_name,
                digest=CapabilityDeltaSubjectDigest(
                    gitTree=head_tree_sha,
                    gitCommit=head_commit_sha,
                ),
            ),
        ),
        predicateType=CAPABILITY_DELTA_PREDICATE_TYPE,
        predicate=CapabilityDeltaPredicateV1(
            predicate_schema_version=CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION,
            capability_payload_schema_version=CAPABILITY_PAYLOAD_SCHEMA_VERSION,
            delta=delta,
            verification=verification or CapabilityDeltaVerificationRef.unbound(),
        ),
    )


__all__ = [
    "CapabilityDeltaAttestationInputs",
    "ObservedSubject",
    "coverage_from_scan",
    "project_capability_delta_attestation",
]
