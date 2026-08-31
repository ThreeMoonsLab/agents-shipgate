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

Four structural rules carry the design.

**One subject, one row.** ``subjects[]`` is keyed on the *subject* — the tool as
a reader recognizes it — never on the change. One tool that moves on both the
catalog and the action surface is one row carrying two changes, not two rows
(#439). The key is not a free label: it is recomputed from the row's own
agent/provider/tool id, so two rows cannot split one logical tool between them.

**A payload cannot contradict itself.** Counts, subject transitions, changed
dimensions, and a state's digests are all *recomputed* when a payload is
parsed, and a payload that disagrees with its own rows is rejected rather than
repaired. A tampered or hand-edited attestation has to fail, not be silently
corrected into a plausible one.

**The published set is closed.** Every model sets ``extra="forbid"``, so an
internal field cannot ride along into either surface's output by accident. What
the payload deliberately does *not* publish, and why, is listed in
``docs/capability-payload.md``; :mod:`agents_shipgate.core.capability_payload`
holds the machine-readable exclusion set that keeps the two in step. Because the
set is closed, ``v1`` is closed: an added field or vocabulary value is ``/v2``,
never an in-place widening a ``v1`` consumer would reject.

**Identity is recorded, never re-derived.** Subject identity comes from the
capability fact, which took it at the adapter read boundary. ``capability_id``
on each record is the internal ``CapabilityFactV1.id`` verbatim, so a consumer
can join a published row back to the fact that produced it.

Pydantic validators express more than JSON Schema can. The generated
``docs/capability-payload-schema.v1.json`` is stage one; the rules that require
recomputation are stage two, enumerated in the spec page and implemented here.
A consumer that runs only stage one is not getting the guarantees the spec
states, and the schema's own description says so.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Annotated, Any, Literal, get_args

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
SUBJECT_KEY_PATTERN = r"^capsubj_[0-9a-f]{16}$"
#: Digests published by this payload are full sha256, lowercase hex. Per-record
#: digests are the capability fact's own, whose width this payload does not fix.
PAYLOAD_DIGEST_PATTERN = r"^[0-9a-f]{64}$"

#: How one subject moved between two states. It is a statement about the
#: **subject's own presence**, not about the kinds of its changes: a tool that
#: merely loses one of several operations is ``modified``, because it is still
#: there. Deriving this from the change kinds instead is the #439 defect in the
#: other direction — a question about subjects answered in changes.
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

#: Restated from ``core.capability_lattice.PERMISSION_CLASS_RANK`` because
#: ``schemas`` cannot import ``core`` without inverting the layering. A parity
#: test pins both the vocabulary and the ranks. Ties are broken by name so
#: ``classes`` has one canonical order in every process — ``financial`` and
#: ``production`` share a rank, and a rank-only sort left their order to
#: hash-randomized set iteration.
PERMISSION_CLASS_RANK: dict[str, int] = {
    "read": 0,
    "write": 1,
    "external": 2,
    "financial": 3,
    "production": 3,
    "destructive": 4,
    "unknown": 5,
}

#: Whether the permission profile was measured from static evidence at all.
#: ``unavailable`` is the fail-closed reading: it is not "no side effects", and
#: ``side_effect_unknown`` is ``True`` alongside it.
CapabilityPermissionStatus = Literal["measured", "unavailable"]

#: Whether the payload's producer established what its analysed surface left
#: out. ``not_requested`` and ``unavailable`` are **not** "nothing was left
#: out": a consumer that reads either as zero re-creates the #437 defect, where
#: a PR whose whole content was one added tool reported a capability delta of
#: ``+0``.
CapabilityAnalysisStatus = Literal["not_requested", "unavailable", "complete"]

#: The per-dimension digests a capability record carries, in the one order this
#: payload emits them.
CAPABILITY_DIGEST_DIMENSIONS: tuple[CapabilityHashName, ...] = tuple(
    sorted(get_args(CapabilityHashName))
)


def canonical_payload_json(payload: Any) -> str:
    """The canonical serialization every digest in this payload is taken over.

    Fully specified, because the format exists for consumers that are not this
    program and may not be this language:

    * **UTF-8, never escaped.** ``json.dumps`` escapes non-ASCII by default, so
      a Python implementation would hash ``caf\\u00e9`` where a JavaScript one
      hashes ``café`` — the same identity, two digests. ``ensure_ascii=False``,
      and the digest takes the UTF-8 bytes.
    * **Object keys sorted, no insignificant whitespace.** Every object key in
      this payload is ASCII — schema field names, and ``cap_``-prefixed hex
      capability ids — so code-point and UTF-16 code-unit ordering coincide and
      the sort is unambiguous across languages.
    * **Integers only; no floats, no ``NaN``/``Infinity``.** The payload has no
      float-valued field, so the one genuinely language-dependent part of JSON
      canonicalization does not arise. ``allow_nan=False`` keeps it that way.
    * **No ``default=`` fallback.** A digest a consumer verifies must not be
      computable over a lossy ``str()`` rendering: two distinct values whose
      text form coincides would digest identically. A value this cannot
      serialize is a bug in the caller, and raising says so.

    Within those constraints this agrees with RFC 8785 (JCS).
    """

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def payload_digest(payload: Any) -> str:
    """sha256 over the UTF-8 bytes of :func:`canonical_payload_json`, lowercase hex."""

    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


def subject_key(*, agent: str, provider: str, tool_id: str) -> str:
    """Derive the payload-wide row key for one subject.

    Kind-free by construction: a tool and its action share this key, so they
    share a row. Documented in ``docs/capability-payload.md`` so an external
    consumer can recompute it from the fields the payload already carries — and
    :class:`CapabilitySubjectRef` recomputes it too, so a payload cannot carry a
    key that the recipe does not produce.
    """

    digest = payload_digest({"agent": agent, "provider": provider, "tool_id": tool_id})
    return f"{SUBJECT_KEY_PREFIX}{digest[:SUBJECT_KEY_DIGEST_CHARS]}"


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

    key: str = Field(pattern=SUBJECT_KEY_PATTERN)
    name: str
    agent: str
    provider: str
    tool_id: str

    @model_validator(mode="after")
    def _key_is_its_own_identity_material(self) -> CapabilitySubjectRef:
        """The key must be the digest the spec publishes, not an arbitrary tag.

        Uniqueness alone does not make "one subject, one row" true: two rows
        under invented keys carrying the same agent/provider/tool id are one
        logical tool published twice, which is exactly the ``+2`` this schema
        exists to make unrepresentable. Recomputing the key turns the documented
        derivation into an enforced one — an external consumer that follows the
        published recipe reaches the same key, or the payload is rejected.
        """

        expected = subject_key(
            agent=self.agent,
            provider=self.provider,
            tool_id=self.tool_id,
        )
        if self.key != expected:
            raise ValueError(
                f"subject key {self.key!r} does not match its identity: "
                f"agent/provider/tool_id derive {expected!r}"
            )
        return self


class CapabilityEffectFacts(BaseModel):
    """The normalized side-effect facts, as published."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: ActionEffect
    externally_visible: bool
    handles_sensitive_data: bool
    financial: bool
    code_execution: bool
    reversibility: Literal["reversible", "irreversible", "unknown"]
    idempotency_known: bool | None
    high_risk: bool


class CapabilityAuthorityFacts(BaseModel):
    """Who the capability acts as, and how widely."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_type: str | None
    credential_mode: str | None
    source: str | None
    scopes: tuple[str, ...]
    broad_scopes: tuple[str, ...]


class CapabilityControlFacts(BaseModel):
    """Controls already declared on the action surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_required: bool | None
    approval_threshold: str | None
    confirmation_required: bool
    safeguard_idempotency: bool | None
    safeguard_audit_log: bool | None
    safeguard_rollback: bool | None
    safeguard_dry_run: bool | None
    evidence_owner: str | None
    evidence_runbook: str | None
    evidence_approval_ticket: str | None


class CapabilityPermissionFacts(BaseModel):
    """The permission lattice classes, without the audit risk score.

    ``classes`` and ``side_effect_unknown`` are the semantic half of
    ``core.capability_lattice.CapabilityPermissionProfile`` and come from the
    same classifier, so this payload cannot disagree with ``mcp audit``. The
    profile's ``risk_score`` / ``risk_level`` / ``reasons`` are deliberately not
    published — see the exclusions section of ``docs/capability-payload.md``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapabilityPermissionStatus
    classes: tuple[CapabilityPermissionClass, ...]
    side_effect_unknown: bool

    @model_validator(mode="after")
    def _profile_is_one_the_classifier_can_produce(self) -> CapabilityPermissionFacts:
        """Reject tuples the lattice cannot emit.

        A consumer reasons about this block — "is this read-only?" — so a
        combination the classifier never produces is not a harmless oddity, it
        is a claim with no meaning. Every rule below is one the lattice's
        ``_normalize_classes`` already guarantees.
        """

        if self.status == "unavailable":
            if not self.side_effect_unknown:
                raise ValueError(
                    "permission.status='unavailable' cannot claim "
                    "side_effect_unknown=False: an unmeasured profile is "
                    "unknown, not side-effect free"
                )
            if self.classes:
                raise ValueError(
                    "permission.status='unavailable' cannot name classes: "
                    f"got {list(self.classes)}"
                )
            return self
        if not self.classes:
            raise ValueError("permission.status='measured' must name at least one class")
        ordered = sorted(
            set(self.classes),
            key=lambda item: (PERMISSION_CLASS_RANK[item], item),
        )
        if list(self.classes) != ordered:
            raise ValueError(
                "permission.classes must be unique and ordered by (rank, name): "
                f"got {list(self.classes)}, expected {ordered}"
            )
        if "read" in self.classes and len(self.classes) > 1:
            raise ValueError(
                "permission.classes cannot pair 'read' with a side-effecting "
                f"class: got {list(self.classes)}"
            )
        if "destructive" in self.classes and "write" not in self.classes:
            raise ValueError(
                "permission.classes with 'destructive' must also carry 'write'"
            )
        if self.side_effect_unknown and "unknown" not in self.classes:
            raise ValueError(
                "permission.side_effect_unknown=True must carry the 'unknown' "
                f"class: got {list(self.classes)}"
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
    source_id: str | None
    source_ref: str | None
    source_path: str | None
    source_start_line: int | None
    source_end_line: int | None
    source_start_column: int | None
    source_pointer: str | None
    provenance_kind: CapabilityEvidenceProvenanceKind
    confidence: Confidence


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
    subject_kind: CapabilitySubjectKind
    resource: tuple[str, ...]
    scope: tuple[str, ...]
    effect: CapabilityEffectFacts
    authority: CapabilityAuthorityFacts
    controls: CapabilityControlFacts
    permission: CapabilityPermissionFacts
    evidence: CapabilityEvidenceRef
    risk_tags: tuple[str, ...]
    digests: CapabilityDigests


def record_semantic_projection(record: CapabilityRecord) -> dict[str, Any]:
    """One published record with its provenance removed.

    The single definition of "the semantic content of a record". Both the state
    digest and the evidence-only check are taken over it, so they cannot come to
    different conclusions about what counts as semantics. The ``evidence`` block
    goes, and so does ``digests.evidence_hash`` — leaving either in would make
    the semantic digest move when only the file a capability was read from
    moved.
    """

    dumped = record.model_dump(mode="json")
    dumped.pop("evidence", None)
    dumped["digests"] = {
        name: value for name, value in dumped["digests"].items() if name != "evidence_hash"
    }
    return dumped


def records_semantically_equal(before: CapabilityRecord, after: CapabilityRecord) -> bool:
    """Whether two records publish the same semantics, provenance aside."""

    return record_semantic_projection(before) == record_semantic_projection(after)


def changed_record_dimensions(
    before: CapabilityRecord,
    after: CapabilityRecord,
) -> tuple[CapabilityHashName, ...]:
    """Which per-dimension digests differ between two published records."""

    return tuple(
        name
        for name in CAPABILITY_DIGEST_DIMENSIONS
        if getattr(before.digests, name) != getattr(after.digests, name)
    )


def capability_record_sort_key(record: CapabilityRecord) -> tuple[str, str, str]:
    return (record.subject_kind, record.operation, record.capability_id)


class CapabilityStateSubject(BaseModel):
    """One subject and every capability it holds in one state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: CapabilitySubjectRef
    capabilities: tuple[CapabilityRecord, ...] = Field(min_length=1)

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
    both. Everything a consumer would otherwise have to take on trust —
    ``changed_dimensions``, the identity relationship between the two sides, and
    whether the direction is honest — is derived from the records themselves, so
    a contradictory attestation is rejected rather than carried.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transition: CapabilityRecordTransition
    changed_dimensions: tuple[CapabilityHashName, ...]
    semantic_direction: CapabilitySemanticDirection
    semantic_changes: tuple[CapabilitySemanticChange, ...]
    before: CapabilityRecord | None
    after: CapabilityRecord | None

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
        _require_sorted(
            self.semantic_changes,
            capability_semantic_change_sort_key,
            what="semantic_changes",
        )
        if self.transition in {"added", "removed"}:
            if self.changed_dimensions:
                raise ValueError(
                    f"transition {self.transition!r} is a membership change and "
                    "cannot name changed dimensions"
                )
            # A membership change has one honest direction. Anything else is a
            # contradiction a reference parser should refuse, not carry.
            if self.semantic_direction != self.transition:
                raise ValueError(
                    f"transition {self.transition!r} must carry semantic_direction "
                    f"{self.transition!r}, not {self.semantic_direction!r}"
                )
            return self

        before, after = self.before, self.after
        if before is None or after is None:  # pragma: no cover - narrowed above
            raise ValueError(f"transition {self.transition!r} needs both sides")
        if self.semantic_direction in {"added", "removed"}:
            raise ValueError(
                f"transition {self.transition!r} carries both sides and cannot "
                f"claim membership direction {self.semantic_direction!r}"
            )
        # ``changed_dimensions`` is not a free annotation: the record publishes
        # every per-dimension digest, so which ones moved is a fact about the two
        # rows carried. Deriving it stops an attestation from naming a dimension
        # that did not move — or omitting one that did.
        derived = changed_record_dimensions(before, after)
        if not derived:
            raise ValueError(
                f"transition {self.transition!r} carries two identical records; "
                "an unchanged capability is not a delta row"
            )
        if tuple(self.changed_dimensions) != derived:
            raise ValueError(
                "changed_dimensions must be exactly the digests that differ "
                f"between the two records: declared {list(self.changed_dimensions)}, "
                f"records give {list(derived)}"
            )
        same_capability = before.capability_id == after.capability_id
        same_identity = before.digests.identity_hash == after.digests.identity_hash
        if self.transition == "changed":
            if not same_capability or not same_identity:
                raise ValueError(
                    "transition 'changed' is one capability moving: it requires the "
                    "same capability_id and identity_hash on both sides (got "
                    f"{before.capability_id} / {after.capability_id})"
                )
        elif same_capability or same_identity:
            raise ValueError(
                "transition 'reidentified' is an identity moving: it requires "
                "different capability_id and identity_hash on the two sides (got "
                f"{before.capability_id} / {after.capability_id})"
            )
        # Evidence-only means *published* semantics did not move. The fact layer
        # folds the semantic assessment into evidence_hash alone, and this
        # payload publishes a permission block derived from it — so inheriting
        # the fact layer's classification unchanged would label a published
        # permission expansion as provenance-only.
        if self.semantic_direction == "evidence_only" and not records_semantically_equal(
            before, after
        ):
            raise ValueError(
                "semantic_direction 'evidence_only' claims only provenance moved, "
                "but the two published records differ in semantic content"
            )
        return self

    @property
    def record(self) -> CapabilityRecord:
        """The side that describes the capability now — after, or before if gone."""

        current = self.after if self.after is not None else self.before
        if current is None:  # pragma: no cover - _sides_match_transition forbids it
            raise ValueError(f"transition {self.transition!r} carries neither side")
        return current


def capability_transition_sort_key(
    entry: CapabilityRecordTransitionEntry,
) -> tuple[str, str, str]:
    return capability_record_sort_key(entry.record)


class CapabilityDeltaSubject(BaseModel):
    """One subject's whole movement between two states — one subject, one row.

    ``transition`` is recomputed from ``present_in_base`` / ``present_in_head``
    rather than supplied, so it cannot describe a movement the payload's own
    presence flags do not show.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: CapabilitySubjectRef
    #: Whether the subject exists at all on each side. This is what
    #: ``transition`` means, and it cannot be recovered from ``changes`` — a
    #: delta row carries only the capabilities that moved, so a subject that
    #: kept one operation and lost another looks, from its changes alone,
    #: exactly like a subject that went away entirely.
    present_in_base: bool
    present_in_head: bool
    transition: CapabilitySubjectTransition
    changes: tuple[CapabilityRecordTransitionEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rollup_matches_changes(self) -> CapabilityDeltaSubject:
        if not (self.present_in_base or self.present_in_head):
            raise ValueError(
                f"subject {self.subject.key} is present on neither side: a row "
                "exists because a subject does"
            )
        if not self.changes:
            raise ValueError(
                f"subject {self.subject.key} carries no changes: a delta row "
                "exists because something moved"
            )
        # Both sides, not only the surviving one: a reidentified entry names two
        # capability ids, and a payload that published one capability under two
        # rows would be the same defect this row shape exists to prevent.
        _require_unique(
            _entry_capability_ids(self.changes),
            what=f"capability_id under subject {self.subject.key}",
        )
        _require_sorted(self.changes, capability_transition_sort_key, what="changes")
        expected = subject_transition(
            present_in_base=self.present_in_base,
            present_in_head=self.present_in_head,
        )
        if self.transition != expected:
            raise ValueError(
                f"subject {self.subject.key} declares transition {self.transition!r}, "
                f"but present_in_base={self.present_in_base} and present_in_head="
                f"{self.present_in_head} make it {expected!r}"
            )
        # Presence bounds what the changes may say. A subject absent from base
        # cannot carry a capability that changed or went away, and one absent
        # from head cannot carry a capability that arrived.
        for present, side, allowed in (
            (self.present_in_base, "base", "added"),
            (self.present_in_head, "head", "removed"),
        ):
            if present:
                continue
            offending = sorted(
                {entry.transition for entry in self.changes if entry.transition != allowed}
            )
            if offending:
                raise ValueError(
                    f"subject {self.subject.key} is absent from {side} but carries "
                    f"{offending} change(s); only {allowed!r} is possible for a "
                    "subject that side never had"
                )
        return self

    @property
    def change_count(self) -> int:
        return len(self.changes)


def _entry_capability_ids(
    changes: Iterable[CapabilityRecordTransitionEntry],
) -> list[str]:
    """Every capability id a change set names, both sides, de-duplicated per entry.

    A ``changed`` row names the same capability on both sides; a
    ``reidentified`` row names two. Across entries they must all be distinct.
    """

    return [
        capability_id
        for entry in changes
        for capability_id in dict.fromkeys(
            side.capability_id for side in (entry.before, entry.after) if side is not None
        )
    ]


def subject_transition(
    *,
    present_in_base: bool,
    present_in_head: bool,
) -> CapabilitySubjectTransition:
    """How a subject moved, from whether it exists on each side.

    ``added`` and ``removed`` are statements about the subject itself, so they
    are read off its presence and never off the kinds of its changes. A tool
    that keeps one operation and loses another is ``modified``: it is still
    there, and calling that ``removed`` tells a reviewer the agent lost a tool it
    still has.
    """

    if not (present_in_base or present_in_head):
        raise ValueError("a subject present on neither side is not a delta row")
    if not present_in_base:
        return "added"
    if not present_in_head:
        return "removed"
    return "modified"


class CapabilityAnalysisCoverage(BaseModel):
    """Subjects one state knows about but could not analyse.

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

    status: CapabilityAnalysisStatus
    subjects_outside_analysis: tuple[CapabilitySubjectRef, ...]

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

    @classmethod
    def not_requested(cls) -> CapabilityAnalysisCoverage:
        """The default a producer publishes when it did not look.

        Spelled as a constructor rather than a field default so the wire field
        stays required: a consumer must never have to guess whether an absent
        coverage block means "nothing was left out".
        """

        return cls(status="not_requested", subjects_outside_analysis=())


class CapabilityCoverageDelta(BaseModel):
    """How the unanalysed surface moved between two states.

    One coverage snapshot cannot answer #437's question. "A tool was added and
    is unbound" and "a tool has been unbound since before this change" are
    different facts, and a reviewer only needs to act on the first — so a delta
    carries **both** sides and names the transition between them.

    ``newly_outside_analysis`` is the #437 row: subjects outside the analysed
    surface in head that were not outside it in base. It is recomputed from the
    two sides on parse, and both directional lists must be empty unless both
    sides were actually established.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: CapabilityAnalysisCoverage
    head: CapabilityAnalysisCoverage
    status: CapabilityAnalysisStatus
    newly_outside_analysis: tuple[CapabilitySubjectRef, ...]
    no_longer_outside_analysis: tuple[CapabilitySubjectRef, ...]

    @model_validator(mode="after")
    def _directions_follow_from_the_two_sides(self) -> CapabilityCoverageDelta:
        expected_status = coverage_delta_status(self.base.status, self.head.status)
        if self.status != expected_status:
            raise ValueError(
                f"analysis_coverage.status {self.status!r} does not follow from "
                f"base {self.base.status!r} and head {self.head.status!r} "
                f"(expected {expected_status!r})"
            )
        newly, no_longer = coverage_transitions(self.base, self.head)
        if tuple(self.newly_outside_analysis) != newly:
            raise ValueError(
                "newly_outside_analysis must be exactly the head subjects absent "
                "from base coverage"
            )
        if tuple(self.no_longer_outside_analysis) != no_longer:
            raise ValueError(
                "no_longer_outside_analysis must be exactly the base subjects "
                "absent from head coverage"
            )
        return self

    @classmethod
    def not_requested(cls) -> CapabilityCoverageDelta:
        return cls.of(
            CapabilityAnalysisCoverage.not_requested(),
            CapabilityAnalysisCoverage.not_requested(),
        )

    @classmethod
    def of(
        cls,
        base: CapabilityAnalysisCoverage,
        head: CapabilityAnalysisCoverage,
    ) -> CapabilityCoverageDelta:
        """Build the delta from two sides — the one place the directions come from."""

        newly, no_longer = coverage_transitions(base, head)
        return cls(
            base=base,
            head=head,
            status=coverage_delta_status(base.status, head.status),
            newly_outside_analysis=newly,
            no_longer_outside_analysis=no_longer,
        )


def coverage_delta_status(
    base: CapabilityAnalysisStatus,
    head: CapabilityAnalysisStatus,
) -> CapabilityAnalysisStatus:
    """A comparison is only as established as its weaker side.

    ``unavailable`` wins over ``not_requested``: it is the fail-open shape made
    visible — the comparison was asked for and could not run — and a consumer
    must be able to tell that from never having asked.
    """

    if base == "complete" and head == "complete":
        return "complete"
    if "unavailable" in (base, head):
        return "unavailable"
    return "not_requested"


def coverage_transitions(
    base: CapabilityAnalysisCoverage,
    head: CapabilityAnalysisCoverage,
) -> tuple[tuple[CapabilitySubjectRef, ...], tuple[CapabilitySubjectRef, ...]]:
    """``(newly_outside, no_longer_outside)``, or empty when either side is unknown."""

    if coverage_delta_status(base.status, head.status) != "complete":
        return ((), ())
    base_keys = {subject.key for subject in base.subjects_outside_analysis}
    head_keys = {subject.key for subject in head.subjects_outside_analysis}
    newly = tuple(
        sorted(
            (s for s in head.subjects_outside_analysis if s.key not in base_keys),
            key=subject_sort_key,
        )
    )
    no_longer = tuple(
        sorted(
            (s for s in base.subjects_outside_analysis if s.key not in head_keys),
            key=subject_sort_key,
        )
    )
    return (newly, no_longer)


def state_digests(
    subjects: Iterable[CapabilityStateSubject],
    coverage: CapabilityAnalysisCoverage,
) -> tuple[str, str, str]:
    """The three digests of a whole state, from everything that state publishes.

    The recipe lives here, with the frozen format, rather than with the
    projection: it is part of what the schema promises an external consumer can
    recompute, and a state payload validates its own declared digests against
    it. All three are taken over the **published** rows, so nothing of ours has
    to run for a consumer to redo them.

    Three, not one, because they answer three questions a reviewer asks
    separately: did what the agent can do move, did only the place we read it
    from move, and did what we failed to analyse move. Together they cover every
    field the state publishes — a payload whose refs match another's is the same
    published state, with nothing left unbound.
    """

    semantic_rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    for subject in subjects:
        for record in subject.capabilities:
            if record.capability_id in evidence:
                # Keying provenance by capability id silently drops a collision,
                # which would make two states with different provenance digest
                # alike. One capability is one row; say so rather than hash a
                # set that is quietly one entry short.
                raise ValueError(
                    f"duplicate capability_id across subjects: {record.capability_id!r}"
                )
            evidence[record.capability_id] = {
                "evidence": record.evidence.model_dump(mode="json"),
                # The record's own evidence digest belongs here too. It is
                # stripped from the semantic projection, so leaving it out of
                # both would leave a published field bound by nothing.
                "evidence_hash": record.digests.evidence_hash,
            }
        semantic_rows.append(
            {
                "subject": subject.subject.model_dump(mode="json"),
                "capabilities": [
                    record_semantic_projection(record) for record in subject.capabilities
                ],
            }
        )
    return (
        payload_digest(semantic_rows),
        payload_digest(evidence),
        payload_digest(coverage.model_dump(mode="json")),
    )


class CapabilityStateRef(BaseModel):
    """What a payload says about one whole state, including one it does not carry.

    A delta names its two sides here. The digests are computed over the
    *published* content of the full state, so a consumer holding both the state
    payload and the delta payload can prove they describe the same state without
    re-running anything of ours.

    ``ref`` is an opaque caller label — a commit sha, a lock path — and is never
    a timestamp: this payload carries no wall clock, so two exports of the same
    inputs are byte-identical.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_standard_version: str
    subject_count: int = Field(ge=0)
    capability_count: int = Field(ge=0)
    capability_set_digest: str = Field(pattern=PAYLOAD_DIGEST_PATTERN)
    evidence_set_digest: str = Field(pattern=PAYLOAD_DIGEST_PATTERN)
    analysis_coverage_digest: str = Field(pattern=PAYLOAD_DIGEST_PATTERN)
    ref: str | None

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> CapabilityStateRef:
        if self.capability_count < self.subject_count:
            raise ValueError(
                f"capability_count ({self.capability_count}) cannot be fewer than "
                f"subject_count ({self.subject_count}): every subject holds at "
                "least one capability"
            )
        return self


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

    subjects: int = Field(ge=0)
    added_subjects: int = Field(ge=0)
    removed_subjects: int = Field(ge=0)
    modified_subjects: int = Field(ge=0)
    capability_changes: int = Field(ge=0)

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

    capability_payload_schema_version: Literal["shipgate.capability_payload/v1"]
    capability_standard_version: str


class CapabilityStatePayloadV1(_CapabilityPayloadBase):
    """What the agent can do, at one state. The committed-state view (#474)."""

    view: Literal["state"]
    state: CapabilityStateRef
    analysis_coverage: CapabilityAnalysisCoverage
    subjects: tuple[CapabilityStateSubject, ...]

    @model_validator(mode="after")
    def _subjects_are_unique_sorted_and_counted(self) -> CapabilityStatePayloadV1:
        _require_unique(
            [entry.subject.key for entry in self.subjects],
            what="subject key",
        )
        _require_sorted(self.subjects, _state_subject_sort_key, what="subjects")
        if self.state.capability_standard_version != self.capability_standard_version:
            raise ValueError(
                "state.capability_standard_version must equal the payload's "
                "capability_standard_version"
            )
        if self.state.subject_count != len(self.subjects):
            raise ValueError(
                f"state.subject_count ({self.state.subject_count}) does not match "
                f"the {len(self.subjects)} subject row(s) carried"
            )
        capabilities = sum(len(entry.capabilities) for entry in self.subjects)
        if self.state.capability_count != capabilities:
            raise ValueError(
                f"state.capability_count ({self.state.capability_count}) does not "
                f"match the {capabilities} capability record(s) carried"
            )
        # A state carries everything its digests are taken over, so it can check
        # them — and must, because the spec tells consumers the digests are
        # recomputable from the payload alone. (A delta's base/head refs describe
        # states it does not carry, so those stay on trust.)
        declared = (
            self.state.capability_set_digest,
            self.state.evidence_set_digest,
            self.state.analysis_coverage_digest,
        )
        computed = state_digests(self.subjects, self.analysis_coverage)
        if declared != computed:
            raise ValueError(
                "state digests do not describe what this payload carries: declared "
                f"{[value[:12] + '…' for value in declared]}, payload gives "
                f"{[value[:12] + '…' for value in computed]}"
            )
        return self


class CapabilityDeltaPayloadV1(_CapabilityPayloadBase):
    """What changed between two states. The exported-attestation view (#470)."""

    view: Literal["delta"]
    base: CapabilityStateRef
    head: CapabilityStateRef
    analysis_coverage: CapabilityCoverageDelta
    summary: CapabilityDeltaSummary
    subjects: tuple[CapabilityDeltaSubject, ...]

    @model_validator(mode="after")
    def _summary_matches_subjects(self) -> CapabilityDeltaPayloadV1:
        _require_unique(
            [entry.subject.key for entry in self.subjects],
            what="subject key",
        )
        _require_sorted(self.subjects, _delta_subject_sort_key, what="subjects")
        _require_unique(
            [
                capability_id
                for entry in self.subjects
                for capability_id in _entry_capability_ids(entry.changes)
            ],
            what="capability_id across subjects",
        )
        expected = delta_summary(self.subjects)
        if self.summary != expected:
            raise ValueError(
                "CapabilityDeltaPayloadV1.summary does not describe its rows: "
                f"declared {self.summary.model_dump()}, rows give {expected.model_dump()}"
            )
        for side, ref in (("base", self.base), ("head", self.head)):
            if ref.capability_standard_version != self.capability_standard_version:
                raise ValueError(
                    f"{side}.capability_standard_version must equal the payload's "
                    "capability_standard_version"
                )
        for side, ref, coverage in (
            ("base", self.base, self.analysis_coverage.base),
            ("head", self.head, self.analysis_coverage.head),
        ):
            digest = payload_digest(coverage.model_dump(mode="json"))
            if ref.analysis_coverage_digest != digest:
                raise ValueError(
                    f"{side}.analysis_coverage_digest does not describe the "
                    f"{side} coverage this delta carries"
                )
        # An empty delta is a claim that the two states are the same state, so it
        # has to be one the payload's own digests support. No rows means every
        # fact matched on every dimension, which makes both published digests
        # equal by construction — so only a hand-written or tampered payload can
        # say "nothing changed" while naming two different states, and a consumer
        # must not have to notice that itself.
        if not self.subjects and (
            self.base.capability_set_digest != self.head.capability_set_digest
            or self.base.evidence_set_digest != self.head.evidence_set_digest
        ):
            raise ValueError(
                "a delta with no subject rows claims base and head are the same "
                f"state, but their digests differ (capability "
                f"{self.base.capability_set_digest[:12]}… vs "
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
    "CAPABILITY_DIGEST_DIMENSIONS",
    "CAPABILITY_PAYLOAD_SCHEMA_PATH",
    "CAPABILITY_PAYLOAD_SCHEMA_VERSION",
    "CAPABILITY_PAYLOAD_SPEC_PATH",
    "PAYLOAD_DIGEST_PATTERN",
    "PERMISSION_CLASS_RANK",
    "SUBJECT_KEY_DIGEST_CHARS",
    "SUBJECT_KEY_PATTERN",
    "SUBJECT_KEY_PREFIX",
    "CapabilityAnalysisCoverage",
    "CapabilityAnalysisStatus",
    "CapabilityAuthorityFacts",
    "CapabilityControlFacts",
    "CapabilityCoverageDelta",
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
    "changed_record_dimensions",
    "coverage_delta_status",
    "coverage_transitions",
    "delta_summary",
    "payload_digest",
    "record_semantic_projection",
    "records_semantically_equal",
    "state_digests",
    "subject_key",
    "subject_sort_key",
    "subject_transition",
]
