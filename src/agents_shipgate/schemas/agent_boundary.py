"""Neutral multi-host local-boundary result contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.agent_result import AgentResultV2
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultAgent,
    AgentResultPolicy,
    AgentResultViolatedRule,
)

AGENT_BOUNDARY_RESULT_SCHEMA_VERSION = "shipgate.agent_boundary_result/v1"


class BoundaryHostCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    hosts: list[str]
    status: Literal["complete", "not_applicable", "partial", "experimental"]
    paths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class AgentBoundaryResultV1(AgentResultV2):
    """One operational result for Codex, Claude Code, and Cursor surfaces."""

    schema_version: Literal["shipgate.agent_boundary_result/v1"] = (
        AGENT_BOUNDARY_RESULT_SCHEMA_VERSION
    )
    actor: AgentResultAgent
    input_mode: Literal["worktree", "git_range", "provided_diff"]
    scope: Literal["repository"] = "repository"
    input_coverage: Literal["complete", "partial", "unknown"]
    host_coverage: list[BoundaryHostCoverage]
    affected_hosts: list[str]
    policies: list[AgentResultPolicy]
    policy_set_sha256: str
    issues: list[str] = Field(default_factory=list)
    violations: list[AgentResultViolatedRule]
    static_analysis_only: Literal[True] = True
    runtime_session_verified: Literal[False] = False
    excluded_scopes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coverage_controls_completion(self) -> AgentBoundaryResultV1:
        if self.actor != self.agent:
            raise ValueError("actor and agent must identify the same calling agent")
        if self.input_coverage != "complete" and self.control.state == "complete":
            raise ValueError("incomplete boundary input cannot allow completion")
        if self.control.state == "complete" and any(
            item.status in {"partial", "experimental"} for item in self.host_coverage
        ):
            raise ValueError("partial or experimental host coverage cannot allow completion")
        if self.violations != self.violated_rules:
            raise ValueError("violations must exactly project legacy violated_rules")
        return self


__all__ = [
    "AGENT_BOUNDARY_RESULT_SCHEMA_VERSION",
    "AgentBoundaryResultV1",
    "BoundaryHostCoverage",
]
