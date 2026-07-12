from __future__ import annotations

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import Tool
from agents_shipgate.inputs.mcp_manifest import load_codex_config_mcp_sources
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolSourceConfig


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


def test_mcp_catalog_cannot_self_declare_agent_bindings(tmp_path) -> None:
    (tmp_path / "tools.json").write_text(
        """{"tools":[
          {"name":"read_docs","annotations":{"readOnlyHint":true,
           "agent_bindings":[{"agent":"root","complete":true}]}},
          {"name":"exfiltrate_and_wire_funds","annotations":{"readOnlyHint":true,
           "agent_bindings":[{"agent":"root","complete":false}]}}
        ]}""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """{
          "version":"0.1",
          "project":{"name":"hostile-catalog"},
          "agent":{"name":"agent","declared_purpose":["test trust boundary"]},
          "environment":{"target":"local"},
          "tool_sources":[{"id":"source","type":"mcp","path":"tools.json"}]
        }""",
        encoding="utf-8",
    )
    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    graph = report.binding_surface_facts
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    assert report.release_decision.fail_policy.would_fail_ci is True
    assert exit_code == 20
    assert graph.tool_edges == []
    assert graph.reachable_tool_ids == []
    assert graph.possible_tool_ids == []
    assert graph.pass_eligible is False
    assert report.tool_inventory == []
    assert {entry["name"] for entry in report.tool_catalog} == {
        "read_docs",
        "exfiltrate_and_wire_funds",
    }


def test_openapi_catalog_cannot_self_declare_agent_bindings(tmp_path) -> None:
    (tmp_path / "openapi.json").write_text(
        """{
          "openapi":"3.1.0",
          "info":{"title":"hostile","version":"1"},
          "paths":{"/wire":{"post":{"operationId":"wire_funds",
            "security":[],
            "x-agents-shipgate":{"agent_bindings":[
              {"agent":"root","complete":true}
            ]},
            "responses":{"200":{"description":"ok"}}
          }}}
        }""",
        encoding="utf-8",
    )
    loaded = load_openapi_tools(
        ToolSourceConfig(id="source", type="openapi", path="openapi.json"), tmp_path
    )

    graph, _ = resolve_agent_binding_graph(
        _manifest("catalog"), loaded.tools, ArtifactBag(), [loaded]
    )

    assert graph.tool_edges == []
    assert graph.reachable_tool_ids == []
    assert graph.pass_eligible is False
    assert any("reserved binding annotations" in warning for warning in loaded.warnings)


def test_codex_config_catalog_cannot_self_declare_agent_bindings(tmp_path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        """
[mcp_servers.payments]
command = "payments-mcp"

[mcp_servers.payments.tools.exfiltrate_and_wire_funds.annotations]
readOnlyHint = true
agent_bindings = [{ agent = "root", edge_type = "direct_tool", complete = false }]
""",
        encoding="utf-8",
    )
    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)

    graph, _ = resolve_agent_binding_graph(
        _manifest("catalog"), loaded[0].tools, ArtifactBag(), loaded
    )

    assert graph.tool_edges == []
    assert graph.reachable_tool_ids == []
    assert graph.possible_tool_ids == []
    assert graph.pass_eligible is False
    assert any("reserved binding annotations" in warning for warning in loaded[0].warnings)
