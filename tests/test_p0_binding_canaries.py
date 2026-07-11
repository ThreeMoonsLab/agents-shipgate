from __future__ import annotations

import pytest

from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import Tool
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def _manifest(binding_mode: str) -> AgentsShipgateManifest:
    payload: dict = {
        "version": "0.1",
        "project": {"name": "binding-canary"},
        "agent": {
            "name": "canary-agent",
            "declared_purpose": ["exercise binding canaries"],
        },
        "environment": {"target": "local"},
        "tool_sources": [{"id": "source", "type": "mcp", "path": "tools.json"}],
    }
    if binding_mode == "structural":
        payload["agent"]["sdk"] = {"type": "test", "object": "root_agent"}
    elif binding_mode == "declared":
        payload["agent_bindings"] = {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [{"tool": "tool-0", "source_id": "source"}],
                    "handoffs": [],
                    "reason": "reviewed canary declaration",
                }
            ]
        }
    elif binding_mode == "ambiguous":
        payload["agent"]["sdk"] = {"type": "test", "object": "missing_root"}
    elif binding_mode == "conflicting":
        payload["agent"]["sdk"] = {"type": "test", "object": "root_agent"}
        payload["agent_bindings"] = {
            "root": {"source_id": "source", "object": "root_agent"},
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [],
                    "handoffs": [],
                    "reason": "conflicting empty declaration",
                }
            ],
        }
    return AgentsShipgateManifest.model_validate(payload)


def _tools(count: int, mode: str) -> list[Tool]:
    result: list[Tool] = []
    for index in range(count):
        annotations: dict = {"readOnlyHint": True}
        if mode in {"structural", "partial", "conflicting"} and index == 0:
            annotations["agent_bindings"] = [
                {
                    "agent": "root_agent",
                    "source_id": "source",
                    "edge_type": "direct_tool",
                    "source": "agent.py",
                    "complete": True,
                }
            ]
        if mode == "partial" and index == 0:
            annotations["binding_surface_partial"] = ["runtime-computed tool list"]
        result.append(
            Tool(
                id=f"tool-{index}",
                name=f"tool-{index}",
                provider="source",
                source_type="mcp",
                source_id="source",
                annotations=annotations,
                extraction_confidence="high",
            )
        )
    return result


CANARIES = [
    *((f"catalog-{index}", "catalog", index + 1, "unknown", False, 0) for index in range(12)),
    *((f"structural-{index}", "structural", index + 1, "structural", True, 1) for index in range(10)),
    *((f"declared-{index}", "declared", index + 1, "declared", True, 1) for index in range(8)),
    *((f"ambiguous-{index}", "ambiguous", index + 1, "unknown", False, 0) for index in range(6)),
    *((f"partial-{index}", "partial", index + 1, "partial", False, 1) for index in range(6)),
    *((f"conflict-{index}", "conflicting", index + 1, "conflicting", False, 1) for index in range(6)),
]


@pytest.mark.parametrize(
    ("case_id", "mode", "tool_count", "status", "pass_eligible", "reachable_count"),
    CANARIES,
    ids=[case[0] for case in CANARIES],
)
def test_48_binding_canaries_have_exact_outcomes(
    case_id: str,
    mode: str,
    tool_count: int,
    status: str,
    pass_eligible: bool,
    reachable_count: int,
) -> None:
    del case_id
    manifest_mode = "structural" if mode == "partial" else mode
    graph, _ = resolve_agent_binding_graph(
        _manifest(manifest_mode),
        _tools(tool_count, mode),
        ArtifactBag(),
    )

    assert graph.status == status
    assert graph.pass_eligible is pass_eligible
    assert len(graph.reachable_tool_ids) == reachable_count
