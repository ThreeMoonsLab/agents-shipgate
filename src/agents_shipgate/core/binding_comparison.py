"""Static graph coverage required before comparing capability absence."""
from __future__ import annotations

from collections.abc import Iterable

from agents_shipgate.core.agent_bindings import FRAMEWORK_TOOLSET_INVENTORY
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, AgentBindingIssue

_UNCERTAIN_ISSUES = frozenset({
    "partial_binding_evidence", "incomplete_handoff_graph", "unresolved_agent_binding",
    "unresolved_bound_tool", "conflicting_binding_evidence", "invalid_binding_annotation",
})

#: The issue kinds that make a resolved graph ``partial`` (see
#: ``resolve_agent_binding_graph``). The status is the sum of these.
_PARTIAL_STATUS_ISSUES = frozenset({
    "partial_binding_evidence", "incomplete_handoff_graph", "unresolved_agent_binding",
    "unresolved_bound_tool",
})

#: Every limit note starts with this, and the persisted notes are the only
#: channel a later reader of a report has. Consumers ask
#: :func:`comparison_withholds_absence` rather than matching the text.
_NOTE_PREFIX = "Capability comparison incomplete on "

_WITHHELD_WHOLE = "Capability additions, removals and changes were not established."
_WITHHELD_ABSENCE = (
    "Removed or narrowed capabilities and resolved findings were not established; "
    "additions and changes of unknown direction are still reported."
)


def _inventory_unknown(issue: AgentBindingIssue) -> bool:
    """A remote toolset whose binding was read and whose leaves cannot be listed.

    Not incomplete binding evidence: the toolset's endpoint, credential
    reference and filter are compared by their own facts, and no leaf tool is
    claimed on either side, so it can neither forge nor hide a removal.
    """
    return issue.kind == "partial_binding_evidence" and issue.source == FRAMEWORK_TOOLSET_INVENTORY


def binding_comparison_limits(
    head: AgentBindingGraphAssessment | None,
    base: AgentBindingGraphAssessment | None,
    *,
    absence_only: bool = False,
) -> list[str]:
    """Name incomplete sides without treating a catalog as an agent graph.

    Missing optional graphs preserve legacy fact-only comparisons. Proven
    presence is not proven absence when a reader left part of a graph unread.

    A toolset marked :data:`FRAMEWORK_TOOLSET_INVENTORY` is a surface nobody
    can enumerate, not a part of the binding the reader left unread: it is
    neither a limit itself nor the reason a graph reads ``partial``.
    ``absence_only`` words the note for a comparison that still reports
    what is added or changed and withholds only what depends on absence.
    """
    notes = []
    for side, graph in (("head", head), ("base", base)):
        if graph is None:
            continue
        reasons = set()
        status_causes = [issue for issue in graph.issues if issue.kind in _PARTIAL_STATUS_ISSUES]
        inventory_only = (
            graph.status == "partial" and bool(status_causes)
            and all(_inventory_unknown(issue) for issue in status_causes)
        )
        if graph.status in {"partial", "conflicting"} and not inventory_only:
            reasons.add("graph_status:" + graph.status)
        if graph.possible_tool_ids:
            reasons.add("possible_tool_bindings")
        if any(not edge.complete for edge in (*graph.tool_edges, *graph.handoff_edges)):
            reasons.add("incomplete_binding_edges")
        actual_agent = any(node.kind == "agent" for node in graph.agents)
        for issue in graph.issues:
            if _inventory_unknown(issue):
                continue
            if issue.kind in _UNCERTAIN_ISSUES or (
                actual_agent and graph.status == "unknown"
                and issue.kind in {"missing_binding_evidence", "ambiguous_root_agent"}
            ):
                location = ":".join(part for part in (issue.source, issue.source_pointer) if part)
                reasons.add(issue.kind + (" at " + location if location else ""))
        if reasons:
            notes.append(
                f"{_NOTE_PREFIX}{side}: " + "; ".join(sorted(reasons)) + ". "
                + (_WITHHELD_ABSENCE if absence_only else _WITHHELD_WHOLE)
            )
    return notes


def comparison_withholds_absence(notes: Iterable[str]) -> bool:
    """Whether persisted diff notes say this comparison's absence claims were withheld."""
    return any(note.startswith(_NOTE_PREFIX) for note in notes)
