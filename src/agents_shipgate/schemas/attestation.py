from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

ATTESTATION_SCHEMA_VERSION = "0.2"


class AttestationVerdictV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge_verdict: str | None = None
    decision: str | None = None
    applicability: str | None = None
    can_merge_without_human: bool = False


class AttestationCapabilitySummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = Field(default=0, ge=0)
    modified: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    trust_root_touched: bool = False
    policy_weakened: bool = False
    change_ids: list[str] = Field(default_factory=list)


class AttestationHumanAckV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = False
    satisfied: bool | None = None
    outstanding: list[str] = Field(default_factory=list)


class AttestationCapabilityLockBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    sha256: str | None = None
    capability_lock_schema_version: str | None = None
    semantic_capability_set_hash: str | None = None
    evidence_set_hash: str | None = None
    source_set_hash: str | None = None
    capability_count: int | None = Field(default=None, ge=0)


class AttestationCapabilityDiffSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    reidentified: int = Field(default=0, ge=0)
    changed: int = Field(default=0, ge=0)
    evidence_changed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)


class AttestationCapabilityDiffBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    sha256: str | None = None
    capability_lock_diff_schema_version: str | None = None
    base_semantic_capability_set_hash: str | None = None
    head_semantic_capability_set_hash: str | None = None
    summary: AttestationCapabilityDiffSummaryV1 | None = None


class ReleaseAttestationV1(BaseModel):
    """Deterministic local release attestation emitted by ``agents-shipgate attest``."""

    model_config = ConfigDict(extra="forbid")

    attestation_schema_version: Literal["0.2"] = ATTESTATION_SCHEMA_VERSION
    cli_version: str
    source_verifier: str
    redacted: bool = True
    base_ref: str | None = None
    head_ref: str | None = None
    base_tree_sha: str | None = None
    head_tree_sha: str | None = None
    mode: str | None = None
    verdict: AttestationVerdictV1
    capability: AttestationCapabilitySummaryV1
    capability_lock: AttestationCapabilityLockBindingV1
    capability_diff: AttestationCapabilityDiffBindingV1 | None = None
    human_ack: AttestationHumanAckV1
    policy_snapshot_sha256: str | None = None
    artifact_sha256: dict[str, str] = Field(default_factory=dict)


class ReleaseAttestationArtifactV1(RootModel[ReleaseAttestationV1]):
    root: ReleaseAttestationV1


__all__ = [
    "ATTESTATION_SCHEMA_VERSION",
    "AttestationCapabilityDiffBindingV1",
    "AttestationCapabilityDiffSummaryV1",
    "AttestationCapabilityLockBindingV1",
    "AttestationCapabilitySummaryV1",
    "AttestationHumanAckV1",
    "AttestationVerdictV1",
    "ReleaseAttestationArtifactV1",
    "ReleaseAttestationV1",
]
