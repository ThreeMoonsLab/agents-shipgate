import hashlib
import json

import pytest

from agents_shipgate.cli.discovery.signals import detect_workspace
from agents_shipgate.cli.discovery.template import render_auto_manifest
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.models import Tool
from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints, risk_tags
from agents_shipgate.inputs.n8n import load_n8n_artifacts


def test_n8n_top_level_config_is_accepted_and_tool_source_type_is_rejected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(project / "workflow.json")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-schema-test
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    manifest = load_manifest(project / "shipgate.yaml")
    assert manifest.n8n is not None
    assert manifest.n8n.workflows[0].path == "workflow.json"

    (project / "invalid-type.yaml").write_text(
        """
version: "0.1"
project:
  name: invalid
agent:
  name: invalid-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: n8n
    type: n8n
    path: workflow.json
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_manifest(project / "invalid-type.yaml")

    (project / "invalid-case.yaml").write_text(
        """
version: "0.1"
project:
  name: invalid
agent:
  name: invalid-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: n8N
    path: workflow.json
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_manifest(project / "invalid-case.yaml")


def test_n8n_malformed_optional_placement_is_rejected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(project / "workflow.json")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: bad-optional
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
  execution_samples:
    - path: evidence/
  optional: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_manifest(project / "shipgate.yaml")


def test_n8n_static_extraction_and_findings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(project / "workflow.json", include_secret=False)
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-test
agent:
  name: n8n-agent
environment:
  target: production_like
n8n:
  workflows:
    - path: workflow.json
permissions:
  scopes:
    - n8n:stripeApi
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    frameworks = report.frameworks["n8n"]
    assert frameworks["workflow_count"] == 1
    assert frameworks["ai_agent_count"] == 1
    assert frameworks["ingress_count"] == 1
    assert frameworks["mcp_server_trigger_count"] == 0
    assert frameworks["mcp_server_exposed_tool_count"] == 0
    assert frameworks["mcp_client_tool_count"] == 1
    assert frameworks["dynamic_tool_surface_count"] >= 2
    inventory = {tool["name"]: tool for tool in report.tool_inventory}
    assert inventory["Delete Customer"]["source_type"] == "n8n_http_tool"
    assert inventory["MCP Client.*"]["source_type"] == "n8n_mcp_client_tool"
    assert inventory["Run External Workflow"]["source_type"] == "n8n_workflow_tool"

    customer_tool = next(tool for tool in report.findings if tool.tool_name == "Delete Customer")
    assert customer_tool.source is not None
    assert customer_tool.source.ref == "workflow.json#node:http-tool"
    assert customer_tool.source.pointer == "/nodes/http-tool"

    check_ids = {finding.check_id for finding in report.findings}
    assert "SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" in check_ids
    assert "SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED" in check_ids
    assert "SHIP-N8N-CREDENTIAL-EVIDENCE-MISSING" in check_ids
    assert "SHIP-N8N-EVAL-COVERAGE-MISSING" in check_ids
    dynamic_finding = next(
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
    )
    assert dynamic_finding.source is not None
    assert dynamic_finding.source.type == "n8n_workflow"
    assert dynamic_finding.source.path == "workflow.json"
    assert dynamic_finding.source.pointer is not None
    n8n_findings = [finding for finding in report.findings if finding.check_id.startswith("SHIP-N8N-")]
    assert n8n_findings
    assert {finding.agent_action for finding in n8n_findings} == {"escalate_to_human"}


def test_n8n_mcp_server_trigger_exposed_tools_are_normalized_as_mcp(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_mcp_server_workflow(project / "workflow.json")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-mcp-server
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    frameworks = report.frameworks["n8n"]
    assert frameworks["mcp_server_trigger_count"] == 1
    assert frameworks["mcp_server_exposed_tool_count"] == 1
    tool = next(item for item in report.tool_inventory if item["name"] == "Lookup Case")
    assert tool["source_type"] == "mcp"
    loaded_tool = next(item for item in report.tool_surface_facts.tools if item.name == "Lookup Case")
    assert loaded_tool.source_type == "mcp"


def test_n8n_directory_mode_filters_non_workflows_and_is_deterministic(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "package.json").write_text('{"name": "not-a-workflow"}', encoding="utf-8")
    (workflows / "random.json").write_text('{"nodes": [], "connections": {}}', encoding="utf-8")
    _write_workflow(workflows / "b.json", tool_node_id="b-tool", tool_name="B Tool")
    _write_workflow(workflows / "a.json", tool_node_id="a-tool", tool_name="A Tool")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-dir
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflows/
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["n8n"]["workflow_file_count"] == 2
    assert [tool["name"] for tool in report.tool_inventory if tool["name"] in {"A Tool", "B Tool"}] == [
        "A Tool",
        "B Tool",
    ]


def test_n8n_directory_mode_keeps_source_warnings_local(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "a-community.json").write_text(
        json.dumps(_community_only_workflow(include_version_id=True)),
        encoding="utf-8",
    )
    (workflows / "b-clean.json").write_text(
        json.dumps(
            _minimal_agent_workflow(
                {
                    "id": "clean-tool",
                    "name": "clean_tool",
                    "type": "n8n-nodes-langchain.toolHttpRequest",
                    "parameters": {"description": "Clean tool.", "method": "GET", "url": "https://example.com"},
                }
            )
        ),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-local-warnings
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflows/
""",
        encoding="utf-8",
    )

    payload = inspect_sources(config_path=project / "shipgate.yaml")

    sources = {source["id"]: source for source in payload["sources"]}
    community_warnings = sources["n8n:workflows/a-community.json"]["warnings"]
    clean_warnings = sources["n8n:workflows/b-clean.json"]["warnings"]
    assert any("no first-party node types" in warning for warning in community_warnings)
    assert not any("no first-party node types" in warning for warning in clean_warnings)


def test_n8n_node_id_identity_survives_prepended_node(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _workflow(tool_node_id="stable-tool", tool_name="Stable Tool")
    workflow["nodes"].insert(
        0,
        {
            "id": "prepended",
            "name": "Prepended",
            "type": "n8n-nodes-base.noOp",
            "parameters": {},
        },
    )
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-stable
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    finding = next(item for item in report.findings if item.tool_name == "Stable Tool")
    assert finding.source is not None
    assert finding.source.ref == "workflow.json#node:stable-tool"
    assert finding.source.pointer == "/nodes/stable-tool"
    assert finding.evidence.get("source_ref") is None


def test_n8n_mcp_operation_field_does_not_create_wildcard_selection(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _minimal_agent_workflow(
        {
            "id": "mcp-client",
            "name": "MCP Client",
            "type": "n8n-nodes-langchain.toolMcp",
            "parameters": {"include": "All", "options": {"operation": "all"}},
        }
    )
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-mcp-operation
agent:
  name: n8n-agent
environment:
  target: production_like
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["n8n"]["dynamic_tool_surface_count"] == 0
    assert "SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED" not in {
        finding.check_id for finding in report.findings
    }


def test_n8n_nested_send_and_wait_text_does_not_count_as_human_review(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _minimal_agent_workflow(
        {
            "id": "code-tool",
            "name": "Run Audit Code",
            "type": "n8n-nodes-base.code",
            "parameters": {"description": "Run audit code.", "jsCode": "return [];"},
        }
    )
    workflow["nodes"].append(
        {
            "id": "not-review",
            "name": "Not Review",
            "type": "n8n-nodes-base.noOp",
            "parameters": {
                "action": "sendAndWait",
                "options": {"operation": "send_and_wait"},
            },
        }
    )
    workflow["connections"]["AI Agent"] = {
        "main": [[{"node": "Not Review", "type": "main", "index": 0}]]
    }
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-false-human-review
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["n8n"]["human_review_node_count"] == 0
    assert any(
        finding.check_id == "SHIP-POLICY-APPROVAL-MISSING"
        and finding.tool_name == "Run Audit Code"
        for finding in report.findings
    )


def test_n8n_downstream_send_and_wait_is_not_approval_evidence(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    reviewed = _minimal_agent_workflow(
        {
            "id": "reviewed-tool",
            "name": "shared_delete",
            "type": "n8n-nodes-base.code",
            "parameters": {"description": "Reviewed delete.", "jsCode": "return [];"},
        }
    )
    reviewed["id"] = "reviewed-workflow"
    reviewed["nodes"].append(
        {
            "id": "human",
            "name": "Send and Wait",
            "type": "n8n-nodes-base.sendAndWait",
            "parameters": {},
        }
    )
    reviewed["connections"]["AI Agent"] = {
        "main": [[{"node": "Send and Wait", "type": "main", "index": 0}]]
    }
    forged = _minimal_agent_workflow(
        {
            "id": "unreviewed-tool",
            "name": "shared_delete",
            "type": "n8n-nodes-base.code",
            "parameters": {"description": "Unreviewed delete.", "jsCode": "return [];"},
        }
    )
    forged["id"] = "unreviewed-workflow"
    forged["nodes"].append(
        {
            "id": "not-review",
            "name": "Not Review",
            "type": "n8n-nodes-base.noOp",
            "parameters": {"options": {"action": "sendAndWait"}},
        }
    )
    forged["connections"]["AI Agent"] = {
        "main": [[{"node": "Not Review", "type": "main", "index": 0}]]
    }
    (workflows / "a-reviewed.json").write_text(json.dumps(reviewed), encoding="utf-8")
    (workflows / "b-unreviewed.json").write_text(json.dumps(forged), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-approval-scope
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflows/
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    shared_tools = [
        tool for tool in report.tool_surface_facts.tools if tool.name == "shared_delete"
    ]
    assert len(shared_tools) == 2
    assert report.frameworks["n8n"]["human_review_node_count"] == 1
    findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-POLICY-APPROVAL-MISSING"
        and finding.tool_name == "shared_delete"
    ]
    assert len(findings) == 2
    assert {finding.source.ref for finding in findings if finding.source is not None} == {
        "workflows/a-reviewed.json#node:reviewed-tool",
        "workflows/b-unreviewed.json#node:unreviewed-tool",
    }


def test_n8n_regular_code_node_bound_as_ai_tool_gets_code_execution_hint(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _workflow()
    for node in workflow["nodes"]:
        if node["id"] == "code-tool":
            node["type"] = "n8n-nodes-base.code"
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-code-tool
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    tool = next(item for item in report.tool_inventory if item["name"] == "Run Audit Code")
    assert tool["source_type"] == "n8n_code_tool"
    assert "code_execution" in tool["risk_tags"]


def test_n8n_disabled_tool_nodes_are_not_live_tools_or_credentials(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _minimal_agent_workflow(
        {
            "id": "disabled-code",
            "name": "disabled_code",
            "type": "n8n-nodes-base.code",
            "disabled": True,
            "parameters": {"description": "Disabled code.", "jsCode": "return [];"},
            "credentials": {"githubApi": {"id": "cred-disabled", "name": "prod_github"}},
        }
    )
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-disabled
agent:
  name: n8n-agent
environment:
  target: production_like
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert "disabled_code" not in {tool["name"] for tool in report.tool_inventory}
    assert report.frameworks["n8n"]["code_tool_count"] == 0
    assert report.frameworks["n8n"]["credential_ref_count"] == 0
    assert not any(
        finding.check_id == "SHIP-POLICY-APPROVAL-MISSING"
        and finding.tool_name == "disabled_code"
        for finding in report.findings
    )


def test_n8n_tool_inventory_source_uses_n8n_inventory_wrapper_type(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "mcp-tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "inventory_tool",
                        "description": "Static inventory entry.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-inventory
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  tool_inventories:
    - path: mcp-tools.json
""",
        encoding="utf-8",
    )

    payload = inspect_sources(config_path=project / "shipgate.yaml")

    assert payload["sources"] == [
        {
            "id": "n8n_inventory:mcp-tools.json",
            "type": "n8n_inventory",
            "tool_count": 1,
            "sample_tool": "inventory_tool",
            "warnings": [],
        }
    ]


def test_n8n_accepts_community_only_workflow_with_version_id(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "workflow.json").write_text(
        json.dumps(_community_only_workflow(include_version_id=True)),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-community
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["n8n"]["workflow_count"] == 1
    assert report.tool_inventory[0]["name"] == "Community Tool"
    assert any(
        "no first-party node types" in warning
        for warning in report.frameworks["n8n"]["warnings"]
    )
    assert any(
        finding.check_id == "SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
        and finding.evidence["surface"]["kind"] == "community_tool"
        for finding in report.findings
    )


def test_n8n_warns_on_community_like_workflow_without_version_id(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "community.json").write_text(
        json.dumps(_community_only_workflow(include_version_id=False)),
        encoding="utf-8",
    )
    _write_workflow(workflows / "first-party.json")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-community-warning
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflows/
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["n8n"]["workflow_count"] == 1
    assert any(
        "no first-party node types and no versionId" in warning
        for warning in report.frameworks["n8n"]["warnings"]
    )


def test_n8n_workflow_ids_are_namespaced_by_source_path(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    _write_workflow(workflows / "a.json", tool_node_id="a-tool", tool_name="a_tool")
    _write_workflow(workflows / "b.json", tool_node_id="b-tool", tool_name="b_tool")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-workflow-ids
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflows/
""",
        encoding="utf-8",
    )
    manifest = load_manifest(project / "shipgate.yaml")
    loaded_sources, artifacts = load_n8n_artifacts(manifest, project)

    assert artifacts is not None
    workflow_ids = {workflow["id"] for workflow in artifacts.workflows}
    assert "workflows/a.json#workflow-1" in workflow_ids
    assert "workflows/b.json#workflow-1" in workflow_ids
    tool_workflow_ids = {
        tool.name: tool.annotations["n8n_workflow_id"]
        for source in loaded_sources
        for tool in source.tools
        if tool.name in {"a_tool", "b_tool"}
    }
    assert tool_workflow_ids == {
        "a_tool": "workflows/a.json#workflow-1",
        "b_tool": "workflows/b.json#workflow-1",
    }


def test_n8n_same_named_tools_in_different_workflows_are_not_deduped(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    _write_workflow(workflows / "a.json", tool_node_id="a-tool", tool_name="lookup_customer")
    _write_workflow(workflows / "b.json", tool_node_id="b-tool", tool_name="lookup_customer")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-same-names
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflows/
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    inventory_entries = [
        tool for tool in report.tool_inventory if tool["name"] == "lookup_customer"
    ]
    assert len(inventory_entries) == 2
    assert {tool["source_ref"] for tool in inventory_entries} == {
        "workflows/a.json#node:a-tool",
        "workflows/b.json#node:b-tool",
    }


def test_n8n_array_export_file_loads_multiple_workflows(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflows = [
        _workflow(tool_node_id="a-tool", tool_name="alpha_tool"),
        _workflow(tool_node_id="b-tool", tool_name="beta_tool"),
    ]
    workflows[0]["id"] = "alpha-workflow"
    workflows[1]["id"] = "beta-workflow"
    (project / "bundle.json").write_text(json.dumps(workflows), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-array-export
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: bundle.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["n8n"]["workflow_count"] == 2
    inventory = {tool["name"]: tool for tool in report.tool_inventory}
    assert inventory["alpha_tool"]["source_ref"] == "bundle.json#node:a-tool"
    assert inventory["beta_tool"]["source_ref"] == "bundle.json#node:b-tool"


def test_n8n_runtime_tool_name_and_runtime_workflow_target_are_dynamic(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _minimal_agent_workflow(
        {
            "id": "runtime-workflow-tool",
            "name": "Runtime Workflow Tool",
            "type": "n8n-nodes-langchain.toolWorkflow",
            "parameters": {
                "toolName": "={{ $json.tool_name }}",
                "description": "Call runtime workflow.",
                "workflowId": "={{ $json.workflow_id }}",
            },
        }
    )
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-runtime-dynamic
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    dynamic_kinds = {
        finding.evidence["surface"]["kind"]
        for finding in report.findings
        if finding.check_id == "SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
    }
    assert {"runtime_tool_name", "unresolved_workflow"} <= dynamic_kinds


def test_n8n_multi_bound_tool_keeps_agent_and_mcp_surfaces(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = {
        "id": "dual-workflow",
        "name": "Dual Workflow",
        "nodes": [
            {
                "id": "dual-tool",
                "name": "Dual Tool",
                "type": "n8n-nodes-langchain.toolWorkflow",
                "parameters": {
                    "description": "Usable by the agent and MCP server.",
                    "workflowId": "local-workflow",
                },
            },
            {
                "id": "agent-a",
                "name": "Agent A",
                "type": "n8n-nodes-langchain.agent",
                "parameters": {},
            },
            {
                "id": "agent-b",
                "name": "Agent B",
                "type": "n8n-nodes-langchain.agent",
                "parameters": {},
            },
            {
                "id": "mcp-trigger",
                "name": "MCP Server Trigger",
                "type": "n8n-nodes-langchain.mcpTrigger",
                "parameters": {},
            },
        ],
        "connections": {
            "Dual Tool": {
                "ai_tool": [
                    [
                        {"node": "Agent A", "type": "ai_tool", "index": 0},
                        {"node": "Agent B", "type": "ai_tool", "index": 0},
                        {"node": "MCP Server Trigger", "type": "ai_tool", "index": 0},
                    ]
                ]
            }
        },
    }
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-dual-binding
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    entries = [tool for tool in report.tool_inventory if tool["name"] == "Dual Tool"]
    assert {tool["source_type"] for tool in entries} == {"mcp", "n8n_workflow_tool"}
    assert report.frameworks["n8n"]["ai_agent_count"] == 2
    assert report.frameworks["n8n"]["mcp_server_trigger_count"] == 1
    assert report.frameworks["n8n"]["mcp_server_exposed_tool_count"] == 1


def test_n8n_risk_hints_use_canonical_tags_for_all_source_types():
    tools = [
        Tool(id=f"tool:{source_type}", name="delete_customer", source_type=source_type)
        for source_type in {
            "n8n_ai_tool",
            "n8n_workflow_tool",
            "n8n_code_tool",
            "n8n_http_tool",
            "n8n_mcp_client_tool",
            "n8n_inventory",
        }
    ]
    enriched = enrich_tools_with_risk_hints(_manifest_stub(), tools)
    allowed = {
        "destructive",
        "external_write",
        "financial_action",
        "customer_communication",
        "sensitive_data_access",
        "infrastructure_change",
        "code_execution",
        "read_only",
        "write",
    }
    for tool in enriched:
        tags = risk_tags(tool)
        assert "write" in tags
        assert set(tags) <= allowed


def test_n8n_redacts_secrets_and_credential_names_from_outputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(project / "workflow.json", include_secret=True)
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-redaction
agent:
  name: n8n-agent
environment:
  target: production_like
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json", "sarif"],
        ci_mode="advisory",
    )

    output = "\n".join(
        [
            (tmp_path / "reports" / "report.json").read_text(encoding="utf-8"),
            (tmp_path / "reports" / "report.sarif").read_text(encoding="utf-8"),
            (tmp_path / "reports" / "packet.json").read_text(encoding="utf-8"),
        ]
    )
    sarif = json.loads((tmp_path / "reports" / "report.sarif").read_text(encoding="utf-8"))
    stripe_key = "sk" + "_live_" + ("a" * 24)
    assert "sk-aaaaaaaaaaaaaaaaaaaaaaaa" not in output
    assert "sk-bbbbbbbbbbbbbbbbbbbbbbbb" not in output
    assert "AKIA1234567890ABCDEF" not in output
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in output
    assert stripe_key not in output
    assert "xoxb-aaaaaaaaaaaaaaaaaaaa" not in output
    assert "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature" not in output
    assert "postgres://shipgate:secret@example.com/n8n" not in output
    assert "sk-workflowaaaaaaaaaaaaaaaa" not in output
    assert "sk-webhookaaaaaaaaaaaaaaaaa" not in output
    assert "sk-humanaaaaaaaaaaaaaaaaaaa" not in output
    assert "Bearer notecredential" not in output
    assert "sk-paramaaaaaaaaaaaaaaaaaaa" not in output
    assert "Bearer schemacredential" not in output
    assert "Bearer outputcredential" not in output
    assert "Bearer abcdefghijklmnop" not in output
    assert "Bearer pinnedtokenvalue" not in output
    assert "stripe_prod_treasury" not in output
    assert "const key" not in output
    assert hashlib.sha256(b"sk-aaaaaaaaaaaaaaaaaaaaaaaa").hexdigest() not in output
    assert hashlib.sha256(b"sk-aaaaaaaaaaaaaaaaaaaaaaaa").hexdigest()[:12] not in output
    assert hashlib.sha256(b"stripe_prod_treasury").hexdigest() not in output
    assert hashlib.sha256(b"stripe_prod_treasury").hexdigest()[:12] not in output
    secret_findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER"
    ]
    assert secret_findings
    secret_kinds = {finding.evidence["secret_kind"] for finding in secret_findings}
    assert {
        "aws_access_key",
        "github_token",
        "stripe_key",
        "slack_token",
        "jwt",
        "database_url",
    } <= secret_kinds
    pointers = {finding.evidence["parameter_pointer"] for finding in secret_findings}
    assert any(pointer.startswith("/nodes/http-tool/parameters") for pointer in pointers)
    assert any(pointer.startswith("/nodes/code-tool/parameters") for pointer in pointers)
    assert any(pointer.startswith("/nodes/code-tool/notes") for pointer in pointers)
    assert any(pointer.startswith("/pinData/") for pointer in pointers)
    assert any(pointer.startswith("/staticData/") for pointer in pointers)
    for finding in secret_findings:
        assert finding.source is not None
        assert finding.source.type == "n8n_workflow"
        assert finding.source.path == "workflow.json"
        assert finding.source.ref != "shipgate.yaml"
        assert finding.source.pointer == finding.evidence["parameter_pointer"]
        assert set(finding.evidence) == {
            "source_ref",
            "parameter_pointer",
            "secret_kind",
        }
    secret_results = [
        result
        for result in sarif["runs"][0]["results"]
        if result["ruleId"] == "SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER"
    ]
    assert secret_results
    for result in secret_results:
        location = result["locations"][0]["physicalLocation"]
        assert location["artifactLocation"]["uri"] == "workflow.json"
        assert location.get("region", {}).get("startLine") is None


def test_n8n_artifacts_redact_names_and_record_ingress_method(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(project / "workflow.json", include_secret=True)
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-artifact-redaction
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )
    manifest = load_manifest(project / "shipgate.yaml")

    loaded_sources, artifacts = load_n8n_artifacts(manifest, project)

    assert artifacts is not None
    artifact_payload = artifacts.model_dump(mode="json")
    tool_payload = [
        tool.model_dump(mode="json")
        for source in loaded_sources
        for tool in source.tools
    ]
    serialized = json.dumps(
        {"artifacts": artifact_payload, "tools": tool_payload},
        sort_keys=True,
    )
    for raw in (
        "sk-workflowaaaaaaaaaaaaaaaa",
        "sk-webhookaaaaaaaaaaaaaaaaa",
        "sk-humanaaaaaaaaaaaaaaaaaaa",
        "sk-paramaaaaaaaaaaaaaaaaaaa",
        "Bearer schemacredential",
        "Bearer outputcredential",
        "Bearer notecredential",
    ):
        assert raw not in serialized
    assert artifacts.workflows[0]["name"] == "Support Agent [REDACTED:openai_api_key]"
    assert artifacts.ingress[0]["name"] == "Webhook [REDACTED:openai_api_key]"
    assert artifacts.ingress[0]["httpMethod"] == "POST"
    assert "public_path_hash" not in artifacts.ingress[0]
    assert artifacts.human_review_nodes[0]["name"] == (
        "Send [REDACTED:openai_api_key]"
    )
    workflow_tool = next(tool for tool in tool_payload if tool["name"] == "Run External Workflow")
    schema_properties = workflow_tool["input_schema"]["properties"]
    assert "[REDACTED:openai_api_key]" in schema_properties
    assert (
        schema_properties["[REDACTED:openai_api_key]"]["description"]
        == "[REDACTED:bearer_token]"
    )
    output_schema = workflow_tool["output_schema"]["properties"]
    assert output_schema["result"]["description"] == "[REDACTED:bearer_token]"


def test_n8n_respond_to_webhook_is_not_ingress(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _workflow()
    workflow["nodes"].append(
        {
            "id": "respond",
            "name": "Respond to Webhook",
            "type": "n8n-nodes-base.respondToWebhook",
            "parameters": {},
        }
    )
    workflow["connections"]["Webhook"] = {
        "main": [[{"node": "Respond to Webhook", "type": "main", "index": 0}]]
    }
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-respond-webhook
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )
    manifest = load_manifest(project / "shipgate.yaml")

    loaded_sources, artifacts = load_n8n_artifacts(manifest, project)

    assert artifacts is not None
    assert len(artifacts.ingress) == 1
    assert artifacts.ingress[0]["node_id"] == "webhook"


def test_n8n_inactive_workflow_is_not_normalized_as_live_surface(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _workflow(tool_node_id="inactive-tool", tool_name="Inactive Delete")
    workflow["active"] = False
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-inactive
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )
    manifest = load_manifest(project / "shipgate.yaml")

    loaded_sources, artifacts = load_n8n_artifacts(manifest, project)

    assert artifacts is not None
    assert artifacts.workflows[0]["active"] is False
    assert [tool for source in loaded_sources for tool in source.tools] == []
    assert artifacts.ingress == []
    assert artifacts.ai_agents == []
    assert any("is inactive; skipping live tool" in warning for warning in artifacts.warnings)


def test_n8n_workflow_tags_and_execution_controls_feed_review(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = _minimal_agent_workflow(
        {
            "id": "retry-http",
            "name": "Delete Billing Record",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "description": "Delete billing record.",
                "method": "DELETE",
                "url": "https://billing.example.com/records/{{$fromAI('record_id')}}",
            },
            "retryOnFail": True,
            "maxTries": 5,
            "continueOnFail": True,
        }
    )
    workflow["tags"] = [{"name": "production"}, {"name": "billing"}]
    workflow["settings"] = {"errorWorkflow": "billing-cleanup"}
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-execution-controls
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )
    manifest = load_manifest(project / "shipgate.yaml")

    loaded_sources, artifacts = load_n8n_artifacts(manifest, project)
    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert artifacts is not None
    assert artifacts.workflows[0]["tags"] == ["production", "billing"]
    assert artifacts.workflows[0]["errorWorkflow"] == "billing-cleanup"
    tool = next(
        item
        for source in loaded_sources
        for item in source.tools
        if item.name == "Delete Billing Record"
    )
    assert tool.source_type == "n8n_http_tool"
    assert tool.annotations["retryPolicy"] == {
        "source": "n8n",
        "retryOnFail": True,
        "maxTries": 5,
    }
    assert tool.annotations["continueOnFail"] is True
    assert tool.annotations["n8n_execution"] == {
        "retryOnFail": True,
        "continueOnFail": True,
        "maxTries": 5,
    }
    assert tool.annotations["n8n_error_workflow"] == "billing-cleanup"
    side_effect_finding = next(
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-SIDEFX-IDEMPOTENCY-MISSING"
        and finding.tool_name == "Delete Billing Record"
    )
    assert side_effect_finding.severity == "critical"
    assert side_effect_finding.evidence["retry_policy_known"] is True


def test_n8n_ai_tool_classification_uses_parameter_signature(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workflow = {
        "id": "signature-workflow",
        "name": "Signature Workflow",
        "nodes": [
            {
                "id": "http-tool",
                "name": "Signature HTTP",
                "type": "community-nodes-example.anyNode",
                "parameters": {
                    "description": "Call external API.",
                    "method": "POST",
                    "url": "https://api.example.com/items",
                },
            },
            {
                "id": "code-tool",
                "name": "Signature Code",
                "type": "community-nodes-example.anyNode",
                "parameters": {"description": "Run code.", "jsCode": "return [];"},
            },
            {
                "id": "workflow-tool",
                "name": "Signature Workflow Tool",
                "type": "community-nodes-example.anyNode",
                "parameters": {
                    "description": "Call workflow.",
                    "workflowId": "child-workflow",
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
            "Signature HTTP": {
                "ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]
            },
            "Signature Code": {
                "ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]
            },
            "Signature Workflow Tool": {
                "ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]
            },
        },
    }
    (project / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: n8n-signature-classification
agent:
  name: n8n-agent
environment:
  target: local
n8n:
  workflows:
    - path: workflow.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    inventory = {tool["name"]: tool for tool in report.tool_inventory}
    assert inventory["Signature HTTP"]["source_type"] == "n8n_http_tool"
    assert inventory["Signature Code"]["source_type"] == "n8n_code_tool"
    assert inventory["Signature Workflow Tool"]["source_type"] == "n8n_workflow_tool"
    assert "code_execution" in inventory["Signature Code"]["risk_tags"]


def test_n8n_detect_and_auto_init_emit_top_level_block(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    _write_workflow(workflows / "agent.json")
    credentials = project / "credentials"
    credentials.mkdir()
    (credentials / "stripe.json").write_text('{"id": "cred-1"}', encoding="utf-8")
    (project / "variables.json").write_text("{}", encoding="utf-8")
    data_tables = project / "data-tables"
    data_tables.mkdir()
    (data_tables / "customers.json").write_text("{}", encoding="utf-8")
    evaluations = project / "evaluations"
    evaluations.mkdir()
    (evaluations / "support.json").write_text("{}", encoding="utf-8")

    result = detect_workspace(project)
    assert result.is_agent_project is True
    assert any(framework.type == "n8n" for framework in result.frameworks)

    manifest_text = render_auto_manifest(project, result)
    assert "n8n:" in manifest_text
    assert "type: n8n" not in manifest_text
    assert "  credential_stubs:" in manifest_text
    assert "    - path: credentials/stripe.json" in manifest_text
    assert "  variable_stubs:" in manifest_text
    assert "    - path: variables.json" in manifest_text
    assert "  data_table_schemas:" in manifest_text
    assert "    - path: data-tables/customers.json" in manifest_text
    assert "  eval_sets:" in manifest_text
    assert "    - path: evaluations/support.json" in manifest_text


def _write_workflow(
    path,
    *,
    include_secret=False,
    tool_node_id="http-tool",
    tool_name="Delete Customer",
):
    path.write_text(
        json.dumps(
            _workflow(
                include_secret=include_secret,
                tool_node_id=tool_node_id,
                tool_name=tool_name,
            )
        ),
        encoding="utf-8",
    )


def _workflow(
    *,
    include_secret=False,
    tool_node_id="http-tool",
    tool_name="Delete Customer",
):
    header = "Bearer abcdefghijklmnop" if include_secret else "Bearer ${N8N_TOKEN}"
    stripe_key = "sk" + "_live_" + ("a" * 24)
    workflow_name = (
        "Support Agent sk-workflowaaaaaaaaaaaaaaaa"
        if include_secret
        else "Support Agent"
    )
    webhook_name = (
        "Webhook sk-webhookaaaaaaaaaaaaaaaaa" if include_secret else "Webhook"
    )
    human_name = "Send sk-humanaaaaaaaaaaaaaaaaaaa" if include_secret else "Send and Wait"
    code = (
        "const key = 'sk-aaaaaaaaaaaaaaaaaaaaaaaa'; "
        "const aws = 'AKIA1234567890ABCDEF'; "
        f"const stripe = '{stripe_key}'; "
        "const slack = 'xoxb-aaaaaaaaaaaaaaaaaaaa'; "
        "const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature'; "
        "const db = 'postgres://shipgate:secret@example.com/n8n'; return [];"
        if include_secret
        else "return [];"
    )
    workflow = {
        "id": "workflow-1",
        "name": workflow_name,
        "nodes": [
            {
                "id": "webhook",
                "name": webhook_name,
                "type": "n8n-nodes-base.webhook",
                "parameters": {"path": "support", "httpMethod": "POST"},
            },
            {
                "id": tool_node_id,
                "name": tool_name,
                "type": "n8n-nodes-langchain.toolHttpRequest",
                "parameters": {
                    "description": "Delete customer account.",
                    "method": "DELETE",
                    "url": "https://api.example.com/customers/{{$fromAI('customer_id','Customer identifier','string')}}",
                    "headers": {
                        "Authorization": header,
                        "X-GitHub-Token": (
                            "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                            if include_secret
                            else "${GITHUB_TOKEN}"
                        ),
                    },
                },
                "credentials": {
                    "stripeApi": {
                        "id": "cred-1",
                        "name": "stripe_prod_treasury",
                    }
                },
            },
            {
                "id": "mcp-client",
                "name": "MCP Client",
                "type": "n8n-nodes-langchain.toolMcp",
                "parameters": {"toolsToInclude": "All"},
            },
            {
                "id": "workflow-tool",
                "name": "Run External Workflow",
                "type": "n8n-nodes-langchain.toolWorkflow",
                "parameters": {
                    "description": "Run external support workflow.",
                    "workflowId": "external-db-workflow-id",
                    **(
                        {
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "sk-paramaaaaaaaaaaaaaaaaaaa": {
                                        "type": "string",
                                        "description": "Bearer schemacredential",
                                    }
                                },
                            },
                            "outputSchema": {
                                "type": "object",
                                "properties": {
                                    "result": {"description": "Bearer outputcredential"}
                                },
                            },
                        }
                        if include_secret
                        else {}
                    ),
                },
            },
            {
                "id": "code-tool",
                "name": "Run Audit Code",
                "type": "n8n-nodes-langchain.toolCode",
                "parameters": {"description": "Run audit code.", "jsCode": code},
                **({"notes": "Rotate Bearer notecredential"} if include_secret else {}),
            },
            {
                "id": "agent",
                "name": "AI Agent",
                "type": "n8n-nodes-langchain.agent",
                "parameters": {},
            },
            {
                "id": "human",
                "name": human_name,
                "type": "n8n-nodes-base.sendAndWait",
                "parameters": {"operation": "sendAndWait"},
            },
        ],
        "connections": {
            tool_name: {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
            "MCP Client": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
            "Run External Workflow": {
                "ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]
            },
            "Run Audit Code": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]},
            "AI Agent": {"main": [[{"node": human_name, "type": "main", "index": 0}]]},
        },
    }
    if include_secret:
        workflow["pinData"] = {
            tool_name: [
                {
                    "json": {
                        "api_key": "sk-bbbbbbbbbbbbbbbbbbbbbbbb",
                        "authorization": "Bearer pinnedtokenvalue",
                    }
                }
            ]
        }
        workflow["staticData"] = {
            "node": {
                tool_node_id: {
                    "last_response": {
                        "authorization": "Bearer pinnedtokenvalue",
                    }
                }
            }
        }
    return workflow


def _write_mcp_server_workflow(path):
    path.write_text(
        json.dumps(
            {
                "id": "mcp-workflow",
                "name": "MCP Server",
                "nodes": [
                    {
                        "id": "lookup-tool",
                        "name": "Lookup Case",
                        "type": "n8n-nodes-langchain.toolWorkflow",
                        "parameters": {
                            "description": "Look up support case metadata.",
                            "workflowId": "case-workflow",
                        },
                    },
                    {
                        "id": "mcp-trigger",
                        "name": "MCP Server Trigger",
                        "type": "n8n-nodes-langchain.mcpTrigger",
                        "parameters": {},
                    },
                ],
                "connections": {
                    "Lookup Case": {
                        "ai_tool": [
                            [
                                {
                                    "node": "MCP Server Trigger",
                                    "type": "ai_tool",
                                    "index": 0,
                                }
                            ]
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _minimal_agent_workflow(tool_node):
    return {
        "id": "minimal-workflow",
        "name": "Minimal Agent",
        "nodes": [
            tool_node,
            {
                "id": "agent",
                "name": "AI Agent",
                "type": "n8n-nodes-langchain.agent",
                "parameters": {},
            },
        ],
        "connections": {
            tool_node["name"]: {
                "ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]
            }
        },
    }


def _community_only_workflow(*, include_version_id):
    workflow = {
        "id": "community-workflow",
        "name": "Community Agent",
        "nodes": [
            {
                "id": "community-tool",
                "name": "Community Tool",
                "type": "community-nodes-example.toolMagic",
                "parameters": {"description": "Community tool."},
            },
            {
                "id": "community-agent",
                "name": "Community Agent",
                "type": "community-nodes-example.agent",
                "parameters": {},
            },
        ],
        "connections": {
            "Community Tool": {
                "ai_tool": [[{"node": "Community Agent", "type": "ai_tool", "index": 0}]]
            }
        },
    }
    if include_version_id:
        workflow["versionId"] = "community-version-1"
    return workflow


def _manifest_stub():
    return type(
        "Manifest",
        (),
        {
            "risk_overrides": type("Overrides", (), {"tools": {}})(),
        },
    )()
