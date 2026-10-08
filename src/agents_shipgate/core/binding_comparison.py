"""Static graph coverage required before comparing capability absence."""
from __future__ import annotations

from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment

_UNCERTAIN_ISSUES = frozenset({
    "partial_binding_evidence", "incomplete_handoff_graph", "unresolved_agent_binding",
    "unresolved_bound_tool", "conflicting_binding_evidence", "invalid_binding_annotation",
})


def binding_comparison_limits(
    head: AgentBindingGraphAssessment | None,
    base: AgentBindingGraphAssessment | None,
) -> list[str]:
    """Name incomplete sides without treating a catalog as an agent graph.

    Missing optional graphs preserve legacy fact-only comparisons. Proven
    presence is not proven absence when a reader left part of a graph unread.
    """
    notes = []
    for side, graph in (("head", head), ("base", base)):
        if graph is None:
            continue
        reasons = set()
        if graph.status in {"partial", "conflicting"}:
            reasons.add("graph_status:" + graph.status)
        if graph.possible_tool_ids:
            reasons.add("possible_tool_bindings")
        if any(not edge.complete for edge in (*graph.tool_edges, *graph.handoff_edges)):
            reasons.add("incomplete_binding_edges")
        actual_agent = any(node.kind == "agent" for node in graph.agents)
        for issue in graph.issues:
            if issue.kind in _UNCERTAIN_ISSUES or (
                actual_agent and graph.status == "unknown"
                and issue.kind in {"missing_binding_evidence", "ambiguous_root_agent"}
            ):
                location = ":".join(part for part in (issue.source, issue.source_pointer) if part)
                reasons.add(issue.kind + (" at " + location if location else ""))
        if reasons:
            notes.append(
                f"Capability comparison incomplete on {side}: " + "; ".join(sorted(reasons))
                + ". Capability additions, removals and changes were not established."
            )
    return notes
