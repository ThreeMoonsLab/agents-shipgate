from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.common import BaselineStatus, Confidence, Severity
from agents_shipgate.schemas.semantic import ToolSemanticEvidence

ToolSurfaceDiffBaseKind = Literal["none", "report", "baseline"]
ToolSurfaceChangeKind = Literal["added", "removed", "changed"]
ToolSurfaceFactScopeKind = Literal["tool_required", "manifest_declared"]
ToolSurfaceControlKind = Literal[
    "approval_policy",
    "confirmation_policy",
    "idempotency_evidence",
]


class ToolSurfaceHashes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str | None = None
    description: str | None = None
    input_schema: str | None = None
    output_schema: str | None = None
    parameters: str | None = None
    annotations: str | None = None


class ToolSurfaceToolFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str = ""
    name: str
    provider: str = ""
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    risk_tags: list[str] = Field(default_factory=list)
    auth_scopes: list[str] = Field(default_factory=list)
    owner: str | None = None
    extraction_confidence: Confidence = "low"
    has_description: bool = False
    hashes: ToolSurfaceHashes = Field(default_factory=ToolSurfaceHashes)

    @model_validator(mode="after")
    def fill_legacy_identity(self) -> ToolSurfaceToolFact:
        if not self.tool_id:
            self.tool_id = f"legacy:{self.source_type}:{self.source_id or ''}:{self.name}"
        if not self.provider:
            self.provider = self.source_id or self.source_type
        return self


class ToolSurfaceScopeFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    kind: ToolSurfaceFactScopeKind
    tool_names: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    broad: bool = False


class ToolSurfaceControlFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceControlKind
    tool: str
    tool_id: str | None = None
    source: str
    reason: str | None = None


class ToolSurfacePolicyFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    key: str
    # Change-detection hash only; not a security boundary.
    value_hash: str
    summary: str | None = None


class ToolSurfaceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[ToolSurfaceToolFact] = Field(default_factory=list)
    scopes: list[ToolSurfaceScopeFact] = Field(default_factory=list)
    controls: list[ToolSurfaceControlFact] = Field(default_factory=list)
    policies: list[ToolSurfacePolicyFact] = Field(default_factory=list)


class ToolSurfaceDiffBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceDiffBaseKind = "none"
    path: str | None = None
    report_schema_version: str | None = None
    baseline_schema_version: str | None = None


class ToolSurfaceDiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools_added: int = 0
    tools_removed: int = 0
    tools_changed: int = 0
    new_scopes: int = 0
    removed_scopes: int = 0
    new_high_risk_effects: int = 0
    removed_high_risk_effects: int = 0
    controls_added: int = 0
    controls_removed: int = 0
    metadata_changes: int = 0
    policy_drift_items: int = 0
    new_findings: int = 0
    resolved_findings: int = 0
    unchanged_findings: int = 0
    accepted_debt: int = 0


class ToolSurfaceFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    before: Any = None
    after: Any = None


class ToolSurfaceToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceChangeKind
    tool_id: str | None = None
    name: str
    provider: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    changes: list[ToolSurfaceFieldChange] = Field(default_factory=list)
    # v0.19 reviewer-grade provenance: tool path:line for jump-to-source.
    # Populated by ``enrich_tool_surface_diff_with_source`` from the
    # live ``tool_inventory``; defaults to None for the
    # ``compute_tool_surface_diff`` output before enrichment and for
    # tools that were removed (the index no longer carries them).
    source_path: str | None = None
    source_start_line: int | None = None


class ToolSurfaceHighRiskEffectChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceChangeKind
    tool_id: str | None = None
    tool: str
    tag: str
    # v0.19 reviewer-grade provenance: see ToolSurfaceToolChange above.
    source_path: str | None = None
    source_start_line: int | None = None


class ToolSurfaceScopeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceChangeKind
    scope: str
    scope_kind: ToolSurfaceFactScopeKind
    tool_names: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    broad: bool = False


class ToolSurfaceControlChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceChangeKind
    control: ToolSurfaceControlKind
    tool: str
    tool_id: str | None = None
    source: str | None = None
    reason: str | None = None
    # v0.19 reviewer-grade provenance: tool path:line for jump-to-source.
    # ``source`` (above) is the manifest-policy origin label (e.g.
    # ``"openai_api"``, ``"policies"``); ``source_path`` /
    # ``source_start_line`` carry the underlying TOOL's source so a
    # reviewer can jump straight to the OpenAPI / MCP / SDK definition.
    source_path: str | None = None
    source_start_line: int | None = None


class ToolSurfaceMetadataChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceChangeKind
    tool_id: str | None = None
    tool: str
    metadata: str
    before: Any = None
    after: Any = None
    # v0.19 reviewer-grade provenance: see ToolSurfaceToolChange above.
    source_path: str | None = None
    source_start_line: int | None = None


class ToolSurfacePolicyDrift(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ToolSurfaceChangeKind
    policy_kind: str
    key: str
    before_hash: str | None = None
    after_hash: str | None = None
    before_summary: str | None = None
    after_summary: str | None = None


class ToolSurfaceFindingDeltaItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    check_id: str
    severity: Severity
    title: str
    tool_name: str | None = None
    baseline_status: BaselineStatus | None = None


class ToolSurfaceFindingDeltas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_findings: list[ToolSurfaceFindingDeltaItem] = Field(default_factory=list)
    resolved_findings: list[ToolSurfaceFindingDeltaItem] = Field(default_factory=list)
    unchanged_findings: list[ToolSurfaceFindingDeltaItem] = Field(default_factory=list)
    accepted_debt: list[ToolSurfaceFindingDeltaItem] = Field(default_factory=list)


class ToolSurfaceDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base: ToolSurfaceDiffBase = Field(default_factory=ToolSurfaceDiffBase)
    summary: ToolSurfaceDiffSummary = Field(default_factory=ToolSurfaceDiffSummary)
    tools: list[ToolSurfaceToolChange] = Field(default_factory=list)
    high_risk_effects: list[ToolSurfaceHighRiskEffectChange] = Field(default_factory=list)
    scopes: list[ToolSurfaceScopeChange] = Field(default_factory=list)
    controls: list[ToolSurfaceControlChange] = Field(default_factory=list)
    metadata_changes: list[ToolSurfaceMetadataChange] = Field(default_factory=list)
    policy_drift: list[ToolSurfacePolicyDrift] = Field(default_factory=list)
    finding_deltas: ToolSurfaceFindingDeltas = Field(
        default_factory=ToolSurfaceFindingDeltas
    )
    notes: list[str] = Field(default_factory=list)


ActionEffect = Literal[
    "read",
    "write",
    "destructive",
    "external_communication",
    "financial_write",
    "production_operation",
    "privileged_data_access",
    "code_execution",
    "identity_access",
]
ActionSurfaceChangeType = Literal[
    "ACTION_ADDED",
    "ACTION_REMOVED",
    "ACTION_MODIFIED",
    "SCOPE_EXPANDED",
    "EFFECT_ESCALATED",
    "RISK_TAG_ADDED",
    "APPROVAL_REMOVED",
    "SAFEGUARD_REMOVED",
    "INPUT_SCHEMA_EXPANDED",
]


class ActionApprovalFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool | None = None
    threshold: str | None = None


class ActionSafeguardsFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency: bool | None = None
    audit_log: bool | None = None
    rollback: bool | None = None
    dry_run: bool | None = None


class ActionEvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str | None = None
    runbook: str | None = None
    approval_ticket: str | None = None


class ActionSurfaceHashes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_hash: str
    schema_hash: str
    policy_hash: str
    risk_hash: str


class ActionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    agent_id: str
    tool_id: str
    tool_name: str
    provider: str
    source_type: str
    source_id: str | None = None
    # Reviewer-grade provenance for policy findings derived from action facts.
    # These fields are intentionally excluded from ActionSurfaceHashes so moving
    # a tool within a file does not change action identity or baseline matching.
    source_ref: str | None = None
    source_location: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    operation: str
    effect: ActionEffect
    # v0.29: explicit evidence behind the conservative effect and authority.
    # Optional only so frozen pre-v0.29 reports remain readable.
    semantic_assessment: ToolSemanticEvidence | None = None
    risk_tags: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)
    approval_policy: ActionApprovalFact = Field(default_factory=ActionApprovalFact)
    safeguards: ActionSafeguardsFact = Field(default_factory=ActionSafeguardsFact)
    evidence: ActionEvidenceFact = Field(default_factory=ActionEvidenceFact)
    input_fields: list[str] = Field(default_factory=list)
    required_input_fields: list[str] = Field(default_factory=list)
    input_schema_hash: str
    hashes: ActionSurfaceHashes


class ActionSurfaceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_version: str = "0.2"
    actions: list[ActionFact] = Field(default_factory=list)


class ActionSurfaceDiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions_added: int = 0
    actions_removed: int = 0
    actions_modified: int = 0
    scope_expansions: int = 0
    effect_escalations: int = 0
    risk_tags_added: int = 0
    approvals_removed: int = 0
    safeguards_removed: int = 0
    input_schema_expansions: int = 0
    blocking_findings: int = 0


class ActionSurfaceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionSurfaceChangeType
    action_id: str
    tool_id: str | None = None
    agent_id: str | None = None
    tool_name: str | None = None
    operation: str | None = None
    severity: Severity = "info"
    reason: str
    before: Any = None
    after: Any = None
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    # v0.19 reviewer-grade provenance: tool path:line populated by
    # ``enrich_action_surface_diff_with_source`` from the live
    # ``tool_inventory``. Kept on a structured field instead of being
    # baked into ``reason`` because ``ActionSurfaceChange.model_dump``
    # lands in policy-finding ``evidence`` payloads and finding
    # fingerprints hash ``evidence`` — a string suffix in ``reason``
    # would leak line numbers into baseline identity. The internal
    # diff used by policy evaluation is never enriched; only the
    # public diff renders the source fields.
    source_path: str | None = None
    source_start_line: int | None = None


class ActionSurfaceDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base: ToolSurfaceDiffBase = Field(default_factory=ToolSurfaceDiffBase)
    summary: ActionSurfaceDiffSummary = Field(default_factory=ActionSurfaceDiffSummary)
    added: list[ActionSurfaceChange] = Field(default_factory=list)
    removed: list[ActionSurfaceChange] = Field(default_factory=list)
    modified: list[ActionSurfaceChange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
