import json

import pytest

from agents_shipgate.checks.adk import _has_long_running_contract
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.schemas.manifest import ToolSourceConfig

# A shared mapping tool bound to a coordinator and two sub-agents: the
# canonical Google ADK multi-agent shape (see google/adk-samples). The
# module-level raise proves the extractor never imports the file.
SHARED_TOOL_AGENT_SOURCE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

raise RuntimeError("this file must never be imported")


def map_salesforce_account_to_sap_bp(account_id: str) -> dict:
    """Map a Salesforce account to an SAP business partner."""
    return {"business_partner": account_id}


def map_salesforce_product_to_sap_material(product_id: str) -> dict:
    """Map a Salesforce product to an SAP material."""
    return {"material": product_id}


tool_map_account = FunctionTool(func=map_salesforce_account_to_sap_bp)
tool_map_product = FunctionTool(func=map_salesforce_product_to_sap_material)

salesforce_agent = LlmAgent(
    name="salesforce_agent",
    instruction="Read Salesforce records.",
    tools=[tool_map_account, tool_map_product],
)

sap_agent = LlmAgent(
    name="sap_agent",
    instruction="Read SAP records.",
    tools=[tool_map_account, tool_map_product],
)

root_agent = LlmAgent(
    name="smart_closer",
    instruction="Coordinate Salesforce and SAP mapping.",
    tools=[tool_map_account, tool_map_product],
    sub_agents=[salesforce_agent, sap_agent],
)
'''

SHARED_TOOL_MANIFEST = """
version: "0.1"
project:
  name: adk-shared-function-tool
agent:
  name: smart_closer
  declared_purpose:
    - map salesforce records onto sap records
environment:
  target: local
tool_sources:
  - id: adk_smart_closer
    type: google_adk
    path: agent.py
"""


def _shared_tool_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(SHARED_TOOL_AGENT_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(SHARED_TOOL_MANIFEST, encoding="utf-8")
    return project


def test_google_adk_python_static_extraction_without_importing_user_code(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Support
  version: "1.0"
paths:
  /records:
    get:
      operationId: support.lookup_record
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (project / "mcp.json").write_text(
        """
{
  "tools": [
    {
      "name": "support.search",
      "description": "Search support records.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "agent.py").write_text(
        """
from pathlib import Path
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, LongRunningFunctionTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import OpenAPIToolset

raise RuntimeError("this file must never be imported")

def guard(*args, **kwargs):
    return None

def lookup(case_id: str) -> dict:
    \"\"\"Look up support case metadata.\"\"\"
    return {"status": "ok", "case_id": case_id}

def request_approval(amount: float) -> dict:
    \"\"\"Request approval for a reimbursement.\"\"\"
    return {"status": "pending"}

EVAL_FILES = ["evals.json"]
lookup_tool = FunctionTool(func=lookup)
approval_tool = LongRunningFunctionTool(func=request_approval)
api_toolset = OpenAPIToolset(spec_str=Path("openapi.yaml").read_text(), spec_str_type="yaml")
mcp_toolset = McpToolset(tool_filter=["support.search"], inventory_path="mcp.json")

root_agent = LlmAgent(
    name="root_agent",
    instruction="Handle support reimbursements.",
    tools=[
        lookup_tool,
        approval_tool,
        api_toolset,
        mcp_toolset,
    ],
    before_tool_callback=guard,
)
""",
        encoding="utf-8",
    )
    (project / "evals.json").write_text('{"eval_set_id": "support"}', encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-python-test
agent:
  name: root-agent
  declared_purpose:
    - handle support reimbursements
environment:
  target: local
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
google_adk:
  eval_sets:
    - evals.json
policies:
  require_approval_for_tools:
    - request_approval
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["google_adk"]["agent_count"] == 1
    assert report.frameworks["google_adk"]["function_tool_count"] == 2
    assert report.frameworks["google_adk"]["long_running_tool_count"] == 1
    assert report.frameworks["google_adk"]["toolset_count"] == 2
    assert report.frameworks["google_adk"]["dynamic_toolset_count"] == 0
    assert report.frameworks["google_adk"]["eval_file_count"] == 1
    names = {tool["name"] for tool in report.tool_inventory}
    assert {"lookup", "request_approval", "support.lookup_record", "support.search"} <= names
    assert "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE" not in {
        finding.check_id for finding in report.findings
    }
    assert "SHIP-ADK-LONGRUNNING-CONTRACT-MISSING" in {
        finding.check_id for finding in report.findings
    }


def test_google_adk_shared_function_tool_is_one_capability_with_three_bindings(tmp_path):
    """Regression for #321.

    Binding one ``FunctionTool`` to a coordinator and two sub-agents used to
    emit one tool observation per binding; the second collided on
    ``(source_type, source_id, native_locator)`` and aborted the scan with
    ``InputParseError`` before any finding or release decision existed.

    The function is one action, so it must enter the catalog exactly once
    while every agent that can call it keeps a first-class binding edge.
    """
    project = _shared_tool_project(tmp_path)

    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    # A decision exists at all: the input no longer fails to parse.
    assert exit_code == 0
    assert report.release_decision is not None

    # One canonical observation per function definition, not per binding.
    catalog = {entry["name"]: entry for entry in report.tool_catalog}
    assert set(catalog) == {
        "map_salesforce_account_to_sap_bp",
        "map_salesforce_product_to_sap_material",
    }
    for entry in catalog.values():
        assert len(entry["observation_ids"]) == 1

    # Every binding survives, and all three agents reach both tools.
    graph = report.binding_surface_facts
    agents = {agent.name: agent.agent_id for agent in graph.agents}
    assert set(agents) == {"smart_closer", "salesforce_agent", "sap_agent"}
    bound = {(edge.agent_id, edge.tool_id) for edge in graph.tool_edges}
    assert bound == {
        (agent_id, entry["tool_id"])
        for agent_id in agents.values()
        for entry in catalog.values()
    }
    assert graph.root_agent_id == agents["smart_closer"]
    assert sorted(graph.reachable_tool_ids) == sorted(
        entry["tool_id"] for entry in catalog.values()
    )
    assert graph.possible_tool_ids == []
    assert graph.issues == []

    # Reviewer evidence names every binding agent, not just the first one.
    for entry in catalog.values():
        claims = entry["binding_assessment"]["claims"]
        assert {claim["value"].split("->")[0] for claim in claims} == set(agents.values())

    # Unique tools and bindings stay separately countable.
    surface = report.frameworks["google_adk"]
    assert surface["agent_count"] == 3
    assert surface["function_tool_count"] == 2
    assert surface["tool_binding_count"] == 6
    assert surface["warnings"] == []


def test_google_adk_shared_toolset_variable_is_loaded_once(tmp_path):
    """One toolset construction shared by two agents is one tool surface.

    Re-loading it per binding would inflate the catalog with duplicate
    observations of the same MCP inventory under different source ids.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "mcp.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "support.search",
                        "description": "Search support records.",
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (project / "agent.py").write_text(
        """
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset

raise RuntimeError("this file must never be imported")

shared_toolset = McpToolset(tool_filter=["support.search"], inventory_path="mcp.json")

reader_agent = LlmAgent(name="reader_agent", tools=[shared_toolset])
root_agent = LlmAgent(
    name="root_agent",
    tools=[shared_toolset],
    sub_agents=[reader_agent],
)
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-shared-toolset
agent:
  name: root-agent
  declared_purpose:
    - search support records
environment:
  target: local
tool_sources:
  - id: adk_shared_toolset
    type: google_adk
    path: agent.py
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    catalog = [entry for entry in report.tool_catalog if entry["name"] == "support.search"]
    assert len(catalog) == 1
    graph = report.binding_surface_facts
    agents = {agent.name: agent.agent_id for agent in graph.agents}
    assert {(edge.agent_id, edge.tool_id) for edge in graph.tool_edges} == {
        (agents["root_agent"], catalog[0]["tool_id"]),
        (agents["reader_agent"], catalog[0]["tool_id"]),
    }
    assert report.frameworks["google_adk"]["toolset_count"] == 1
    assert report.frameworks["google_adk"]["tool_binding_count"] == 2


def test_google_adk_conflicting_long_running_bindings_route_to_review(tmp_path):
    """One function bound as both long-running and standard is contradictory.

    Collapsing to one observation must not let binding order pick the
    contract: keep the stricter one and surface the conflict.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        """
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, LongRunningFunctionTool

raise RuntimeError("this file must never be imported")


def start_migration(tenant_id: str) -> dict:
    \"\"\"Start a tenant migration.\"\"\"
    return {"status": "pending"}


fast = FunctionTool(func=start_migration)
slow = LongRunningFunctionTool(func=start_migration)

worker_agent = LlmAgent(name="worker_agent", tools=[fast])
root_agent = LlmAgent(name="root_agent", tools=[slow], sub_agents=[worker_agent])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-long-running-conflict
agent:
  name: root-agent
  declared_purpose:
    - migrate tenants
environment:
  target: local
tool_sources:
  - id: adk_conflict
    type: google_adk
    path: agent.py
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    surface = report.frameworks["google_adk"]
    assert surface["function_tool_count"] == 1
    assert surface["tool_binding_count"] == 2
    assert any("long-running" in warning for warning in surface["warnings"])
    assert "SHIP-ADK-LONGRUNNING-CONTRACT-MISSING" in {
        finding.check_id for finding in report.findings
    }


def test_google_adk_true_duplicate_source_still_fails_closed(tmp_path):
    """Sharing a tool is not the same as declaring one twice.

    The observation-identity guard exists to reject a genuinely duplicated
    declaration within one source; collapsing shared bindings must not
    weaken it into "same locator is always fine".
    """
    project = _shared_tool_project(tmp_path)
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-duplicate-entrypoint
agent:
  name: smart_closer
  declared_purpose:
    - map salesforce records onto sap records
environment:
  target: local
google_adk:
  python_entrypoints:
    - agent.py
    - agent.py
""",
        encoding="utf-8",
    )

    with pytest.raises(InputParseError) as excinfo:
        run_scan(
            config_path=project / "shipgate.yaml",
            output_dir=tmp_path / "reports",
            formats=["json"],
            ci_mode="advisory",
        )

    assert "Duplicate tool observation identity" in str(excinfo.value)


def test_google_adk_agent_config_dynamic_toolset_findings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.yaml").write_text(
        """
agent_class: LlmAgent
name: root_agent
instruction: Review support cases.
tools:
  - name: McpToolset
  - name: OpenAPIToolset
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-config-test
agent:
  name: root-agent
environment:
  target: production_like
tool_sources:
  - id: adk
    type: google_adk
    path: agent.yaml
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    check_ids = {finding.check_id for finding in report.findings}
    dynamic_findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE"
    ]
    assert "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE" in check_ids
    assert "SHIP-ADK-MCP-TOOLSET-UNFILTERED" in check_ids
    assert "SHIP-ADK-EVAL-COVERAGE-MISSING" in check_ids
    assert len(dynamic_findings) == 2
    assert {finding.evidence["toolset"]["kind"] for finding in dynamic_findings} == {
        "mcp",
        "openapi",
    }
    for finding in dynamic_findings:
        assert finding.confidence == "high"
        assert finding.evidence["explicit_inventory"] is False
        assert set(finding.evidence["toolset"]) == {
            "kind",
            "source_ref",
            "agent_name",
        }
    doctor = inspect_sources(config_path=project / "shipgate.yaml")
    assert doctor["frameworks"]["google_adk"]["dynamic_toolset_count"] == 2


def test_google_adk_agent_config_non_list_tools_fails_closed(tmp_path):
    """A non-list ``tools:`` value must surface as a dynamic/unparseable
    toolset, not silently collapse to a confident ``tool_count: 0``.

    Regression for the fail-open path: ``tools`` present but in a shape the
    static extractor cannot enumerate (here a templated string) previously
    became ``[]`` with no warning and no finding, reading as a deliberate
    zero-tool agent. It must now route to the dynamic-toolset signal.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.yaml").write_text(
        """
agent_class: LlmAgent
name: root_agent
instruction: Review support cases.
tools: ${RUNTIME_TOOLSET}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-config-test
agent:
  name: root-agent
environment:
  target: production_like
tool_sources:
  - id: adk
    type: google_adk
    path: agent.yaml
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    check_ids = {finding.check_id for finding in report.findings}
    assert "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE" in check_ids
    doctor = inspect_sources(config_path=project / "shipgate.yaml")
    adk = doctor["frameworks"]["google_adk"]
    assert adk["dynamic_toolset_count"] == 1
    # The unparseable surface must leave an evidence trail, not a silent pass.
    assert any("unparseable" in w or "dynamic" in w for w in adk["warnings"])


def test_google_adk_top_level_config_can_supply_inputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "support.lookup",
      "description": "Look up support metadata.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-top-level-test
agent:
  name: root-agent
  declared_purpose:
    - look up support metadata
environment:
  target: local
google_adk:
  tool_inventories:
    - tools.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.tool_catalog[0]["name"] == "support.lookup"
    assert report.tool_inventory == []
    assert report.frameworks["google_adk"]["tool_inventory_file_count"] == 1
    assert "SHIP-ADK-EVAL-COVERAGE-MISSING" not in {
        finding.check_id for finding in report.findings
    }


def test_google_adk_source_rejects_path_traversal(tmp_path):
    outside = tmp_path / "agent.py"
    outside.write_text("root_agent = None", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    manifest = type(
        "Manifest",
        (),
        {
            "tool_sources": [
                ToolSourceConfig(id="adk", type="google_adk", path="../agent.py")
            ],
            "google_adk": None,
        },
    )()

    try:
        load_google_adk_artifacts(manifest, project)
    except InputParseError as exc:
        assert "resolves outside manifest directory" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")


def test_sarif_report_is_written(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text('{"tools": []}', encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: sarif-test
agent:
  name: sarif-agent
  declared_purpose:
    - test reporting
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )

    run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["sarif"],
        ci_mode="advisory",
    )

    payload = json.loads((tmp_path / "reports" / "report.sarif").read_text(encoding="utf-8"))
    assert payload["version"] == "2.1.0"
    assert payload["runs"][0]["tool"]["driver"]["name"] == "Agents Shipgate"
    assert payload["runs"][0]["results"]


def test_google_adk_long_running_contract_accepts_google_operation_shape():
    assert _has_long_running_contract(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "done": {"type": "boolean"},
                "metadata": {"type": "object"},
            },
        }
    )
