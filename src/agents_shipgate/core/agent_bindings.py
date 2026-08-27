from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from agents_shipgate.core.artifact_models import (
    ConductorArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
)
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import (
    BindingSemanticAssessment,
    LoadedToolSource,
    SemanticClaim,
    SemanticIssue,
    Tool,
)
from agents_shipgate.core.tool_identity import ToolSelectorIndex
from agents_shipgate.schemas.bindings import (
    AgentBindingGraphAssessment,
    AgentBindingIssue,
    AgentBindingNode,
    AgentHandoffBindingEdge,
    AgentToolBindingEdge,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolSourceConfig

#: ``AgentBindingIssue.source`` / ``AgentToolBindingEdge.source`` for everything
#: a ``tool_sources[].binding`` declaration produces. Routing on this typed
#: value rather than on the pointer's text is what lets the pointer be a plain
#: manifest location and the prose be rewritten freely (#329).
TOOL_SOURCE_BINDING_DECLARATION = "tool_source_binding_declaration"

_TOOL_EDGE_TYPES = {
    "direct_tool",
    "tool_node",
    "toolset",
    "workflow",
}
_HANDOFF_EDGE_TYPES = {"subagent", "handoff"}


@dataclass(frozen=True)
class _AgentObservation:
    name: str
    source_id: str | None
    source_ref: str | None
    source_pointer: str | None
    complete: bool
    #: A published tool surface declared under ``tool_sources[].binding``
    #: rather than an agent object anyone observed (#432).
    tool_source_surface: bool = False

    @property
    def agent_id(self) -> str:
        payload: dict[str, object] = {
            "name": self.name,
            "source_id": self.source_id,
            "source_ref": None if self.source_id else self.source_ref,
        }
        # Only surfaces carry the extra key, so every id an agent observation
        # has ever produced is byte-identical to what it produced before. It is
        # carried at all because a ``tool_sources[].id`` and an agent name are
        # independent repository-chosen namespaces: without it, a Google ADK
        # source ``orders`` holding an agent named ``orders`` would dedupe the
        # surface and the agent into one node and silently bind the source's
        # whole catalog to that agent. A prescribed fix whose own side effect
        # rewires the graph is the #385 class.
        if self.tool_source_surface:
            payload["tool_source_surface"] = True
        return f"agent_v1:{hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]}"


@dataclass(frozen=True)
class _RawToolEdge:
    agent_name: str
    source_id: str | None
    tool_name: str
    edge_type: str
    source: str
    source_pointer: str | None
    complete: bool


@dataclass(frozen=True)
class _RawHandoffEdge:
    source_agent: str
    target_agent: str
    source_id: str | None
    edge_type: str
    source: str
    source_pointer: str | None
    complete: bool


def resolve_agent_binding_graph(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    artifacts: ArtifactBag,
    loaded_sources: list[LoadedToolSource] | None = None,
) -> tuple[AgentBindingGraphAssessment, list[Tool]]:
    """Resolve the exact static graph rooted at the configured agent.

    Catalog membership is deliberately not evidence. Only reviewed manifest
    declarations and framework-specific structural observations create edges.
    """

    (
        agent_observations,
        raw_tool_edges,
        raw_handoffs,
        partials,
        invalid_annotations,
    ) = _observations(tools, artifacts, loaded_sources or [])
    declarations = manifest.agent_bindings.declarations
    # A declaration may introduce an agent the extractors never saw — a
    # decorator-only repository has no agent object to observe. It must NOT
    # introduce a second node for one the extractors *did* see: the seeded
    # node carries no ``source_id``, so it lands under a different dedupe key,
    # and ``_resolve_agent`` then finds two nodes for the name and resolves
    # neither. Declaring a structurally known agent used to be self-defeating
    # for exactly that reason — the resolver rejected names its own scanner
    # had emitted (#385).
    observed_names = {observation.name for observation in agent_observations}
    for declaration in declarations:
        for name in (
            *([] if declaration.agent == "root" else [declaration.agent]),
            *declaration.handoffs,
        ):
            if name in observed_names:
                continue
            observed_names.add(name)
            agent_observations.append(
                _AgentObservation(name, None, "shipgate.yaml", None, True)
            )

    # Added after the declaration seeding above, never before it: a surface is
    # named by a ``tool_sources[].id``, and letting that name into
    # ``observed_names`` would suppress the seed for an ``agent_bindings``
    # declaration that happens to share it.
    surfaces = _declared_source_surfaces(manifest)
    surface_agent_ids = frozenset(
        observation.agent_id for _, _, observation in surfaces
    )
    agent_observations.extend(observation for _, _, observation in surfaces)

    agents = _dedupe_agents(agent_observations)
    root, root_issues = _select_root(manifest, agents, raw_handoffs, surface_agent_ids)
    if root is not None and all(agent.agent_id != root.agent_id for agent in agents):
        agents.append(root)
    issues: list[AgentBindingIssue] = list(root_issues)
    issues.extend(
        AgentBindingIssue(
            kind="invalid_binding_annotation",
            message=message,
            source="framework_extraction",
        )
        for message in sorted(invalid_annotations)
    )
    selector_index = ToolSelectorIndex.build(tools)
    # Surfaces are excluded from name resolution deliberately. Adding a
    # ``binding`` block must not change how any name already resolved: a
    # ``tool_sources[].id`` sharing a name with an observed agent would
    # otherwise make that name ambiguous and break the declaration that used
    # to resolve it (#385).
    by_agent_name = _agents_by_name(
        [agent for agent in agents if agent.agent_id not in surface_agent_ids]
    )
    tool_edges: list[AgentToolBindingEdge] = []
    handoff_edges: list[AgentHandoffBindingEdge] = []

    for raw in sorted(
        raw_tool_edges,
        key=lambda item: (
            item.agent_name,
            item.source_id or "",
            item.tool_name,
            item.source,
            item.source_pointer or "",
        ),
    ):
        agent = _resolve_agent(raw.agent_name, raw.source_id, by_agent_name)
        if agent is None:
            issues.append(
                AgentBindingIssue(
                    kind="unresolved_agent_binding",
                    message=f"Binding references unresolved agent {raw.agent_name!r}.",
                    source=raw.source,
                    source_pointer=raw.source_pointer,
                )
            )
            continue
        matches = [
            tool
            for tool in tools
            if tool.name == raw.tool_name
            and (
                raw.source_id is None
                or tool.source_id == raw.source_id
                or tool.provider == raw.source_id
                or tool.annotations.get("adk_agent_source_id") == raw.source_id
                or tool.annotations.get("n8n_workflow_id") == raw.source_id
            )
        ]
        if len(matches) != 1:
            issues.append(
                AgentBindingIssue(
                    kind="unresolved_bound_tool",
                    message=(
                        f"Structural binding for {raw.tool_name!r} matched "
                        f"{len(matches)} canonical tools."
                    ),
                    agent_id=agent.agent_id,
                    source=raw.source,
                    source_pointer=raw.source_pointer,
                )
            )
            continue
        if raw.edge_type not in _TOOL_EDGE_TYPES:
            issues.append(
                AgentBindingIssue(
                    kind="invalid_binding_annotation",
                    message=f"Unknown tool binding edge type {raw.edge_type!r}.",
                    agent_id=agent.agent_id,
                    tool_id=matches[0].id,
                    source=raw.source,
                    source_pointer=raw.source_pointer,
                )
            )
            continue
        tool_edges.append(
            AgentToolBindingEdge(
                agent_id=agent.agent_id,
                tool_id=matches[0].id,
                edge_type=raw.edge_type,
                confidence="high" if raw.complete else "medium",
                provenance_kind="ast_extraction",
                source=raw.source,
                source_pointer=raw.source_pointer,
                complete=raw.complete,
            )
        )

    for raw in raw_handoffs:
        source_agent = _resolve_agent(raw.source_agent, raw.source_id, by_agent_name)
        target_agent = _resolve_agent(raw.target_agent, raw.source_id, by_agent_name)
        if source_agent is None or target_agent is None:
            issues.append(
                AgentBindingIssue(
                    kind="incomplete_handoff_graph",
                    message=(
                        f"Handoff {raw.source_agent!r} -> {raw.target_agent!r} "
                        "could not be resolved exactly."
                    ),
                    source=raw.source,
                    source_pointer=raw.source_pointer,
                )
            )
            continue
        if raw.edge_type not in _HANDOFF_EDGE_TYPES:
            issues.append(
                AgentBindingIssue(
                    kind="invalid_binding_annotation",
                    message=f"Unknown handoff edge type {raw.edge_type!r}.",
                    agent_id=source_agent.agent_id,
                    source=raw.source,
                    source_pointer=raw.source_pointer,
                )
            )
            continue
        handoff_edges.append(
            AgentHandoffBindingEdge(
                source_agent_id=source_agent.agent_id,
                target_agent_id=target_agent.agent_id,
                edge_type=raw.edge_type,
                confidence="high" if raw.complete else "medium",
                provenance_kind="ast_extraction",
                source=raw.source,
                source_pointer=raw.source_pointer,
                complete=raw.complete,
            )
        )

    structural_by_agent: dict[str, set[str]] = defaultdict(set)
    structural_complete: dict[str, bool] = defaultdict(lambda: True)
    for edge in tool_edges:
        structural_by_agent[edge.agent_id].add(edge.tool_id)
        structural_complete[edge.agent_id] &= edge.complete
    structural_handoffs: dict[str, set[str]] = defaultdict(set)
    structural_handoffs_complete: dict[str, bool] = defaultdict(lambda: True)
    for edge in handoff_edges:
        structural_handoffs[edge.source_agent_id].add(edge.target_agent_id)
        structural_handoffs_complete[edge.source_agent_id] &= edge.complete

    for index, declaration in enumerate(declarations):
        agent = root if declaration.agent == "root" else _resolve_agent(
            declaration.agent, None, by_agent_name
        )
        pointer = f"/agent_bindings/declarations/{index}"
        if agent is None:
            issues.append(
                AgentBindingIssue(
                    kind="unresolved_agent_binding",
                    message=_unresolved_agent_message(
                        "Declaration references agent",
                        declaration.agent,
                        by_agent_name,
                    ),
                    source="shipgate.yaml",
                    source_pointer=f"{pointer}/agent",
                )
            )
            continue
        declared_ids: set[str] = set()
        for selector_index_value, selector in enumerate(declaration.tools):
            result = selector_index.resolve(selector)
            if not result.resolved:
                issues.append(
                    AgentBindingIssue(
                        kind="unresolved_bound_tool",
                        message=result.message or "Binding selector did not resolve exactly.",
                        agent_id=agent.agent_id,
                        source="shipgate.yaml",
                        source_pointer=f"{pointer}/tools/{selector_index_value}",
                    )
                )
                continue
            tool = result.matches[0]
            declared_ids.add(tool.id)
            tool_edges.append(
                AgentToolBindingEdge(
                    agent_id=agent.agent_id,
                    tool_id=tool.id,
                    edge_type="direct_tool",
                    confidence="high",
                    provenance_kind="static_declaration",
                    source="agent_binding_declaration",
                    source_pointer=f"{pointer}/tools/{selector_index_value}",
                    complete=True,
                )
            )
        structural_ids = structural_by_agent.get(agent.agent_id, set())
        if structural_ids and structural_complete[agent.agent_id] and declared_ids != structural_ids:
            issues.append(
                AgentBindingIssue(
                    kind="conflicting_binding_evidence",
                    message=(
                        f"Closed-world declaration for {declaration.agent!r} does not "
                        "match the complete structural tool set."
                    ),
                    agent_id=agent.agent_id,
                    source="shipgate.yaml",
                    source_pointer=pointer,
                )
            )
        declared_handoff_ids: set[str] = set()
        for handoff_index, target_name in enumerate(declaration.handoffs):
            target = _resolve_agent(target_name, None, by_agent_name)
            if target is None:
                issues.append(
                    AgentBindingIssue(
                        kind="unresolved_agent_binding",
                        message=_unresolved_agent_message(
                            "Declaration references handoff target",
                            target_name,
                            by_agent_name,
                        ),
                        agent_id=agent.agent_id,
                        source="shipgate.yaml",
                        source_pointer=f"{pointer}/handoffs/{handoff_index}",
                    )
                )
                continue
            declared_handoff_ids.add(target.agent_id)
            handoff_edges.append(
                AgentHandoffBindingEdge(
                    source_agent_id=agent.agent_id,
                    target_agent_id=target.agent_id,
                    edge_type="handoff",
                    confidence="high",
                    provenance_kind="static_declaration",
                    source="agent_binding_declaration",
                    source_pointer=f"{pointer}/handoffs/{handoff_index}",
                    complete=True,
                )
            )
        structural_target_ids = structural_handoffs.get(agent.agent_id, set())
        if (
            structural_target_ids
            and structural_handoffs_complete[agent.agent_id]
            and declared_handoff_ids != structural_target_ids
        ):
            issues.append(
                AgentBindingIssue(
                    kind="conflicting_binding_evidence",
                    message=(
                        f"Closed-world declaration for {declaration.agent!r} does not "
                        "match the complete structural handoff set."
                    ),
                    agent_id=agent.agent_id,
                    source="shipgate.yaml",
                    source_pointer=pointer,
                )
            )

    for source, pointer, observation in surfaces:
        bound = [tool for tool in tools if source.id in tool.configured_source_ids]
        if not bound:
            # A reviewed declaration that binds nothing is not a no-op: with
            # the surface seeded as an entry point, staying silent would report
            # a proven binding graph over an empty analysed surface. It fails
            # closed and names the source, because the repair is in what that
            # source reads, not in this block's wording.
            issues.append(
                AgentBindingIssue(
                    kind="missing_binding_evidence",
                    message=(
                        f"The reviewed binding declaration on tool source "
                        f"{source.id!r} binds no tool, because that source "
                        f"contributed nothing to the tool catalog."
                    ),
                    agent_id=observation.agent_id,
                    source=TOOL_SOURCE_BINDING_DECLARATION,
                    source_pointer=pointer,
                )
            )
            continue
        for tool in bound:
            tool_edges.append(
                AgentToolBindingEdge(
                    agent_id=observation.agent_id,
                    tool_id=tool.id,
                    edge_type="direct_tool",
                    confidence="high",
                    provenance_kind="static_declaration",
                    source=TOOL_SOURCE_BINDING_DECLARATION,
                    source_pointer=pointer,
                    complete=True,
                )
            )

    if partials:
        for partial in sorted(partials):
            issues.append(
                AgentBindingIssue(
                    kind="partial_binding_evidence",
                    message=partial,
                    source="framework_extraction",
                )
            )

    tool_edges = _dedupe_tool_edges(tool_edges)
    handoff_edges = _dedupe_handoff_edges(handoff_edges)
    reachable_agents, paths = _reachable_agents(root, handoff_edges, surface_agent_ids)
    for edge in handoff_edges:
        if edge.source_agent_id in reachable_agents and not edge.complete:
            issues.append(
                AgentBindingIssue(
                    kind="incomplete_handoff_graph",
                    message=(
                        "A handoff from a reachable agent is incomplete; its downstream "
                        "capability surface cannot be proven."
                    ),
                    agent_id=edge.source_agent_id,
                    source=edge.source,
                    source_pointer=edge.source_pointer,
                )
            )
    reachable_ids = sorted(
        {
            edge.tool_id
            for edge in tool_edges
            if edge.agent_id in reachable_agents and edge.complete
        }
    )
    possible_ids = sorted(
        {
            edge.tool_id
            for edge in tool_edges
            if edge.agent_id in reachable_agents and not edge.complete
        }
        - set(reachable_ids)
    )
    unbound_ids = sorted(set(selector_index.by_id) - set(reachable_ids) - set(possible_ids))

    # An incomplete positive edge means the tool may be reachable. It is not
    # part of the proven capability surface, but it must still fail closed as
    # a first-class evidence gap rather than disappearing from assessment.
    incomplete_by_tool = {
        edge.tool_id: edge
        for edge in tool_edges
        if edge.agent_id in reachable_agents and not edge.complete
    }
    for tool_id in possible_ids:
        edge = incomplete_by_tool[tool_id]
        issues.append(
            AgentBindingIssue(
                kind="partial_binding_evidence",
                message=(
                    "A possibly reachable tool has an incomplete binding edge; "
                    "provide a complete static graph or reviewed declaration."
                ),
                agent_id=edge.agent_id,
                tool_id=tool_id,
                source=edge.source,
                source_pointer=edge.source_pointer,
            )
        )

    # A graph is rooted by a selected agent or by a declared published surface.
    # Both are entry points; only the first has a name to report.
    rooted = root is not None or bool(surface_agent_ids)
    if root is not None and not declarations and not surfaces and not tool_edges and tools:
        issues.append(
            AgentBindingIssue(
                kind="missing_binding_evidence",
                message=(
                    "Tools were extracted into the catalog, but no static edge binds "
                    "them to the configured root agent."
                ),
                agent_id=root.agent_id,
            )
        )
    if not rooted and tools and not issues:
        issues.append(
            AgentBindingIssue(
                kind="ambiguous_root_agent",
                message="The root agent could not be identified unambiguously.",
            )
        )

    conflict = any(issue.kind == "conflicting_binding_evidence" for issue in issues)
    partial = any(
        issue.kind
        in {
            "partial_binding_evidence",
            "incomplete_handoff_graph",
            "unresolved_agent_binding",
            "unresolved_bound_tool",
        }
        for issue in issues
    )
    declared = bool(declarations or surfaces) and not conflict and not partial and rooted
    structural = bool(tool_edges or (rooted and not tools)) and not issues
    status = (
        "conflicting"
        if conflict
        else "partial"
        if partial
        else "declared"
        if declared
        else "structural"
        if structural
        else "unknown"
    )
    pass_eligible = rooted and not issues and status in {"declared", "structural"}

    graph = AgentBindingGraphAssessment(
        root_agent_id=root.agent_id if root else None,
        status=status,
        agents=sorted(agents, key=lambda item: item.agent_id),
        tool_edges=tool_edges,
        handoff_edges=handoff_edges,
        reachable_tool_ids=reachable_ids,
        possible_tool_ids=possible_ids,
        unbound_tool_ids=unbound_ids,
        issues=sorted(
            issues,
            key=lambda item: (
                item.kind,
                item.agent_id or "",
                item.tool_id or "",
                item.source or "",
                item.source_pointer or "",
                item.message,
            ),
        ),
        pass_eligible=pass_eligible,
    )
    _attach_binding_assessments(tools, graph, paths)
    return graph, tools


def _observations(
    tools: list[Tool], artifacts: ArtifactBag, loaded_sources: list[LoadedToolSource]
) -> tuple[
    list[_AgentObservation],
    list[_RawToolEdge],
    list[_RawHandoffEdge],
    set[str],
    set[str],
]:
    agents: list[_AgentObservation] = []
    edges: list[_RawToolEdge] = []
    handoffs: list[_RawHandoffEdge] = []
    partials: set[str] = set()
    invalid_annotations: set[str] = set()

    for loaded in loaded_sources:
        for observation in loaded.binding_observations:
            complete = observation.tools_complete and observation.handoffs_complete
            agents.append(
                _AgentObservation(
                    observation.agent,
                    observation.source_id,
                    observation.source,
                    observation.source_pointer,
                    complete,
                )
            )
            for tool_name in observation.tool_names:
                edges.append(
                    _RawToolEdge(
                        observation.agent,
                        observation.source_id,
                        tool_name,
                        "direct_tool",
                        observation.source,
                        observation.source_pointer,
                        observation.tools_complete,
                    )
                )
            for target_agent in observation.handoff_names:
                agents.append(
                    _AgentObservation(
                        target_agent,
                        observation.source_id,
                        observation.source,
                        observation.source_pointer,
                        observation.handoffs_complete,
                    )
                )
                handoffs.append(
                    _RawHandoffEdge(
                        observation.agent,
                        target_agent,
                        observation.source_id,
                        "handoff",
                        observation.source,
                        observation.source_pointer,
                        observation.handoffs_complete,
                    )
                )
            partials.update(observation.issues)

    for tool in tools:
        for reason in _string_list(tool.annotations.get("binding_surface_partial")):
            partials.add(reason)
        raw_bindings = tool.annotations.get("agent_bindings")
        if raw_bindings is not None and not isinstance(raw_bindings, list):
            invalid_annotations.add(
                f"Tool {tool.name!r} has a non-list structural binding annotation."
            )
        if isinstance(raw_bindings, list):
            for raw in raw_bindings:
                if not isinstance(raw, dict) or not isinstance(raw.get("agent"), str):
                    invalid_annotations.add(
                        f"Tool {tool.name!r} has a malformed structural binding annotation."
                    )
                    continue
                source_id = raw.get("source_id") if isinstance(raw.get("source_id"), str) else tool.source_id
                source = str(raw.get("source") or tool.source_ref or tool.source_type)
                pointer = raw.get("source_pointer") if isinstance(raw.get("source_pointer"), str) else tool.source_location
                raw_complete = raw.get("complete", True)
                if type(raw_complete) is not bool:
                    invalid_annotations.add(
                        f"Tool {tool.name!r} has a non-boolean binding completeness annotation."
                    )
                    continue
                complete = raw_complete
                agents.append(_AgentObservation(raw["agent"], source_id, source, pointer, complete))
                edges.append(
                    _RawToolEdge(
                        raw["agent"],
                        source_id,
                        tool.name,
                        str(raw.get("edge_type") or "direct_tool"),
                        source,
                        pointer,
                        complete,
                    )
                )
        raw_handoffs = tool.annotations.get("agent_handoffs")
        if raw_handoffs is not None and not isinstance(raw_handoffs, list):
            invalid_annotations.add(
                f"Tool {tool.name!r} has a non-list handoff annotation."
            )
        if isinstance(raw_handoffs, list):
            for raw in raw_handoffs:
                if not isinstance(raw, dict):
                    invalid_annotations.add(
                        f"Tool {tool.name!r} has a malformed handoff annotation."
                    )
                    continue
                source_agent = raw.get("source_agent")
                target_agent = raw.get("target_agent")
                if not isinstance(source_agent, str) or not isinstance(target_agent, str):
                    invalid_annotations.add(
                        f"Tool {tool.name!r} has a handoff without exact agent names."
                    )
                    continue
                raw_complete = raw.get("complete", True)
                if type(raw_complete) is not bool:
                    invalid_annotations.add(
                        f"Tool {tool.name!r} has a non-boolean handoff completeness annotation."
                    )
                    continue
                handoffs.append(
                    _RawHandoffEdge(
                        source_agent,
                        target_agent,
                        _str(raw.get("source_id")) or tool.source_id,
                        str(raw.get("edge_type") or "handoff"),
                        str(raw.get("source") or tool.source_ref or tool.source_type),
                        _str(raw.get("source_pointer")) or tool.source_location,
                        raw_complete,
                    )
                )
        adk_agent = tool.annotations.get("adk_agent_name")
        if isinstance(adk_agent, str):
            adk_source_id = _str(tool.annotations.get("adk_agent_source_id")) or tool.source_id
            agents.append(_AgentObservation(adk_agent, adk_source_id, tool.source_ref, tool.source_location, True))
            edges.append(_RawToolEdge(adk_agent, adk_source_id, tool.name, "direct_tool", tool.source_ref or "google_adk", tool.source_location, True))
        if tool.source_type == "codex_plugin_mcp_inventory":
            plugin = _str(tool.annotations.get("codex_plugin"))
            server = _str(tool.annotations.get("codex_plugin_mcp_server"))
            if plugin and server:
                agent_name = f"{plugin}/{server}"
                agents.append(
                    _AgentObservation(
                        agent_name,
                        tool.source_id,
                        tool.source_ref,
                        tool.source_location,
                        True,
                    )
                )
                edges.append(
                    _RawToolEdge(
                        agent_name,
                        tool.source_id,
                        tool.name,
                        "direct_tool",
                        tool.source_ref or "codex_plugin_mcp_inventory",
                        tool.source_location,
                        True,
                    )
                )

    langchain = artifacts.get("langchain", LangChainArtifacts)
    if langchain:
        for record in langchain.agent_bindings:
            name = str(record.get("agent") or "root")
            source_ref = _str(record.get("source_ref"))
            agents.append(_AgentObservation(name, _str(record.get("source_id")), source_ref, _line_pointer(record), True))
            for tool_name in _string_list(record.get("tools")):
                edges.append(_RawToolEdge(name, _str(record.get("source_id")), tool_name, "tool_node" if record.get("kind") == "tool_node" else "direct_tool", source_ref or "langchain", _line_pointer(record), True))
        for record in langchain.tool_nodes:
            name = str(record.get("agent") or record.get("name") or "root")
            source_ref = _str(record.get("source_ref"))
            agents.append(_AgentObservation(name, _str(record.get("source_id")), source_ref, _line_pointer(record), True))
            for tool_name in _string_list(record.get("tools")):
                edges.append(_RawToolEdge(name, _str(record.get("source_id")), tool_name, "tool_node", source_ref or "langchain", _line_pointer(record), True))
        partials.update(str(item.get("reason")) for item in langchain.dynamic_tool_surfaces if item.get("reason"))

    crewai = artifacts.get("crewai", CrewAiArtifacts)
    if crewai:
        for record in crewai.agents:
            name = str(record.get("name") or "root")
            source_ref = _str(record.get("source_ref"))
            agents.append(_AgentObservation(name, _str(record.get("source_id")), source_ref, _line_pointer(record), True))
            for tool_name in _string_list(record.get("tools")):
                edges.append(_RawToolEdge(name, _str(record.get("source_id")), tool_name, "direct_tool", source_ref or "crewai", _line_pointer(record), True))
        partials.update(str(item.get("reason")) for item in crewai.dynamic_tool_surfaces if item.get("reason"))
        for record in crewai.crews:
            crew_name = str(record.get("name") or "crew")
            source_id = _str(record.get("source_id"))
            source_ref = _str(record.get("source_ref"))
            agents.append(_AgentObservation(crew_name, source_id, source_ref, _line_pointer(record), True))
            for member in _string_list(record.get("agents")):
                handoffs.append(
                    _RawHandoffEdge(
                        crew_name,
                        member,
                        source_id,
                        "subagent",
                        source_ref or "crewai",
                        _line_pointer(record),
                        True,
                    )
                )

    adk = artifacts.get("google_adk", GoogleAdkArtifacts)
    if adk:
        for record in adk.agents:
            name = str(record.get("name") or "root")
            agents.append(_AgentObservation(name, _str(record.get("source_id")), _str(record.get("source_ref")), _str(record.get("source_ref")), True))
        for record in adk.sub_agents:
            source_name = _str(record.get("agent_name"))
            for target in _string_list(record.get("sub_agents")):
                if source_name:
                    agents.append(_AgentObservation(target, _str(record.get("source_id")), _str(record.get("source_ref")), _str(record.get("source_ref")), True))
                    handoffs.append(_RawHandoffEdge(source_name, target, _str(record.get("source_id")), "subagent", _str(record.get("source_ref")) or "google_adk", _str(record.get("source_ref")), True))
            # Only Python-entrypoint records carry ``sub_agent_count``; Agent
            # Config records describe sub-agents by config path instead.
            if "sub_agent_count" not in record:
                continue
            # A sub-agent the extractor could name but not match to an agent
            # definition — imported from a module this scan does not read, or
            # rebound ambiguously — is a branch of the capability surface
            # nobody followed. Left unsaid, an imported sub-agent owning a
            # delete tool produced a `structural` graph with `pass_eligible`
            # and no trace of the tool anywhere (#385 review).
            unresolved = _string_list(record.get("unresolved_sub_agents"))
            if unresolved:
                # Deliberately says "could not match", not "no such agent":
                # the agent may well be in the report under its own name,
                # reached through a source this entrypoint cannot resolve. The
                # claim is about the spelling, and it must not read as a claim
                # the agent does not exist.
                partials.add(
                    f"Google ADK agent {source_name!r} hands off to "
                    f"{', '.join(sorted(unresolved))}, which this scan could not "
                    "match to an agent definition; that sub-agent's tools were "
                    "not analyzed."
                )
            # Comparing counts, rather than testing for an empty name list, is
            # what makes a partially enumerated list fail closed: naming two of
            # three sub-agents used to report the two as the whole handoff set.
            declared_count = record.get("sub_agent_count")
            if not isinstance(declared_count, int) or declared_count > len(
                _string_list(record.get("sub_agents"))
            ) + len(unresolved):
                partials.add(f"Google ADK agent {source_name!r} has sub-agents that were not statically named.")
        for toolset in adk.toolsets:
            if toolset.dynamic or not toolset.resolved:
                partials.add(f"Google ADK toolset {toolset.name or toolset.kind!r} is not statically enumerable.")

    n8n = artifacts.get("n8n", N8nArtifacts)
    if n8n:
        for record in n8n.ai_agents:
            agents.append(_AgentObservation(str(record.get("name") or record.get("node_id") or "root"), _str(record.get("workflow_id")), _str(record.get("source_ref")), _str(record.get("source_pointer")), True))
        partials.update(str(item.get("reason")) for item in n8n.dynamic_tool_surfaces if item.get("reason"))

    conductor = artifacts.get("conductor", ConductorArtifacts)
    if conductor and conductor.dynamic_tool_surfaces:
        partials.add(
            "Conductor workflow tool bindings include dynamic or unresolved surfaces."
        )

    return agents, edges, handoffs, partials, invalid_annotations


def _declared_source_surfaces(
    manifest: AgentsShipgateManifest,
) -> list[tuple[ToolSourceConfig, str, _AgentObservation]]:
    """Every ``tool_sources[]`` entry whose published surface is reviewed.

    One node per declared source rather than one shared node, because a
    repository may publish two servers and each is its own reviewed surface —
    electing one of them the root of the other would be a claim nobody made.
    The node is named and keyed by the configured source id so
    ``agent_bindings.root: {object: <id>, source_id: <id>}`` — the spelling a
    reader reaches for — resolves to it.
    """

    return [
        (
            source,
            f"/tool_sources/{index}/binding",
            _AgentObservation(
                name=source.id,
                source_id=source.id,
                source_ref="shipgate.yaml",
                source_pointer=f"/tool_sources/{index}/binding",
                complete=True,
                tool_source_surface=True,
            ),
        )
        for index, source in enumerate(manifest.tool_sources)
        if source.binding is not None
    ]


def _select_root(
    manifest: AgentsShipgateManifest,
    agents: list[AgentBindingNode],
    raw_handoffs: list[_RawHandoffEdge],
    surface_agent_ids: frozenset[str] = frozenset(),
) -> tuple[AgentBindingNode | None, list[AgentBindingIssue]]:
    """Pick the one agent the reviewed graph is rooted at, or say why not.

    A ``tool_sources[].binding`` surface is a node in the graph but it is not
    an agent object, so it is deliberately invisible to every *heuristic* here:
    those exist to find the one agent a framework wired, and a published tool
    surface is neither wired nor an alternative to one. It stays selectable by
    an explicit selector, because ``tool_sources[].id`` is a name the manifest
    itself spells and ``root: {object: <id>, source_id: <id>}`` is the spelling
    a reader reaches for first (#432).

    When nothing observed an agent and the surfaces are the whole reviewed
    graph, there is no root to name and no ambiguity to report: one surface
    becomes the root, several leave it unset, and the surfaces root the graph
    either way.
    """

    root_config = manifest.agent_bindings.root
    observed = [agent for agent in agents if agent.agent_id not in surface_agent_ids]
    surfaces = [agent for agent in agents if agent.agent_id in surface_agent_ids]
    candidates = observed
    if root_config is not None:
        candidates = [agent for agent in agents if agent.name == root_config.object]
        if root_config.source_id:
            candidates = [agent for agent in candidates if agent.source_id == root_config.source_id]
    elif manifest.agent.sdk and manifest.agent.sdk.object:
        candidates = [agent for agent in observed if agent.name == manifest.agent.sdk.object]
    elif len(manifest.agent_bindings.declarations) == 1:
        declared = manifest.agent_bindings.declarations[0].agent
        if declared == "root":
            if len(observed) == 1:
                return observed[0], []
            synthetic = AgentBindingNode(
                agent_id=_AgentObservation("root", None, "shipgate.yaml", None, True).agent_id,
                name="root",
                source_ref="shipgate.yaml",
            )
            return synthetic, []
        candidates = [agent for agent in observed if agent.name == declared]
    else:
        target_names = {edge.target_agent for edge in raw_handoffs}
        top_level = [agent for agent in observed if agent.name not in target_names]
        if len(top_level) == 1:
            candidates = top_level
        elif not observed and surfaces:
            return (surfaces[0] if len(surfaces) == 1 else None), []
    if len(candidates) == 1:
        return candidates[0], []
    return None, [
        AgentBindingIssue(
            kind="ambiguous_root_agent",
            message=_root_selection_message(candidates, agents),
            source="shipgate.yaml",
        )
    ]


def _root_selection_message(
    candidates: list[AgentBindingNode],
    agents: list[AgentBindingNode],
) -> str:
    """Say which of the three root failures happened, and never invent a selector.

    "No root agent matched the configured selector" was reported for a scan
    that configured no selector at all, and for a catalog — a checked-in MCP
    tool export, an OpenAPI spec — that produces no code objects for any
    selector to match. It sent two separate adoption walks hunting for a value
    that cannot exist (#432).

    The empty case is judged on the whole graph rather than on observed agents
    alone: a declared source surface is a name the selector could have matched,
    so a repository that has one has a selector problem, not a nothing-to-select
    problem, and telling it that nothing was observed would be true and useless.
    """

    if candidates:
        return f"Root selector matched {len(candidates)} agents."
    if agents:
        return "No entry in the binding graph matched the configured root selector."
    return (
        "No agent object was observed anywhere in this scan, so no "
        "agent_bindings.root selector can match one. State the reviewed "
        "surface in shipgate.yaml instead: tool_sources[].binding for a source "
        "whose published tools are the surface under review, or one "
        "agent_bindings.declarations entry naming agent: root."
    )


def _dedupe_agents(observations: list[_AgentObservation]) -> list[AgentBindingNode]:
    # The key is exactly what ``agent_id`` hashes, surface flag included, so
    # two observations dedupe if and only if they are one node.
    by_key: dict[tuple[str, str | None, str | None, bool], _AgentObservation] = {}
    for observation in observations:
        by_key[
            (
                observation.name,
                observation.source_id,
                None if observation.source_id else observation.source_ref,
                observation.tool_source_surface,
            )
        ] = observation
    return [
        AgentBindingNode(
            agent_id=observation.agent_id,
            name=observation.name,
            source_id=observation.source_id,
            source_ref=observation.source_ref,
            source_pointer=observation.source_pointer,
        )
        for observation in by_key.values()
    ]


def _agents_by_name(agents: list[AgentBindingNode]) -> dict[str, list[AgentBindingNode]]:
    result: dict[str, list[AgentBindingNode]] = defaultdict(list)
    for agent in agents:
        result[agent.name].append(agent)
    return result


def _unresolved_agent_message(
    subject: str,
    name: str,
    by_name: dict[str, list[AgentBindingNode]],
) -> str:
    """Say which of the two failures happened: unknown name, or ambiguous one.

    They need opposite repairs. "Unresolved" alone sent a reader hunting for a
    spelling mistake even when the name was spelled exactly as the scan
    reported it and the real problem was two agents sharing it.
    """

    candidates = by_name.get(name, [])
    if len(candidates) > 1:
        sources = ", ".join(
            sorted({agent.source_id or agent.source_ref or "unknown source" for agent in candidates})
        )
        return (
            f"{subject} {name!r}, which names {len(candidates)} agents "
            f"in the binding graph ({sources}); the name is ambiguous."
        )
    return f"{subject} {name!r}, which no agent in the binding graph matches."


def _resolve_agent(name: str, source_id: str | None, by_name: dict[str, list[AgentBindingNode]]) -> AgentBindingNode | None:
    candidates = by_name.get(name, [])
    if source_id is not None:
        candidates = [agent for agent in candidates if agent.source_id == source_id]
    return candidates[0] if len(candidates) == 1 else None


def _reachable_agents(
    root: AgentBindingNode | None,
    handoffs: list[AgentHandoffBindingEdge],
    surface_agent_ids: frozenset[str] = frozenset(),
) -> tuple[set[str], dict[str, list[str]]]:
    """Walk the complete handoff graph from every declared entry point.

    A ``tool_sources[].binding`` surface is an entry point in its own right: a
    published tool server has no root agent to be reached *from*, and a
    repository may publish more than one. Seeding them alongside the root is
    what lets several declared surfaces coexist without one of them having to
    be elected the root of the others (#432).
    """

    seeds = sorted(
        ({root.agent_id} if root is not None else set()) | set(surface_agent_ids)
    )
    if not seeds:
        return set(), {}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in handoffs:
        if edge.complete:
            outgoing[edge.source_agent_id].append(edge.target_agent_id)
    reachable = set(seeds)
    paths = {seed: [seed] for seed in seeds}
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        for target in sorted(outgoing.get(current, [])):
            if target in reachable:
                continue
            reachable.add(target)
            paths[target] = [*paths[current], target]
            queue.append(target)
    return reachable, paths


def _attach_binding_assessments(
    tools: list[Tool],
    graph: AgentBindingGraphAssessment,
    agent_paths: dict[str, list[str]],
) -> None:
    edges_by_tool: dict[str, list[AgentToolBindingEdge]] = defaultdict(list)
    for edge in graph.tool_edges:
        if edge.tool_id in graph.reachable_tool_ids:
            edges_by_tool[edge.tool_id].append(edge)
    graph_issue_by_tool: dict[str, list[AgentBindingIssue]] = defaultdict(list)
    graph_issue_by_agent: dict[str, list[AgentBindingIssue]] = defaultdict(list)
    for issue in graph.issues:
        if issue.tool_id:
            graph_issue_by_tool[issue.tool_id].append(issue)
        if issue.agent_id:
            graph_issue_by_agent[issue.agent_id].append(issue)
    for tool in tools:
        edges = sorted(
            edges_by_tool.get(tool.id, []),
            key=lambda edge: (edge.agent_id, edge.source, edge.source_pointer or ""),
        )
        if edges:
            edge = edges[0]
            claims = [
                SemanticClaim(
                    dimension="binding",
                    value=f"{item.agent_id}->{tool.id}",
                    confidence=item.confidence,
                    provenance_kind=item.provenance_kind,
                    basis=(
                        "reviewed_declaration"
                        if item.provenance_kind == "static_declaration"
                        else "typed_provider_fact"
                    ),
                    source=item.source,
                    source_pointer=item.source_pointer,
                    evidence={"edge_type": item.edge_type, "complete": item.complete},
                )
                for item in edges
            ]
            issues = [
                SemanticIssue(
                    kind=issue.kind,
                    dimension="binding",
                    message=issue.message,
                    source=issue.source,
                    source_pointer=issue.source_pointer,
                )
                for issue in [
                    *graph_issue_by_tool.get(tool.id, []),
                    *graph_issue_by_agent.get(edge.agent_id, []),
                ]
            ]
            status = "declared" if any(item.provenance_kind == "static_declaration" for item in edges) else "structural"
            tool.binding_assessment = BindingSemanticAssessment(
                status=status,
                confidence="high",
                root_agent_id=graph.root_agent_id,
                reachable_path=[*agent_paths.get(edge.agent_id, [edge.agent_id]), tool.id],
                claims=claims,
                issues=issues,
                pass_eligible=not issues and all(item.complete for item in edges),
            )
        else:
            tool.binding_assessment = BindingSemanticAssessment(
                status="unknown",
                confidence="low",
                root_agent_id=graph.root_agent_id,
                pass_eligible=False,
            )


def _dedupe_tool_edges(edges: list[AgentToolBindingEdge]) -> list[AgentToolBindingEdge]:
    by_key = {
        (
            edge.agent_id,
            edge.tool_id,
            edge.edge_type,
            edge.provenance_kind,
            edge.source,
            edge.source_pointer,
        ): edge
        for edge in edges
    }
    return [by_key[key] for key in sorted(by_key)]


def _dedupe_handoff_edges(edges: list[AgentHandoffBindingEdge]) -> list[AgentHandoffBindingEdge]:
    by_key = {
        (
            edge.source_agent_id,
            edge.target_agent_id,
            edge.edge_type,
            edge.provenance_kind,
            edge.source,
            edge.source_pointer,
        ): edge
        for edge in edges
    }
    return [by_key[key] for key in sorted(by_key)]


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _line_pointer(record: dict[str, Any]) -> str | None:
    source = _str(record.get("source_ref"))
    line = record.get("line")
    return f"{source}:{line}" if source and isinstance(line, int) else source


__all__ = ["TOOL_SOURCE_BINDING_DECLARATION", "resolve_agent_binding_graph"]
