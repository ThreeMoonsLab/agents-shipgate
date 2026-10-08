from __future__ import annotations

import pytest

from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import (
    AgentBindingObservation,
    AuthInfo,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def _tool(name: str, *, source_id: str = "catalog", agent: str | None = None) -> Tool:
    annotations = {"readOnlyHint": True}
    if agent is not None:
        annotations["agent_bindings"] = [
            {
                "agent": agent,
                "source_id": source_id,
                "edge_type": "direct_tool",
                "source": "agent.py",
                "source_pointer": "/Agent/tools",
                "complete": True,
            }
        ]
    return Tool(
        id=f"tool:{source_id}:{name}",
        name=name,
        provider=source_id,
        source_type="mcp",
        source_id=source_id,
        annotations=annotations,
        auth=AuthInfo(mode="none", explicit=True),
        extraction_confidence="high",
    )


def _manifest(*, bindings: dict | None = None, sdk_object: str | None = None):
    agent: dict = {
        "name": "test-agent",
        "declared_purpose": ["test exact static bindings"],
    }
    if sdk_object:
        agent["sdk"] = {"type": "test", "object": sdk_object}
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "binding-test"},
            "agent": agent,
            "environment": {"target": "local"},
            "tool_sources": [{"id": "catalog", "type": "mcp", "path": "tools.json"}],
            "agent_bindings": bindings or {},
        }
    )


def test_catalog_membership_never_implies_agent_binding() -> None:
    graph, tools = resolve_agent_binding_graph(
        _manifest(), [_tool("orders.process")], ArtifactBag()
    )

    assert graph.pass_eligible is False
    assert graph.reachable_tool_ids == []
    assert graph.unbound_tool_ids == ["tool:catalog:orders.process"]
    assert {issue.kind for issue in graph.issues} == {"ambiguous_root_agent"}
    assert tools[0].binding_assessment is not None
    assert tools[0].binding_assessment.pass_eligible is False


def test_structural_binding_selects_only_reachable_tools() -> None:
    bound = _tool("orders.lookup", agent="root_agent")
    unbound = _tool("orders.delete")
    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent"), [bound, unbound], ArtifactBag()
    )

    assert graph.status == "structural"
    assert graph.pass_eligible is True
    assert graph.reachable_tool_ids == [bound.id]
    assert graph.unbound_tool_ids == [unbound.id]


def test_exact_reviewed_empty_binding_can_prove_zero_capabilities() -> None:
    graph, _ = resolve_agent_binding_graph(
        _manifest(
            bindings={
                "declarations": [
                    {
                        "agent": "root",
                        "complete": True,
                        "tools": [],
                        "handoffs": [],
                        "reason": "reviewed empty root surface",
                    }
                ]
            }
        ),
        [],
        ArtifactBag(),
    )

    assert graph.status == "declared"
    assert graph.pass_eligible is True
    assert graph.reachable_tool_ids == []


def test_reviewed_binding_uses_canonical_source_qualified_selector() -> None:
    first = _tool("lookup", source_id="provider-a")
    second = _tool("lookup", source_id="provider-b")
    graph, _ = resolve_agent_binding_graph(
        _manifest(
            bindings={
                "declarations": [
                    {
                        "agent": "root",
                        "complete": True,
                        "tools": [{"tool": "lookup", "source_id": "provider-b"}],
                        "reason": "reviewed provider-b binding",
                    }
                ]
            }
        ),
        [first, second],
        ArtifactBag(),
    )

    assert graph.pass_eligible is True
    assert graph.reachable_tool_ids == [second.id]
    assert graph.unbound_tool_ids == [first.id]


def test_declaration_cannot_erase_complete_structural_edge() -> None:
    structural = _tool("orders.delete", source_id="sdk", agent="root_agent")
    declared_only = _tool("orders.lookup", source_id="sdk")
    graph, _ = resolve_agent_binding_graph(
        _manifest(
            sdk_object="root_agent",
            bindings={
                "root": {"source_id": "sdk", "object": "root_agent"},
                "declarations": [
                    {
                        "agent": "root",
                        "complete": True,
                        "tools": [{"tool": "orders.lookup", "source_id": "sdk"}],
                        "reason": "incorrect reviewed downgrade",
                    }
                ],
            },
        ),
        [structural, declared_only],
        ArtifactBag(),
    )

    assert graph.status == "conflicting"
    assert graph.pass_eligible is False
    assert "conflicting_binding_evidence" in {issue.kind for issue in graph.issues}
    assert structural.id in graph.reachable_tool_ids


def test_dynamic_binding_annotation_fails_closed() -> None:
    tool = _tool("lookup", agent="root_agent")
    tool.annotations["binding_surface_partial"] = ["tools are loaded from runtime config"]
    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent"), [tool], ArtifactBag()
    )

    assert graph.status == "partial"
    assert graph.pass_eligible is False
    assert "partial_binding_evidence" in {issue.kind for issue in graph.issues}


def test_incomplete_positive_edge_creates_tool_specific_gap() -> None:
    tool = _tool("exfiltrate", agent="root_agent")
    tool.annotations["agent_bindings"][0]["complete"] = False

    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent"), [tool], ArtifactBag()
    )

    assert graph.reachable_tool_ids == []
    assert graph.possible_tool_ids == [tool.id]
    assert graph.status == "partial"
    assert graph.pass_eligible is False
    assert any(
        issue.kind == "partial_binding_evidence" and issue.tool_id == tool.id
        for issue in graph.issues
    )


def test_agent_level_observation_preserves_toolless_router_handoff() -> None:
    worker_tool = _tool("lookup", source_id="sdk")
    loaded = LoadedToolSource(
        source_id="sdk",
        source_type="openai_agents_sdk",
        tools=[worker_tool],
        binding_observations=[
            AgentBindingObservation(
                agent="router",
                source_id="sdk",
                source="agent.py",
                source_pointer="agent.py:4",
                handoff_names=["worker"],
            ),
            AgentBindingObservation(
                agent="worker",
                source_id="sdk",
                source="agent.py",
                source_pointer="agent.py:5",
                tool_names=["lookup"],
            ),
        ],
    )

    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="router"),
        [worker_tool],
        ArtifactBag(),
        [loaded],
    )

    assert graph.pass_eligible is True
    assert graph.reachable_tool_ids == [worker_tool.id]
    assert len(graph.handoff_edges) == 1


def test_constructor_causes_keep_each_site_and_cannot_be_closed_by_wiring() -> None:
    from agents_shipgate.ci.release_decision import _binding_declaration_template, _semantic_gap
    from agents_shipgate.core.agent_bindings import FRAMEWORK_CONSTRUCTOR_OWNERSHIP

    tool = _tool("lookup")
    cause = "An opaque imported carrier remains unread."
    unrelated = "constructor identity is not established is merely wording here"
    observation = AgentBindingObservation(
        agent="root_agent", source_id="catalog", source="agent.py",
        source_pointer="agent.py:4", tool_names=["lookup"], tools_complete=False,
        issues=[cause, unrelated],
        constructor_issues={"agent.py:4": cause, "agent.py:8": cause},
    )
    assert "constructor_issues" not in observation.model_dump(mode="json")
    loaded = LoadedToolSource(
        source_id="catalog", source_type="openai_agents_sdk", tools=[tool],
        binding_observations=[observation],
    )
    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent", bindings={"declarations": [{
            "agent": "root_agent", "complete": True, "reason": "reviewed fixture wiring",
            "tools": [{"tool": "lookup", "source_id": "catalog"}],
        }]}), [tool], ArtifactBag(), [loaded],
    )
    assert graph.reachable_tool_ids == [tool.id]
    assert not graph.possible_tool_ids
    assert not graph.pass_eligible
    ownership = [issue for issue in graph.issues if issue.source == FRAMEWORK_CONSTRUCTOR_OWNERSHIP]
    assert [issue.source_pointer for issue in ownership] == ["agent.py:4", "agent.py:8"]
    assert all(issue.agent_id == graph.root_agent_id for issue in ownership)
    assert [issue.message for issue in graph.issues if issue.source == "framework_extraction"] == [unrelated]
    assert tool.binding_assessment is not None and not tool.binding_assessment.pass_eligible
    for issue in ownership:
        assert _binding_declaration_template(graph, issue, [tool]) is None
        gap = _semantic_gap(tool, kind=issue.kind, why=issue.message,
                            issue_source=issue.source, source_ref=issue.source_pointer)
        assert gap.next_action.kind == "provide_source"
        assert gap.next_action.path == issue.source_pointer
        assert not gap.next_action.accepted_values
        assert gap.next_action.declaration_template is None


@pytest.mark.parametrize("declared", [True, False])
@pytest.mark.parametrize("reachable", [True, False])
@pytest.mark.parametrize("unsafe_agent", [None, "root_agent", "worker"])
def test_shared_tool_keeps_constructor_causes_only_from_reachable_owners(
    declared: bool, reachable: bool, unsafe_agent: str | None,
) -> None:
    from agents_shipgate.ci.release_decision import _semantic_gap
    from agents_shipgate.core.agent_bindings import FRAMEWORK_CONSTRUCTOR_OWNERSHIP

    tool = _tool("lookup")
    cause = "The actual constructor carrier remains unread."
    observations = []
    declarations = []
    for name in ("root_agent", "worker"):
        unsafe = name == unsafe_agent
        pointer = f"{name}.py:4"
        handoffs = ["worker"] if name == "root_agent" and reachable else []
        observations.append(AgentBindingObservation(
            agent=name, source_id="catalog", source=f"{name}.py",
            source_pointer=pointer, tool_names=["lookup"], handoff_names=handoffs,
            tools_complete=not unsafe, issues=[cause] if unsafe else [],
            constructor_issues={pointer: cause} if unsafe else {},
        ))
        declarations.append({
            "agent": name, "complete": True, "reason": "reviewed fixture wiring",
            "tools": [{"tool": "lookup", "source_id": "catalog"}],
            "handoffs": handoffs,
        })
    loaded = LoadedToolSource(
        source_id="catalog", source_type="openai_agents_sdk", tools=[tool],
        binding_observations=observations,
    )
    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent", bindings={"declarations": declarations} if declared else None),
        [tool], ArtifactBag(), [loaded],
    )
    complete_owner_reachable = declared or reachable or unsafe_agent != "root_agent"
    assert graph.reachable_tool_ids == ([tool.id] if complete_owner_reachable else [])
    assert graph.possible_tool_ids == ([] if complete_owner_reachable else [tool.id])
    graph_causes = [issue for issue in graph.issues if issue.source == FRAMEWORK_CONSTRUCTOR_OWNERSHIP]
    assert len(graph_causes) == (unsafe_agent is not None)
    assert graph.pass_eligible is (unsafe_agent is None)
    binding = tool.binding_assessment
    assert binding is not None
    assert binding.pass_eligible is (unsafe_agent is None)
    if not declared and unsafe_agent is not None:
        unsafe_id = next(agent.agent_id for agent in graph.agents if agent.name == unsafe_agent)
        assert all(claim.value != f"{unsafe_id}->{tool.id}" for claim in binding.claims)
    inherited = [issue for issue in binding.issues if issue.source == FRAMEWORK_CONSTRUCTOR_OWNERSHIP]
    expected = unsafe_agent is not None and (unsafe_agent == "root_agent" or reachable)
    assert len(inherited) == expected
    for issue in inherited:
        assert issue.source_pointer == f"{unsafe_agent}.py:4"
        gap = _semantic_gap(tool, kind=issue.kind, why=issue.message,
                            issue_source=issue.source, source_ref=issue.source_pointer)
        assert gap.next_action.kind == "provide_source"
        assert gap.next_action.path == issue.source_pointer
        assert not gap.next_action.accepted_values
        assert gap.next_action.declaration_template is None


def test_catalog_annotation_cannot_claim_constructor_cause_provenance() -> None:
    from agents_shipgate.core.agent_bindings import FRAMEWORK_CONSTRUCTOR_OWNERSHIP

    tool = _tool("lookup", agent="root_agent")
    annotation = tool.annotations["agent_bindings"][0]
    annotation["complete"] = False
    annotation["source"] = FRAMEWORK_CONSTRUCTOR_OWNERSHIP
    graph, _ = resolve_agent_binding_graph(_manifest(sdk_object="root_agent"), [tool], ArtifactBag())
    assert not graph.pass_eligible
    assert graph.possible_tool_ids == [tool.id]
    assert all(issue.source != FRAMEWORK_CONSTRUCTOR_OWNERSHIP for issue in graph.issues)


def test_incomplete_handoff_from_reachable_agent_fails_closed() -> None:
    root_tool = _tool("route", source_id="sdk", agent="root_agent")
    root_tool.annotations["agent_handoffs"] = [
        {
            "source_agent": "root_agent",
            "target_agent": "worker",
            "source_id": "sdk",
            "complete": False,
        }
    ]
    worker_tool = _tool("lookup", source_id="sdk", agent="worker")

    graph, _ = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent"),
        [root_tool, worker_tool],
        ArtifactBag(),
    )

    assert graph.reachable_tool_ids == [root_tool.id]
    assert graph.pass_eligible is False
    assert "incomplete_handoff_graph" in {issue.kind for issue in graph.issues}


@pytest.mark.parametrize("cycle", [False, True])
def test_candidate_handoff_paths_preserve_downstream_tools_without_proving_them(cycle):
    route = _tool("route", source_id="sdk", agent="root_agent")
    child = _tool("child", source_id="sdk", agent="child")
    grandchild = _tool("grandchild", source_id="sdk", agent="grandchild")
    disconnected = _tool("disconnected", source_id="sdk", agent="other")
    route.annotations["agent_handoffs"] = [dict(
        source_agent="root_agent", target_agent="child", source_id="sdk", complete=False,
    )]
    child.annotations["agent_handoffs"] = [dict(
        source_agent="child", target_agent="grandchild", source_id="sdk", complete=True,
    )]
    if cycle:
        grandchild.annotations["agent_handoffs"] = [dict(
            source_agent="grandchild", target_agent="child", source_id="sdk", complete=True,
        )]
    graph, tools = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent"), [route, child, grandchild, disconnected], ArtifactBag(),
    )
    assert graph.reachable_tool_ids == [route.id]
    assert graph.possible_tool_ids == sorted([child.id, grandchild.id])
    assert graph.unbound_tool_ids == [disconnected.id]
    assert not graph.pass_eligible
    assert {issue.tool_id for issue in graph.issues if issue.tool_id} >= {child.id, grandchild.id}
    assert all(not tool.binding_assessment.pass_eligible for tool in tools if tool.id in graph.possible_tool_ids)


def test_a_complete_path_wins_over_an_unread_owner_of_the_same_tool():
    shared = _tool("shared", source_id="sdk", agent="root_agent")
    shared.annotations["agent_bindings"].append(dict(
        agent="child", source_id="sdk", edge_type="direct_tool", source="agent.py",
        source_pointer="/child/tools", complete=False,
    ))
    shared.annotations["agent_handoffs"] = [dict(
        source_agent="root_agent", target_agent="child", source_id="sdk", complete=False,
    )]
    graph, tools = resolve_agent_binding_graph(
        _manifest(sdk_object="root_agent"), [shared], ArtifactBag(),
    )
    assert graph.reachable_tool_ids == [shared.id] and graph.possible_tool_ids == []
    assert len(tools[0].binding_assessment.claims) == 1
    assert tools[0].binding_assessment.claims[0].evidence["complete"] is True
    assert not tools[0].binding_assessment.pass_eligible
    assert not graph.pass_eligible  # The unread handoff remains visible globally.
