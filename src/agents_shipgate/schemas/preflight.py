from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.surfaces import ActionEffect

PREFLIGHT_SCHEMA_VERSION = "0.2"

PreflightActor = Literal["coding_agent", "human"]
PreflightActionKind = Literal["continue", "review", "gather_evidence", "verify"]
ProtectedSurfaceScopeType = Literal["whole_file", "key_level", "capability_surface"]
PreflightEvidenceSeverity = Literal["info", "low", "medium", "high", "critical"]
PreflightSignalKind = Literal[
    "protected_surface_touch",
    "host_grant_drift",
    "missing_evidence",
    "least_privilege",
    "policy_drift",
    "verify_required",
]


class PreflightNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: PreflightActor
    kind: PreflightActionKind
    command: str | None = None
    why: str


class PreflightProtectedSurface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    pattern: str
    scope_type: ProtectedSurfaceScopeType
    human_review_required: bool = True
    present: bool = False
    present_paths: list[str] = Field(default_factory=list)
    description: str


class PreflightProtectedSurfaceTouch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str
    pattern: str
    scope_type: ProtectedSurfaceScopeType
    requires_human_review: bool = True


class PreflightRequiredEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    field: str
    satisfied: bool
    severity: PreflightEvidenceSeverity
    reason: str
    recommendation: str


class CapabilityRequestControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_required: bool | None = None
    approval_threshold: str | None = None
    confirmation_required: bool | None = None
    safeguard_idempotency: bool | None = None
    safeguard_audit_log: bool | None = None
    safeguard_rollback: bool | None = None
    safeguard_dry_run: bool | None = None


class CapabilityRequestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str | None = None
    runbook: str | None = None
    approval_ticket: str | None = None


class CapabilityRequestV1(BaseModel):
    """Static preflight review input for a proposed action/capability.

    This is a planning-time declaration, not evidence that the capability is
    safe. `verify` remains responsible for gating the actual diff.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["capability_request_v1"] = "capability_request_v1"
    tool_name: str
    provider: str | None = None
    operation: str | None = None
    effect: ActionEffect
    risk_tags: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    source_type: str | None = None
    controls: CapabilityRequestControls = Field(default_factory=CapabilityRequestControls)
    evidence: CapabilityRequestEvidence = Field(default_factory=CapabilityRequestEvidence)


class HostPermissionRequestV1(BaseModel):
    """Planning-time request for coding-agent host authority.

    This describes what a coding agent intends to add or rely on before it edits
    host configuration. It is not a runtime permission broker and it never grants
    authority.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["host_permission_request_v1"] = "host_permission_request_v1"
    host: str
    surface: str
    operation: str
    path: str | None = None
    subject: str
    requested_access: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class PreflightPlanContextV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str | None = None
    task: str | None = None


class PreflightPlanV1(BaseModel):
    """Single proactive input object for coding-agent planning.

    Agents should prefer passing this object via ``preflight --plan``. Legacy
    flags remain shorthands for callers that only have paths, a diff, or one
    capability request.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["preflight_plan_v1"] = "preflight_plan_v1"
    changed_files: list[str] = Field(default_factory=list)
    diff_text: str | None = None
    capability_requests: list[CapabilityRequestV1] = Field(default_factory=list)
    host_permission_requests: list[HostPermissionRequestV1] = Field(default_factory=list)
    context: PreflightPlanContextV1 = Field(default_factory=PreflightPlanContextV1)


class TrustRootNodeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    pattern: str
    scope_type: ProtectedSurfaceScopeType
    present_paths: list[str] = Field(default_factory=list)
    file_hashes: dict[str, str] = Field(default_factory=dict)


class TrustRootGraphV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    nodes: list[TrustRootNodeV1] = Field(default_factory=list)
    graph_hash: str


class PreflightDriftSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed: bool
    base_hash: str | None = None
    head_hash: str | None = None
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)


class PreflightSignalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: PreflightSignalKind
    severity: PreflightEvidenceSeverity
    actor: PreflightActor
    subject: str
    path: str | None = None
    reason: str
    recommendation: str
    related_command: str | None = None


class PreflightResultV1(BaseModel):
    """Machine-readable planning surface for coding agents.

    The result is intentionally non-gating. It routes protected-surface and
    evidence questions before edits; `release_decision.decision` remains the
    only merge gate.
    """

    model_config = ConfigDict(extra="forbid")

    preflight_schema_version: Literal["0.1"] = "0.1"
    workspace: str
    config: str
    protected_surfaces: list[PreflightProtectedSurface] = Field(default_factory=list)
    forbidden_file_edits: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    required_evidence: list[PreflightRequiredEvidence] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    protected_surface_touches: list[PreflightProtectedSurfaceTouch] = Field(
        default_factory=list
    )
    requires_human_review: bool = False
    policy_snapshot_hash: str | None = None
    trust_root_graph_hash: str
    trust_root_graph: TrustRootGraphV1
    policy_drift: PreflightDriftSummary | None = None
    trust_root_graph_diff: PreflightDriftSummary | None = None
    first_next_action: PreflightNextAction
    notes: list[str] = Field(default_factory=list)


class PreflightResultV2(PreflightResultV1):
    """Current proactive planning surface for coding agents.

    This is still a non-gating projection. It can require verification or human
    review, but the merge/release gate remains ``release_decision.decision``.
    """

    preflight_schema_version: Literal["0.2"] = "0.2"
    signals: list[PreflightSignalV1] = Field(default_factory=list)
    requires_verify: bool = False
    verification_command: str | None = None
    allowed_next_commands: list[str] = Field(default_factory=list)
    plan_summary: dict[str, Any] = Field(default_factory=dict)
    host_grant_drift: dict[str, Any] | None = None


__all__ = [
    "PREFLIGHT_SCHEMA_VERSION",
    "CapabilityRequestControls",
    "CapabilityRequestEvidence",
    "CapabilityRequestV1",
    "HostPermissionRequestV1",
    "PreflightDriftSummary",
    "PreflightPlanContextV1",
    "PreflightPlanV1",
    "PreflightNextAction",
    "PreflightProtectedSurface",
    "PreflightProtectedSurfaceTouch",
    "PreflightRequiredEvidence",
    "PreflightResultV1",
    "PreflightResultV2",
    "PreflightSignalKind",
    "PreflightSignalV1",
    "TrustRootGraphV1",
    "TrustRootNodeV1",
]
