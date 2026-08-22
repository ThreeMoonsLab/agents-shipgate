from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Confidence, ProvenanceKind

BindingStatus = Literal["declared", "structural", "partial", "unknown", "conflicting"]
BindingEdgeType = Literal[
    "direct_tool",
    "tool_node",
    "toolset",
    "workflow",
    "subagent",
    "handoff",
]


class AgentBindingNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    name: str
    source_id: str | None = None
    source_ref: str | None = None
    source_pointer: str | None = None


class AgentToolBindingEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    tool_id: str
    edge_type: BindingEdgeType
    confidence: Confidence
    provenance_kind: ProvenanceKind
    source: str
    source_pointer: str | None = None
    complete: bool = True


class AgentHandoffBindingEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_agent_id: str
    target_agent_id: str
    edge_type: Literal["subagent", "handoff"]
    confidence: Confidence
    provenance_kind: ProvenanceKind
    source: str
    source_pointer: str | None = None
    complete: bool = True


class AgentBindingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "missing_binding_evidence",
        "partial_binding_evidence",
        "conflicting_binding_evidence",
        "ambiguous_root_agent",
        "unresolved_agent_binding",
        "unresolved_bound_tool",
        "incomplete_handoff_graph",
        "invalid_binding_annotation",
    ]
    message: str
    agent_id: str | None = None
    tool_id: str | None = None
    source: str | None = None
    source_pointer: str | None = None


class AgentBindingGraphAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_agent_id: str | None = None
    status: BindingStatus
    agents: list[AgentBindingNode] = Field(default_factory=list)
    tool_edges: list[AgentToolBindingEdge] = Field(default_factory=list)
    handoff_edges: list[AgentHandoffBindingEdge] = Field(default_factory=list)
    reachable_tool_ids: list[str] = Field(default_factory=list)
    possible_tool_ids: list[str] = Field(default_factory=list)
    unbound_tool_ids: list[str] = Field(default_factory=list)
    issues: list[AgentBindingIssue] = Field(default_factory=list)
    pass_eligible: bool = False


class BindingSurfaceDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_report_schema_version: str | None = None
    added_reachable_tool_ids: list[str] = Field(default_factory=list)
    removed_reachable_tool_ids: list[str] = Field(default_factory=list)
    # v0.35: tools this change *newly excluded* from the analysed surface —
    # head ``unbound_tool_ids`` minus base ``unbound_tool_ids``. Both halves
    # of the narrowing land here: a tool the diff added to a source and left
    # unwired, and a tool that was reachable at base and is not any more.
    #
    # It is a separate list rather than a derivation of the reachable ones
    # because it answers a different question. ``removed_reachable_tool_ids``
    # says the agent lost a capability, which is a *narrowing of what the
    # agent can do*; this says the gate lost a subject, which is a narrowing
    # of *what the gate looked at*. A pre-existing unbound catalog entry stays
    # out of both — catalog membership is not evidence of capability (#385) —
    # so this is exactly the set that arrived without ever being judged.
    added_unbound_tool_ids: list[str] = Field(default_factory=list)
    added_handoffs: list[str] = Field(default_factory=list)
    removed_handoffs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = [
    "AgentBindingGraphAssessment",
    "AgentBindingIssue",
    "AgentBindingNode",
    "AgentHandoffBindingEdge",
    "AgentToolBindingEdge",
    "BindingEdgeType",
    "BindingStatus",
    "BindingSurfaceDiff",
]
