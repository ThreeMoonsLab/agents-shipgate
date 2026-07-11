from __future__ import annotations

from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import AuthInfo, Tool
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
