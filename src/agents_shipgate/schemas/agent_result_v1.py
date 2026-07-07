from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate import __version__

AgentResultAgent = Literal["codex", "claude-code", "cursor"]
AgentResultDecision = Literal["allow", "warn", "require_review", "block"]
AgentResultRiskLevel = Literal["none", "low", "medium", "high", "critical"]
AgentResultActor = Literal["coding_agent", "human"]
AgentResultActionKind = Literal[
    "continue",
    "warn",
    "review",
    "repair",
    "install",
    "stop",
    "none",
]
AgentResultDiagnosticLevel = Literal["info", "warning", "error"]
AgentResultPolicySource = Literal[
    "explicit",
    "workspace",
    "packaged_default",
    "report_effective_policy",
    "missing",
    "invalid",
]


class AgentResultTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "agents-shipgate"
    version: str = __version__


class AgentResultSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str | None = None
    agent: str | None = None
    diff: str | None = None
    base: str | None = None
    head: str | None = None


class AgentResultNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: AgentResultActor
    kind: AgentResultActionKind
    command: str | None = None
    why: str


class AgentResultViolatedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    check_id: str
    action: AgentResultDecision
    risk_level: AgentResultRiskLevel
    title: str
    path: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommendation: str


class AgentResultAffectedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int | None = None
    end_line: int | None = None
    pointer: str | None = None
    source_type: str | None = None


class AgentResultHumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = False
    why: str | None = None
    required_reviewers: list[str] = Field(default_factory=list)


class AgentResultRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: AgentResultActor = "human"
    safe_to_attempt: bool = False
    instructions: list[str] = Field(default_factory=list)
    command: str | None = None
    forbidden_shortcuts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _safe_repairs_route_to_agent(self) -> AgentResultRepair:
        if self.safe_to_attempt and self.actor != "coding_agent":
            raise ValueError("safe repairs must route to the coding agent")
        return self


class AgentResultPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    source: AgentResultPolicySource
    snapshot_sha256: str | None = None
    path: str | None = None
    discovery: list[str] = Field(default_factory=list)


class AgentResultDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: AgentResultDiagnosticLevel
    code: str
    message: str
    path: str | None = None


class AgentResultTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    summary: str


class AgentResultV1(BaseModel):
    """Single-object JSON contract for local coding-agent preflight checks."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent_result_v1"] = "agent_result_v1"
    agent: AgentResultAgent = "codex"
    tool: AgentResultTool = Field(default_factory=AgentResultTool)
    subject: AgentResultSubject = Field(default_factory=AgentResultSubject)
    decision: AgentResultDecision
    risk_level: AgentResultRiskLevel
    audit_id: str
    policy_version: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    completion_allowed: bool = False
    # Contract v10 (additive): the check→verify deferral, machine-readable.
    # True when the diff touches a tool surface — declared or undeclared —
    # that the boundary check does not gate; the evaluator simultaneously
    # escalates what would have been a clean ``allow`` to ``warn``, so this
    # is always observed alongside ``decision="warn"``. Lives on the shared
    # base so ``agent_result_control_fields`` validates against both the
    # boundary schema and this legacy compatibility schema.
    verify_required: bool = False
    must_stop: bool = True
    first_next_action: AgentResultNextAction
    human_review: AgentResultHumanReview = Field(default_factory=AgentResultHumanReview)
    repair: AgentResultRepair = Field(default_factory=AgentResultRepair)
    policy: AgentResultPolicy
    violated_rules: list[AgentResultViolatedRule] = Field(default_factory=list)
    affected_files: list[AgentResultAffectedFile] = Field(default_factory=list)
    required_reviewers: list[str] = Field(default_factory=list)
    explanation: str | None = None
    suggested_fixes: list[str] = Field(default_factory=list)
    agent_repair_instructions: list[str] = Field(default_factory=list)
    diagnostics: list[AgentResultDiagnostic] = Field(default_factory=list)
    trace: list[AgentResultTraceEvent] = Field(default_factory=list)
    source_artifacts: dict[str, str] = Field(default_factory=dict)
    release_decision: dict[str, Any] | None = None
    trigger: dict[str, Any] | None = None
    finding_fingerprints: list[str] = Field(default_factory=list)
    policy_snapshot_sha256: str | None = None
    exit_code_hint: int = 0

    @model_validator(mode="after")
    def _action_matches_decision(self) -> AgentResultV1:
        action = self.first_next_action
        if action.kind == "install":
            if action.actor != "coding_agent":
                raise ValueError("install actions must route to the coding agent")
            if self.completion_allowed:
                raise ValueError("install actions cannot allow completion")
            return self

        if self.decision == "allow":
            if not self.completion_allowed or self.must_stop:
                raise ValueError("allow results must allow completion without stopping")
            if action.actor != "coding_agent":
                raise ValueError("allow results must route to the coding agent")
            if action.kind not in {"continue", "none"}:
                raise ValueError("allow results must continue or do nothing")
        elif self.decision == "warn":
            if not self.completion_allowed or self.must_stop:
                raise ValueError("warn results must allow completion without stopping")
            if action.actor != "coding_agent":
                raise ValueError("warn results must route to the coding agent")
            if action.kind != "warn":
                raise ValueError("warn results must use kind='warn'")
        elif self.decision == "require_review":
            if self.completion_allowed or not self.must_stop:
                raise ValueError("require_review results must stop completion")
            if not self.human_review.required:
                raise ValueError("require_review results must require human review")
            if action.actor != "human" or action.kind != "review":
                raise ValueError("require_review results must route to human review")
        elif self.decision == "block":
            if self.completion_allowed:
                raise ValueError("block results cannot allow completion")
            if action.actor == "coding_agent":
                if action.kind != "repair":
                    raise ValueError("agent-routed block results must use kind='repair'")
                if not self.repair.safe_to_attempt or self.repair.actor != "coding_agent":
                    raise ValueError("agent-routed blocks require a safe coding-agent repair")
                if self.must_stop:
                    raise ValueError("agent-repairable blocks must not stop")
            elif action.actor == "human":
                if action.kind != "stop":
                    raise ValueError("human-routed block results must use kind='stop'")
                if not self.must_stop or not self.human_review.required:
                    raise ValueError("human-routed blocks must stop for human review")
            else:  # pragma: no cover - Literal exhaustiveness guard.
                raise ValueError("unsupported block route")
        return self


AgentResult = AgentResultV1
AgentResultFile = AgentResultAffectedFile
AgentResultRule = AgentResultViolatedRule


__all__ = [
    "AgentResult",
    "AgentResultActionKind",
    "AgentResultActor",
    "AgentResultAffectedFile",
    "AgentResultAgent",
    "AgentResultDecision",
    "AgentResultDiagnostic",
    "AgentResultDiagnosticLevel",
    "AgentResultFile",
    "AgentResultHumanReview",
    "AgentResultNextAction",
    "AgentResultPolicy",
    "AgentResultPolicySource",
    "AgentResultRepair",
    "AgentResultRiskLevel",
    "AgentResultRule",
    "AgentResultSubject",
    "AgentResultTool",
    "AgentResultTraceEvent",
    "AgentResultV1",
    "AgentResultViolatedRule",
]
