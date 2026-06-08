from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.artifact_models import OpenAIApiArtifacts
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.capability_traces import build_capability_runtime_evidence
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, AuthInfo, Tool, ToolRiskHint
from agents_shipgate.inputs.traces import TRACE_SOURCE_KEY, load_trace_artifacts
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ArtifactPathConfig


def _manifest() -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "trace-evidence"},
            "agent": {
                "name": "support-agent",
                "declared_purpose": ["Support customer workflows."],
            },
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "declared", "type": "mcp", "path": "tools.json"}
            ],
        }
    )


def _tool(name: str, *, tool_id: str | None = None) -> Tool:
    return Tool(
        id=tool_id or f"tool:{name}",
        name=name,
        description=f"{name} test tool",
        source_type="openai_api",
        source_id="openai",
        source_ref="tools.json#0",
        source_path="tools.json",
        source_pointer="/0",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        auth=AuthInfo(type="oauth", scopes=["support:write"]),
        risk_hints=[
            ToolRiskHint(tag="write", source="test", confidence="high"),
        ],
        extraction_confidence="high",
    )


def _context(
    tools: list[Tool],
    trace_samples: list[dict],
) -> ScanContext:
    manifest = _manifest()
    agent = Agent(
        id="agent:support-agent",
        name="support-agent",
        declared_purpose=list(manifest.agent.declared_purpose),
        tools=[tool.name for tool in tools],
    )
    facts = build_capability_facts(
        manifest,
        agent_id=agent.id,
        tools=tools,
    )
    bag = ArtifactBag(
        {"openai_api": OpenAIApiArtifacts(trace_samples=trace_samples)}
    )
    return ScanContext(
        manifest=manifest,
        agent=agent,
        tools=tools,
        config_path=Path("shipgate.yaml"),
        framework_artifacts=bag,
        capability_facts=facts,
    )


def test_trace_loader_keeps_allowlisted_scalars_and_source_metadata(tmp_path):
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(
        """
{"tool_name":"issue_refund","provider":"stripe","operation":"refund","approved":false,"success":false,"reason":"manager missing","actor":"agent","trace_id":"tr_1","run_id":"run_1","call_id":"call_1","timestamp":"2026-01-01T00:00:00Z","arguments":{"payment_id":"pi_secret"},"output":"raw output","messages":["raw prompt"]}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    files, traces = load_trace_artifacts(
        [ArtifactPathConfig(path="traces.jsonl")],
        tmp_path,
        warnings,
        label="test",
        source_type="validation_agent_trace",
    )

    assert files == ["traces.jsonl"]
    assert warnings == []
    assert traces == [
        {
            "tool_name": "issue_refund",
            "provider": "stripe",
            "operation": "refund",
            "approved": False,
            "success": False,
            "reason": "manager missing",
            "actor": "agent",
            "trace_id": "tr_1",
            "run_id": "run_1",
            "call_id": "call_1",
            "timestamp": "2026-01-01T00:00:00Z",
            TRACE_SOURCE_KEY: {
                "source_type": "validation_agent_trace",
                "source_ref": "traces.jsonl",
                "source_path": "traces.jsonl",
                "source_line": 1,
                "source_pointer": None,
                "source_index": 0,
            },
        }
    ]
    assert "arguments" not in traces[0]
    assert "output" not in traces[0]
    assert "messages" not in traces[0]


def test_capability_runtime_evidence_links_by_capability_id_first() -> None:
    tool = _tool("issue_refund")
    context = _context([tool], [])
    capability_id = context.capability_facts[0].id
    context.framework_artifacts.set(
        "openai_api",
        OpenAIApiArtifacts(
            trace_samples=[
                {
                    "tool_name": "not_the_tool_name",
                    "capability_id": capability_id,
                    TRACE_SOURCE_KEY: {"source_type": "openai_api_trace"},
                }
            ]
        ),
    )

    evidence = build_capability_runtime_evidence(context)

    assert evidence.summary.trace_count == 1
    assert evidence.summary.matched_trace_count == 1
    assert evidence.matched[0].matched_capability_id == capability_id
    assert evidence.matched[0].match_reason == "capability_id"


def test_capability_runtime_evidence_links_by_unique_tool_name() -> None:
    context = _context(
        [_tool("issue_refund")],
        [{"tool_name": "issue_refund", "approved": False}],
    )

    evidence = build_capability_runtime_evidence(context)

    assert evidence.summary.matched_trace_count == 1
    assert evidence.matched[0].tool_name == "issue_refund"
    assert evidence.matched[0].match_reason == "tool_name"


def test_capability_runtime_evidence_marks_unknown_and_ambiguous_tools() -> None:
    ambiguous = _context(
        [
            _tool("shared_tool", tool_id="tool:one"),
            _tool("shared_tool", tool_id="tool:two"),
        ],
        [{"tool_name": "shared_tool"}, {"tool_name": "missing_tool"}],
    )

    evidence = build_capability_runtime_evidence(ambiguous)

    assert [row.match_reason for row in evidence.unmatched] == [
        "unknown_tool",
        "ambiguous_tool",
    ]
    assert evidence.summary.unmatched_trace_count == 2
