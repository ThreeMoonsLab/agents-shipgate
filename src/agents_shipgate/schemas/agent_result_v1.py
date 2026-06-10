from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentResultDecision = Literal["allow", "warn", "require_review", "block"]
AgentResultRiskLevel = Literal["none", "low", "medium", "high", "critical"]
AgentResultActor = Literal["coding_agent", "human"]
AgentResultActionKind = Literal["continue", "warn", "review", "stop", "none"]
AgentResultDiagnosticLevel = Literal["info", "warning", "error"]


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


class AgentResultDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: AgentResultDiagnosticLevel
    code: str
    message: str
    path: str | None = None


class AgentResultAffectedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int | None = None
    end_line: int | None = None
    pointer: str | None = None
    source_type: str | None = None


class AgentResultTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    summary: str


class AgentResultV1(BaseModel):
    """Single-object JSON contract for coding-agent gate projections.

    Emitted by both the local Codex preflight check and the GitHub/verify
    artifact projection. Fields that are only available to one producer are
    optional so every emitted object still validates against this one v1 schema.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent_result_v1"] = "agent_result_v1"
    agent: Literal["codex"] = "codex"
    decision: AgentResultDecision
    risk_level: AgentResultRiskLevel
    audit_id: str
    policy_version: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    first_next_action: AgentResultNextAction
    violated_rules: list[AgentResultViolatedRule] = Field(default_factory=list)
    diagnostics: list[AgentResultDiagnostic] = Field(default_factory=list)
    release_decision: dict[str, Any] | None = None
    trigger: dict[str, Any] | None = None
    finding_fingerprints: list[str] = Field(default_factory=list)
    required_reviewers: list[str] | None = None
    affected_files: list[AgentResultAffectedFile] | None = None
    suggested_fixes: list[str] | None = None
    agent_repair_instructions: list[str] | None = None
    policy_snapshot_sha256: str | None = None
    trace: list[AgentResultTraceEvent] | None = None
    source_artifacts: dict[str, str] | None = None
    exit_code_hint: int | None = None

    @model_validator(mode="after")
    def _action_matches_decision(self) -> AgentResultV1:
        if self.decision == "allow":
            if self.first_next_action.actor != "coding_agent":
                raise ValueError("allow results must route to the coding agent")
            if self.first_next_action.kind not in {"continue", "none"}:
                raise ValueError("allow results must continue or do nothing")
        elif self.decision == "warn":
            if self.first_next_action.actor != "coding_agent":
                raise ValueError("warn results must route to the coding agent")
            if self.first_next_action.kind != "warn":
                raise ValueError("warn results must use kind='warn'")
        elif self.decision == "require_review":
            if self.first_next_action.actor != "human":
                raise ValueError("require_review results must route to a human")
            if self.first_next_action.kind != "review":
                raise ValueError("require_review results must use kind='review'")
        elif self.decision == "block":
            if self.first_next_action.actor != "human":
                raise ValueError("block results must route to a human")
            if self.first_next_action.kind != "stop":
                raise ValueError("block results must use kind='stop'")
        return self
