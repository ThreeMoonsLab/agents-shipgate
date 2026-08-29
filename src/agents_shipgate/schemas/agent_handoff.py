from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate import __version__
from agents_shipgate.schemas.agent_control import AgentControl
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN
from agents_shipgate.schemas.verifier import Applicability, MergeVerdict, map_merge_verdict

AGENT_HANDOFF_SCHEMA_VERSION = "shipgate.agent_handoff/v8"
AGENT_HANDOFF_SCHEMA_PATH = "docs/agent-handoff-schema.v8.json"

AgentHandoffOperation = Literal["verify_pr", "verify_local", "verify_preview"]
RemediationPlanSafety = Literal["allowed", "forbidden", "patch"]


class AgentHandoffTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "agents-shipgate"
    version: str = __version__


class AgentHandoffSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str
    config: str
    base_ref: str | None = None
    head_ref: str = "HEAD"
    changed_files: list[str] = Field(default_factory=list)


class AgentHandoffGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gating_signal: Literal["release_decision.decision"] = "release_decision.decision"
    static_analysis_only: Literal[True] = True
    runtime_behavior_verified: Literal[False] = False
    static_verdict_disclaimer: str = STATIC_VERDICT_DISCLAIMER
    decision: str | None = None
    merge_verdict: MergeVerdict
    applicability: Applicability | None = None
    can_merge_without_human: bool = False
    ci_would_fail: bool | None = None


class AgentHandoffController(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completion_allowed: bool = False
    must_stop: bool = True
    stop_reason: str | None = None
    allowed_next_commands: list[str] = Field(default_factory=list)
    forbidden_file_edits: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


class AgentHandoffGateV3(AgentHandoffGate):
    """Current gate projection with explicit execution applicability."""

    applicability: Applicability


class AgentHandoffBlockedBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: Literal["blocker", "review_item"]
    id: str | None = None
    fingerprint: str | None = None
    check_id: str
    severity: str
    title: str
    baseline_status: str | None = None
    blocks_release: bool | None = None
    capability_refs: list[str] = Field(default_factory=list)
    capability_trace_refs: list[str] = Field(default_factory=list)
    support_hash: str | None = None


class AgentHandoffRemediationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safety: RemediationPlanSafety
    id: str | None = None
    actor: str | None = None
    kind: str
    target: str | None = None
    finding_id: str | None = None
    check_id: str | None = None
    command: str | None = None
    reason: str
    patch: dict[str, Any] | None = None


class AgentHandoffReproducibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    subject_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    input_set_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    executor_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    decision_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    config_sha256: str | None = None
    baseline_sha256: str | None = None
    policy_packs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_sha256: dict[str, str] = Field(default_factory=dict)


class AgentHandoffArtifactV2(BaseModel):
    """Compact machine handoff emitted beside verifier artifacts.

    This is a projection only. The release gate remains
    ``report.json.release_decision.decision``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.agent_handoff/v2"] = "shipgate.agent_handoff/v2"
    contract_version: str
    tool: AgentHandoffTool = Field(default_factory=AgentHandoffTool)
    operation: AgentHandoffOperation
    subject: AgentHandoffSubject
    gate: AgentHandoffGate
    controller: AgentHandoffController
    next_action: dict[str, Any] | None = None
    human_review: dict[str, Any] | None = None
    fix_task: dict[str, Any] | None = None
    blocked_by: list[AgentHandoffBlockedBy] = Field(default_factory=list)
    remediation_plan: list[AgentHandoffRemediationStep] = Field(default_factory=list)
    capability_review: dict[str, Any] = Field(default_factory=dict)
    reproducibility: AgentHandoffReproducibility = Field(
        default_factory=AgentHandoffReproducibility
    )
    artifacts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _gate_projects_release_decision(self) -> AgentHandoffArtifactV2:
        if self.gate.static_verdict_disclaimer != STATIC_VERDICT_DISCLAIMER:
            raise ValueError("AgentHandoffArtifact must preserve the static-verdict disclaimer")
        if self.gate.decision is None:
            return self
        expected = map_merge_verdict(self.gate.decision)
        if self.gate.merge_verdict != expected:
            raise ValueError(
                "AgentHandoffArtifact.gate.merge_verdict must project from "
                "gate.decision via map_merge_verdict "
                f"(expected {expected!r}, got {self.gate.merge_verdict!r})."
            )
        return self

    @model_validator(mode="after")
    def _controller_projects_gate(self) -> AgentHandoffArtifactV2:
        if self.controller.completion_allowed != self.gate.can_merge_without_human:
            raise ValueError(
                "AgentHandoffArtifact.controller.completion_allowed must equal "
                "gate.can_merge_without_human."
            )
        return self


class AgentHandoffArtifactV1(AgentHandoffArtifactV2):
    """Frozen v1 handoff reader retained for legacy artifact ingestion."""

    schema_version: Literal["shipgate.agent_handoff/v1"] = "shipgate.agent_handoff/v1"


class AgentHandoffArtifact(BaseModel):
    """Current compact handoff with one authoritative AgentControl block."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "gate": {
                                "properties": {
                                    "can_merge_without_human": {"const": True}
                                },
                                "required": ["can_merge_without_human"],
                            }
                        },
                        "required": ["gate"],
                    },
                    "then": {
                        "properties": {
                            "control": {
                                "properties": {"state": {"const": "complete"}},
                                "required": ["state"],
                            },
                            "fix_task": {"type": "null"},
                        }
                    },
                    "else": {
                        "properties": {
                            "control": {
                                "properties": {
                                    "state": {
                                        "enum": [
                                            "agent_action_required",
                                            "review_publishable",
                                            "human_review_required",
                                        ]
                                    }
                                },
                                "required": ["state"],
                            }
                        }
                    },
                },
                {
                    # See ``VerifierArtifact``: the continuation is part of the
                    # condition, and an artifact that omits the field still
                    # matches and is still held to the original requirement.
                    "if": {
                        "properties": {
                            "declaration_continuation": {"not": {"const": True}},
                            "control": {
                                "properties": {
                                    "completion_allowed": {"const": False},
                                    "permissions": {
                                        "properties": {"update_pr": {"const": True}},
                                        "required": ["update_pr"],
                                    },
                                },
                                "required": ["completion_allowed", "permissions"],
                            },
                        },
                        "required": ["control"],
                    },
                    "then": {
                        "required": ["gate"],
                        "properties": {
                            "gate": {
                                "properties": {
                                    "applicability": {"const": "verified"},
                                    "decision": {
                                        "type": "string",
                                        "not": {"const": "blocked"},
                                    },
                                },
                                "required": ["applicability", "decision"],
                            }
                        },
                    },
                },
                {
                    "if": {
                        "properties": {
                            "authorization": {
                                "properties": {"status": {"const": "accepted"}},
                                "required": ["status"],
                            }
                        },
                        "required": ["authorization"],
                    },
                    "then": {
                        "properties": {
                            "gate": {
                                "properties": {
                                    "decision": {"const": "review_required"},
                                    "merge_verdict": {
                                        "const": "human_review_required"
                                    },
                                    "can_merge_without_human": {"const": False},
                                },
                                "required": [
                                    "decision",
                                    "merge_verdict",
                                    "can_merge_without_human",
                                ],
                            },
                            "control": {
                                "properties": {
                                    "state": {"const": "agent_action_required"},
                                    "completion_allowed": {"const": False},
                                    "next_action": {
                                        "properties": {"kind": {"const": "repair"}},
                                        "required": ["kind"],
                                    },
                                    "allowed_next_commands": {
                                        "minItems": 1,
                                        "maxItems": 1,
                                    },
                                },
                                "required": [
                                    "state",
                                    "completion_allowed",
                                    "next_action",
                                    "allowed_next_commands",
                                ],
                            },
                            "fix_task": {"type": "null"},
                        }
                    },
                },
            ]
        },
    )

    schema_version: Literal["shipgate.agent_handoff/v8"] = AGENT_HANDOFF_SCHEMA_VERSION
    contract_version: str
    tool: AgentHandoffTool = Field(default_factory=AgentHandoffTool)
    operation: AgentHandoffOperation
    subject: AgentHandoffSubject
    gate: AgentHandoffGateV3
    control: AgentControl
    authorization: AuthorizationEvaluationV1
    fix_task: dict[str, Any] | None = None
    blocked_by: list[AgentHandoffBlockedBy] = Field(default_factory=list)
    remediation_plan: list[AgentHandoffRemediationStep] = Field(default_factory=list)
    capability_review: dict[str, Any] = Field(default_factory=dict)
    forbidden_file_edits: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    reproducibility: AgentHandoffReproducibility = Field(
        default_factory=AgentHandoffReproducibility
    )
    artifacts: dict[str, str] = Field(default_factory=dict)
    #: Carried from the verifier: this run's trust-root delta is a declaration
    #: continuation, receipt-pinned on both sides, so a blocked decision may
    #: authorize publication and nothing more (#429). Appended, because the
    #: top-level section order is itself a pinned contract.
    declaration_continuation: bool = False

    @model_validator(mode="after")
    def _single_gate_and_control(self) -> AgentHandoffArtifact:
        if self.gate.static_verdict_disclaimer != STATIC_VERDICT_DISCLAIMER:
            raise ValueError("AgentHandoffArtifact must preserve the static-verdict disclaimer")
        expected_verdict = (
            map_merge_verdict(self.gate.decision)
            if self.gate.decision is not None
            else ("mergeable" if self.gate.applicability == "not_applicable" else "unknown")
        )
        if self.gate.merge_verdict != expected_verdict:
            raise ValueError("handoff merge_verdict must project from the release gate")
        expected_merge = bool(
            self.gate.decision == "passed"
            or (self.gate.decision is None and self.gate.applicability == "not_applicable")
        )
        if self.gate.can_merge_without_human != expected_merge:
            raise ValueError("handoff can_merge_without_human is not a pure gate projection")
        if self.control.completion_allowed != expected_merge:
            raise ValueError("handoff control must exactly project gate merge authority")
        if self.control.state == "complete" and self.fix_task is not None:
            raise ValueError("complete handoff control cannot carry a pending fix task")
        if not self.control.completion_allowed and self.control.permissions.publishes:
            # Same substrate binding as verifier and verify-run, keyed on the
            # permission vector: publication is a claim about an evaluated,
            # non-blocked change whatever route carries it.
            if self.gate.applicability != "verified" or self.gate.decision is None:
                raise ValueError(
                    "publication authority requires a verified handoff gate with a decision"
                )
            if self.gate.decision == "blocked" and not self.declaration_continuation:
                raise ValueError("a blocked handoff gate cannot authorize publication")
        if self.authorization.status == "accepted":
            if self.gate.decision != "review_required":
                raise ValueError(
                    "accepted authorization requires a review_required release decision"
                )
            if self.control.state != "agent_action_required":
                raise ValueError(
                    "accepted authorization requires agent_action_required control"
                )
            action = self.control.next_action
            if action.kind != "repair" or getattr(action, "command", None) != (
                self.authorization.command
            ):
                raise ValueError(
                    "accepted authorization must exactly bind the repair command"
                )
            if self.control.allowed_next_commands != [self.authorization.command]:
                raise ValueError(
                    "accepted authorization must expose only its exact allowed command"
                )
            if self.fix_task is not None:
                raise ValueError(
                    "accepted authorization is an operational overlay, not a fix task"
                )
        return self


__all__ = [
    "AGENT_HANDOFF_SCHEMA_PATH",
    "AGENT_HANDOFF_SCHEMA_VERSION",
    "AgentHandoffArtifact",
    "AgentHandoffArtifactV1",
    "AgentHandoffArtifactV2",
    "AgentHandoffBlockedBy",
    "AgentHandoffController",
    "AgentHandoffGate",
    "AgentHandoffGateV3",
    "AgentHandoffOperation",
    "AgentHandoffRemediationStep",
    "AgentHandoffReproducibility",
    "AgentHandoffSubject",
    "AgentHandoffTool",
]
