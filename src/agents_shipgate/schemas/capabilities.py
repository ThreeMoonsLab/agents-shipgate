from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.capability_change import CapabilitySubjectKind
from agents_shipgate.schemas.common import Confidence, ProvenanceKind
from agents_shipgate.schemas.surfaces import ActionEffect

CAPABILITY_LOCK_SCHEMA_VERSION = "0.1"
CAPABILITY_LOCK_DIFF_SCHEMA_VERSION = "0.1"

CapabilityHashName = Literal[
    "identity_hash",
    "effect_hash",
    "authority_hash",
    "control_hash",
    "schema_hash",
    "risk_hash",
    "evidence_hash",
]


class CapabilityIdentity(BaseModel):
    """Stable semantic identity for an agent capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    tool_id: str
    tool_name: str
    provider: str
    operation: str
    subject_kind: CapabilitySubjectKind = "action"
    resource: tuple[str, ...] = Field(default_factory=tuple)
    scope: tuple[str, ...] = Field(default_factory=tuple)


class CapabilityEffect(BaseModel):
    """Normalized side-effect view derived from ``SideEffect``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: ActionEffect
    externally_visible: bool = False
    handles_sensitive_data: bool = False
    financial: bool = False
    code_execution: bool = False
    reversibility: Literal["reversible", "irreversible", "unknown"] = "unknown"
    idempotency_known: bool | None = None
    high_risk: bool = False


class CapabilityAuthority(BaseModel):
    """Authority source and scope facts for a capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_type: str | None = None
    credential_mode: str | None = None
    source: str | None = None
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    broad_scopes: tuple[str, ...] = Field(default_factory=tuple)


class CapabilityControls(BaseModel):
    """Policy and safeguard controls already known to the action surface."""

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


class CapabilityEvidence(BaseModel):
    """Structured source provenance for a capability fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_location: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    provenance_kind: ProvenanceKind = "static_declaration"
    confidence: Confidence = "medium"


class CapabilityHashes(BaseModel):
    """Separate hashes for the future lock/diff substrate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_hash: str
    effect_hash: str
    authority_hash: str
    control_hash: str
    schema_hash: str
    risk_hash: str
    evidence_hash: str


class CapabilityFactV1(BaseModel):
    """Durable capability fact used by experimental locks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    identity: CapabilityIdentity
    effect: CapabilityEffect
    authority: CapabilityAuthority
    controls: CapabilityControls
    evidence: CapabilityEvidence
    risk_tags: tuple[str, ...] = Field(default_factory=tuple)
    hashes: CapabilityHashes


def capability_fact_sort_key(
    fact: CapabilityFactV1,
) -> tuple[str, str, str, str, str, str]:
    return (
        fact.identity.agent_id,
        fact.identity.provider,
        fact.identity.operation,
        fact.identity.tool_name,
        "\n".join(fact.identity.scope),
        fact.id,
    )


class CapabilityLockSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_path: str
    manifest_dir: str
    project_name: str | None = None
    agent_id: str
    agent_name: str
    environment_target: str | None = None
    tool_count: int = 0
    toolkit_bound_count: int = 0
    source_count: int = 0
    source_warning_count: int = 0
    plugins_enabled: bool = True


class CapabilityLockSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_count: int = 0
    high_risk_count: int = 0
    broad_scope_count: int = 0
    write_count: int = 0
    external_communication_count: int = 0
    financial_count: int = 0
    code_execution_count: int = 0


class CapabilityLockHashes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_capability_set_hash: str
    evidence_set_hash: str
    source_set_hash: str


class CapabilityLockFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_lock_schema_version: Literal["0.1"] = CAPABILITY_LOCK_SCHEMA_VERSION
    experimental: Literal[True] = True
    cli_version: str
    source: CapabilityLockSource
    summary: CapabilityLockSummary
    hashes: CapabilityLockHashes
    capabilities: list[CapabilityFactV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_capabilities(self) -> CapabilityLockFileV1:
        self.capabilities.sort(key=capability_fact_sort_key)
        return self


class CapabilityLockRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    capability_lock_schema_version: str
    semantic_capability_set_hash: str
    evidence_set_hash: str
    source_set_hash: str
    capability_count: int


class CapabilityLockDiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = 0
    removed: int = 0
    reidentified: int = 0
    changed: int = 0
    evidence_changed: int = 0
    unchanged: int = 0


class CapabilityLockChangedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    operation: str
    changed_hashes: tuple[CapabilityHashName, ...] = Field(default_factory=tuple)
    before: CapabilityFactV1
    after: CapabilityFactV1


class CapabilityLockDiffV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_lock_diff_schema_version: Literal["0.1"] = (
        CAPABILITY_LOCK_DIFF_SCHEMA_VERSION
    )
    experimental: Literal[True] = True
    base: CapabilityLockRef
    head: CapabilityLockRef
    summary: CapabilityLockDiffSummary
    added: list[CapabilityFactV1] = Field(default_factory=list)
    removed: list[CapabilityFactV1] = Field(default_factory=list)
    reidentified: list[CapabilityLockChangedFact] = Field(default_factory=list)
    changed: list[CapabilityLockChangedFact] = Field(default_factory=list)
    evidence_changed: list[CapabilityLockChangedFact] = Field(default_factory=list)


class CapabilityLockArtifactV1(RootModel[CapabilityLockFileV1 | CapabilityLockDiffV1]):
    root: CapabilityLockFileV1 | CapabilityLockDiffV1


__all__ = [
    "CAPABILITY_LOCK_DIFF_SCHEMA_VERSION",
    "CAPABILITY_LOCK_SCHEMA_VERSION",
    "CapabilityAuthority",
    "CapabilityControls",
    "CapabilityEffect",
    "CapabilityEvidence",
    "CapabilityFactV1",
    "CapabilityHashName",
    "CapabilityHashes",
    "CapabilityIdentity",
    "CapabilityLockArtifactV1",
    "CapabilityLockChangedFact",
    "CapabilityLockDiffSummary",
    "CapabilityLockDiffV1",
    "CapabilityLockFileV1",
    "CapabilityLockHashes",
    "CapabilityLockRef",
    "CapabilityLockSource",
    "CapabilityLockSummary",
    "capability_fact_sort_key",
]
