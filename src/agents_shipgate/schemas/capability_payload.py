"""``shipgate.capability_payload/v1`` — the one capability payload.

Two public surfaces are planned over the same internal truth: the exported
capability delta published as a standalone attestation
([#470](https://github.com/ThreeMoonsLab/agents-shipgate/issues/470)) and the
committed capability state
([#474](https://github.com/ThreeMoonsLab/agents-shipgate/issues/474)). Both are
projections of the capability-fact layer in
:mod:`agents_shipgate.schemas.capabilities`. If each defined its own
serialization we would ship two divergent schemas of one structure — the
recurring "second implementation" class. This module freezes the shared payload
**before either surface exists**, so both consume it and neither invents one.

It is a payload, not an envelope. The attestation's predicate type and signing,
and the state file's location and regeneration discipline, belong to those
surfaces; this module owns only what they both serialize. Nothing here gates:
``release_decision.decision`` remains the only release gate.

Three structural rules carry the design.

**One subject, one row.** ``subjects[]`` is keyed on the *subject* — the tool as
a reader recognizes it — never on the change. One tool that moves on both the
catalog and the action surface is one row carrying two changes, not two rows
(#439), and the counts in ``summary`` are subject counts recomputed from the
rows rather than supplied alongside them. A payload whose summary disagrees with
its rows is rejected rather than silently corrected, so the ``+2``-for-one-tool
shape cannot be stated in this schema even by a hand-written or tampered
document.

**The published set is closed.** Every model sets ``extra="forbid"``, so an
internal field cannot ride along into either surface's output by accident. What
the payload deliberately does *not* publish, and why, is listed in
``docs/capability-payload.md``; :mod:`agents_shipgate.core.capability_payload`
holds the machine-readable exclusion set that keeps the two in step.

**Identity is recorded, never re-derived.** Subject identity comes from the
capability fact, which took it at the adapter read boundary. ``capability_id``
on each record is the internal ``CapabilityFactV1.id`` verbatim, so a consumer
can join a published row back to the fact that produced it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.capabilities import (
    CapabilityEvidenceProvenanceKind,
)
from agents_shipgate.schemas.capability_change import CapabilitySubjectKind
from agents_shipgate.schemas.capability_semantics import (
    CapabilityHashName,
    CapabilitySemanticChange,
    CapabilitySemanticDirection,
    capability_semantic_change_sort_key,
)
from agents_shipgate.schemas.common import Confidence
from agents_shipgate.schemas.surfaces import ActionEffect

CAPABILITY_PAYLOAD_SCHEMA_VERSION = "shipgate.capability_payload/v1"
CAPABILITY_PAYLOAD_SCHEMA_PATH = "docs/capability-payload-schema.v1.json"
CAPABILITY_PAYLOAD_SPEC_PATH = "docs/capability-payload.md"

#: Prefix of :attr:`CapabilitySubjectRef.key`. The key is a digest rather than a
#: readable string because it must be stable under display-name collisions: two
#: providers may both publish ``search``, and joining them would be exactly the
#: subject conflation this schema exists to prevent.
SUBJECT_KEY_PREFIX = "capsubj_"
SUBJECT_KEY_DIGEST_CHARS = 16

#: How one subject moved between two states. Subject-level, so it rolls up the
#: per-capability transitions below: ``added`` and ``removed`` only when every
#: capability of the subject moved that way, ``modified`` otherwise.
CapabilitySubjectTransition = Literal["added", "removed", "modified"]

#: How one capability record moved. ``reidentified`` keeps the pairing the fact
#: layer already proved (scope or resource moved within one canonical tool
#: identity) rather than reporting an unrelated add and remove.
CapabilityRecordTransition = Literal["added", "removed", "changed", "reidentified"]

CapabilityPermissionClass = Literal[
    "read",
    "write",
    "destructive",
    "external",
    "financial",
    "production",
    "unknown",
]

#: Whether the permission profile was measured from static evidence at all.
#: ``unavailable`` is the fail-closed reading: it is not "no side effects", and
#: ``side_effect_unknown`` is ``True`` alongside it.
CapabilityPermissionStatus = Literal["measured", "unavailable"]


def canonical_payload_json(payload: Any) -> str:
    """Canonical JSON for digesting — sorted keys, no insignificant space.

    Deliberately without a ``default=`` fallback. A digest an external consumer
    verifies must not be computable over a lossy ``str()`` rendering: two
    distinct values whose text form coincides would digest identically, and the
    consumer would read two different states as one. A value this cannot
    serialize is a bug in the caller, and raising says so.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def payload_digest(payload: Any) -> str:
    """sha256 over :func:`canonical_payload_json`, as lowercase hex."""

    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


class CapabilitySubjectRef(BaseModel):
    """The thing a reader recognizes: one tool, under one agent and provider.

    ``key`` is the row identity for the whole payload. It is derived from
    ``agent``/``provider``/``tool_id`` only — deliberately **not** from
    ``subject_kind`` — because a tool and its action are the same subject to a
    reader, and keying on the kind is precisely how one added tool became two
    rows and a ``+2`` (#439).

    ``name`` is the adopter-facing spelling and is not identity: two providers
    may ship the same name. Consumers that render a name must qualify it with
    ``provider`` when two rows share one ``name``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    agent: str
    provider: str
    tool_id: str


class CapabilityEffectFacts(BaseModel):
    """The normalized side-effect facts, as published."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: ActionEffect
    externally_visible: bool = False
    handles_sensitive_data: bool = False
    financial: bool = False
    code_execution: bool = False
    reversibility: Literal["reversible", "irreversible", "unknown"] = "unknown"
    idempotency_known: bool | None = None
    high_risk: bool = False


class CapabilityAuthorityFacts(BaseModel):
    """Who the capability acts as, and how widely."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_type: str | None = None
    credential_mode: str | None = None
    source: str | None = None
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    broad_scopes: tuple[str, ...] = Field(default_factory=tuple)


class CapabilityControlFacts(BaseModel):
    """Controls already declared on the action surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_required: bool | None = None
    approval_threshold: str | None = None
    confirmation_required: bool = False
    safeguard_idempotency: bool | None = None
    safeguard_audit_log: bool | None = None
    safeguard_rollback: bool | None = None
    safeguard_dry_run: bool | None = None
    evidence_owner: str | None = None
    evidence_runbook: str | None = None
    evidence_approval_ticket: str | None = None


class CapabilityPermissionFacts(BaseModel):
    """The permission lattice classes, without the audit risk score.

    ``classes`` and ``side_effect_unknown`` are the semantic half of
    ``core.capability_lattice.CapabilityPermissionProfile`` and come from the
    same classifier, so this payload cannot disagree with ``mcp audit``. The
    profile's ``risk_score`` / ``risk_level`` / ``reasons`` are deliberately not
    published — see the exclusions section of ``docs/capability-payload.md``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapabilityPermissionStatus = "unavailable"
    classes: tuple[CapabilityPermissionClass, ...] = Field(default_factory=tuple)
    side_effect_unknown: bool = True

    @model_validator(mode="after")
    def _unavailable_is_fail_closed(self) -> CapabilityPermissionFacts:
        if self.status == "unavailable" and not self.side_effect_unknown:
            raise ValueError(
                "CapabilityPermissionFacts.status='unavailable' cannot claim "
                "side_effect_unknown=False: an unmeasured profile is unknown, "
                "not side-effect free"
            )
        return self


class CapabilityEvidenceRef(BaseModel):
    """Where the capability was read from, and how confidently.

    This is the provenance a consumer needs to open the file that made the
    claim. ``source_location`` is deliberately absent: it is a rendering of
    ``source_path`` plus ``source_start_line``, and publishing both would be two
    spellings of one value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    provenance_kind: CapabilityEvidenceProvenanceKind = "static_declaration"
    confidence: Confidence = "medium"


class CapabilityDigests(BaseModel):
    """The per-dimension digests the fact layer already separates.

    Published verbatim, under the internal field names, so a consumer comparing
    two payloads asks the same question the engine asks — which dimension moved
    — without re-deriving anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_hash: str
    binding_hash: str
    effect_hash: str
    authority_hash: str
    control_hash: str
    schema_hash: str
    risk_hash: str
    evidence_hash: str


class CapabilityRecord(BaseModel):
    """One capability of one subject: an operation with its effect and authority.

    ``capability_id`` is the internal ``CapabilityFactV1.id`` verbatim. It is the
    join key back to the fact layer and the reason the round trip in
    ``tests/test_capability_payload.py`` can compare published rows with the
    facts that produced them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_id: str
    operation: str
    subject_kind: CapabilitySubjectKind = "action"
    resource: tuple[str, ...] = Field(default_factory=tuple)
    scope: tuple[str, ...] = Field(default_factory=tuple)
    effect: CapabilityEffectFacts
    authority: CapabilityAuthorityFacts
    controls: CapabilityControlFacts
    permission: CapabilityPermissionFacts
    evidence: CapabilityEvidenceRef
    risk_tags: tuple[str, ...] = Field(default_factory=tuple)
    digests: CapabilityDigests


def capability_record_sort_key(record: CapabilityRecord) -> tuple[str, str, str]:
    return (record.subject_kind, record.operation, record.capability_id)


class CapabilityStateSubject(BaseModel):
    """One subject and every capability it holds in one state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: CapabilitySubjectRef
    capabilities: tuple[CapabilityRecord, ...]

    @model_validator(mode="after")
    def _records_are_unique_and_sorted(self) -> CapabilityStateSubject:
        _require_unique(
            [record.capability_id for record in self.capabilities],
            what=f"capability_id under subject {self.subject.key}",
        )
        if not self.capabilities:
            raise ValueError(
                f"subject {self.subject.key} carries no capabilities: a subject "
                "row exists because a capability does"
            )
        _require_sorted(self.capabilities, capability_record_sort_key, what="capabilities")
        return self


class CapabilityRecordTransitionEntry(BaseModel):
    """How one capability record moved between the two states.

    ``before`` / ``after`` are populated by the transition: ``added`` has no
    before, ``removed`` has no after, and ``changed`` / ``reidentified`` carry
    both. Enforced here rather than left to the producer, so a consumer can rely
    on the pairing without a null check on every field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transition: CapabilityRecordTransition
    changed_dimensions: tuple[CapabilityHashName, ...] = Field(default_factory=tuple)
    semantic_direction: CapabilitySemanticDirection = "unknown"
    semantic_changes: tuple[CapabilitySemanticChange, ...] = Field(default_factory=tuple)
    before: CapabilityRecord | None = None
    after: CapabilityRecord | None = None

    @model_validator(mode="after")
    def _sides_match_transition(self) -> CapabilityRecordTransitionEntry:
        expects_before = self.transition != "added"
        expects_after = self.transition != "removed"
        if expects_before is (self.before is None):
            raise ValueError(
                f"transition {self.transition!r} "
                f"{'requires' if expects_before else 'forbids'} a 'before' record"
            )
        if expects_after is (self.after is None):
            raise ValueError(
                f"transition {self.transition!r} "
                f"{'requires' if expects_after else 'forbids'} an 'after' record"
            )
        if self.transition in {"added", "removed"} and self.changed_dimensions:
            raise ValueError(
                f"transition {self.transition!r} is a membership change and "
                "cannot name changed dimensions"
            )
        if self.transition in {"changed", "reidentified"} and not self.changed_dimensions:
            raise ValueError(
                f"transition {self.transition!r} must name at least one changed "
                "dimension; an unchanged capability is not a delta row"
            )
        _require_sorted(
            self.semantic_changes,
            capability_semantic_change_sort_key,
            what="semantic_changes",
        )
        if sorted(set(self.changed_dimensions)) != list(self.changed_dimensions):
            raise ValueError("changed_dimensions must be sorted and unique")
        return self

    @property
    def record(self) -> CapabilityRecord:
        """The side that describes the capability now — after, or before if gone."""

        current = self.after if self.after is not None else self.before
        if current is None:  # pragma: no cover - _sides_match_transition forbids it
            raise ValueError(
                f"transition {self.transition!r} carries neither side"
            )
        return current


def capability_transition_sort_key(
    entry: CapabilityRecordTransitionEntry,
) -> tuple[str, str, str]:
    return capability_record_sort_key(entry.record)


class CapabilityDeltaSubject(BaseModel):
    """One subject's whole movement between two states — one subject, one row.

    ``transition`` is a rollup of ``changes``, recomputed here rather than
    supplied, so it cannot describe a movement the rows do not show.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: CapabilitySubjectRef
    transition: CapabilitySubjectTransition
    changes: tuple[CapabilityRecordTransitionEntry, ...]

    @model_validator(mode="after")
    def _rollup_matches_changes(self) -> CapabilityDeltaSubject:
        if not self.changes:
            raise ValueError(
                f"subject {self.subject.key} carries no changes: a delta row "
                "exists because something moved"
            )
        # Both sides, not only the surviving one: a reidentified entry names two
        # capability ids, and a payload that published one capability under two
        # rows would be the same defect this row shape exists to prevent.
        _require_unique(
            [
                capability_id
                for entry in self.changes
                # De-duplicate *within* an entry: a ``changed`` row names the
                # same capability on both sides, while a ``reidentified`` row
                # names two. Across entries they must still all be distinct.
                for capability_id in dict.fromkeys(
                    side.capability_id
                    for side in (entry.before, entry.after)
                    if side is not None
                )
            ],
            what=f"capability_id under subject {self.subject.key}",
        )
        _require_sorted(self.changes, capability_transition_sort_key, what="changes")
        expected = subject_transition(entry.transition for entry in self.changes)
        if self.transition != expected:
            raise ValueError(
                f"subject {self.subject.key} declares transition "
                f"{self.transition!r} but its changes roll up to {expected!r}"
            )
        return self

    @property
    def change_count(self) -> int:
        return len(self.changes)


def subject_transition(
    transitions: Iterable[CapabilityRecordTransition],
) -> CapabilitySubjectTransition:
    """Roll per-capability transitions up to the subject.

    A subject is ``added`` only when every capability it holds is new, and
    ``removed`` only when every one is gone. Anything else — including a subject
    that gained one capability and lost another — is ``modified``, because
    calling it ``added`` would overstate and calling it ``removed`` would
    understate.
    """

    kinds = set(transitions)
    if not kinds:
        raise ValueError("cannot roll up an empty transition set")
    if kinds == {"added"}:
        return "added"
    if kinds == {"removed"}:
        return "removed"
    return "modified"


#: Whether the payload's producer established what its analysed surface left
#: out. ``not_requested`` and ``unavailable`` are **not** "nothing was left
#: out": a consumer that reads either as zero re-creates the #437 defect, where
#: a PR whose whole content was one added tool reported a capability delta of
#: ``+0``.
CapabilityAnalysisStatus = Literal["not_requested", "unavailable", "complete"]


class CapabilityAnalysisCoverage(BaseModel):
    """Subjects the producer knows about but could not analyse.

    Capability rows describe the *analysed* surface — what the binding graph
    proved the agent can reach. A tool that is present but unbound is precisely
    the tool that is missing from it, so a payload that carried only the rows
    would report no capability change on a change that added a tool (#437).

    This block is the separate axis that says so, and it names the subjects
    rather than only counting them (#433). It is deliberately **not** joined to
    ``subjects[]``: a tool that lost its binding is both removed from analysed
    capability and newly outside analysis, and both statements are true.

    ``status`` is load-bearing. Naming subjects requires having looked, so
    anything other than ``complete`` must carry an empty list — the schema
    refuses to let "we did not look" be written in the same shape as "we looked
    and found none".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapabilityAnalysisStatus = "not_requested"
    subjects_outside_analysis: tuple[CapabilitySubjectRef, ...] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def _named_subjects_require_a_completed_analysis(self) -> CapabilityAnalysisCoverage:
        if self.status != "complete" and self.subjects_outside_analysis:
            raise ValueError(
                f"analysis_coverage.status={self.status!r} cannot name subjects "
                "outside analysis: naming them requires having looked"
            )
        _require_unique(
            [subject.key for subject in self.subjects_outside_analysis],
            what="subject key outside analysis",
        )
        _require_sorted(
            self.subjects_outside_analysis,
            subject_sort_key,
            what="subjects_outside_analysis",
        )
        return self


class CapabilityStateRef(BaseModel):
    """What a payload says about one whole state, including one it does not carry.

    A delta names its two sides here. ``capability_set_digest`` and
    ``evidence_set_digest`` are computed over the *published* rows of the full
    state, so a consumer holding both the state payload and the delta payload
    can prove they describe the same state without re-running anything of ours.

    ``ref`` is an opaque caller label — a commit sha, a lock path — and is never
    a timestamp: this payload carries no wall clock, so two exports of the same
    inputs are byte-identical.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_standard_version: str
    subject_count: int = 0
    capability_count: int = 0
    capability_set_digest: str
    evidence_set_digest: str
    ref: str | None = None


class CapabilityDeltaSummary(BaseModel):
    """Subject counts, recomputed from the rows.

    The directional counts partition the subjects — every subject is added,
    removed, or modified and nothing else — so ``subjects`` is their sum.
    ``capability_changes`` is the finer number and is deliberately a *separate*
    field: the question "how much did this PR change what the agent can do?" is
    a question about subjects, and answering it in changes inflates the number
    by however many dimensions each subject happens to touch (#439).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subjects: int = 0
    added_subjects: int = 0
    removed_subjects: int = 0
    modified_subjects: int = 0
    capability_changes: int = 0

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> CapabilityDeltaSummary:
        directional = self.added_subjects + self.removed_subjects + self.modified_subjects
        if directional != self.subjects:
            raise ValueError(
                "CapabilityDeltaSummary directional subject counts "
                f"({directional}) must sum to subjects ({self.subjects})"
            )
        if self.capability_changes < self.subjects:
            raise ValueError(
                "CapabilityDeltaSummary.capability_changes "
                f"({self.capability_changes}) cannot be fewer than subjects "
                f"({self.subjects}): every subject row carries at least one change"
            )
        return self


class _CapabilityPayloadBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_payload_schema_version: Literal["shipgate.capability_payload/v1"] = (
        CAPABILITY_PAYLOAD_SCHEMA_VERSION
    )
    capability_standard_version: str
    analysis_coverage: CapabilityAnalysisCoverage = Field(
        default_factory=CapabilityAnalysisCoverage
    )


class CapabilityStatePayloadV1(_CapabilityPayloadBase):
    """What the agent can do, at one state. The committed-state view (#474)."""

    view: Literal["state"] = "state"
    state: CapabilityStateRef
    subjects: tuple[CapabilityStateSubject, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _subjects_are_unique_sorted_and_counted(self) -> CapabilityStatePayloadV1:
        _require_unique(
            [entry.subject.key for entry in self.subjects],
            what="subject key",
        )
        _require_sorted(self.subjects, _state_subject_sort_key, what="subjects")
        if self.state.subject_count != len(self.subjects):
            raise ValueError(
                f"state.subject_count ({self.state.subject_count}) does not "
                f"match the {len(self.subjects)} subject row(s) carried"
            )
        capabilities = sum(len(entry.capabilities) for entry in self.subjects)
        if self.state.capability_count != capabilities:
            raise ValueError(
                f"state.capability_count ({self.state.capability_count}) does "
                f"not match the {capabilities} capability record(s) carried"
            )
        if self.state.capability_standard_version != self.capability_standard_version:
            raise ValueError(
                "state.capability_standard_version must equal the payload's "
                "capability_standard_version"
            )
        return self


class CapabilityDeltaPayloadV1(_CapabilityPayloadBase):
    """What changed between two states. The exported-attestation view (#470)."""

    view: Literal["delta"] = "delta"
    base: CapabilityStateRef
    head: CapabilityStateRef
    summary: CapabilityDeltaSummary
    subjects: tuple[CapabilityDeltaSubject, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _summary_matches_subjects(self) -> CapabilityDeltaPayloadV1:
        _require_unique(
            [entry.subject.key for entry in self.subjects],
            what="subject key",
        )
        _require_sorted(self.subjects, _delta_subject_sort_key, what="subjects")
        expected = delta_summary(self.subjects)
        if self.summary != expected:
            raise ValueError(
                "CapabilityDeltaPayloadV1.summary does not describe its rows: "
                f"declared {self.summary.model_dump()}, rows give "
                f"{expected.model_dump()}"
            )
        for side, ref in (("base", self.base), ("head", self.head)):
            if ref.capability_standard_version != self.capability_standard_version:
                raise ValueError(
                    f"{side}.capability_standard_version must equal the "
                    "payload's capability_standard_version"
                )
        # An empty delta is a claim that the two states are the same state, so
        # it has to be one the payload's own digests support. No rows means
        # every fact matched on every dimension, which makes both published
        # digests equal by construction — so only a hand-written or tampered
        # payload can say "nothing changed" while naming two different states,
        # and a consumer must not have to notice that itself.
        if not self.subjects and (
            self.base.capability_set_digest != self.head.capability_set_digest
            or self.base.evidence_set_digest != self.head.evidence_set_digest
        ):
            raise ValueError(
                "a delta with no subject rows claims base and head are the "
                "same state, but their digests differ "
                f"(capability {self.base.capability_set_digest[:12]}… vs "
                f"{self.head.capability_set_digest[:12]}…, evidence "
                f"{self.base.evidence_set_digest[:12]}… vs "
                f"{self.head.evidence_set_digest[:12]}…)"
            )
        return self


def delta_summary(
    subjects: tuple[CapabilityDeltaSubject, ...],
) -> CapabilityDeltaSummary:
    """The only place a delta summary is computed."""

    by_transition: dict[CapabilitySubjectTransition, int] = {
        "added": 0,
        "removed": 0,
        "modified": 0,
    }
    for entry in subjects:
        by_transition[entry.transition] += 1
    return CapabilityDeltaSummary(
        subjects=len(subjects),
        added_subjects=by_transition["added"],
        removed_subjects=by_transition["removed"],
        modified_subjects=by_transition["modified"],
        capability_changes=sum(entry.change_count for entry in subjects),
    )


CapabilityPayload = Annotated[
    CapabilityStatePayloadV1 | CapabilityDeltaPayloadV1,
    Field(discriminator="view"),
]


class CapabilityPayloadV1(RootModel[CapabilityPayload]):
    """Either view of the one payload, discriminated on ``view``."""

    root: CapabilityPayload


class CapabilityStatePayloadArtifactV1(RootModel[CapabilityStatePayloadV1]):
    root: CapabilityStatePayloadV1


class CapabilityDeltaPayloadArtifactV1(RootModel[CapabilityDeltaPayloadV1]):
    root: CapabilityDeltaPayloadV1


def subject_key(*, agent: str, provider: str, tool_id: str) -> str:
    """Derive the payload-wide row key for one subject.

    Kind-free by construction: a tool and its action share this key, so they
    share a row. Documented in ``docs/capability-payload.md`` so an external
    consumer can recompute it from the fields the payload already carries.
    """

    digest = payload_digest(
        {"agent": agent, "provider": provider, "tool_id": tool_id}
    )
    return f"{SUBJECT_KEY_PREFIX}{digest[:SUBJECT_KEY_DIGEST_CHARS]}"


def subject_sort_key(subject: CapabilitySubjectRef) -> tuple[str, str, str, str]:
    """The one row ordering. Two builds of the same inputs must agree on it."""

    return (subject.agent, subject.provider, subject.name, subject.key)


def _state_subject_sort_key(entry: CapabilityStateSubject) -> tuple[str, str, str, str]:
    return subject_sort_key(entry.subject)


def _delta_subject_sort_key(entry: CapabilityDeltaSubject) -> tuple[str, str, str, str]:
    return subject_sort_key(entry.subject)


def _require_unique(keys: list[str], *, what: str) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise ValueError(
                f"duplicate {what}: {key!r}. One subject is one row, and one "
                "capability is one row within it — a payload cannot state the "
                "same thing twice"
            )
        seen.add(key)


def _require_sorted(
    items: Iterable[Any],
    key: Callable[[Any], Any],
    *,
    what: str,
) -> None:
    ordered = [key(item) for item in items]
    if ordered != sorted(ordered):
        raise ValueError(
            f"{what} must be emitted in sorted order so two builds of the same "
            "inputs serialize byte-identically"
        )


__all__ = [
    "CAPABILITY_PAYLOAD_SCHEMA_PATH",
    "CAPABILITY_PAYLOAD_SCHEMA_VERSION",
    "CAPABILITY_PAYLOAD_SPEC_PATH",
    "SUBJECT_KEY_DIGEST_CHARS",
    "SUBJECT_KEY_PREFIX",
    "CapabilityAnalysisCoverage",
    "CapabilityAnalysisStatus",
    "CapabilityAuthorityFacts",
    "CapabilityControlFacts",
    "CapabilityDeltaPayloadArtifactV1",
    "CapabilityDeltaPayloadV1",
    "CapabilityDeltaSubject",
    "CapabilityDeltaSummary",
    "CapabilityDigests",
    "CapabilityEffectFacts",
    "CapabilityEvidenceRef",
    "CapabilityPayload",
    "CapabilityPayloadV1",
    "CapabilityPermissionClass",
    "CapabilityPermissionFacts",
    "CapabilityPermissionStatus",
    "CapabilityRecord",
    "CapabilityRecordTransition",
    "CapabilityRecordTransitionEntry",
    "CapabilityStatePayloadArtifactV1",
    "CapabilityStatePayloadV1",
    "CapabilityStateRef",
    "CapabilityStateSubject",
    "CapabilitySubjectRef",
    "CapabilitySubjectTransition",
    "canonical_payload_json",
    "capability_record_sort_key",
    "capability_transition_sort_key",
    "delta_summary",
    "payload_digest",
    "subject_key",
    "subject_sort_key",
    "subject_transition",
]
