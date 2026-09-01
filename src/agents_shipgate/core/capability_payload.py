"""The one projection from capability facts into the frozen public payload.

:mod:`agents_shipgate.schemas.capability_payload` fixes the wire shape. This
module is the only thing that fills it. Both planned surfaces — the exported
delta attestation (#470) and the committed capability state (#474) — call these
functions rather than serializing capability facts themselves, which is the
whole point of freezing the schema before either ships.

No emitter lives here: nothing writes a file, adds a command, or reaches the
release decision. ``release_decision.decision`` remains the only gate.

The state and the delta come from one computation. ``project_capability_delta``
builds both full states internally and takes the ``CapabilityStateRef`` of each
side from them, so the digests a delta names are exactly the digests the
matching state payload publishes — the property a consumer needs in order to
prove a delta and a state describe the same commit.

**The published field set is closed and declared.** ``PUBLISHED_FACT_FIELDS``
and ``UNPUBLISHED_FACT_FIELDS`` between them must cover every field of
``CapabilityFactV1``; ``tests/test_capability_payload.py`` enforces that, so a
new internal field cannot reach either surface — or be silently dropped from
both — without someone writing down which it is and why.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents_shipgate.core.capability_delta import (
    CapabilityDeltaRow,
    CapabilityFactContext,
    diff_capability_fact_sets,
)
from agents_shipgate.core.capability_lattice import classify_semantic_permission
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_STANDARD_VERSION,
    CapabilityFactV1,
)
from agents_shipgate.schemas.capability_payload import (
    CAPABILITY_PAYLOAD_SCHEMA_VERSION,
    CapabilityAnalysisCoverage,
    CapabilityAuthorityFacts,
    CapabilityControlFacts,
    CapabilityCoverageDelta,
    CapabilityDeltaPayloadV1,
    CapabilityDeltaSubject,
    CapabilityDigests,
    CapabilityEffectFacts,
    CapabilityEvidenceRef,
    CapabilityPermissionFacts,
    CapabilityRecord,
    CapabilityRecordTransition,
    CapabilityRecordTransitionEntry,
    CapabilityStatePayloadV1,
    CapabilityStateRef,
    CapabilityStateSubject,
    CapabilitySubjectRef,
    capability_record_sort_key,
    capability_transition_sort_key,
    changed_record_dimensions,
    delta_summary,
    published_semantic_shift,
    state_digests,
    subject_key,
    subject_sort_key,
    subject_transition,
)


class CapabilityPayloadError(ValueError):
    """The facts cannot be projected into the frozen payload.

    Raised rather than papered over: a payload that quietly dropped or merged a
    capability would be a worse answer than no payload, because both consuming
    surfaces are things another tool is meant to trust without re-deriving.
    """


#: Internal ``CapabilityFactV1`` path -> the payload path that publishes it.
#: Paths are dotted from the fact root; payload paths are dotted from one
#: ``CapabilityStateSubject`` (so ``subject.*`` fields live on the shared
#: subject row and everything else on the ``CapabilityRecord``).
PUBLISHED_FACT_FIELDS: dict[str, str] = {
    "id": "capabilities[].capability_id",
    "identity.agent_id": "subject.agent",
    "identity.tool_id": "subject.tool_id",
    "identity.tool_name": "subject.name",
    "identity.provider": "subject.provider",
    "identity.operation": "capabilities[].operation",
    "identity.subject_kind": "capabilities[].subject_kind",
    "identity.resource": "capabilities[].resource",
    "identity.scope": "capabilities[].scope",
    "effect.effect": "capabilities[].effect.effect",
    "effect.externally_visible": "capabilities[].effect.externally_visible",
    "effect.handles_sensitive_data": "capabilities[].effect.handles_sensitive_data",
    "effect.financial": "capabilities[].effect.financial",
    "effect.code_execution": "capabilities[].effect.code_execution",
    "effect.reversibility": "capabilities[].effect.reversibility",
    "effect.idempotency_known": "capabilities[].effect.idempotency_known",
    "effect.high_risk": "capabilities[].effect.high_risk",
    "authority.auth_type": "capabilities[].authority.auth_type",
    "authority.credential_mode": "capabilities[].authority.credential_mode",
    "authority.source": "capabilities[].authority.source",
    "authority.scopes": "capabilities[].authority.scopes",
    "authority.broad_scopes": "capabilities[].authority.broad_scopes",
    "controls.approval_required": "capabilities[].controls.approval_required",
    "controls.approval_threshold": "capabilities[].controls.approval_threshold",
    "controls.confirmation_required": "capabilities[].controls.confirmation_required",
    "controls.safeguard_idempotency": "capabilities[].controls.safeguard_idempotency",
    "controls.safeguard_audit_log": "capabilities[].controls.safeguard_audit_log",
    "controls.safeguard_rollback": "capabilities[].controls.safeguard_rollback",
    "controls.safeguard_dry_run": "capabilities[].controls.safeguard_dry_run",
    "controls.evidence_owner": "capabilities[].controls.evidence_owner",
    "controls.evidence_runbook": "capabilities[].controls.evidence_runbook",
    "controls.evidence_approval_ticket": "capabilities[].controls.evidence_approval_ticket",
    "evidence.source_type": "capabilities[].evidence.source_type",
    "evidence.source_id": "capabilities[].evidence.source_id",
    "evidence.source_ref": "capabilities[].evidence.source_ref",
    "evidence.source_path": "capabilities[].evidence.source_path",
    "evidence.source_start_line": "capabilities[].evidence.source_start_line",
    "evidence.source_end_line": "capabilities[].evidence.source_end_line",
    "evidence.source_start_column": "capabilities[].evidence.source_start_column",
    "evidence.source_pointer": "capabilities[].evidence.source_pointer",
    "evidence.provenance_kind": "capabilities[].evidence.provenance_kind",
    "evidence.confidence": "capabilities[].evidence.confidence",
    "risk_tags": "capabilities[].risk_tags",
    "hashes.identity_hash": "capabilities[].digests.identity_hash",
    "hashes.binding_hash": "capabilities[].digests.binding_hash",
    "hashes.effect_hash": "capabilities[].digests.effect_hash",
    "hashes.authority_hash": "capabilities[].digests.authority_hash",
    "hashes.control_hash": "capabilities[].digests.control_hash",
    "hashes.schema_hash": "capabilities[].digests.schema_hash",
    "hashes.risk_hash": "capabilities[].digests.risk_hash",
    "hashes.evidence_hash": "capabilities[].digests.evidence_hash",
}

#: Internal ``CapabilityFactV1`` path -> why the payload does not publish it.
#: Enumeration stops at these paths, so excluding a subtree excludes its
#: children without listing every one.
UNPUBLISHED_FACT_FIELDS: dict[str, str] = {
    "evidence.source_location": (
        "A rendering of source_path plus source_start_line. Publishing both "
        "would put two spellings of one value on the wire, and a consumer that "
        "joined on the wrong one would be joining on a display string."
    ),
    "semantic_assessment": (
        "The derivation, not the fact. Its conclusions are published as "
        "effect, authority and permission; the claim/issue tree underneath is "
        "the extractor's internal working, and freezing it here would freeze "
        "internals this schema has to be able to change."
    ),
}

#: Fields of the existing capability *lock file* — the state artifact this
#: payload supersedes — that the payload deliberately does not carry, and why.
UNPUBLISHED_LOCK_FIELDS: dict[str, str] = {
    "capability_lock_schema_version": (
        "Replaced by capability_payload_schema_version. One document, one "
        "version field."
    ),
    "experimental": (
        "A lock-file era flag. This payload is frozen and versioned instead; a "
        "boolean that is always false is not a compatibility signal."
    ),
    "cli_version": (
        "Release identity, not capability truth. Carrying it would change every "
        "consumer's digest on a release that changed nothing about what the "
        "agent can do."
    ),
    "source": (
        "Scan bookkeeping — config path, manifest dir, tool and source counts, "
        "plugin state. It describes the run that produced the answer, not the "
        "capability the answer is about."
    ),
    "summary": (
        "Recomputed from the rows. A stored count is a count that can disagree "
        "with the rows it summarizes, which is exactly how one added tool came "
        "to report +2 (#439)."
    ),
    "hashes": (
        "Lock-level set hashes over internal fact content. The payload "
        "publishes capability_set_digest and evidence_set_digest instead, "
        "computed over the *published* rows so a consumer can recompute them "
        "from the payload alone without running anything of ours."
    ),
}


def _coverage(
    analysis_coverage: CapabilityAnalysisCoverage | None,
) -> CapabilityAnalysisCoverage:
    """Default to ``not_requested`` — which is not a claim that nothing is out."""

    if analysis_coverage is None:
        return CapabilityAnalysisCoverage.not_requested()
    return analysis_coverage


def project_capability_record(fact: CapabilityFactV1) -> CapabilityRecord:
    """Project one internal capability fact into its published record."""

    return CapabilityRecord(
        capability_id=fact.id,
        operation=fact.identity.operation,
        subject_kind=fact.identity.subject_kind,
        resource=fact.identity.resource,
        scope=fact.identity.scope,
        effect=CapabilityEffectFacts(
            effect=fact.effect.effect,
            externally_visible=fact.effect.externally_visible,
            handles_sensitive_data=fact.effect.handles_sensitive_data,
            financial=fact.effect.financial,
            code_execution=fact.effect.code_execution,
            reversibility=fact.effect.reversibility,
            idempotency_known=fact.effect.idempotency_known,
            high_risk=fact.effect.high_risk,
        ),
        authority=CapabilityAuthorityFacts(
            auth_type=fact.authority.auth_type,
            credential_mode=fact.authority.credential_mode,
            source=fact.authority.source,
            scopes=fact.authority.scopes,
            broad_scopes=fact.authority.broad_scopes,
        ),
        controls=CapabilityControlFacts(
            approval_required=fact.controls.approval_required,
            approval_threshold=fact.controls.approval_threshold,
            confirmation_required=fact.controls.confirmation_required,
            safeguard_idempotency=fact.controls.safeguard_idempotency,
            safeguard_audit_log=fact.controls.safeguard_audit_log,
            safeguard_rollback=fact.controls.safeguard_rollback,
            safeguard_dry_run=fact.controls.safeguard_dry_run,
            evidence_owner=fact.controls.evidence_owner,
            evidence_runbook=fact.controls.evidence_runbook,
            evidence_approval_ticket=fact.controls.evidence_approval_ticket,
        ),
        permission=_permission_facts(fact),
        evidence=CapabilityEvidenceRef(
            source_type=fact.evidence.source_type,
            source_id=fact.evidence.source_id,
            source_ref=fact.evidence.source_ref,
            source_path=fact.evidence.source_path,
            source_start_line=fact.evidence.source_start_line,
            source_end_line=fact.evidence.source_end_line,
            source_start_column=fact.evidence.source_start_column,
            source_pointer=fact.evidence.source_pointer,
            provenance_kind=fact.evidence.provenance_kind,
            confidence=fact.evidence.confidence,
        ),
        risk_tags=fact.risk_tags,
        digests=CapabilityDigests(
            identity_hash=fact.hashes.identity_hash,
            binding_hash=fact.hashes.binding_hash,
            effect_hash=fact.hashes.effect_hash,
            authority_hash=fact.hashes.authority_hash,
            control_hash=fact.hashes.control_hash,
            schema_hash=fact.hashes.schema_hash,
            risk_hash=fact.hashes.risk_hash,
            evidence_hash=fact.hashes.evidence_hash,
        ),
    )


def project_capability_state(
    facts: Sequence[CapabilityFactV1],
    *,
    ref: str | None = None,
    analysis_coverage: CapabilityAnalysisCoverage | None = None,
) -> CapabilityStatePayloadV1:
    """Project a whole capability-fact set into the state view of the payload.

    ``analysis_coverage`` is supplied by the caller because it cannot be derived
    from capability facts: the subjects it names are exactly the ones that
    produced no fact. Omitting it publishes ``status: "not_requested"``, which a
    consumer must not read as "nothing was left out".
    """

    # Snapshot first. `facts` is caller-owned and this function walks it more
    # than once; a list that answers differently on a later pass would produce a
    # payload whose rows and whose digests describe different revisions.
    snapshot = tuple(facts)
    subjects = _state_subjects(snapshot)
    coverage = _coverage(analysis_coverage)
    return CapabilityStatePayloadV1(
        capability_payload_schema_version=CAPABILITY_PAYLOAD_SCHEMA_VERSION,
        capability_standard_version=CAPABILITY_STANDARD_VERSION,
        view="state",
        analysis_coverage=coverage,
        state=state_ref(subjects, coverage, ref=ref),
        subjects=subjects,
    )


def project_capability_delta(
    base_facts: Sequence[CapabilityFactV1],
    head_facts: Sequence[CapabilityFactV1],
    *,
    base_ref: str | None = None,
    head_ref: str | None = None,
    base_analysis_coverage: CapabilityAnalysisCoverage | None = None,
    head_analysis_coverage: CapabilityAnalysisCoverage | None = None,
) -> CapabilityDeltaPayloadV1:
    """Project a base/head capability-fact pair into the delta view.

    The diff itself is not recomputed here: it is
    :func:`agents_shipgate.core.capability_delta.diff_capability_fact_sets`, the
    same engine the capability lock diff and the reviewer surfaces consume. This
    function only groups the engine's per-capability rows onto their subjects.

    Coverage is supplied **per side**, because one snapshot cannot answer the
    #437 question: "a tool was added and is unbound" and "a tool has been
    unbound since before this change" are different facts, and only the first is
    something a reviewer of this diff must act on. The payload names the
    transition between the two sides rather than making a consumer infer it.
    """

    # One snapshot per side, taken before anything reads them. This function
    # walks each side in the diff, the subject-ref, the presence and the
    # state-ref passes, and `_state_subjects` walks it again — so without this
    # the "one computation" the two views share is not a consistency boundary at
    # all. A list that returns a different fact on a later pass, or one mutated
    # by another thread, produced a valid payload whose delta row named one
    # revision while `head` digested another.
    base_snapshot = tuple(base_facts)
    head_snapshot = tuple(head_facts)
    diff = diff_capability_fact_sets(list(base_snapshot), list(head_snapshot))
    entries: list[tuple[CapabilitySubjectRef, CapabilityRecordTransitionEntry]] = []
    for context in diff.added:
        entries.append(_membership_entry(context, transition="added"))
    for context in diff.removed:
        entries.append(_membership_entry(context, transition="removed"))
    for row in diff.reidentified:
        entries.append(_paired_entry(row, transition="reidentified"))
    for row in (*diff.changed, *diff.evidence_changed):
        entries.append(_paired_entry(row, transition="changed"))

    grouped: dict[str, list[CapabilityRecordTransitionEntry]] = {}
    for ref_model, entry in entries:
        grouped.setdefault(ref_model.key, []).append(entry)
    refs = _delta_subject_refs(base_snapshot, head_snapshot)

    # Presence is read off the fact sets, never off the changes. A subject that
    # kept one capability and lost another looks, from its changes alone,
    # exactly like one that went away — and calling that "removed" would tell a
    # reviewer the agent lost a tool it still has.
    in_base = _subject_keys(base_snapshot)
    in_head = _subject_keys(head_snapshot)
    subjects = tuple(
        sorted(
            (
                CapabilityDeltaSubject(
                    subject=refs[key],
                    present_in_base=key in in_base,
                    present_in_head=key in in_head,
                    transition=subject_transition(
                        present_in_base=key in in_base,
                        present_in_head=key in in_head,
                    ),
                    changes=tuple(sorted(group, key=capability_transition_sort_key)),
                )
                for key, group in grouped.items()
            ),
            key=lambda entry: subject_sort_key(entry.subject),
        )
    )
    base_coverage = _coverage(base_analysis_coverage)
    head_coverage = _coverage(head_analysis_coverage)
    return CapabilityDeltaPayloadV1(
        capability_payload_schema_version=CAPABILITY_PAYLOAD_SCHEMA_VERSION,
        capability_standard_version=CAPABILITY_STANDARD_VERSION,
        view="delta",
        analysis_coverage=CapabilityCoverageDelta.of(base_coverage, head_coverage),
        base=state_ref(_state_subjects(base_snapshot), base_coverage, ref=base_ref),
        head=state_ref(_state_subjects(head_snapshot), head_coverage, ref=head_ref),
        summary=delta_summary(subjects),
        subjects=subjects,
    )


def state_ref(
    subjects: tuple[CapabilityStateSubject, ...],
    coverage: CapabilityAnalysisCoverage,
    *,
    ref: str | None = None,
) -> CapabilityStateRef:
    """Describe a whole state, digests included, from its published rows.

    The digest recipe itself lives with the frozen format
    (:func:`agents_shipgate.schemas.capability_payload.state_digests`), so the
    projection and the validator a consumer's parse runs cannot disagree about
    what a state hashes to.
    """

    capability_digest, evidence_digest, coverage_digest = state_digests(subjects, coverage)
    return CapabilityStateRef(
        capability_standard_version=CAPABILITY_STANDARD_VERSION,
        subject_count=len(subjects),
        capability_count=sum(len(subject.capabilities) for subject in subjects),
        capability_set_digest=capability_digest,
        evidence_set_digest=evidence_digest,
        analysis_coverage_digest=coverage_digest,
        ref=ref,
    )


def _state_subjects(
    facts: Sequence[CapabilityFactV1],
) -> tuple[CapabilityStateSubject, ...]:
    grouped: dict[str, list[CapabilityFactV1]] = {}
    for fact in facts:
        grouped.setdefault(_subject_ref(fact).key, []).append(fact)
    refs = _side_refs(facts)
    subjects = [
        CapabilityStateSubject(
            subject=refs[key],
            capabilities=tuple(
                sorted(
                    (project_capability_record(fact) for fact in group),
                    key=capability_record_sort_key,
                )
            ),
        )
        for key, group in grouped.items()
    ]
    return tuple(sorted(subjects, key=lambda entry: subject_sort_key(entry.subject)))


def _subject_keys(facts: Sequence[CapabilityFactV1]) -> set[str]:
    return {_subject_ref(fact).key for fact in facts}


def _side_refs(facts: Sequence[CapabilityFactV1]) -> dict[str, CapabilitySubjectRef]:
    """One ref per subject on one side, with a build-order-independent name."""

    refs: dict[str, CapabilitySubjectRef] = {}
    for fact in facts:
        ref_model = _subject_ref(fact)
        refs[ref_model.key] = _merge_subject_refs(refs.get(ref_model.key), ref_model)
    return refs


def _delta_subject_refs(
    base_facts: Sequence[CapabilityFactV1],
    head_facts: Sequence[CapabilityFactV1],
) -> dict[str, CapabilitySubjectRef]:
    """Name a delta's subjects as head spells them, not as the alphabet does.

    Reducing both sides with a lexical-minimum tie-break silently published the
    *old* name for a renamed tool, which is the one spelling a reviewer of this
    diff cannot find in the head tree. The head spelling wins wherever the
    subject still exists; a subject only in base keeps the only name it has.
    Identity is still checked across the two sides — a key that covered two
    identities would be a collision, and that must fail rather than merge.
    """

    base_refs = _side_refs(base_facts)
    head_refs = _side_refs(head_facts)
    refs: dict[str, CapabilitySubjectRef] = {}
    for key in base_refs.keys() | head_refs.keys():
        base_ref = base_refs.get(key)
        head_ref = head_refs.get(key)
        if base_ref is not None and head_ref is not None:
            _merge_subject_refs(base_ref, head_ref)
        refs[key] = head_ref if head_ref is not None else base_ref  # type: ignore[assignment]
    return refs


def _subject_ref(fact: CapabilityFactV1) -> CapabilitySubjectRef:
    identity = fact.identity
    return CapabilitySubjectRef(
        key=subject_key(
            agent=identity.agent_id,
            provider=identity.provider,
            tool_id=identity.tool_id,
        ),
        name=identity.tool_name,
        agent=identity.agent_id,
        provider=identity.provider,
        tool_id=identity.tool_id,
    )


def _merge_subject_refs(
    existing: CapabilitySubjectRef | None,
    incoming: CapabilitySubjectRef,
) -> CapabilitySubjectRef:
    """Reconcile two refs that share a key.

    The identity check runs **first and unconditionally**. ``subject.key`` is a
    truncated digest, so equal keys are near-certainly — but not provably — the
    same subject; letting a matching display name short-circuit the check would
    make a key collision merge two tools silently, which is the conflation this
    schema exists to prevent. Failing closed on a collision costs nothing,
    because the projection cannot produce one from distinct identities.

    ``name`` is a display string and can legitimately differ between the two
    sides of a rename, so pick the lexicographically first — a stable choice,
    not a judgement — rather than letting build order decide which spelling
    reaches the wire.
    """

    if existing is None:
        return incoming
    if (existing.agent, existing.provider, existing.tool_id) != (
        incoming.agent,
        incoming.provider,
        incoming.tool_id,
    ):
        raise CapabilityPayloadError(
            f"subject key {existing.key} covers two different identities: "
            f"{(existing.agent, existing.provider, existing.tool_id)} and "
            f"{(incoming.agent, incoming.provider, incoming.tool_id)}"
        )
    if existing.name == incoming.name:
        return existing
    return existing.model_copy(update={"name": min(existing.name, incoming.name)})


def _membership_entry(
    context: CapabilityFactContext,
    *,
    transition: CapabilityRecordTransition,
) -> tuple[CapabilitySubjectRef, CapabilityRecordTransitionEntry]:
    record = project_capability_record(context.fact)
    ref_model = _subject_ref(context.fact)
    side = "after" if transition == "added" else "before"
    sides: dict[str, CapabilityRecord | None] = {"before": None, "after": None}
    sides[side] = record
    return (
        ref_model,
        CapabilityRecordTransitionEntry(
            transition=transition,
            changed_dimensions=(),
            semantic_direction=transition,
            semantic_changes=(),
            **sides,
        ),
    )


def _paired_entry(
    row: CapabilityDeltaRow,
    *,
    transition: CapabilityRecordTransition,
) -> tuple[CapabilitySubjectRef, CapabilityRecordTransitionEntry]:
    before_ref = _subject_ref(row.before)
    after_ref = _subject_ref(row.after)
    if before_ref.key != after_ref.key:
        # The engine pairs only within one agent/tool/provider lineage, so this
        # is unreachable today. Fail closed rather than silently attributing a
        # paired change to one of two subjects: an attestation that names the
        # wrong subject is worse than one that refuses to be produced.
        raise CapabilityPayloadError(
            f"capability delta row {row.id} pairs two subjects "
            f"({before_ref.key} and {after_ref.key})"
        )
    before_record = project_capability_record(row.before)
    after_record = project_capability_record(row.after)
    # The engine's classification is over the *facts*; this payload publishes a
    # narrower set, so the direction and the explanations are derived from the
    # published records by the format itself. One implementation, and the parser
    # recomputes it — so neither can be asserted.
    direction, semantic_changes = published_semantic_shift(before_record, after_record)
    return (
        # The published ref for the row is chosen by `_delta_subject_refs`; this
        # call is the identity guard on the pairing, not a naming decision.
        _merge_subject_refs(before_ref, after_ref),
        CapabilityRecordTransitionEntry(
            transition=transition,
            changed_dimensions=changed_record_dimensions(before_record, after_record),
            semantic_direction=direction,
            semantic_changes=semantic_changes,
            before=before_record,
            after=after_record,
        ),
    )


def _permission_facts(fact: CapabilityFactV1) -> CapabilityPermissionFacts:
    """Publish the permission lattice classes for one fact.

    Shares :func:`classify_semantic_permission` with ``mcp audit``, so the
    payload cannot report a different permission class than the audit surface
    for the same evidence. When the fact carries no semantic assessment — an
    older fact payload, which the fact schema still accepts — the profile is
    ``unavailable`` and side effects are unknown. That is the fail-closed
    reading: unmeasured is not "read-only".
    """

    assessment = fact.semantic_assessment
    if assessment is None:
        return CapabilityPermissionFacts(
            status="unavailable",
            classes=(),
            side_effect_unknown=True,
        )
    classification = classify_semantic_permission(assessment)
    return CapabilityPermissionFacts(
        status="measured",
        classes=classification.classes,
        side_effect_unknown=classification.side_effect_unknown,
    )


__all__ = [
    "PUBLISHED_FACT_FIELDS",
    "UNPUBLISHED_FACT_FIELDS",
    "UNPUBLISHED_LOCK_FIELDS",
    "CapabilityPayloadError",
    "project_capability_delta",
    "project_capability_record",
    "project_capability_state",
    "state_ref",
]
