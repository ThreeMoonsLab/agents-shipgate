import json

import pytest
import yaml

from agents_shipgate.checks.adk import _has_long_running_contract
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.core.adopter_text import (
    DUPLICATE_TOOL_IN_SOURCE,
    internal_vocabulary,
)
from agents_shipgate.core.artifact_models import GoogleAdkArtifacts
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.google_adk import (
    _load_python_path,
    load_google_adk_artifacts,
)
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

    # Still fails closed — and now says so in the adopter's terms (#329): the
    # tool, the file it came from, and the two places a duplicate can be
    # declared. The identity triple that detected it stays in `details`.
    message = str(excinfo.value)
    assert "map_salesforce_account_to_sap_bp" in message
    assert "agent.py" in message
    assert "shipgate.yaml" in message
    assert not internal_vocabulary(message), message
    assert excinfo.value.details["failure"] == DUPLICATE_TOOL_IN_SOURCE
    assert excinfo.value.details["native_locator"] == (
        "agent.py#map_salesforce_account_to_sap_bp"
    )


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


# The google/adk-samples multi-agent shape, spelled the way real samples
# spell it: the LlmAgent's ``name=`` differs from the Python variable, and
# ``sub_agents`` names the variable. Reproduced from google/adk-samples#1745,
# where every tool the sub-agents owned — including all three financial
# writes — fell out of the analyzed surface with no evidence gap naming it.
SUB_AGENT_NAME_MISMATCH_SOURCE = '''
from google.adk.agents import LlmAgent

raise RuntimeError("this file must never be imported")


def get_salesforce_opportunities() -> list[dict]:
    """List open Salesforce opportunities."""
    return []


def create_salesforce_quote(opportunity_id: str) -> str:
    """Create a Salesforce quote."""
    return "quote"


def create_sap_sales_order(business_partner_id: str) -> str:
    """Create an SAP sales order."""
    return "order"


def get_manager_email() -> str:
    """Look up the approving manager."""
    return "manager@example.com"


salesforce_agent = LlmAgent(
    name="SalesforceAgent",
    instruction="Work Salesforce records.",
    tools=[get_salesforce_opportunities, create_salesforce_quote],
)

sap_agent = LlmAgent(
    name="SapAgent",
    instruction="Work SAP records.",
    tools=[create_sap_sales_order],
)

root_agent = LlmAgent(
    name="SmartCloserAgent",
    instruction="Coordinate Salesforce and SAP.",
    tools=[get_manager_email],
    sub_agents=[salesforce_agent, sap_agent],
)
'''

SUB_AGENT_NAME_MISMATCH_MANIFEST = """
version: "0.1"
project:
  name: adk-sub-agent-names
agent:
  name: SmartCloserAgent
  declared_purpose:
    - close deals across salesforce and sap
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
agent_bindings:
  root:
    source_id: adk_agent
    object: SmartCloserAgent
"""


def _sub_agent_name_mismatch_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(SUB_AGENT_NAME_MISMATCH_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        SUB_AGENT_NAME_MISMATCH_MANIFEST, encoding="utf-8"
    )
    return project


def test_google_adk_sub_agents_resolve_variable_spellings_to_agent_names(tmp_path):
    """``sub_agents=[salesforce_agent]`` must reach the agent named SalesforceAgent.

    ADK routes a handoff by the agent's ``name=``; the list spells the Python
    variable. Reading the variable as an agent name produced one phantom node
    per sub-agent — holding no tools — so every sub-agent tool dropped out of
    the root-reachable surface (#385).
    """
    project = _sub_agent_name_mismatch_project(tmp_path)

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    assert sorted(agent.name for agent in graph.agents) == [
        "SalesforceAgent",
        "SapAgent",
        "SmartCloserAgent",
    ]
    names = {entry["tool_id"]: entry["name"] for entry in report.tool_catalog}
    assert sorted(names[tool_id] for tool_id in graph.reachable_tool_ids) == [
        "create_salesforce_quote",
        "create_sap_sales_order",
        "get_manager_email",
        "get_salesforce_opportunities",
    ]
    assert graph.unbound_tool_ids == []


def test_google_adk_unreachable_sub_agent_tools_are_never_silently_excluded(tmp_path):
    """No catalog tool is both outside the analyzed surface and unnamed.

    The root is pinned to the Salesforce sub-agent, so the coordinator's own
    tool and the whole SAP surface are unreachable. Being told the gate did
    not look is materially different from being told nothing, so every
    unreached tool must appear in ``evidence_gaps`` by name.
    """
    project = _sub_agent_name_mismatch_project(tmp_path)
    (project / "shipgate.yaml").write_text(
        SUB_AGENT_NAME_MISMATCH_MANIFEST.replace(
            "object: SmartCloserAgent", "object: SalesforceAgent"
        ),
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    names = {entry["tool_id"]: entry["name"] for entry in report.tool_catalog}
    unreached = {names[tool_id] for tool_id in graph.unbound_tool_ids}
    assert unreached == {"create_sap_sales_order", "get_manager_email"}

    decision = report.release_decision
    assert decision is not None
    gap_text = " ".join(
        f"{gap.subject} {gap.why}" for gap in decision.evidence_coverage.evidence_gaps
    )
    for name in unreached:
        assert name in gap_text
    assert decision.decision == "insufficient_evidence"


def test_google_adk_declaration_resolves_an_agent_the_scan_already_named(tmp_path):
    """A declaration naming an observed agent must resolve, not collide with it.

    Seeding a synthetic node for every declared agent gave a structurally
    observed agent a second, source-id-less node; the resolver then saw two
    candidates for the name and rejected both. Declaring an agent the scan
    itself reported was self-defeating (#385).
    """
    project = _sub_agent_name_mismatch_project(tmp_path)
    (project / "shipgate.yaml").write_text(
        SUB_AGENT_NAME_MISMATCH_MANIFEST
        + """  declarations:
    - agent: SapAgent
      complete: true
      tools:
        - tool: create_sap_sales_order
      reason: Reviewed from the sap_agent LlmAgent tools list.
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    assert [agent.name for agent in graph.agents].count("SapAgent") == 1
    assert not [issue for issue in graph.issues if issue.kind == "unresolved_agent_binding"]
    declared = {
        edge.tool_id
        for edge in graph.tool_edges
        if edge.provenance_kind == "static_declaration"
    }
    names = {entry["tool_id"]: entry["name"] for entry in report.tool_catalog}
    assert {names[tool_id] for tool_id in declared} == {"create_sap_sales_order"}


def test_google_adk_partially_named_sub_agents_fail_closed(tmp_path):
    """Naming two of three sub-agents must not report the two as the whole set.

    The un-nameable element is a branch of the capability surface that was
    not followed; the graph has to say so instead of reporting the named
    subset as complete.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        '''
from google.adk.agents import LlmAgent

raise RuntimeError("this file must never be imported")


def read_case(case_id: str) -> dict:
    """Read one support case."""
    return {"case_id": case_id}


def escalate(case_id: str) -> dict:
    """Escalate one support case."""
    return {"case_id": case_id}


reader_agent = LlmAgent(name="ReaderAgent", tools=[read_case])
root_agent = LlmAgent(
    name="RootAgent",
    tools=[escalate],
    sub_agents=[reader_agent, build_partner_agent()],
)
''',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-partial-sub-agents
agent:
  name: RootAgent
  declared_purpose:
    - triage support cases
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
agent_bindings:
  root:
    source_id: adk_agent
    object: root_agent
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    assert graph.status == "partial"
    assert graph.pass_eligible is False
    assert any(
        issue.kind == "partial_binding_evidence"
        and "not statically named" in issue.message
        for issue in graph.issues
    )


def test_google_adk_imported_sub_agent_is_not_reported_as_a_proven_surface(tmp_path):
    """A sub-agent this scan cannot see must not read as proof of no capability.

    ``from sub import worker`` qualifies to a name, but no agent definition in
    the scanned entrypoint matches it, and ``sub.py`` is not a declared source —
    so ``delete_record`` never enters the catalog at all and the per-tool gap
    cannot cover it. Counting the element as named let the graph report
    ``structural`` / ``pass_eligible`` with an entire sub-agent invisible,
    recreating the silent exclusion this change exists to close (#385 review).
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "sub.py").write_text(
        '''
from google.adk.agents import LlmAgent


def delete_record(record_id: str) -> str:
    """Delete a record permanently."""
    return "deleted"


worker = LlmAgent(name="WorkerAgent", tools=[delete_record])
''',
        encoding="utf-8",
    )
    (project / "root.py").write_text(
        '''
from google.adk.agents import LlmAgent

from sub import worker

raise RuntimeError("this file must never be imported")


def read_status() -> str:
    """Read the pipeline status."""
    return "ok"


root_agent = LlmAgent(name="RootAgent", tools=[read_status], sub_agents=[worker])
''',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-imported-sub-agent
agent:
  name: RootAgent
  declared_purpose:
    - coordinate record work
environment:
  target: local
tool_sources:
  - id: adk_root
    type: google_adk
    path: root.py
agent_bindings:
  root:
    source_id: adk_root
    object: RootAgent
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    assert graph.status == "partial"
    assert graph.pass_eligible is False
    # No phantom node stands in for the agent: an empty tool set on a node
    # named after an import reads as proof the sub-agent has no capability.
    assert [agent.name for agent in graph.agents] == ["RootAgent"]
    assert any(
        issue.kind == "partial_binding_evidence"
        and "sub.worker" in issue.message
        and "not analyzed" in issue.message
        for issue in graph.issues
    )


def test_google_adk_factory_locals_do_not_leak_between_scopes(tmp_path):
    """Two factories reusing one local name must not cross their sub-agents.

    ``_agent_calls`` walks nested functions, so a variable-to-agent map keyed
    by the bare name collapses ``build_a``'s ``worker`` into ``build_b``'s.
    That routed ``RootA`` to ``WorkerB`` — the gate then analyzed a tool the
    root cannot call and excluded the one it can, while still reporting
    ``pass_eligible`` (#385 review).
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        '''
from google.adk.agents import LlmAgent

raise RuntimeError("this file must never be imported")


def read_a() -> str:
    """Read the A ledger."""
    return "a"


def write_b(value: str) -> str:
    """Write to the B ledger."""
    return "b"


def build_a():
    worker = LlmAgent(name="WorkerA", tools=[read_a])
    return LlmAgent(name="RootA", sub_agents=[worker])


def build_b():
    worker = LlmAgent(name="WorkerB", tools=[write_b])
    return LlmAgent(name="RootB", sub_agents=[worker])


root_agent = build_a()
''',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-two-factories
agent:
  name: RootA
  declared_purpose:
    - work the A ledger
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
agent_bindings:
  root:
    source_id: adk_agent
    object: RootA
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    by_id = {agent.agent_id: agent.name for agent in graph.agents}
    assert {
        (by_id[edge.source_agent_id], by_id[edge.target_agent_id])
        for edge in graph.handoff_edges
    } == {("RootA", "WorkerA"), ("RootB", "WorkerB")}
    names = {entry["tool_id"]: entry["name"] for entry in report.tool_catalog}
    assert sorted(names[tool_id] for tool_id in graph.reachable_tool_ids) == ["read_a"]
    # RootB's surface is out of scope for this root, and says so by name.
    decision = report.release_decision
    assert decision is not None
    assert any(
        gap.kind == "missing_binding_evidence" and "write_b" in gap.subject
        for gap in decision.evidence_coverage.evidence_gaps
    )


def test_google_adk_ambiguous_agent_variable_resolves_to_nothing(tmp_path):
    """One name rebound to two agents in one scope is not settled by position.

    Straight-line AST order is not control flow, so picking either binding
    would be a guess about which agent the handoff reaches. Fail closed.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        '''
from google.adk.agents import LlmAgent

raise RuntimeError("this file must never be imported")


def read_a() -> str:
    """Read the A ledger."""
    return "a"


def write_b(value: str) -> str:
    """Write to the B ledger."""
    return "b"


worker = LlmAgent(name="WorkerA", tools=[read_a])
root_agent = LlmAgent(name="RootAgent", sub_agents=[worker])
worker = LlmAgent(name="WorkerB", tools=[write_b])
''',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: adk-ambiguous-agent-variable
agent:
  name: RootAgent
  declared_purpose:
    - work the ledgers
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
agent_bindings:
  root:
    source_id: adk_agent
    object: RootAgent
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    graph = report.binding_surface_facts
    assert graph.status == "partial"
    assert graph.pass_eligible is False
    assert not graph.handoff_edges
    assert any(
        issue.kind == "partial_binding_evidence" and "worker" in issue.message
        for issue in graph.issues
    )


# --- #386: the prescribed remediation must close the gap it was issued for ---


# Neither parameter list is annotated, so the AST can name these two tools but
# cannot read their schemas: ``_json_schema_type`` falls back to ``string`` for
# every one of them. That is a real ``incomplete_surface`` — and the reason a
# reviewed inventory is worth writing, since the inventory is where the real
# parameter types come from. Since #393 a *fully* annotated, fully resolvable
# module reports a proven surface instead, so the inventory tests below need a
# source whose surface genuinely is not proven; see
# ``test_a_fully_static_adk_module_needs_no_inventory_to_reach_high_confidence``
# for the other half of that pair.
_ADK_AGENT_SOURCE = """
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def get_manager_email(employee_id) -> dict:
    \"\"\"Look up the manager's email for an employee.\"\"\"
    return {"email": "manager@example.com"}


def send_email(to, body) -> dict:
    \"\"\"Send an email.\"\"\"
    return {"status": "sent"}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=get_manager_email), FunctionTool(func=send_email)],
)
"""

_ADK_MANIFEST = """
version: "0.1"
project:
  name: adk-inventory-remediation
agent:
  name: closer-agent
  declared_purpose:
    - route approval mail
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
"""


def _inventory_entry_from(instruction: str) -> object:
    """Parse the `- {path: ..., source_id: ...}` entry an instruction prescribes.

    The remediation is only worth emitting if it can be pasted, so every test
    that checks the text reads it through a YAML parser rather than asserting a
    substring — a value that silently splits into two keys still contains the
    substring (#386 review).
    """

    snippet = next(
        part for part in instruction.split("`") if part.strip().startswith("- {")
    )
    return yaml.safe_load(snippet.strip().removeprefix("- "))


def _coverage(report):
    """(catalog size, reachable tools, incomplete_surface subjects)."""

    evidence = report.release_decision.evidence_coverage
    return (
        len(report.tool_catalog),
        evidence.binding_coverage.reachable_tools,
        {
            gap.subject
            for gap in evidence.evidence_gaps
            if gap.kind == "incomplete_surface"
        },
    )


def test_prescribed_inventory_remediation_closes_the_gap_it_was_issued_for(tmp_path):
    """#386: following the emitted instruction must not leave the user worse off.

    The remediation the tool prints for ``incomplete_surface`` used to say only
    "reference it from google_adk.tool_inventories". Doing exactly that made the
    inventory an independent source: the catalog doubled, the reachable/catalog
    ratio fell, the ``action_surface`` selectors that used to resolve became
    ambiguous, and the very gap that asked for the file stayed open. This test
    reads the instruction the report emits, applies it mechanically, and asserts
    the three properties acceptance criteria 1, 2, and 4 name.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_ADK_AGENT_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(_ADK_MANIFEST, encoding="utf-8")

    before, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "before",
        formats=["json"],
        ci_mode="advisory",
    )
    before_catalog, before_reachable, before_gaps = _coverage(before)
    assert before_gaps == {
        "get_manager_email [adk_agent]",
        "send_email [adk_agent]",
    }

    # The instruction itself is under test: an agent following it has only this
    # string to go on, so it has to name the field that makes the file complete
    # the source rather than shadow it.
    instruction = next(
        gap.next_action.expects
        for gap in before.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "incomplete_surface"
    )
    assert "google_adk.tool_inventories" in instruction
    # Parsed, not substring-matched: the instruction's value is that a reader
    # can copy the entry verbatim, so the test reads it the way they would.
    assert _inventory_entry_from(instruction) == {
        "path": "<saved file>",
        "source_id": "adk_agent",
    }

    # Apply it exactly: save the emitted skeleton, reference it with source_id.
    skeleton = (tmp_path / "before" / "suggested-inventory.json").read_text(
        encoding="utf-8"
    )
    (project / "tool-inventory.json").write_text(skeleton, encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        _ADK_MANIFEST
        + """
google_adk:
  tool_inventories:
    - path: tool-inventory.json
      source_id: adk_agent
""",
        encoding="utf-8",
    )

    after, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "after",
        formats=["json"],
        ci_mode="advisory",
    )
    after_catalog, after_reachable, after_gaps = _coverage(after)

    # 1. the gap the instruction was issued for is closed
    assert after_gaps == set()
    # 2. naming tools the source already exposes does not grow the catalog
    assert after_catalog == before_catalog
    # 4. the coverage ratio never falls after a prescribed remediation
    assert after_reachable * before_catalog >= before_reachable * after_catalog
    assert after.source_warnings == []


def test_inventory_without_source_id_is_named_rather_than_silently_degrading(tmp_path):
    """The pre-#386 spelling still loads — and now says what it cost.

    Left silent, a user who follows an older instruction (or an agent working
    from a memorized one) lands back in the reported shape with no third step
    offered. The catalog growth is unchanged for compatibility; what changes is
    that the report names the cause and the one-line repair.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_ADK_AGENT_SOURCE, encoding="utf-8")
    (project / "tool-inventory.json").write_text(
        '{"tools": [{"name": "get_manager_email", "description": "Look it up."}]}',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        _ADK_MANIFEST
        + """
google_adk:
  tool_inventories:
    - tool-inventory.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert len(report.tool_catalog) == 3
    assert len(report.source_warnings) == 1
    warning = report.source_warnings[0]
    assert "declares no source_id" in warning
    assert "source_id='adk_agent'" in warning


def test_source_qualified_action_rows_survive_inventory_completion(tmp_path):
    """#386 review: the tool's own scaffold must still resolve after its own fix.

    ``_action_selector`` emits ``source_id`` on every action row it scaffolds,
    and same-name providers *require* a source-qualified selector. Completing
    the source rekeys the canonical tool to the inventory's identity, so without
    member-source aliases a user who pasted Shipgate's scaffold and then applied
    Shipgate's inventory instruction gets ``unresolved_tool_selector`` on rows
    that resolved a minute earlier.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_ADK_AGENT_SOURCE, encoding="utf-8")
    # Both effects are declared at or above what the scan observes for them.
    # ``get_manager_email`` reads an address, but the name heuristic reads
    # "email" as outbound communication, so the row that keeps this fixture
    # honest is an acknowledged override rather than a stronger effect (#409).
    declarations = """
action_surface:
  actions:
    - tool: get_manager_email
      source_id: adk_agent
      effect: read
      override:
        evidence: agent.py returns a stored address; no client is constructed
        reason: the name matches the comms heuristic but the body sends nothing
      authority:
        mode: none
    - tool: send_email
      source_id: adk_agent
      effect: external_communication
      authority:
        mode: none
"""
    (project / "shipgate.yaml").write_text(
        _ADK_MANIFEST + declarations, encoding="utf-8"
    )

    before, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "before",
        formats=["json"],
        ci_mode="advisory",
    )
    unresolved = {"unresolved_tool_selector", "ambiguous_tool_selector"}
    before_kinds = {
        gap.kind for gap in before.release_decision.evidence_coverage.evidence_gaps
    }
    assert not (before_kinds & unresolved)

    (project / "tool-inventory.json").write_text(
        (tmp_path / "before" / "suggested-inventory.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        _ADK_MANIFEST
        + declarations
        + """
google_adk:
  tool_inventories:
    - path: tool-inventory.json
      source_id: adk_agent
""",
        encoding="utf-8",
    )

    after, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "after",
        formats=["json"],
        ci_mode="advisory",
    )
    evidence = after.release_decision.evidence_coverage
    after_kinds = {gap.kind for gap in evidence.evidence_gaps}

    assert not (after_kinds & unresolved), [
        (gap.kind, gap.subject) for gap in evidence.evidence_gaps
    ]
    # The declarations still apply, so the gap the inventory closed stays closed
    # rather than reappearing as an unresolved-selector row.
    assert "incomplete_surface" not in after_kinds
    assert evidence.semantic_coverage.pass_eligible_actions == 2


@pytest.mark.parametrize(
    "source_id",
    [
        "adk_agent",
        "google_adk:agents/agent,prod.py",
        "google_adk:agents/agent#main.py",
        "google_adk:agents/{env}.py",
        "google_adk:agents/agent: prod.py",
    ],
    ids=["plain", "comma", "hash", "braces", "colon-space"],
)
def test_prescribed_entry_parses_back_to_the_source_it_names(tmp_path, source_id: str):
    """#386 review: an unquoted source id splits the flow mapping it sits in.

    ``source_id`` is unconstrained and generated framework ids embed the
    configured path, so a comma turned ``source_id: google_adk:agent,prod.py``
    into ``source_id: google_adk:agent`` plus a stray ``prod.py`` key — which
    ``extra="forbid"`` then rejects. The exact text the tool prescribed failed
    manifest validation.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_ADK_AGENT_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        _ADK_MANIFEST.replace("id: adk_agent", f"id: {json.dumps(source_id)}"),
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    instruction = next(
        gap.next_action.expects
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "incomplete_surface"
    )
    assert _inventory_entry_from(instruction) == {
        "path": "<saved file>",
        "source_id": source_id,
    }

    # The skeleton note prescribes the same entry and must survive the same way.
    note = json.loads(
        (tmp_path / "reports" / "suggested-inventory.json").read_text(encoding="utf-8")
    )["note"]
    assert _inventory_entry_from(note) == {
        "path": "<saved file>",
        "source_id": source_id,
    }


def _complete_the_source(project, tmp_path, extra: str = "") -> None:
    """Apply the prescribed remediation to an already-scanned project."""

    (project / "tool-inventory.json").write_text(
        (tmp_path / "before" / "suggested-inventory.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        _ADK_MANIFEST
        + extra
        + """
google_adk:
  tool_inventories:
    - path: tool-inventory.json
      source_id: adk_agent
""",
        encoding="utf-8",
    )


def test_the_generated_action_scaffold_still_resolves_after_completion(tmp_path):
    """#386 follow-up: test the selector Shipgate actually emits, `tool_id` and all.

    The earlier regression hand-wrote `tool` + `source_id`, but
    `_action_selector` also emits `tool_id`, and `resolve` prioritizes it.
    Completion rekeys the canonical id, so the *generated* scaffold — the exact
    thing the tool tells a user to paste — still broke.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_ADK_AGENT_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(_ADK_MANIFEST, encoding="utf-8")

    before, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "before",
        formats=["json"],
        ci_mode="advisory",
    )

    templates = [
        gap.next_action.declaration_template
        for gap in before.release_decision.evidence_coverage.evidence_gaps
        if gap.next_action.declaration_template
        and gap.next_action.declaration_template.get("tool") == "get_manager_email"
    ]
    assert templates, "expected a scaffolded action declaration to test"
    template = templates[0]
    # The selector under test is the emitted one, not a hand-written subset.
    assert template["tool_id"]
    assert template["source_id"] == "adk_agent"

    declaration = f"""
action_surface:
  actions:
    - tool: {json.dumps(template["tool"])}
      tool_id: {json.dumps(template["tool_id"])}
      source_id: {json.dumps(template["source_id"])}
      effect: read
      authority:
        mode: none
"""
    _complete_the_source(project, tmp_path, declaration)

    after, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "after",
        formats=["json"],
        ci_mode="advisory",
    )

    kinds = {
        gap.kind for gap in after.release_decision.evidence_coverage.evidence_gaps
    }
    assert "unresolved_tool_selector" not in kinds
    assert "ambiguous_tool_selector" not in kinds
    assert "incomplete_surface" not in kinds


def test_source_qualified_policy_and_suppression_survive_completion(tmp_path):
    """#386 follow-up: aliases have to reach every selector consumer.

    `_action_has_policy_control` and `_matching_suppression` compared the
    canonical primary fields directly, so completing the source silently
    un-declared a source-qualified confirmation policy — the scan then reported
    a missing `confirmation.required` and moved to `blocked` on a manifest the
    user never touched — and made a source-qualified `checks.ignore` inert.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_ADK_AGENT_SOURCE, encoding="utf-8")
    qualified = """
action_surface:
  actions:
    - tool: send_email
      source_id: adk_agent
      effect: write
      authority:
        mode: none
      approval:
        required: true
      safeguards:
        audit_log: true

policies:
  require_confirmation_for_tools:
    - tool: send_email
      source_id: adk_agent

checks:
  ignore:
    - check_id: SHIP-DOC-MISSING-DESCRIPTION
      tool: send_email
      source_id: adk_agent
      reason: "Preview helper is documented in the runbook."
"""
    (project / "shipgate.yaml").write_text(_ADK_MANIFEST + qualified, encoding="utf-8")

    before, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "before",
        formats=["json"],
        ci_mode="advisory",
    )

    def state(report):
        active = {f.check_id for f in report.findings if not f.suppressed}
        suppressed = {f.check_id for f in report.findings if f.suppressed}
        return report.release_decision.decision, active, suppressed

    before_decision, before_active, before_suppressed = state(before)
    assert "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING" not in before_active
    assert "SHIP-DOC-MISSING-DESCRIPTION" in before_suppressed

    _complete_the_source(project, tmp_path, qualified)

    after, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "after",
        formats=["json"],
        ci_mode="advisory",
    )
    after_decision, after_active, after_suppressed = state(after)

    # The confirmation policy still applies, so no control goes missing ...
    assert "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING" not in after_active
    # ... the source-qualified suppression still bites ...
    assert "SHIP-DOC-MISSING-DESCRIPTION" in after_suppressed
    # ... and applying the remediation did not make the verdict worse.
    assert after_decision == before_decision



# --- #393: extraction confidence is measured, not assumed ---------------------


def _proven_module(
    *,
    preamble: str = "",
    extra_tools: str = "",
    agent_kwargs: str = "",
    trailer: str = "",
) -> str:
    """One ADK module with exactly one root agent, varied at four points.

    Every ambiguity below is injected into *this* agent rather than added as a
    sibling. A second module-level ``Agent(...)`` makes the root selector
    ambiguous, which excludes the tools from scope for an unrelated reason and
    would have made these tests pass without measuring anything.
    """

    return f'''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
{preamble}

def lookup_account(record_id: str) -> dict:
    """Look up a Salesforce account."""
    return {{"record_id": record_id}}


def create_quote(record_id: str, amount: float) -> dict:
    """Create a quote against an opportunity."""
    return {{"quote": record_id}}


root_agent = LlmAgent(
    name="smart_closer",
    instruction="Close deals.",
    tools=[
        FunctionTool(func=lookup_account),
        FunctionTool(func=create_quote),{extra_tools}
    ],{agent_kwargs}
)
{trailer}'''


_PROVEN_MANIFEST = """
version: "0.1"
project:
  name: adk-proven-surface
agent:
  name: smart_closer
  declared_purpose:
    - close deals across salesforce records
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
"""

_PROVEN_ACTIONS = """
action_surface:
  actions:
    - tool: lookup_account
      effect: read
      authority:
        mode: none
    - tool: create_quote
      effect: write
      authority:
        mode: none
"""


def _proven_project(tmp_path, *, source: str | None = None, manifest_extra: str = ""):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        source if source is not None else _proven_module(), encoding="utf-8"
    )
    (project / "shipgate.yaml").write_text(
        _PROVEN_MANIFEST + manifest_extra, encoding="utf-8"
    )
    return project


def _scan_proven(tmp_path, project):
    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    return report


def test_a_fully_static_adk_module_needs_no_inventory_to_reach_high_confidence(
    tmp_path,
):
    """#393: `medium` used to be the ceiling of the ADK AST path.

    Nothing a repository could do to its own source raised it, so
    `insufficient_evidence` was not a property of any repository — it was the
    framework's default first-run verdict, reproducible on the most statically
    trivial module there is. The remedy it prescribed was transcription: copy
    the tools Shipgate had just extracted correctly into
    `suggested-inventory.json`, which adds no fact to the system.
    """

    report = _scan_proven(tmp_path, _proven_project(tmp_path))

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}
    evidence = report.release_decision.evidence_coverage
    assert evidence.low_confidence_tool_count == 0
    assert evidence.source_warning_count == 0
    # What remains is the human assertion the gate genuinely needs, not an
    # extraction failure the repository cannot act on.
    assert {gap.kind for gap in evidence.evidence_gaps} == {
        "missing_effect_evidence",
        "missing_authority_evidence",
    }
    # No transcription is requested, so the skeleton is not even written.
    assert not (tmp_path / "reports" / "suggested-inventory.json").exists()


def test_a_proven_adk_surface_with_declared_actions_reaches_a_merge_verdict(tmp_path):
    """The loop terminates. Before #393 this exact project could not.

    Declaring every action's effect and authority — the one thing the gate
    genuinely needs a human for — still left `incomplete_surface` on every tool
    and `insufficient_evidence` as the verdict, because the extraction ceiling
    was unreachable from the manifest. Effect and authority remain human
    assertions; what changed is that they are now the *last* step rather than
    one behind a step no repository could take.
    """

    report = _scan_proven(
        tmp_path, _proven_project(tmp_path, manifest_extra=_PROVEN_ACTIONS)
    )

    decision = report.release_decision
    assert decision.decision == "passed"
    assert decision.evidence_coverage.semantic_coverage.pass_eligible_actions == 2
    assert decision.evidence_coverage.evidence_gaps == []


#: One construct per row, each leaving some part of the tool surface unproven,
#: with the reason code the adapter has to record for it. Every row starts from
#: the module above, which reaches `high` on its own, so the construct is the
#: only variable. The three `mutable_tool_binding` rows and
#: `agent_built_from_kwargs` were all silently promoted to `high` by the first
#: draft of this change.
_UNPROVEN_CONSTRUCTS = [
    pytest.param(
        {
            "preamble": "from external import imported_tool\n",
            "extra_tools": "\n        imported_tool,",
        },
        "unresolved_tool_reference",
        id="unresolved_tool_reference",
    ),
    pytest.param(
        {
            "preamble": "base_tools = []\n",
            "extra_tools": "\n        *base_tools,",
        },
        "unresolved_tool_expression",
        id="starred_tool_element",
    ),
    pytest.param(
        {
            "preamble": (
                "from external import missing\n\nwrapper = FunctionTool(func=missing)\n"
            ),
            "extra_tools": "\n        wrapper,",
        },
        "unresolved_tool_wrapper",
        id="unresolved_tool_wrapper",
    ),
    pytest.param(
        {
            "preamble": "from google.adk.tools.mcp_tool import McpToolset\n",
            "extra_tools": '\n        McpToolset(tool_filter=["a"]),',
        },
        "dynamic_toolset",
        id="dynamic_toolset",
    ),
    pytest.param(
        {
            "preamble": "from google.adk.tools import LongRunningFunctionTool\n",
            "extra_tools": "\n        LongRunningFunctionTool(func=lookup_account),",
        },
        "conflicting_tool_contract",
        id="conflicting_tool_contract",
    ),
    pytest.param(
        {
            "preamble": "from external import helper_agent\n",
            "agent_kwargs": "\n    sub_agents=[helper_agent],",
        },
        "unresolved_sub_agent",
        id="unresolved_sub_agent",
    ),
    pytest.param(
        {
            "preamble": "overrides = {}\n",
            "agent_kwargs": "\n    **overrides,",
        },
        "dynamic_agent_kwargs",
        id="agent_built_from_kwargs",
    ),
    pytest.param(
        {
            "preamble": "from external import imported_tool\n",
            "trailer": "root_agent.tools.append(imported_tool)\n",
        },
        "mutable_tool_binding",
        id="tools_mutated_after_construction",
    ),
    pytest.param(
        {
            "preamble": "from external import imported_tool\n",
            "trailer": "bucket = root_agent.tools\nbucket.append(imported_tool)\n",
        },
        "mutable_tool_binding",
        id="tools_mutated_through_an_alias",
    ),
    pytest.param(
        {
            "preamble": "from external import imported_tool\n",
            "trailer": 'setattr(root_agent, "tools", [imported_tool])\n',
        },
        "mutable_tool_binding",
        id="tools_replaced_by_setattr",
    ),
]


@pytest.mark.parametrize("module_kwargs, reason", _UNPROVEN_CONSTRUCTS)
def test_one_unproven_construct_holds_the_whole_module_at_medium(
    tmp_path, module_kwargs: dict, reason: str
):
    """The proof is earned by the file, not by the agent that reads cleanest.

    Scoping completeness per resolved tool would let `lookup_account` claim a
    proof its own module cannot support: in every row here, a tool nobody
    enumerated is reachable from the same agent.
    """

    project = _proven_project(tmp_path, source=_proven_module(**module_kwargs))

    report = _scan_proven(tmp_path, project)

    catalog = {tool["name"]: tool for tool in report.tool_catalog}
    assert catalog["lookup_account"]["confidence"] == "medium"
    assert catalog["create_quote"]["confidence"] == "medium"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert {gap.subject.split(" ")[0] for gap in gaps} == {
        "lookup_account",
        "create_quote",
    }
    # The row has to name the construct responsible: one sentence repeated on
    # every AST tool in every repository is the defect #393 reports.
    assert all(reason in gap.why for gap in gaps)


def test_a_dynamic_tools_expression_holds_the_module_at_medium(tmp_path):
    """The reported shape: `tools=` is not a literal sequence at all.

    Kept apart from the table because it replaces the tools list rather than
    adding to it, so the agent contributes no tools of its own and a second
    agent has to own the ones under test.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="extra_tools = []\n",
            agent_kwargs="\n    sub_agents=[dynamic_agent],",
        ).replace(
            "root_agent = LlmAgent(",
            'dynamic_agent = LlmAgent(name="dyn", tools=extra_tools + [])\n\n'
            "root_agent = LlmAgent(",
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"medium"}
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("dynamic_tools_expression" in gap.why for gap in gaps)


@pytest.mark.parametrize(
    "signature, reason",
    [
        pytest.param("record_id", "untyped_parameter", id="untyped_parameter"),
        pytest.param(
            "record_id: str, **headers",
            "variadic_parameters",
            id="variadic_parameters",
        ),
        pytest.param(
            "record_id: str",
            "decorated_tool_function",
            id="decorated_tool_function",
        ),
    ],
)
def test_a_function_whose_own_interface_is_unreadable_stays_medium(
    tmp_path, signature: str, reason: str
):
    """A fully resolved module can still hold an unresolvable function.

    An unannotated parameter is typed `string` by the JSON-schema fallback,
    `**kwargs` is dropped altogether, and a decorator replaces the callable ADK
    introspects — each would put a guess into the report wearing a schema's
    clothes. Unlike the module-scoped reasons, this one is about one callable,
    so its siblings keep their proof.
    """

    decorator = (
        "import functools\n\n\n@functools.cache\n"
        if reason == "decorated_tool_function"
        else ""
    )
    project = _proven_project(
        tmp_path,
        source=_proven_module(extra_tools="\n        loose_tool,")
        + f'''

{decorator}def loose_tool({signature}) -> dict:
    """A tool whose interface cannot be read."""
    return {{}}
''',
    )

    report = _scan_proven(tmp_path, project)

    catalog = {tool["name"]: tool for tool in report.tool_catalog}
    assert catalog["loose_tool"]["confidence"] == "medium"
    assert catalog["lookup_account"]["confidence"] == "high"
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert gap.subject.startswith("loose_tool")
    assert reason in gap.why


def test_an_unclassified_extractor_warning_fails_closed(tmp_path, monkeypatch):
    """The backstop, proven load-bearing by a negative control.

    Every ambiguity the extractor knows about routes through
    ``_surface_warning``, which records a reason. A future one added with a
    plain ``artifacts.warnings.append`` would leave ``surface_gaps`` empty and
    promote an unresolved module to ``high`` — the fail-open shape where a
    block-level "safe" signal clears a path-wide guard. Simulating exactly that
    slip must still cost the module its proof.
    """

    from agents_shipgate.inputs import google_adk

    original = google_adk._PythonAdkExtractor._record_eval_references

    def leak_an_unclassified_warning(self):
        original(self)
        self.artifacts.warnings.append("a future ambiguity nobody classified")

    monkeypatch.setattr(
        google_adk._PythonAdkExtractor,
        "_record_eval_references",
        leak_an_unclassified_warning,
    )

    report = _scan_proven(tmp_path, _proven_project(tmp_path))

    assert {tool["confidence"] for tool in report.tool_catalog} == {"medium"}
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert "unclassified_extractor_warning" in gap.why


def test_an_eval_artifact_warning_never_costs_the_surface_its_proof(tmp_path):
    """Eval collateral says nothing about which tools an agent can call."""

    project = _proven_project(
        tmp_path,
        source=_proven_module(trailer='eval_set = "evals/missing.eval.json"\n'),
    )

    report = _scan_proven(tmp_path, project)

    assert any("eval reference" in warning for warning in report.source_warnings)
    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}


def test_a_qualified_module_path_is_not_mistaken_for_an_agent_attribute(tmp_path):
    """``google.adk.tools`` is a package, not an agent's tool list.

    The mutation guard keys on any ``.tools`` access, so without the
    imported-root exclusion a module spelling its imports in full would be
    accused of mutating a tool list it never touched.

    This module is unproven either way: the dotted-import spelling is not one
    ``_qualified_name`` resolves, so the element reads as an unresolvable tool
    expression. That is a separate, pre-existing limitation — what matters here
    is *which* reason is recorded.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="import google.adk.tools\n",
            extra_tools="\n        google.adk.tools.FunctionTool(func=lookup_account),",
        ),
    )

    report = _scan_proven(tmp_path, project)

    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert "unresolved_tool_expression" in gap.why
    assert "mutable_tool_binding" not in gap.why


@pytest.mark.parametrize(
    "definition",
    [
        pytest.param(
            "def build():\n"
            "    def loose_tool(x: str) -> dict:\n"
            '        """Built at runtime."""\n'
            "        return {}\n"
            "    return loose_tool\n\n\n"
            "loose_tool = build()\n",
            id="defined_inside_a_factory",
        ),
        pytest.param(
            "from external import replacement\n\n\n"
            "def loose_tool(x: str) -> dict:\n"
            '    """Outer."""\n'
            "    return {}\n\n\n"
            "loose_tool = replacement\n",
            id="rebound_after_definition",
        ),
        pytest.param(
            "class Ops:\n"
            "    def loose_tool(self, x: str) -> dict:\n"
            '        """Lifted out of a class body."""\n'
            "        return {}\n",
            id="only_defined_as_a_method",
        ),
        pytest.param(
            "import os\n\n"
            'if os.getenv("X"):\n'
            "    def loose_tool(x: str) -> dict:\n"
            '        """A."""\n'
            "        return {}\n"
            "else:\n"
            "    def loose_tool(x: str, y: str) -> dict:\n"
            '        """B."""\n'
            "        return {}\n",
            id="two_conditional_definitions",
        ),
        pytest.param(
            "from external import loose_tool\n\n\n"
            "def loose_tool(x: str) -> dict:\n"
            '    """Shadowed by an import."""\n'
            "    return {}\n",
            id="shadowed_by_an_import",
        ),
    ],
)
def test_a_definition_the_name_may_not_refer_to_is_never_proven(
    tmp_path, definition: str
):
    """Naming a tool and proving its signature are different claims.

    ``tools=[loose_tool]`` resolves through a flat, scope-blind name map, which
    is what lets the adapter report the tool at all. In each of these modules
    that map answers with a definition the running agent will not use — the
    factory's inner function, the pre-rebinding one, a method, whichever
    conditional branch the walk saw last, the local one an import shadows. All
    five reported a proven surface when this change was first written.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(extra_tools="\n        loose_tool,")
        + "\n"
        + definition,
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"medium"}
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert "shadowed_tool_definition" in gap.why


def test_the_conventional_functiontool_wrapper_variable_is_still_proven(tmp_path):
    """`lookup_tool = FunctionTool(func=lookup)` is the idiomatic ADK spelling.

    The rebinding check keys on names bound anywhere in the module, so it has
    to distinguish a name bound *beside* a definition from one bound *over* it.
    Getting this wrong would demote almost every real ADK entrypoint.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module().replace(
            "root_agent = LlmAgent(",
            "lookup_tool = FunctionTool(func=lookup_account)\n\n"
            "root_agent = LlmAgent(",
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}


# --- PR #400 review: five fail-open paths in the high-confidence promotion ---
#
# Every case below reached `release_decision="passed"` with no evidence gaps
# while a reachable tool was omitted from the catalog or its interface was
# represented by a guessed schema. They are grouped by the finding they close.


_INVENTORY_JSON = (
    '{"tools": [{"name": "remote_lookup", "description": "Look up a remote '
    'record by identifier.", "inputSchema": {"type": "object", "properties": '
    '{"q": {"type": "string"}}}}]}'
)


def _toolset_project(tmp_path, *, body: str, agent_kwargs: str = "", trailer: str = ""):
    """A module whose only tools come from a *resolved* MCP toolset."""

    project = tmp_path / "project"
    project.mkdir()
    (project / "inventory.json").write_text(_INVENTORY_JSON, encoding="utf-8")
    (project / "agent.py").write_text(
        f'''
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
{body}

root_agent = LlmAgent(
    name="smart_closer",
    instruction="Close deals.",
    tools=[
        McpToolset(
            tool_filter=["remote_lookup"],
            inventory_path="inventory.json",
        ),
    ],{agent_kwargs}
)
{trailer}''',
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        _PROVEN_MANIFEST
        + """
action_surface:
  actions:
    - tool: remote_lookup
      effect: read
      authority:
        mode: none
""",
        encoding="utf-8",
    )
    return project


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param("FunctionTool(func=imported_tool)", id="imported_function"),
        pytest.param("FunctionTool(func=lambda record_id: {})", id="lambda"),
        pytest.param("FunctionTool(func=helpers.thing)", id="attribute"),
        pytest.param("FunctionTool()", id="missing_func"),
        pytest.param(
            "LongRunningFunctionTool(func=imported_tool)", id="long_running_imported"
        ),
    ],
)
def test_an_inline_wrapper_this_module_cannot_resolve_is_recorded(tmp_path, wrapper):
    """A recognised wrapper naming a function the module does not define.

    `_extract_tool_expr` returned unconditionally from the `FunctionTool`
    branch, so an unresolvable `func` produced no tool, no warning, and no gap.
    The agent could call it; the report said the surface was proven and
    complete without it — a strictly smaller tool surface, labelled `passed`.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble=(
                "from google.adk.tools import LongRunningFunctionTool\n"
                "from external import imported_tool\n"
                "import helpers\n"
            ),
            extra_tools=f"\n        {wrapper},",
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    assert {tool["confidence"] for tool in report.tool_catalog} == {"medium"}
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert "unresolved_tool_wrapper" in gap.why


@pytest.mark.parametrize(
    "shadow",
    [
        pytest.param(
            # Deliberately not `def build(lookup_account): return LlmAgent(...)`,
            # which is the reported repro: a second module-level agent makes the
            # root selector ambiguous, which drops the tools out of scope for an
            # unrelated reason and would let this pass without the guard.
            "\n\ndef build(lookup_account):\n    return lookup_account\n",
            id="function_parameter",
        ),
        pytest.param(
            "\n\nclass lookup_account:\n    pass\n",
            id="class_of_the_same_name",
        ),
        pytest.param(
            "\ntry:\n    pass\nexcept ValueError as lookup_account:\n    pass\n",
            id="exception_target",
        ),
        pytest.param(
            "\nimport sys\n\nmatch sys.argv:\n"
            "    case [lookup_account]:\n        pass\n",
            id="pattern_capture",
        ),
        pytest.param(
            "\n\ndef rebind():\n"
            "    global lookup_account\n"
            "    lookup_account = None\n",
            id="global_declaration",
        ),
    ],
)
def test_every_python_binding_form_costs_a_name_its_proof(tmp_path, shadow: str):
    """`def` and `=` are not the only ways a name gets bound.

    Parameters are `ast.arg`, classes bind through `ClassDef.name`, and
    `except ... as`, `case ... as`, and `global` each have their own shape.
    Collecting only `Name` stores let a parameter named after a module function
    resolve as that function, be marked proven, and return `passed`.
    """

    project = _proven_project(
        tmp_path, source=_proven_module(trailer=shadow.lstrip("\n"))
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("shadowed_tool_definition" in gap.why for gap in gaps)


def test_a_wrapper_variable_rebound_after_assignment_is_not_proven(tmp_path):
    """`_wrapper_assignments` is last-write-wins, so the variable needs checking too.

    Checking only the wrapped function's name left `w = FunctionTool(func=known)`
    followed by `w = imported_tool` resolving through the first assignment.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="from external import imported_tool\n",
            extra_tools="\n        wrapper,",
            trailer=(
                "wrapper = FunctionTool(func=lookup_account)\n"
                "wrapper = imported_tool\n"
            ),
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("shadowed_tool_definition" in gap.why for gap in gaps)


@pytest.mark.parametrize(
    "body, agent_kwargs, trailer, reason",
    [
        pytest.param(
            "\noverrides = {}\n", "\n    **overrides,", "", "dynamic_agent_kwargs",
            id="agent_built_from_kwargs",
        ),
        pytest.param(
            "\nfrom external import imported_tool\n",
            "",
            "root_agent.tools.append(imported_tool)\n",
            "mutable_tool_binding",
            id="tools_mutated_after_construction",
        ),
        pytest.param(
            "\nfrom external import helper_agent\n",
            "\n    sub_agents=[helper_agent],",
            "",
            "unresolved_sub_agent",
            id="unresolved_sub_agent",
        ),
    ],
)
def test_module_gaps_reach_tools_a_resolved_toolset_contributed(
    tmp_path, body: str, agent_kwargs: str, trailer: str, reason: str
):
    """A module can be unproven while owning no function tools at all.

    The finalizer walked only `canonical_function_tools`, so when every tool
    came from a resolved OpenAPI/MCP toolset the loop was empty and the module's
    recorded gaps evaporated. The toolset's own schema is still trustworthy —
    what is not is the claim that these are *the* agent's tools — so the tools
    are lowered, never raised.
    """

    project = _toolset_project(
        tmp_path, body=body, agent_kwargs=agent_kwargs, trailer=trailer
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    assert {tool["confidence"] for tool in report.tool_catalog} == {"medium"}
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert reason in gap.why


def test_a_resolved_toolset_in_a_proven_module_keeps_its_high_confidence(tmp_path):
    """The propagation only ever lowers; it must not disturb the clean case."""

    report = _scan_proven(tmp_path, _toolset_project(tmp_path, body=""))

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}
    assert report.release_decision.decision == "passed"


@pytest.mark.parametrize(
    "trailer",
    [
        pytest.param(
            'getattr(root_agent, "tools").append(imported_tool)\n',
            id="getattr_then_append",
        ),
        pytest.param(
            "import builtins\n"
            'builtins.setattr(root_agent, "tools", [imported_tool])\n',
            id="setattr_through_a_module",
        ),
        pytest.param(
            'delattr(root_agent, "tools")\n',
            id="delattr",
        ),
    ],
)
def test_reflective_access_to_tools_is_not_a_way_around_the_mutation_guard(
    tmp_path, trailer: str
):
    """`getattr(agent, "tools")` carries the attribute name as data.

    It contains no `Attribute` node named `tools`, so the structural check
    walked straight past it and the module kept claiming a proven surface while
    a tool was appended to the agent at runtime.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="from external import imported_tool\n", trailer=trailer
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("mutable_tool_binding" in gap.why for gap in gaps)


def test_an_imported_name_rebound_to_an_agent_is_no_longer_a_package(tmp_path):
    """The module-path exemption has to be binding-aware.

    `from x import agents` leaves `agents` in the alias map; rebinding it to an
    `LlmAgent` does not remove it. `agents.tools.append(...)` was therefore
    exempted as a dotted package path.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="from external import agents, imported_tool\n",
            trailer="agents = root_agent\nagents.tools.append(imported_tool)\n",
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("mutable_tool_binding" in gap.why for gap in gaps)


@pytest.mark.parametrize(
    "annotation, returns",
    [
        pytest.param("set[str]", "dict", id="set"),
        pytest.param("tuple[int, str]", "dict", id="tuple"),
        pytest.param("int | None", "dict", id="optional_union"),
        pytest.param("Optional[int]", "dict", id="typing_optional"),
        pytest.param("Literal['a', 'b']", "dict", id="literal"),
        pytest.param("SomeModel", "dict", id="custom_class"),
        pytest.param("typing.List[str]", "dict", id="module_qualified_generic"),
        pytest.param("list[SomeModel]", "dict", id="list_of_models"),
        pytest.param("dict[int, str]", "dict", id="non_string_dict_key"),
        pytest.param("str", "SomeModel", id="unrepresentable_return"),
        pytest.param("str", "set[str]", id="unrepresentable_return_generic"),
    ],
)
def test_an_annotation_the_emitter_cannot_represent_is_still_a_guess(
    tmp_path, annotation: str, returns: str
):
    """Annotation presence is not proof the emitted schema is faithful.

    `_json_schema_type` reads the unparsed string and falls back to `"string"`
    for everything it does not recognise, so each of these shipped as
    `{"type": "string"}` while the tool was reported high and enumerated.
    `typing.List[str]` is the sharpest case: the type is representable, but the
    emitter's string match misses the module prefix and emits a scalar anyway.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble=(
                "import typing\n"
                "from typing import Literal, Optional\n"
                "from external import SomeModel\n"
            ),
            extra_tools="\n        loose_tool,",
        )
        + f'''

def loose_tool(record_id: {annotation}) -> {returns}:
    """Look up a record by identifier and return the stored fields."""
    return {{}}
''',
    )

    report = _scan_proven(tmp_path, project)

    catalog = {tool["name"]: tool for tool in report.tool_catalog}
    assert catalog["loose_tool"]["confidence"] == "medium"
    assert catalog["lookup_account"]["confidence"] == "high"
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert gap.subject.startswith("loose_tool")
    assert "unrepresentable_annotation" in gap.why


@pytest.mark.parametrize(
    "annotation, returns",
    [
        pytest.param("str", "dict", id="scalars"),
        pytest.param("int", "list", id="bare_containers"),
        pytest.param("list[str]", "dict[str, int]", id="parameterised_containers"),
        pytest.param("bool", "list[dict[str, str]]", id="nested_containers"),
        pytest.param("float", "dict", id="float"),
    ],
)
def test_annotations_the_emitter_represents_faithfully_stay_proven(
    tmp_path, annotation: str, returns: str
):
    """The faithfulness check must not demote what the emitter gets right.

    Bare `list`/`dict` count: `{"type": "array"}` omits the element schema but
    does not misstate the value, which is a different thing from a guess.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(extra_tools="\n        loose_tool,")
        + f'''

def loose_tool(record_id: {annotation}) -> {returns}:
    """Look up a record by identifier and return the stored fields."""
    return {{}}
''',
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}


def test_an_absent_return_annotation_is_an_omission_not_a_guess(tmp_path):
    """`output_schema` stays `{}` with no return annotation, which claims nothing."""

    project = _proven_project(
        tmp_path,
        source=_proven_module(extra_tools="\n        loose_tool,")
        + '''

def loose_tool(record_id: str):
    """Look up a record by identifier and return the stored fields."""
    return {}
''',
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}


# --- PR #400 second review: six more fail-open paths --------------------------


def test_an_identity_binding_cannot_close_an_unproven_tool_set(tmp_path):
    """An identity assertion proves operation sameness, not surface completeness.

    `_merge_bound_observations` starts from the primary and copies nothing
    about extraction across — deliberately, because promoting the primary's
    fidelity is what naming a reviewed inventory is for (#386). That reasoning
    only holds for claims about one tool's own interface. Here an ADK toolset
    observation carrying `dynamic_agent_kwargs` merged into a high-confidence
    direct-OpenAPI primary and came out high, pass-eligible, and `passed`, with
    the module's gap nowhere in the report.
    """

    project = tmp_path / "project"
    project.mkdir()
    (project / "api.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Records
  version: "1.0"
paths:
  /records/{id}:
    get:
      operationId: lookup_record
      summary: Look up a record by identifier and return the stored fields.
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: ok
""".lstrip(),
        encoding="utf-8",
    )
    (project / "agent.py").write_text(
        '''
from google.adk.agents import LlmAgent
from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import (
    OpenAPIToolset,
)

overrides = {}
root_agent = LlmAgent(
    name="smart_closer",
    instruction="Close deals.",
    tools=[OpenAPIToolset(spec_path="api.yaml")],
    **overrides,
)
'''.lstrip(),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        '''
version: "0.1"
project:
  name: adk-identity-merge
agent:
  name: smart_closer
  declared_purpose:
    - look up records
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
  - id: direct_openapi
    type: openapi
    path: api.yaml

tool_identity:
  bindings:
    - id: lookup-record
      provider: records
      reason: the ADK toolset and the direct spec expose one operation
      primary:
        source_id: direct_openapi
        tool: lookup_record
      members:
        - source_id: direct_openapi
          tool: lookup_record
        - source_id: adk_agent:openapi:1
          tool: lookup_record

action_surface:
  actions:
    - tool: lookup_record
      effect: read
      authority:
        mode: none
'''.lstrip(),
        encoding="utf-8",
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    assert {tool["confidence"] for tool in report.tool_catalog} == {"medium"}
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert "dynamic_agent_kwargs" in gap.why
    # The remediation must not ask for the spec that is already there.
    assert gap.next_action.kind == "provide_source"
    assert "cannot close this" in gap.next_action.expects


@pytest.mark.parametrize(
    "symbol, replacement",
    [
        pytest.param("FunctionTool", "imported_replacement", id="wrapper_rebound"),
        pytest.param("LlmAgent", "replacement_agent", id="agent_class_rebound"),
    ],
)
def test_a_rebound_framework_symbol_is_not_googles_constructor(
    tmp_path, symbol: str, replacement: str
):
    """`_qualified_name` resolves through a spelling-based alias table.

    After `from google.adk.tools import FunctionTool` and
    `FunctionTool = replacement`, a foreign factory was still read with
    Google's semantics: its argument catalogued as an ADK tool, the module
    marked proven. Rebinding `LlmAgent` has the same effect one level up.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble=f"from external import {replacement}\n",
            trailer=f"{symbol} = {replacement}\n",
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("shadowed_framework_symbol" in gap.why for gap in gaps)


@pytest.mark.parametrize(
    "signature, expected_properties",
    [
        pytest.param(
            "context: str, record_id: str",
            ["context", "record_id"],
            id="context_is_an_ordinary_input",
        ),
        pytest.param(
            "ctx: str, record_id: str",
            ["ctx", "record_id"],
            id="ctx_is_an_ordinary_input",
        ),
        pytest.param(
            "tool_context, record_id: str",
            ["record_id"],
            id="tool_context_name_fallback",
        ),
        pytest.param(
            "ctx: ToolContext, record_id: str",
            ["record_id"],
            id="injected_by_annotation",
        ),
    ],
)
def test_only_a_verifiable_injected_parameter_leaves_the_schema(
    tmp_path, signature: str, expected_properties: list[str]
):
    """ADK identifies injected context by type, with `tool_context` as fallback.

    Dropping every parameter merely *spelled* `ctx` or `context` deleted real
    model-visible inputs: `def known(context: str, record_id: str)` shipped a
    one-property schema, was marked proven, and returned `passed`.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="from google.adk.tools import ToolContext\n",
            extra_tools="\n        loose_tool,",
        )
        + f'''

def loose_tool({signature}) -> dict:
    """Look up a record by identifier and return the stored fields."""
    return {{}}
''',
    )

    artifacts = GoogleAdkArtifacts()
    loaded = _load_python_path(
        project / "agent.py", project, "adk_agent", "agent.py", artifacts
    )
    tool = next(
        tool
        for source in loaded
        for tool in source.tools
        if tool.name == "loose_tool"
    )
    assert sorted(tool.input_schema.get("properties", {})) == expected_properties
    assert tool.extraction_confidence == "high"

    report = _scan_proven(tmp_path, project)
    catalog = {row["name"]: row for row in report.tool_catalog}
    assert catalog["loose_tool"]["confidence"] == "high"


@pytest.mark.parametrize(
    "shadow, annotation",
    [
        pytest.param("from domain import Account as str", "str", id="str_shadowed"),
        pytest.param("from domain import Bag as list", "list", id="list_shadowed"),
        pytest.param("from domain import Rec as dict", "dict", id="dict_shadowed"),
        pytest.param("from domain import Seq as List", "List", id="typing_list_faked"),
    ],
)
def test_a_shadowed_annotation_name_is_not_the_type_it_looks_like(
    tmp_path, shadow: str, annotation: str
):
    """`from domain import Account as str` makes ADK see `Account` at runtime.

    The faithfulness check read the spelling only, so the emitted string schema
    was accepted as accurate and the tool reported high and enumerated.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble=f"{shadow}\n", extra_tools="\n        loose_tool,"
        )
        + f'''

def loose_tool(value: {annotation}) -> dict:
    """Look up a record by identifier and return the stored fields."""
    return {{}}
''',
    )

    report = _scan_proven(tmp_path, project)

    tool = next(
        tool for tool in report.tool_catalog if tool["name"] == "loose_tool"
    )
    assert tool["confidence"] == "medium"
    gap = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    )
    assert "unrepresentable_annotation" in gap.why


def test_a_genuine_typing_alias_is_still_faithful(tmp_path):
    """The provenance check must not reject `from typing import List`."""

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            preamble="from typing import Dict, List\n",
            extra_tools="\n        loose_tool,",
        )
        + '''

def loose_tool(items: List[str], index: Dict[str, int]) -> dict:
    """Look up a record by identifier and return the stored fields."""
    return {}
''',
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}


@pytest.mark.parametrize(
    "preamble, trailer",
    [
        pytest.param(
            "from builtins import getattr as read_attr\n"
            "from external import imported_tool\n",
            'read_attr(root_agent, "tools").append(imported_tool)\n',
            id="aliased_builtin",
        ),
        pytest.param(
            "from external import imported_tool\n",
            'vars(root_agent)["tools"].append(imported_tool)\n',
            id="vars_dictionary",
        ),
        pytest.param(
            "from external import imported_tool\n",
            'root_agent.__dict__["tools"].append(imported_tool)\n',
            id="dunder_dict",
        ),
    ],
)
def test_indirect_reflective_mutation_is_still_a_mutation(
    tmp_path, preamble: str, trailer: str
):
    """Three more spellings that carry the attribute name as data.

    `from builtins import getattr as read_attr` calls the real builtin under a
    local name; `vars()` and `__dict__` reach the same attribute through a
    mapping. None contains an `Attribute` node named `tools`, and the raw
    spelling check saw only `read_attr`.
    """

    project = _proven_project(
        tmp_path, source=_proven_module(preamble=preamble, trailer=trailer)
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("mutable_tool_binding" in gap.why for gap in gaps)


def test_an_ordinary_dictionary_with_a_tools_key_is_not_a_mutation(tmp_path):
    """The dictionary forms are matched on `vars()`/`__dict__`, not on any key.

    Flagging every `["tools"]` subscript would demote modules that merely carry
    a config mapping, which is common and harmless.
    """

    project = _proven_project(
        tmp_path,
        source=_proven_module(
            trailer='CONFIG = {"tools": []}\nENABLED = CONFIG["tools"]\n'
        ),
    )

    report = _scan_proven(tmp_path, project)

    assert {tool["confidence"] for tool in report.tool_catalog} == {"high"}


def test_a_star_import_makes_every_name_in_the_module_unknowable(tmp_path):
    """`from x import *` can rebind any name, and binds none the table can see.

    The alias is recorded under `"*"`, so a local `def lookup_account(...)`
    still looked singly-bound and proven while the import may replace it.
    """

    project = _proven_project(
        tmp_path, source=_proven_module(trailer="from external import *\n")
    )

    report = _scan_proven(tmp_path, project)

    assert report.release_decision.decision != "passed"
    gaps = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "low_confidence_tool"
    ]
    assert gaps and all("star_import_shadowing" in gap.why for gap in gaps)
