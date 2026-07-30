from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.agent_control import AgentControl
from agents_shipgate.schemas.surfaces import ActionEffect

PREFLIGHT_SCHEMA_VERSION = "0.3"
MAX_PREFLIGHT_DIFF_BYTES = 32 * 1024 * 1024

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

    @field_validator("approval_threshold")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class CapabilityRequestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str | None = None
    runbook: str | None = None
    approval_ticket: str | None = None

    @field_validator("owner", "runbook", "approval_ticket")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


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

    @field_validator("tool_name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("provider", "operation", "source_type")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("risk_tags")
    @classmethod
    def normalize_risk_tags(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if any(not value for value in normalized):
            raise ValueError("risk tags must not be blank")
        return list(dict.fromkeys(normalized))

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("scopes must not be blank")
        return list(dict.fromkeys(normalized))


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

    @field_validator("host", "surface", "operation", "subject")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("path", "reason")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class PreflightPlanContextV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str | None = None
    task: str | None = None

    @field_validator("agent", "task")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class PreflightPlanV1(BaseModel):
    """Single proactive input object for coding-agent planning.

    Agents should prefer passing this object via ``preflight --plan``. Legacy
    flags remain shorthands for callers that only have paths, a diff, or one
    capability request.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["preflight_plan_v1"] = "preflight_plan_v1"
    changed_files: list[str] = Field(default_factory=list, max_length=100_000)
    diff_text: str | None = None
    capability_requests: list[CapabilityRequestV1] = Field(
        default_factory=list,
        max_length=10_000,
    )
    host_permission_requests: list[HostPermissionRequestV1] = Field(
        default_factory=list,
        max_length=10_000,
    )
    context: PreflightPlanContextV1 = Field(default_factory=PreflightPlanContextV1)

    @model_validator(mode="after")
    def enforce_static_input_bounds(self) -> PreflightPlanV1:
        if (
            self.diff_text is not None
            and len(self.diff_text.encode("utf-8")) > MAX_PREFLIGHT_DIFF_BYTES
        ):
            raise ValueError(
                f"diff_text exceeds the {MAX_PREFLIGHT_DIFF_BYTES}-byte static input limit"
            )
        for path in self.changed_files:
            if len(path.encode("utf-8")) > 4096:
                raise ValueError("changed_files entries must not exceed 4096 UTF-8 bytes")
        return self


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
    protected_surface_touches: list[PreflightProtectedSurfaceTouch] = Field(default_factory=list)
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


class PreflightResultV3(PreflightResultV2):
    """Current planning result with one authoritative operational control.

    The inherited v0.1/v0.2 fields remain compatibility projections for one
    migration cycle.  ``control`` is authoritative and construction fails if
    those projections contradict it.
    """

    preflight_schema_version: Literal["0.3"] = "0.3"
    control: AgentControl

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "control": {
                                "properties": {"state": {"const": "complete"}},
                                "required": ["state"],
                            }
                        },
                        "required": ["control"],
                    },
                    "then": {
                        "properties": {
                            "requires_human_review": {"const": False},
                            "requires_verify": {"const": False},
                            "verification_command": {"type": "null"},
                            "first_next_action": {
                                "properties": {
                                    "actor": {"const": "coding_agent"},
                                    "kind": {"const": "continue"},
                                    "command": {"type": "null"},
                                }
                            },
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "control": {
                                "properties": {"state": {"const": "human_review_required"}},
                                "required": ["state"],
                            }
                        },
                        "required": ["control"],
                    },
                    "then": {
                        "properties": {
                            "requires_human_review": {"const": True},
                            "first_next_action": {
                                "properties": {
                                    "actor": {"const": "human"},
                                    "command": {"type": "null"},
                                }
                            },
                        }
                    },
                },
            ]
        },
    )

    @model_validator(mode="after")
    def _legacy_fields_project_control(self) -> PreflightResultV3:
        control = self.control
        expected_human = control.state == "human_review_required"
        if self.requires_human_review != expected_human:
            raise ValueError("requires_human_review must exactly project control.state")
        if self.requires_verify != control.verify_required:
            raise ValueError("requires_verify must exactly project control.verify_required")
        if self.allowed_next_commands != control.allowed_next_commands:
            raise ValueError(
                "allowed_next_commands must exactly project control.allowed_next_commands"
            )

        legacy = self.first_next_action
        if control.state == "complete":
            if legacy.actor != "coding_agent" or legacy.kind != "continue":
                raise ValueError("complete preflight control must project a legacy continue action")
            if legacy.command is not None or legacy.why != control.reason:
                raise ValueError("legacy continue action must exactly project complete control")
            if self.verification_command is not None:
                raise ValueError("complete preflight control cannot carry a verification command")
        elif control.state == "agent_action_required":
            action = control.next_action
            if legacy.actor != "coding_agent" or action.kind != "verify":
                raise ValueError(
                    "preflight v0.3 supports only an exact coding-agent verify projection"
                )
            if legacy.kind != "verify" or legacy.command != action.command:
                raise ValueError("verify control must exactly project the legacy verify action")
            if legacy.why != action.why or self.verification_command != action.command:
                raise ValueError("legacy verification fields must match control.next_action")
        else:
            if legacy.actor != "human" or legacy.command is not None:
                raise ValueError("human preflight control must route the legacy action to a human")
            if control.next_action.actor != "human":  # pragma: no cover - union lock.
                raise ValueError("human control must carry a human next action")
            if legacy.why != control.next_action.why:
                raise ValueError("legacy human action must exactly project control.next_action")
        return self


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
    "PreflightResultV3",
    "PreflightSignalKind",
    "PreflightSignalV1",
    "TrustRootGraphV1",
    "TrustRootNodeV1",
]
