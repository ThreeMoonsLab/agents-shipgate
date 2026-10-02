from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.agent_control import (
    ALL_PERMISSIONS_DENIED_SCHEMA,
    PERMISSION_FIELDS,
    AgentActionRequiredControl,
    AgentControl,
    ExactCommand,
    HumanReviewRequiredControl,
    NoAgentPermissions,
    NoHumanReview,
    NonEmptyText,
    ReviewPublishableControl,
)
from agents_shipgate.schemas.instruction_structure import (
    ConditionalInstructionEditRule,
    InstructionStructureEvidence,
)
from agents_shipgate.schemas.surfaces import ActionEffect

PREFLIGHT_SCHEMA_VERSION = "0.6"
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
                    # Mirrors the Pydantic rule: the pre-edit surface never
                    # carries publication authority.
                    "properties": {
                        "control": {
                            "properties": {
                                "state": {"not": {"const": "review_publishable"}},
                                "permissions": {
                                    "properties": {"update_pr": {"const": False}},
                                    "required": ["update_pr"],
                                },
                            }
                        }
                    }
                },
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
        # Preflight runs *before* the change exists, so there is no evaluated
        # subject and nothing to publish. Reject the publishable route outright
        # rather than documenting that it never happens.
        if control.state == "review_publishable":
            raise ValueError(
                "preflight evaluates a planned change, so it cannot authorize publication"
            )
        if control.permissions.publishes and not control.completion_allowed:
            raise ValueError(
                "preflight cannot grant progress authority for an unevaluated change"
            )
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
        # ``planning_only`` exists only in the v0.6 control union, so it
        # never reaches this branch from a v0.3 or v0.5 model. It projects the
        # same legacy ``continue`` action ``complete`` did: the fields a
        # pre-v0.3 reader switches on never carried authority.
        if control.state in {"complete", "planning_only"}:
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


class TrustRootNodeV2(TrustRootNodeV1):
    instruction_structures: dict[str, InstructionStructureEvidence] = Field(
        default_factory=dict, exclude_if=lambda value: not value,
    )


class TrustRootGraphV2(TrustRootGraphV1):
    schema_version: Literal["0.2"] = "0.2"
    nodes: list[TrustRootNodeV2] = Field(default_factory=list)


class PreflightProtectedSurfaceTouchV2(PreflightProtectedSurfaceTouch):
    instruction_structure_unchanged: Literal[True] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _proof_routes_without_human(self):
        if self.instruction_structure_unchanged and (
            self.requires_human_review or self.kind not in {"agent_instructions", "tool_surface_decl"}
        ):
            raise ValueError("Instruction structure proof must project a non-human instruction touch")
        return self


class PreflightResultV5(PreflightResultV3):
    """Planning with structural comparison; raw graph identity remains current."""

    # The historical grammar prohibited update_pr even on the already
    # supported complete planning result. Match the existing model exactly:
    # unevaluated, incomplete plans never carry publication authority. This
    # changes the successor grammar only, not runtime control or frozen files.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "control": {
                                "properties": {"state": {"not": {"const": "complete"}}},
                                "required": ["state"],
                            }
                        },
                        "required": ["control"],
                    },
                    "then": PreflightResultV3.model_config["json_schema_extra"]["allOf"][0],
                },
                *PreflightResultV3.model_config["json_schema_extra"]["allOf"][1:],
            ]
        },
    )

    preflight_schema_version: Literal["0.5"] = "0.5"
    trust_root_graph: TrustRootGraphV2
    protected_surface_touches: list[PreflightProtectedSurfaceTouchV2] = Field(default_factory=list)
    conditional_file_edits: list[ConditionalInstructionEditRule] = Field(default_factory=list)


class PlanningOnlyControl(BaseModel):
    """Preflight had nothing to route, and it authorizes nothing (#610).

    Preflight answers a question about a *planned* change. When the plan names
    no changed file, capability request or host permission request, and nothing
    else raises a signal, the only thing that finished is the planning. This
    state says exactly that.

    It is deliberately not the shared ``complete``, which carries terminal
    authority -- ``merge`` and ``report_complete`` -- that every consumer of the
    shared union is entitled to act on. Before v0.6 an empty plan returned it,
    so leaving files out of a plan minted merge authority that no evaluation of
    any change stood behind. Here every permission is false, as on every other
    preflight route: preflight never authorizes merge or completion.

    A separate declaration rather than a subclass of the shared control base:
    that base types ``state`` as the shared four-state vocabulary, and this
    state must never enter it. The shared ``AgentControl`` union is embedded
    by six durable schemas and does not change.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            # The shared field list with ``permissions`` required, as
            # ``review_publishable`` requires it: this state did not exist
            # before v0.6, so an omitted vector is a malformed current payload,
            # never an old one. Borrowed rather than copied, so a field added
            # to the shared controls and not here fails the published-schema
            # round trip instead of drifting.
            "required": list(
                ReviewPublishableControl.model_config["json_schema_extra"]["required"]
            )
        },
    )

    state: Literal["planning_only"]
    reason: NonEmptyText
    completion_allowed: Literal[False] = False
    must_stop: Literal[False] = False
    verify_required: Literal[False] = False
    next_action: None = None
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list, max_length=0)
    # No default: the vector is the claim this state exists to make. A stored
    # vector must also name all six; ``PreflightResultV6`` refuses one that
    # does not, as the published schema does.
    permissions: NoAgentPermissions
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)
    stop_reason: None = None


# Preflight's own control vocabulary. It drops ``complete`` (preflight never has
# an evaluated change to stand behind) and ``review_publishable`` (nothing exists
# yet to publish), and adds ``planning_only``.
type PreflightControl = Annotated[
    PlanningOnlyControl | AgentActionRequiredControl | HumanReviewRequiredControl,
    Field(discriminator="state"),
]


def _control_state_is(state: str) -> dict[str, Any]:
    return {
        "properties": {
            "control": {
                "properties": {"state": {"const": state}},
                "required": ["state"],
            }
        },
        "required": ["control"],
    }


_V3_RULES = PreflightResultV3.model_config["json_schema_extra"]["allOf"]
#: What a ``planning_only`` answer cannot carry: it exists because the plan
#: named nothing to route and no signal fired.
_PLANNING_ONLY_EMPTY = (
    "allowed_next_commands",
    "changed_files",
    "protected_surface_touches",
    "required_evidence",
    "signals",
)


class PreflightResultV6(PreflightResultV5):
    """Planning results that can never carry authority (#610).

    ``control`` is preflight's own union: ``planning_only``,
    ``agent_action_required`` or ``human_review_required``. Every permission is
    stated and false on every route, in the model and in the generated schema
    alike, so an empty or docs-only plan cannot be read as evidence that a
    change was verified or may merge. Everything else is v0.5 unchanged.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    # Every route states all six permissions, each false: the
                    # union's variants pin the values, and
                    # ``_vector_is_stated`` refuses a vector that omits one.
                    "properties": {
                        "control": {
                            "properties": {"permissions": ALL_PERMISSIONS_DENIED_SCHEMA},
                            "required": ["permissions"],
                        }
                    }
                },
                {
                    # The legacy ``continue`` projection ``complete`` had in
                    # v0.3, no command beside it, and nothing the plan named:
                    # an answer that says "nothing to route" while carrying a
                    # touch or a signal contradicts itself (#610 review).
                    "if": _control_state_is("planning_only"),
                    "then": {
                        "properties": {
                            **_V3_RULES[1]["then"]["properties"],
                            **{field: {"maxItems": 0} for field in _PLANNING_ONLY_EMPTY},
                        }
                    },
                },
                {
                    "if": _control_state_is("agent_action_required"),
                    "then": {
                        "properties": {
                            "control": {
                                "properties": {
                                    "next_action": {"properties": {"kind": {"const": "verify"}}}
                                }
                            },
                            "requires_human_review": {"const": False},
                            "requires_verify": {"const": True},
                            "verification_command": {"type": "string"},
                            "first_next_action": {
                                "properties": {
                                    "actor": {"const": "coding_agent"},
                                    "kind": {"const": "verify"},
                                    "command": {"type": "string"},
                                }
                            },
                        }
                    },
                },
                # The human route is unchanged from v0.3.
                _V3_RULES[2],
            ]
        },
    )

    preflight_schema_version: Literal["0.6"] = "0.6"
    control: PreflightControl

    @model_validator(mode="before")
    @classmethod
    def _vector_is_stated(cls, data: Any) -> Any:
        """Refuse a stored control that does not state all six permissions.

        The shared variants rebuild an omitted or partial vector, because a
        payload from before the vector existed has none. A 0.6 payload is
        never that old, so it is held to what the published schema requires
        instead of being filled in. A control passed as a model instance was
        built here and always states it.
        """

        if isinstance(data, Mapping):
            control = data.get("control")
            if isinstance(control, Mapping):
                permissions = control.get("permissions")
                if not isinstance(permissions, Mapping) or not set(PERMISSION_FIELDS) <= set(
                    permissions
                ):
                    raise ValueError(
                        "a preflight 0.6 control must state all six permissions, each false"
                    )
        return data

    @model_validator(mode="after")
    def _planning_only_names_nothing(self) -> PreflightResultV6:
        if self.control.state == "planning_only":
            carried = [field for field in _PLANNING_ONLY_EMPTY if getattr(self, field)]
            if carried:
                raise ValueError(
                    "a planning_only answer names nothing to route, so it cannot carry "
                    + ", ".join(carried)
                )
        return self


type AnyPreflightResult = (
    PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | PreflightResultV5 | PreflightResultV6
)

# The one version ladder for reading a stored result back, used by the core
# builder and the CLI alike so a new version cannot be readable in one and
# refused by the other. ``0.4`` is absent on purpose: that bump moved only the
# schema file, and its payloads still say ``0.3``. Anything unrecognised is
# read as ``0.1``, whose closed model refuses it by name.
_RESULT_MODEL_BY_VERSION: dict[str, type[PreflightResultV1]] = {
    "0.6": PreflightResultV6,
    "0.5": PreflightResultV5,
    "0.3": PreflightResultV3,
    "0.2": PreflightResultV2,
}


def parse_preflight_result(payload: dict[str, Any]) -> AnyPreflightResult:
    """Validate a stored preflight result as the version it names.

    Raises ``pydantic.ValidationError``; callers attach their own input label.
    A version that is not a string -- a list or an object from a malformed
    file -- is unrecognised like any other, never a ``TypeError``.
    """

    version = payload.get("preflight_schema_version")
    model = (
        _RESULT_MODEL_BY_VERSION.get(version, PreflightResultV1)
        if isinstance(version, str)
        else PreflightResultV1
    )
    return model.model_validate(payload)


__all__ = [
    "AnyPreflightResult",
    "parse_preflight_result",
    "PlanningOnlyControl",
    "PreflightControl",
    "PreflightResultV6",
    "PreflightResultV5",
    "PreflightProtectedSurfaceTouchV2",
    "TrustRootGraphV2",
    "TrustRootNodeV2",
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
