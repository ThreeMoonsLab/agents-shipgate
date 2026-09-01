"""``shipgate.capability_delta_attestation/v1`` — the delta as an in-toto predicate.

``verify`` already computes the capability delta and binds it to a receipt. Its
only consumers were our own renderers. This module publishes the same delta as a
**standalone attestation**: an
[in-toto](https://github.com/in-toto/attestation) Statement whose predicate
carries the frozen ``shipgate.capability_payload/v1`` delta verbatim, so a
runtime gateway, a policy engine, a dashboard, or another CI system can read
*"what can the agent do after this change"* without running Agents Shipgate
([#470](https://github.com/ThreeMoonsLab/agents-shipgate/issues/470)).

**There is no second payload shape.** ``predicate.delta`` is
:class:`~agents_shipgate.schemas.capability_payload.CapabilityDeltaPayloadV1`,
unchanged and unwrapped, and the delta an attestation carries is the delta a
reviewer reads, because both come from
:mod:`agents_shipgate.core.capability_payload`. This module adds only the
envelope: which artifact the delta is *about*, and which verification run
produced it.

Three rules carry the envelope.

**The subject is the reviewed tree, and the payload says so too.** The statement
names exactly one subject, whose digest set carries the ``gitTree`` of the
evaluated head. ``predicate.delta.head.ref`` must equal that same tree id, and
``predicate.delta.base.ref`` must be the base tree id. Without the join, a valid
attestation for one commit could be relabelled as another by editing four
characters of the subject; with it, the subject and the payload have to agree or
the file is rejected. ``ref`` is documented as an opaque caller label in the
payload spec — this surface narrows it to a git object id and enforces that.

**A receipt binding is a value, never an omission.** ``predicate.verification``
carries ``status`` with ``bound`` or ``unbound``, following the same fail-closed
shape ``analysis_coverage`` uses: only ``bound`` may name identities, and a
consumer that requires the chain back into the verification receipt checks
``status`` rather than probing for absent fields. ``verify`` always emits
``bound``; a delta projected outside a verification run — the worked example
generated from a shipped sample — is honestly ``unbound``.

**It gates nothing.** ``release_decision.decision`` remains the only release
gate. This attestation carries no verdict, no severity and no release impact,
for the reason the payload spec gives: publishing a per-subject impact in an
interchange format invites a consumer to gate on it, which is a second verdict
by another name.

Signing is deliberately out of scope for ``v1``. The statement is emitted
unsigned, as a DSSE payload would be *before* it is wrapped; a producer that
wants authenticity wraps these bytes in a DSSE envelope of its own. What the
format guarantees on its own is *self-consistency*: every count, transition,
derived direction and state digest in the payload is recomputable from the
payload, and the subject is bound to it. See ``docs/capability-delta-attestation.md``.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from agents_shipgate.schemas.capability_payload import (
    CapabilityDeltaPayloadV1,
    canonical_payload_json,
)

#: The in-toto Statement type this envelope is. Fixed by the in-toto
#: Attestation Framework, not by us.
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"

#: The predicate type URI. It is the wire identity of this format: a consumer
#: switches on it and nothing else, so it moves only when the predicate changes
#: incompatibly — at which point it becomes ``…/capability-delta/v2``.
CAPABILITY_DELTA_PREDICATE_TYPE = "https://threemoonslab.com/agents-shipgate/capability-delta/v1"

#: The version of the *predicate body*, carried inside the predicate so a
#: payload separated from its statement still says what it is.
CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION = "shipgate.capability_delta_attestation/v1"
CAPABILITY_DELTA_ATTESTATION_SCHEMA_PATH = "docs/capability-delta-attestation-schema.v1.json"
CAPABILITY_DELTA_ATTESTATION_SPEC_PATH = "docs/capability-delta-attestation.md"

#: The artifact ``verify`` writes beside ``verifier.json``.
CAPABILITY_DELTA_ATTESTATION_FILENAME = "capability-delta-attestation.json"
#: The key this artifact is published under in ``verifier.artifacts`` and in
#: the verification artifact manifest. One constant, because the emitter, the
#: authorization execution closure and the contract all name it and a typo in
#: any one of them is a silent hole rather than an error.
CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY = "capability_delta_attestation_json"

#: A git object id, in either the SHA-1 or the SHA-256 object format. The same
#: pattern the verification plan pins its tree and commit ids to, restated here
#: because this schema is published for consumers that never read that one.
GIT_OBJECT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
#: A verification identity, as ``verification-receipt.json`` spells it.
CONTENT_ID_PATTERN = r"^sha256:[0-9a-f]{64}$"

StrictText = Annotated[str, Field(strict=True)]
GitObjectId = Annotated[str, Field(strict=True, pattern=GIT_OBJECT_PATTERN)]
ContentId = Annotated[str, Field(strict=True, pattern=CONTENT_ID_PATTERN)]

#: Whether this attestation is chained to a verification receipt. Only ``bound``
#: may name identities — the fail-closed shape ``analysis_coverage`` uses, for
#: the same reason: "no chain" must not be writable as "a chain we did not
#: publish".
CapabilityDeltaBindingStatus = Literal["bound", "unbound"]


class CapabilityDeltaSubjectDigest(BaseModel):
    """The in-toto ``DigestSet`` of the reviewed tree.

    ``gitTree`` is required and is the identity: two commits with the same tree
    reviewed the same content, and it is the one id a consumer can recompute
    from a checkout without knowing the history. ``gitCommit`` is the reviewed
    commit when the producer had one — always, for ``verify`` — and is context,
    not identity.

    **This is the one object here that is not ours**, and it follows in-toto's
    rules rather than the payload's. in-toto types a ``DigestSet`` as
    ``map<string, string>``, so an algorithm the producer did not compute is
    *absent*, never ``null``: publishing ``"gitCommit": null`` would put a
    non-string in a map an in-toto consumer types as strings, and the flagship
    reason for this format is that such a consumer can read it. Everywhere else
    in this schema absence is spelled as a value; here it is spelled as
    absence, because the type is someone else's.

    Closed rather than the open algorithm map in-toto allows, because a
    consumer that cannot account for an algorithm cannot verify the subject,
    and this schema is frozen the way its payload is. A further algorithm is a
    ``/v2``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gitTree: GitObjectId  # noqa: N815 - the in-toto DigestSet algorithm name.
    gitCommit: GitObjectId | None = None  # noqa: N815 - ditto.

    @model_serializer(mode="plain")
    def _as_digest_set(self) -> dict[str, str]:
        """Emit a ``map<string, string>``, dropping what was not computed."""

        digests = {"gitTree": self.gitTree}
        if self.gitCommit is not None:
            digests["gitCommit"] = self.gitCommit
        return digests


class CapabilityDeltaAttestationSubject(BaseModel):
    """One in-toto ``ResourceDescriptor``: what this attestation is about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A stable, credential-free repository locator — the same value the
    #: verification plan calls ``repository_id``. It is a label: identity is the
    #: digest.
    name: StrictText
    digest: CapabilityDeltaSubjectDigest

    @model_validator(mode="after")
    def _name_is_present(self) -> CapabilityDeltaAttestationSubject:
        if not self.name.strip():
            raise ValueError(
                "attestation subject name must not be blank: a consumer that "
                "collects attestations from several repositories has nothing "
                "else to group them by"
            )
        return self


class CapabilityDeltaVerificationRef(BaseModel):
    """The chain back into the verification receipt that produced this delta.

    Two identities and no more. ``input_set_id`` is *what was reviewed* — the
    content address of the normalized input set — and ``subject_id`` is the
    resolved git subject. Both are properties of the inputs, so two runs of one
    review on two machines publish the same values.

    ``request_id``, ``engine_requirement_id`` and ``decision_id`` are
    deliberately **not** published. They mix in the engine build, the Python
    version and the platform, so an interchange format carrying them would emit
    different bytes for an identical review and would leak the builder's
    machine. They stay in ``verification-receipt.json``, which ``input_set_id``
    is the join key into.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapabilityDeltaBindingStatus
    input_set_id: ContentId | None
    subject_id: ContentId | None

    @model_validator(mode="after")
    def _identities_require_a_binding(self) -> CapabilityDeltaVerificationRef:
        named = [self.input_set_id, self.subject_id]
        if self.status == "bound" and any(value is None for value in named):
            raise ValueError(
                "verification.status='bound' must carry every identity: a "
                "partial chain is one a consumer cannot follow"
            )
        if self.status == "unbound" and any(value is not None for value in named):
            raise ValueError(
                "verification.status='unbound' cannot name identities: naming "
                "them is what 'bound' means"
            )
        return self

    @classmethod
    def unbound(cls) -> CapabilityDeltaVerificationRef:
        """The value a producer outside a verification run publishes.

        Spelled as a constructor rather than a field default so the wire fields
        stay required: a consumer must never have to decide what an absent
        binding block would have meant.
        """

        return cls(status="unbound", input_set_id=None, subject_id=None)


class CapabilityDeltaPredicateV1(BaseModel):
    """The predicate body: the frozen delta payload, plus who produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predicate_schema_version: Literal["shipgate.capability_delta_attestation/v1"]
    #: Restated inside the predicate because the delta and the envelope version
    #: independently: a consumer pinned to the payload schema reads this rather
    #: than reaching into ``delta``.
    capability_payload_schema_version: Literal["shipgate.capability_payload/v1"]
    delta: CapabilityDeltaPayloadV1
    verification: CapabilityDeltaVerificationRef

    @model_validator(mode="after")
    def _delta_is_anchored_to_git_trees(self) -> CapabilityDeltaPredicateV1:
        if self.delta.capability_payload_schema_version != (
            self.capability_payload_schema_version
        ):
            raise ValueError(
                "predicate.capability_payload_schema_version must equal the "
                "version the carried delta declares"
            )
        for side, ref in (("base", self.delta.base.ref), ("head", self.delta.head.ref)):
            if ref is None or not _is_git_object_id(ref):
                raise ValueError(
                    f"delta.{side}.ref must be the git tree object id of the "
                    f"{side} state; the payload allows any opaque label, this "
                    f"surface does not (got {ref!r})"
                )
        if self.delta.base.ref == self.delta.head.ref and self.delta.subjects:
            # Two refs naming one tree is legitimate — a branch that reverts
            # itself reviews the content it started from — but a delta over one
            # tree cannot have moved anything. Rejecting the combination stops
            # a populated delta from being relabelled onto a tree it does not
            # describe by copying one ref over the other.
            raise ValueError(
                "delta.base.ref and delta.head.ref name the same tree, so the "
                f"delta must be empty; it carries {len(self.delta.subjects)} "
                "changed subject(s)"
            )
        return self


class CapabilityDeltaAttestationV1(BaseModel):
    """An in-toto Statement carrying one capability delta.

    Field order on the wire is the in-toto order; the canonical serialization
    sorts keys anyway, so nothing depends on it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    statement_type: Literal["https://in-toto.io/Statement/v1"] = Field(alias="_type")
    subject: tuple[CapabilityDeltaAttestationSubject, ...]
    predicate_type: Literal["https://threemoonslab.com/agents-shipgate/capability-delta/v1"] = (
        Field(alias="predicateType")
    )
    predicate: CapabilityDeltaPredicateV1

    @model_validator(mode="after")
    def _one_subject_bound_to_the_payload(self) -> CapabilityDeltaAttestationV1:
        if len(self.subject) != 1:
            raise ValueError(
                "a capability-delta attestation names exactly one subject — the "
                f"reviewed tree — and this one names {len(self.subject)}"
            )
        head_ref = self.predicate.delta.head.ref
        if self.subject[0].digest.gitTree != head_ref:
            raise ValueError(
                "the attested subject is not the state the delta describes: "
                f"subject gitTree {self.subject[0].digest.gitTree!r} vs "
                f"delta.head.ref {head_ref!r}"
            )
        return self


def _is_git_object_id(value: str) -> bool:
    return re.fullmatch(GIT_OBJECT_PATTERN, value) is not None


def attestation_json(attestation: CapabilityDeltaAttestationV1) -> dict[str, object]:
    """The wire object, with in-toto's own field names."""

    return attestation.model_dump(mode="json", by_alias=True)


def render_attestation_json(attestation: CapabilityDeltaAttestationV1) -> str:
    """The bytes ``verify`` writes, and the bytes a digest is taken over.

    The same canonicalization the payload's own digests use (RFC 8785-compatible
    within this schema's constraints), so an external consumer that recomputes
    anything about this file reads exactly the bytes it was given.
    """

    return canonical_payload_json(attestation_json(attestation)) + "\n"


__all__ = [
    "CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY",
    "CAPABILITY_DELTA_ATTESTATION_FILENAME",
    "CAPABILITY_DELTA_ATTESTATION_SCHEMA_PATH",
    "CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION",
    "CAPABILITY_DELTA_ATTESTATION_SPEC_PATH",
    "CAPABILITY_DELTA_PREDICATE_TYPE",
    "CONTENT_ID_PATTERN",
    "GIT_OBJECT_PATTERN",
    "IN_TOTO_STATEMENT_TYPE",
    "CapabilityDeltaAttestationSubject",
    "CapabilityDeltaAttestationV1",
    "CapabilityDeltaBindingStatus",
    "CapabilityDeltaPredicateV1",
    "CapabilityDeltaSubjectDigest",
    "CapabilityDeltaVerificationRef",
    "attestation_json",
    "render_attestation_json",
]
