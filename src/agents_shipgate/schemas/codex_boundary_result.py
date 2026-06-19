from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CODEX_BOUNDARY_RESULT_SCHEMA_VERSION = "shipgate.codex_boundary_result/v1"

CodexBoundaryDecision = Literal["allow", "warn", "require_review", "block"]
CodexBoundaryRiskLevel = Literal["none", "low", "medium", "high", "critical"]
CodexBoundaryActor = Literal["coding_agent", "human"]
CodexBoundaryActionKind = Literal["continue", "warn", "review", "stop", "none"]
CodexBoundaryDiagnosticLevel = Literal["info", "warning", "error"]


class CodexBoundaryNextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: CodexBoundaryActor
    kind: CodexBoundaryActionKind
    command: str | None = None
    why: str


class CodexBoundaryViolatedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    check_id: str
    action: CodexBoundaryDecision
    risk_level: CodexBoundaryRiskLevel
    title: str
    path: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommendation: str


class CodexBoundaryDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: CodexBoundaryDiagnosticLevel
    code: str
    message: str
    path: str | None = None


class CodexBoundaryResultV1(BaseModel):
    """Single-object JSON contract for local Codex boundary preflight checks."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.codex_boundary_result/v1"] = (
        CODEX_BOUNDARY_RESULT_SCHEMA_VERSION
    )
    agent: Literal["codex"] = "codex"
    decision: CodexBoundaryDecision
    risk_level: CodexBoundaryRiskLevel
    audit_id: str
    policy_version: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    first_next_action: CodexBoundaryNextAction
    violated_rules: list[CodexBoundaryViolatedRule] = Field(default_factory=list)
    diagnostics: list[CodexBoundaryDiagnostic] = Field(default_factory=list)
    release_decision: dict[str, Any] | None = None
    trigger: dict[str, Any] | None = None
    finding_fingerprints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _action_matches_decision(self) -> CodexBoundaryResultV1:
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


__all__ = [
    "CODEX_BOUNDARY_RESULT_SCHEMA_VERSION",
    "CodexBoundaryActionKind",
    "CodexBoundaryActor",
    "CodexBoundaryDecision",
    "CodexBoundaryDiagnostic",
    "CodexBoundaryDiagnosticLevel",
    "CodexBoundaryNextAction",
    "CodexBoundaryResultV1",
    "CodexBoundaryRiskLevel",
    "CodexBoundaryViolatedRule",
]
