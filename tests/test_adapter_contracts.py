from __future__ import annotations

import json
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import _flatten_and_deduplicate_tools, _load_sources
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import (
    ActionFact,
    ActionSurfaceFacts,
    LoadedToolSource,
    Tool,
    ValidationArtifacts,
)
from agents_shipgate.report.action_surface_diff import build_action_surface_facts

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORT_SAMPLE = REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"
GOOGLE_ADK_SAMPLE = REPO_ROOT / "samples" / "google_adk_agent" / "shipgate.yaml"
LANGCHAIN_SAMPLE = REPO_ROOT / "samples" / "simple_langchain_agent" / "shipgate.yaml"
CREWAI_SAMPLE = REPO_ROOT / "samples" / "simple_crewai_agent" / "shipgate.yaml"
OPENAI_API_SAMPLE = REPO_ROOT / "samples" / "simple_openai_api_agent" / "shipgate.yaml"
ANTHROPIC_SAMPLE = REPO_ROOT / "samples" / "simple_anthropic_agent" / "shipgate.yaml"
HITL_SAMPLE = REPO_ROOT / "samples" / "hitl_evidence_agent" / "shipgate.yaml"

CONFIDENCE_VALUES = {"low", "medium", "high"}
REQUIRED_ACTION_KEYS = {
    "action_id",
    "agent_id",
    "tool_id",
    "tool_name",
    "provider",
    "source_type",
    "source_id",
    "operation",
    "effect",
    "risk_tags",
    "required_scopes",
    "approval_policy",
    "safeguards",
    "evidence",
    "input_fields",
    "required_input_fields",
    "input_schema_hash",
    "hashes",
}
REQUIRED_APPROVAL_KEYS = {"required", "threshold"}
REQUIRED_SAFEGUARD_KEYS = {"idempotency", "audit_log", "rollback", "dry_run"}
REQUIRED_EVIDENCE_KEYS = {"owner", "runbook", "approval_ticket"}
REQUIRED_HASH_KEYS = {"identity_hash", "schema_hash", "policy_hash", "risk_hash"}


@dataclass(frozen=True)
class AdapterCase:
    manifest_path: Callable[[Path], Path]
    select_source: Callable[[LoadedToolSource], bool]
    expected_tool_source_types: frozenset[str]


ADAPTER_CASES = [
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: SUPPORT_SAMPLE,
            select_source=lambda source: source.source_type == "mcp",
            expected_tool_source_types=frozenset({"mcp"}),
        ),
        id="mcp",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: SUPPORT_SAMPLE,
            select_source=lambda source: source.source_id == "support_openapi",
            expected_tool_source_types=frozenset({"openapi"}),
        ),
        id="openapi",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: SUPPORT_SAMPLE,
            select_source=lambda source: source.source_id == "openai_sdk_static",
            expected_tool_source_types=frozenset({"sdk_function"}),
        ),
        id="openai_agents_sdk",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: GOOGLE_ADK_SAMPLE,
            select_source=lambda source: source.source_id == "adk_support",
            expected_tool_source_types=frozenset({"google_adk_function"}),
        ),
        id="google_adk",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: LANGCHAIN_SAMPLE,
            select_source=lambda source: source.source_id == "langchain_agent",
            expected_tool_source_types=frozenset(
                {
                    "langchain_function",
                    "langchain_structured_tool",
                }
            ),
        ),
        id="langchain",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: CREWAI_SAMPLE,
            select_source=lambda source: source.source_id == "crewai_agent",
            expected_tool_source_types=frozenset(
                {
                    "crewai_class_tool",
                    "crewai_function",
                    "crewai_prebuilt_tool",
                }
            ),
        ),
        id="crewai",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda tmp_path: _write_n8n_contract_manifest(tmp_path),
            select_source=lambda source: source.source_type == "n8n",
            expected_tool_source_types=frozenset({"n8n_http_tool"}),
        ),
        id="n8n",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: OPENAI_API_SAMPLE,
            select_source=lambda source: source.source_type == "openai_api",
            expected_tool_source_types=frozenset({"openai_api"}),
        ),
        id="openai_api",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda _tmp_path: ANTHROPIC_SAMPLE,
            select_source=lambda source: source.source_type == "anthropic_api",
            expected_tool_source_types=frozenset({"anthropic_api"}),
        ),
        id="anthropic_api",
    ),
    pytest.param(
        AdapterCase(
            manifest_path=lambda tmp_path: _write_codex_plugin_contract_manifest(tmp_path),
            select_source=lambda source: (
                source.source_type == "codex_plugin_mcp_inventory"
            ),
            expected_tool_source_types=frozenset({"codex_plugin_mcp_inventory"}),
        ),
        id="codex_plugin",
    ),
]


@pytest.mark.parametrize("case", ADAPTER_CASES)
def test_tool_emitting_adapters_produce_normalized_tools_and_action_facts(
    case: AdapterCase,
    tmp_path: Path,
) -> None:
    manifest_path = case.manifest_path(tmp_path)
    manifest = load_manifest(manifest_path)
    loaded_sources, _artifact_bag = _load_sources(
        manifest,
        manifest_path.parent,
        verbose=False,
    )
    selected_sources = [
        source for source in loaded_sources if case.select_source(source)
    ]

    assert selected_sources, "adapter contract case did not select any loaded source"
    assert all(source.tools for source in selected_sources), (
        "every selected adapter source must exercise at least one emitted Tool"
    )
    for source in selected_sources:
        for tool in source.tools:
            _assert_normalized_tool(tool, source)

    tools, _dedupe_warnings = _flatten_and_deduplicate_tools(selected_sources)
    assert tools
    assert {tool.source_type for tool in tools} == case.expected_tool_source_types
    assert len(tools) == len({tool.id for tool in tools})

    agent_id = _agent_id(manifest)
    first = build_action_surface_facts(manifest, agent_id=agent_id, tools=tools)
    second = build_action_surface_facts(manifest, agent_id=agent_id, tools=tools)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    _assert_action_facts(first, tools, agent_id=agent_id)


def test_validation_adapter_emits_no_tools_or_action_facts() -> None:
    manifest = load_manifest(HITL_SAMPLE)
    loaded_sources, artifact_bag = _load_sources(
        manifest,
        HITL_SAMPLE.parent,
        verbose=False,
    )

    validation_artifacts = artifact_bag.get("validation", ValidationArtifacts)
    assert validation_artifacts is not None
    assert [source for source in loaded_sources if source.source_type == "validation"] == []

    real_tools, _dedupe_warnings = _flatten_and_deduplicate_tools(loaded_sources)
    assert real_tools
    facts = build_action_surface_facts(
        manifest,
        agent_id=_agent_id(manifest),
        tools=real_tools,
    )
    assert facts.actions
    assert all(action.source_type != "validation" for action in facts.actions)


def _assert_normalized_tool(tool: Tool, source: LoadedToolSource) -> None:
    assert source.source_id
    assert isinstance(tool.id, str) and tool.id.strip()
    assert isinstance(tool.name, str) and tool.name.strip()
    assert isinstance(tool.source_type, str) and tool.source_type.strip()
    assert tool.source_id == source.source_id
    assert tool.extraction_confidence in CONFIDENCE_VALUES
    assert tool.extraction.get("confidence") == tool.extraction_confidence

    data = tool.model_dump(mode="json")
    _assert_json_serializable(data)
    assert Tool.model_validate(data).model_dump(mode="json") == data
    assert isinstance(data["input_schema"], dict)
    assert isinstance(data["output_schema"], dict)
    assert isinstance(data["annotations"], dict)
    assert isinstance(data["auth"]["scopes"], list)
    assert isinstance(data["parameters"], list)
    assert isinstance(data["risk_hints"], list)

    for parameter in data["parameters"]:
        assert isinstance(parameter["name"], str) and parameter["name"].strip()
        assert isinstance(parameter["required"], bool)

    for hint in data["risk_hints"]:
        assert isinstance(hint["tag"], str) and hint["tag"].strip()
        assert isinstance(hint["source"], str) and hint["source"].strip()
        assert hint["confidence"] in CONFIDENCE_VALUES


def _assert_action_facts(
    facts: ActionSurfaceFacts,
    tools: list[Tool],
    *,
    agent_id: str,
) -> None:
    data = facts.model_dump(mode="json")
    _assert_json_serializable(data)
    assert ActionSurfaceFacts.model_validate(data).model_dump(mode="json") == data

    by_tool_id = {tool.id: tool for tool in tools}
    assert len(facts.actions) == len(by_tool_id)
    assert {action.tool_id for action in facts.actions} == set(by_tool_id)
    assert [action.action_id for action in facts.actions] == sorted(
        action.action_id for action in facts.actions
    )

    for action in facts.actions:
        tool = by_tool_id[action.tool_id]
        assert action.action_id.startswith(f"{agent_id}:")
        assert action.action_id.count(":") >= 3
        assert action.tool_name == tool.name
        assert action.source_type == tool.source_type
        assert action.source_id == tool.source_id
        assert action.input_schema_hash == action.hashes.schema_hash

        action_data = action.model_dump(mode="json")
        _assert_json_serializable(action_data)
        assert ActionFact.model_validate(action_data).model_dump(mode="json") == action_data
        assert REQUIRED_ACTION_KEYS <= set(action_data)
        assert REQUIRED_APPROVAL_KEYS <= set(action_data["approval_policy"])
        assert REQUIRED_SAFEGUARD_KEYS <= set(action_data["safeguards"])
        assert REQUIRED_EVIDENCE_KEYS <= set(action_data["evidence"])
        assert REQUIRED_HASH_KEYS <= set(action_data["hashes"])
        assert all(action_data["hashes"][key] for key in REQUIRED_HASH_KEYS)


def _agent_id(manifest: AgentsShipgateManifest) -> str:
    return f"agent:{manifest.project.name}/{manifest.agent.name}"


def _assert_json_serializable(data: object) -> None:
    """Fail if a normalized model dump cannot be emitted as canonical JSON."""
    json.dumps(data, sort_keys=True)


def _write_n8n_contract_manifest(tmp_path: Path) -> Path:
    project = tmp_path / "n8n-contract"
    project.mkdir()
    (project / "workflow.json").write_text(
        json.dumps(
            {
                "id": "workflow-1",
                "name": "Adapter Contract Workflow",
                "nodes": [
                    {
                        "id": "http-tool",
                        "name": "Lookup Ticket",
                        "type": "n8n-nodes-langchain.toolHttpRequest",
                        "parameters": {
                            "description": "Lookup a support ticket by identifier.",
                            "method": "GET",
                            "url": (
                                "https://api.example.test/tickets/"
                                "{{$fromAI('ticket_id','Ticket identifier','string')}}"
                            ),
                        },
                    },
                    {
                        "id": "agent",
                        "name": "AI Agent",
                        "type": "n8n-nodes-langchain.agent",
                        "parameters": {},
                    },
                ],
                "connections": {
                    "Lookup Ticket": {
                        "ai_tool": [
                            [{"node": "AI Agent", "type": "ai_tool", "index": 0}]
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = project / "shipgate.yaml"
    # Intentionally omits `action_surface:` to exercise the undeclared-tool
    # action_id fallback path.
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: n8n-contract
            agent:
              name: n8n-agent
              declared_purpose:
                - verify adapter contracts
            environment:
              target: local
            n8n:
              workflows:
                - path: workflow.json
            output:
              packet:
                enabled: false
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return manifest


def _write_codex_plugin_contract_manifest(tmp_path: Path) -> Path:
    project = tmp_path / "codex-plugin-contract"
    plugin_root = project / "plugins" / "browserish"
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / "skills" / "browser").mkdir(parents=True)
    (project / "inventories").mkdir(parents=True)

    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "browserish",
                "version": "1.0.0",
                "description": "Review browser automation from static plugin files.",
                "skills": "./skills/",
                "mcpServers": "./.mcp.json",
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "skills" / "browser" / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: browser
            description: Inspect local browser state for review.
            ---

            # Browser
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (plugin_root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "browser": {
                        "command": "python",
                        "args": ["-c", "raise SystemExit('must not execute')"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (project / "inventories" / "browser-tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "open_page",
                        "description": "Open a browser page for local inspection.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    manifest = project / "shipgate.yaml"
    # Intentionally omits `action_surface:` to exercise the undeclared-tool
    # action_id fallback path.
    manifest.write_text(
        textwrap.dedent(
            """
            version: "0.1"
            project:
              name: codex-plugin-contract
            agent:
              name: codex-plugin-review
              declared_purpose:
                - verify plugin adapter contracts
            environment:
              target: local
            tool_sources:
              - id: browserish
                type: codex_plugin
                mode: package
                path: plugins/browserish
            codex_plugins:
              mcp_tool_inventories:
                - plugin: browserish
                  server: browser
                  path: inventories/browser-tools.json
            output:
              packet:
                enabled: false
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return manifest
