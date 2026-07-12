"""Current machine control result shared by local check and MCP projections.

The v1 result remains readable in :mod:`agent_result_v1`.  New producers emit
this v2 shape, whose only operational authority is ``control``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.agent_control import AgentControl
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultAffectedFile,
    AgentResultAgent,
    AgentResultDecision,
    AgentResultDiagnostic,
    AgentResultPolicy,
    AgentResultRepair,
    AgentResultRiskLevel,
    AgentResultSubject,
    AgentResultTool,
    AgentResultTraceEvent,
    AgentResultViolatedRule,
)

AGENT_RESULT_SCHEMA_VERSION = "agent_result_v2"


class AgentResultV2(BaseModel):
    """Schema-enforced local operational result.

    ``decision`` is diagnostic.  Consumers authorize completion or continued
    work exclusively from ``control.state``.
    """

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
                            "repair": {"properties": {"safe_to_attempt": {"const": False}}}
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "repair": {
                                "properties": {"safe_to_attempt": {"const": True}},
                                "required": ["safe_to_attempt"],
                            }
                        },
                        "required": ["repair"],
                    },
                    "then": {
                        "properties": {
                            "control": {
                                "properties": {
                                    "state": {"const": "agent_action_required"},
                                    "next_action": {
                                        "properties": {"kind": {"const": "repair"}},
                                        "required": ["kind"],
                                    },
                                },
                                "required": ["state", "next_action"],
                            }
                        }
                    },
                },
            ]
        },
    )

    schema_version: Literal["agent_result_v2"] = AGENT_RESULT_SCHEMA_VERSION
    agent: AgentResultAgent = "codex"
    tool: AgentResultTool = Field(default_factory=AgentResultTool)
    subject: AgentResultSubject = Field(default_factory=AgentResultSubject)
    decision: AgentResultDecision
    risk_level: AgentResultRiskLevel
    audit_id: str
    policy_version: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    control: AgentControl
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

    @model_validator(mode="after")
    def _reviewers_project_control(self) -> AgentResultV2:
        expected = list(self.control.human_review.required_reviewers)
        if self.required_reviewers != expected:
            raise ValueError("required_reviewers must exactly project control.human_review")
        if self.control.state == "agent_action_required":
            if self.control.next_action.kind == "repair":
                if not self.repair.safe_to_attempt or self.repair.actor != "coding_agent":
                    raise ValueError("agent-routed repair control requires an agent-safe repair")
        elif self.repair.safe_to_attempt:
            raise ValueError(
                "an agent-safe repair requires control.state='agent_action_required' "
                "with next_action.kind='repair'"
            )
        return self


AgentResult = AgentResultV2

__all__ = ["AGENT_RESULT_SCHEMA_VERSION", "AgentResult", "AgentResultV2"]
