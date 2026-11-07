"""``shipgate.capability_delta_attestation/v2`` — the v2 delta as an in-toto predicate.

The same envelope as ``v1`` — one subject, the reviewed tree, bound to
``delta.head.ref``; a receipt binding spelled as a value; no verdict — carrying
``shipgate.capability_payload/v2``, whose records publish each tool's
definition digests. Predicate type
``https://threemoonslab.com/agents-shipgate/capability-delta/v2``.

``v1`` stays published and is still emitted: ``verify`` writes
``capability-delta-attestation.json`` (``v1``) and
``capability-delta-attestation.v2.json`` (this) from one projection, so a
consumer pinned to ``v1`` keeps working while one that needs to see a rewritten
tool description reads ``v2``. Every envelope rule is ``v1``'s, inherited.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from agents_shipgate.schemas.capability_attestation import (
    CapabilityDeltaAttestationV1,
    CapabilityDeltaPredicateV1,
)
from agents_shipgate.schemas.capability_payload_v2 import CapabilityDeltaPayloadV2

CAPABILITY_DELTA_PREDICATE_TYPE_V2 = (
    "https://threemoonslab.com/agents-shipgate/capability-delta/v2"
)
CAPABILITY_DELTA_ATTESTATION_V2_SCHEMA_VERSION = "shipgate.capability_delta_attestation/v2"
CAPABILITY_DELTA_ATTESTATION_V2_SCHEMA_PATH = (
    "docs/capability-delta-attestation-schema.v2.json"
)
CAPABILITY_DELTA_ATTESTATION_V2_FILENAME = "capability-delta-attestation.v2.json"
CAPABILITY_DELTA_ATTESTATION_V2_ARTIFACT_KEY = "capability_delta_attestation_v2_json"


class CapabilityDeltaPredicateV2(CapabilityDeltaPredicateV1):
    """The v2 predicate body: the v2 delta payload, plus who produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predicate_schema_version: Literal["shipgate.capability_delta_attestation/v2"]  # type: ignore[assignment]
    capability_payload_schema_version: Literal["shipgate.capability_payload/v2"]  # type: ignore[assignment]
    delta: CapabilityDeltaPayloadV2


class CapabilityDeltaAttestationV2(CapabilityDeltaAttestationV1):
    """An in-toto Statement carrying one v2 capability delta."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    predicate_type: Literal[  # type: ignore[assignment]
        "https://threemoonslab.com/agents-shipgate/capability-delta/v2"
    ] = Field(alias="predicateType")
    predicate: CapabilityDeltaPredicateV2


__all__ = [
    "CAPABILITY_DELTA_ATTESTATION_V2_ARTIFACT_KEY",
    "CAPABILITY_DELTA_ATTESTATION_V2_FILENAME",
    "CAPABILITY_DELTA_ATTESTATION_V2_SCHEMA_PATH",
    "CAPABILITY_DELTA_ATTESTATION_V2_SCHEMA_VERSION",
    "CAPABILITY_DELTA_PREDICATE_TYPE_V2",
    "CapabilityDeltaAttestationV2",
    "CapabilityDeltaPredicateV2",
]
