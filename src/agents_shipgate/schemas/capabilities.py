from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.capability_change import CapabilitySubjectKind
from agents_shipgate.schemas.capability_semantics import (
    CapabilityHashName,
    CapabilitySemanticChange,
    CapabilitySemanticDirection,
)
from agents_shipgate.schemas.common import Confidence
from agents_shipgate.schemas.semantic import ToolSemanticEvidence
from agents_shipgate.schemas.surfaces import ActionEffect

CAPABILITY_LOCK_SCHEMA_VERSION = "0.9"
CAPABILITY_LOCK_DIFF_SCHEMA_VERSION = "0.10"
CAPABILITY_STANDARD_VERSION = "0.6"
#: The parts of a tool definition a client hands the model, beyond the input
#: schema that ``schema_hash`` already covers. Ordered as reviewers read them.
CapabilityDefinitionPart = Literal["description", "output_schema", "annotations"]
CAPABILITY_DEFINITION_PARTS: tuple[CapabilityDefinitionPart, ...] = (
    "description",
    "output_schema",
    "annotations",
)
CapabilityEvidenceProvenanceKind = Literal[
    "static_declaration",
    "ast_extraction",
    "keyword_heuristic",
    "regex_heuristic",
    "policy_pack",
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
    provenance_kind: CapabilityEvidenceProvenanceKind = "static_declaration"
    confidence: Confidence = "medium"


class CapabilityHashes(BaseModel):
    """Separate hashes for the future lock/diff substrate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_hash: str
    binding_hash: str = "legacy_binding_unknown"
    effect_hash: str
    authority_hash: str
    control_hash: str
    schema_hash: str
    risk_hash: str
    evidence_hash: str


class CapabilityDefinition(BaseModel):
    """Digests of the tool definition a client publishes to the model.

    Capability standard v0.6. ``hashes.schema_hash`` already covers the input
    schema; these cover the rest of what an MCP ``tools/list`` entry (or an
    OpenAPI operation, or a function docstring) hands the model: the
    description, the output schema and the annotations. Only digests are
    stored, never the text, so a lock never carries a tool's instructions.

    Deliberately outside ``hashes``: ``CapabilityHashName`` is the closed
    digest vocabulary of the frozen ``shipgate.capability_payload/v1`` and of
    the report's ``capability_change.changed_hashes``, and neither can grow a
    member without a new version of its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description_hash: str
    output_schema_hash: str
    annotations_hash: str


class CapabilityFactV1(BaseModel):
    """Durable capability fact used by stable capability locks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    identity: CapabilityIdentity
    effect: CapabilityEffect
    authority: CapabilityAuthority
    controls: CapabilityControls
    evidence: CapabilityEvidence
    # Capability standard v0.2: normalized semantic evidence is carried
    # alongside the legacy effect/authority projections. Optional for old
    # lock readers; newly emitted v0.3 locks populate it.
    semantic_assessment: ToolSemanticEvidence | None = None
    risk_tags: tuple[str, ...] = Field(default_factory=tuple)
    hashes: CapabilityHashes
    # Capability standard v0.6: ``None`` when the fact was built without the
    # tool record (public ``ActionFact`` data) or read from an older lock. A
    # fact with no definition is never compared on its definition.
    definition: CapabilityDefinition | None = None

    @model_validator(mode="after")
    def _semantic_projection_is_consistent(self) -> CapabilityFactV1:
        semantic = self.semantic_assessment
        if semantic is None:
            return self
        if self.effect.effect != semantic.conservative_effect:
            raise ValueError(
                "CapabilityFactV1.effect.effect must equal "
                "semantic_assessment.conservative_effect"
            )
        if self.authority.auth_type != semantic.authority.auth_type:
            raise ValueError(
                "CapabilityFactV1.authority.auth_type must project semantic authority"
            )
        if self.authority.credential_mode != semantic.authority.credential_mode:
            raise ValueError(
                "CapabilityFactV1.authority.credential_mode must project semantic authority"
            )
        if self.authority.scopes != tuple(sorted(set(semantic.authority.scopes))):
            raise ValueError("CapabilityFactV1.authority.scopes must project semantic authority")
        return self


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
    # Capability standard v0.6: one digest over every fact's ``definition``,
    # so a reviewed lock binds the tool definitions it was reviewed with.
    # ``None`` only on a lock read from an older schema.
    definition_set_hash: str | None = None


class CapabilityLockFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_lock_schema_version: Literal["0.9"] = CAPABILITY_LOCK_SCHEMA_VERSION
    experimental: Literal[False] = False
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
    definition_set_hash: str | None = None
    capability_count: int


class CapabilityLockDiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = 0
    removed: int = 0
    reidentified: int = 0
    changed: int = 0
    evidence_changed: int = 0
    unchanged: int = 0
    # Independent of the buckets above: a fact whose semantics did not move
    # is still ``unchanged`` there, and is counted here when the definition
    # its tool publishes moved.
    definition_changed: int = 0


class CapabilityLockDefinitionChange(BaseModel):
    """One capability whose published tool definition moved between locks.

    Names which parts moved and their digests on each side, never the text.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    provider: str
    operation: str
    parts: tuple[CapabilityDefinitionPart, ...]
    before: CapabilityDefinition
    after: CapabilityDefinition

    @model_validator(mode="after")
    def _parts_are_the_moved_digests(self) -> CapabilityLockDefinitionChange:
        moved = tuple(
            part
            for part in CAPABILITY_DEFINITION_PARTS
            if getattr(self.before, f"{part}_hash") != getattr(self.after, f"{part}_hash")
        )
        if not moved:
            raise ValueError("a definition change must move at least one part")
        if self.parts != moved:
            raise ValueError(
                "parts must list exactly the definition digests that differ, "
                f"in {CAPABILITY_DEFINITION_PARTS} order"
            )
        return self


class CapabilityLockChangedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    operation: str
    changed_hashes: tuple[CapabilityHashName, ...] = Field(default_factory=tuple)
    semantic_direction: CapabilitySemanticDirection = "unknown"
    semantic_changes: tuple[CapabilitySemanticChange, ...] = Field(default_factory=tuple)
    before: CapabilityFactV1
    after: CapabilityFactV1


class CapabilityLockDiffV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_lock_diff_schema_version: Literal["0.10"] = (
        CAPABILITY_LOCK_DIFF_SCHEMA_VERSION
    )
    experimental: Literal[False] = False
    base: CapabilityLockRef
    head: CapabilityLockRef
    summary: CapabilityLockDiffSummary
    added: list[CapabilityFactV1] = Field(default_factory=list)
    removed: list[CapabilityFactV1] = Field(default_factory=list)
    reidentified: list[CapabilityLockChangedFact] = Field(default_factory=list)
    changed: list[CapabilityLockChangedFact] = Field(default_factory=list)
    evidence_changed: list[CapabilityLockChangedFact] = Field(default_factory=list)
    definition_changed: list[CapabilityLockDefinitionChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _definition_count_matches_rows(self) -> CapabilityLockDiffV1:
        if self.summary.definition_changed != len(self.definition_changed):
            raise ValueError(
                "summary.definition_changed must equal the number of definition_changed rows"
            )
        return self


class CapabilityLockArtifactV1(RootModel[CapabilityLockFileV1 | CapabilityLockDiffV1]):
    root: CapabilityLockFileV1 | CapabilityLockDiffV1


class CapabilityLockFileArtifactV1(RootModel[CapabilityLockFileV1]):
    root: CapabilityLockFileV1


class CapabilityLockDiffArtifactV1(RootModel[CapabilityLockDiffV1]):
    root: CapabilityLockDiffV1


__all__ = [
    "CAPABILITY_DEFINITION_PARTS",
    "CAPABILITY_LOCK_DIFF_SCHEMA_VERSION",
    "CAPABILITY_LOCK_SCHEMA_VERSION",
    "CAPABILITY_STANDARD_VERSION",
    "CapabilityAuthority",
    "CapabilityControls",
    "CapabilityDefinition",
    "CapabilityDefinitionPart",
    "CapabilityEffect",
    "CapabilityEvidence",
    "CapabilityEvidenceProvenanceKind",
    "CapabilityFactV1",
    "CapabilityHashName",
    "CapabilityHashes",
    "CapabilityIdentity",
    "CapabilityLockArtifactV1",
    "CapabilityLockChangedFact",
    "CapabilityLockDefinitionChange",
    "CapabilityLockDiffArtifactV1",
    "CapabilityLockDiffSummary",
    "CapabilityLockDiffV1",
    "CapabilityLockFileArtifactV1",
    "CapabilityLockFileV1",
    "CapabilityLockHashes",
    "CapabilityLockRef",
    "CapabilityLockSource",
    "CapabilityLockSummary",
    "CapabilitySemanticChange",
    "CapabilitySemanticDirection",
    "capability_fact_sort_key",
]
