"""Neutral multi-host local-boundary result contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.agent_result import (
    AgentResultPendingReviewItem,
    AgentResultV2,
)
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultAgent,
    AgentResultPolicy,
    AgentResultViolatedRule,
)

AGENT_BOUNDARY_RESULT_SCHEMA_VERSION = "shipgate.agent_boundary_result/v2"


class BoundaryHostCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    hosts: list[str]
    status: Literal["complete", "not_applicable", "partial", "experimental"]
    paths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class AgentBoundaryResultV1(AgentResultV2):
    """One operational result for Codex, Claude Code, and Cursor surfaces."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    # Mirrors the Pydantic rule below so an external validator
                    # reaches the same verdict: incomplete input never carries
                    # publication authority.
                    "if": {
                        "properties": {
                            "control": {
                                "properties": {
                                    "completion_allowed": {"const": False},
                                    "permissions": {
                                        "properties": {"update_pr": {"const": True}},
                                        "required": ["update_pr"],
                                    },
                                },
                                "required": ["completion_allowed", "permissions"],
                            }
                        },
                        "required": ["control"],
                    },
                    "then": {
                        "required": ["input_coverage"],
                        "properties": {
                            "decision": {"not": {"const": "block"}},
                            "input_coverage": {"const": "complete"},
                            "host_coverage": {
                                "items": {
                                    "properties": {
                                        "status": {
                                            "enum": ["complete", "not_applicable"]
                                        }
                                    }
                                }
                            },
                        },
                    },
                }
            ]
        },
    )

    schema_version: Literal["shipgate.agent_boundary_result/v2"] = (
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
    # Additive: review obligations the graded local mapping carries forward
    # instead of stopping the turn.  Lives on this result (not the shared
    # AgentResultV2 base) so the deprecated codex-boundary v2 format stays
    # byte-frozen.
    pending_review: list[AgentResultPendingReviewItem] = Field(default_factory=list)
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
        # An outstanding review obligation contradicts completion: a graded
        # row still owes a human a look at PR time, so it must keep the agent
        # on a route rather than reading as finished.
        if self.pending_review and self.control.state == "complete":
            raise ValueError("a complete result cannot carry pending review items")
        # Publication asserts an evaluated change. Incomplete host coverage
        # means part of the surface was never read, which is the same epistemic
        # state that already forbids completion.
        if not self.control.completion_allowed and self.control.permissions.publishes:
            if self.input_coverage != "complete":
                raise ValueError(
                    "publication authority requires complete boundary input coverage"
                )
            if any(item.status in {"partial", "experimental"} for item in self.host_coverage):
                raise ValueError(
                    "partial or experimental host coverage cannot authorize publication"
                )
        return self


__all__ = [
    "AGENT_BOUNDARY_RESULT_SCHEMA_VERSION",
    "AgentBoundaryResultV1",
    "BoundaryHostCoverage",
]
