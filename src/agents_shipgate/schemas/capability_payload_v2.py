"""``shipgate.capability_payload/v2`` — v1 plus each tool's published definition.

Capability standard ``0.6`` added a ``definition`` block to every capability
fact: digests of the tool's description, output schema and annotations, which is
what an MCP client hands the model and what a tool-poisoning change rewrites
while the tool keeps its name and arguments. ``v1`` is closed
(``docs/capability-payload.md`` § *Evolution policy*), so carrying them is this
new version; ``v1`` stays published and frozen, and producers emit both for a
cycle.

**What v2 changes, and nothing else.** Each record's ``digests`` gains
``description_hash``, ``output_schema_hash`` and ``annotations_hash`` — the
same digests the capability lock records, under the lock diff's part names.
They are ``null`` together when the fact carries no definition (a fact built
without its tool record): null means "not stated by the evidence", never
"empty". ``changed_dimensions`` may name them, and because they are not
provenance they sit inside ``capability_set_digest``, so two states that differ
only in a tool's description no longer share a ref. Every other model, rule,
canonicalization and digest recipe is ``v1``'s, inherited rather than copied, so
the two versions cannot drift on what they share.

A definition-only change therefore appears as a ``changed`` row whose
``changed_dimensions`` names the moved digests and whose direction is
``unknown`` with no explanations: the payload can see that the text moved, not
which way the capability did. That is the rule ``v1`` already states for a
change that moves only an opaque digest.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.capability_payload import (
    CapabilityDeltaPayloadV1,
    CapabilityDeltaSubject,
    CapabilityDigests,
    CapabilityRecord,
    CapabilityRecordTransitionEntry,
    CapabilityStatePayloadV1,
    CapabilityStateSubject,
    DigestToken,
)

CAPABILITY_PAYLOAD_V2_SCHEMA_VERSION = "shipgate.capability_payload/v2"
CAPABILITY_PAYLOAD_V2_SCHEMA_PATH = "docs/capability-payload-schema.v2.json"

#: The per-record digests v2 publishes: v1's eight plus the three definition
#: parts. Named after the lock diff's ``parts`` (``description``,
#: ``output_schema``, ``annotations``), so a consumer reading both artifacts
#: sees one vocabulary.
CapabilityDigestNameV2 = Literal[
    "annotations_hash",
    "authority_hash",
    "binding_hash",
    "control_hash",
    "description_hash",
    "effect_hash",
    "evidence_hash",
    "identity_hash",
    "output_schema_hash",
    "risk_hash",
    "schema_hash",
]
CAPABILITY_DIGEST_DIMENSIONS_V2: tuple[CapabilityDigestNameV2, ...] = tuple(
    sorted(get_args(CapabilityDigestNameV2))
)
DEFINITION_DIGEST_DIMENSIONS: tuple[CapabilityDigestNameV2, ...] = (
    "annotations_hash",
    "description_hash",
    "output_schema_hash",
)


class CapabilityDigestsV2(CapabilityDigests):
    """v1's digests plus the three parts of the tool's published definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description_hash: DigestToken | None
    output_schema_hash: DigestToken | None
    annotations_hash: DigestToken | None

    @model_validator(mode="after")
    def _definition_is_stated_whole_or_not_at_all(self) -> CapabilityDigestsV2:
        present = [getattr(self, name) is not None for name in DEFINITION_DIGEST_DIMENSIONS]
        if any(present) and not all(present):
            raise ValueError(
                "description_hash, output_schema_hash and annotations_hash are "
                "null together: a definition is either stated by the evidence or "
                "it is not"
            )
        return self


class CapabilityRecordV2(CapabilityRecord):
    model_config = ConfigDict(frozen=True, extra="forbid")

    digests: CapabilityDigestsV2


class CapabilityStateSubjectV2(CapabilityStateSubject):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: tuple[CapabilityRecordV2, ...] = Field(min_length=1)


class CapabilityRecordTransitionEntryV2(CapabilityRecordTransitionEntry):
    model_config = ConfigDict(frozen=True, extra="forbid")

    changed_dimensions: tuple[CapabilityDigestNameV2, ...]  # type: ignore[assignment]
    before: CapabilityRecordV2 | None
    after: CapabilityRecordV2 | None


class CapabilityDeltaSubjectV2(CapabilityDeltaSubject):
    model_config = ConfigDict(frozen=True, extra="forbid")

    changes: tuple[CapabilityRecordTransitionEntryV2, ...] = Field(min_length=1)


class CapabilityStatePayloadV2(CapabilityStatePayloadV1):
    """What the agent can do at one state, definitions included."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_payload_schema_version: Literal["shipgate.capability_payload/v2"]  # type: ignore[assignment]
    subjects: tuple[CapabilityStateSubjectV2, ...]


class CapabilityDeltaPayloadV2(CapabilityDeltaPayloadV1):
    """What changed between two states, definition changes included."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_payload_schema_version: Literal["shipgate.capability_payload/v2"]  # type: ignore[assignment]
    subjects: tuple[CapabilityDeltaSubjectV2, ...]


CapabilityPayloadV2Root = Annotated[
    CapabilityStatePayloadV2 | CapabilityDeltaPayloadV2,
    Field(discriminator="view"),
]


class CapabilityPayloadV2(RootModel[CapabilityPayloadV2Root]):
    """Either view of the v2 payload, discriminated on ``view``."""

    root: CapabilityPayloadV2Root


class CapabilityStatePayloadArtifactV2(RootModel[CapabilityStatePayloadV2]):
    root: CapabilityStatePayloadV2


class CapabilityDeltaPayloadArtifactV2(RootModel[CapabilityDeltaPayloadV2]):
    root: CapabilityDeltaPayloadV2


__all__ = [
    "CAPABILITY_DIGEST_DIMENSIONS_V2",
    "CAPABILITY_PAYLOAD_V2_SCHEMA_PATH",
    "CAPABILITY_PAYLOAD_V2_SCHEMA_VERSION",
    "DEFINITION_DIGEST_DIMENSIONS",
    "CapabilityDeltaPayloadArtifactV2",
    "CapabilityDeltaPayloadV2",
    "CapabilityDeltaSubjectV2",
    "CapabilityDigestNameV2",
    "CapabilityDigestsV2",
    "CapabilityPayloadV2",
    "CapabilityPayloadV2Root",
    "CapabilityRecordTransitionEntryV2",
    "CapabilityRecordV2",
    "CapabilityStatePayloadArtifactV2",
    "CapabilityStatePayloadV2",
    "CapabilityStateSubjectV2",
]
