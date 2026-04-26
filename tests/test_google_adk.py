import json

from agents_shipgate.checks.adk import _has_long_running_contract
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts


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
    doctor = inspect_sources(config_path=project / "shipgate.yaml")
    assert doctor["frameworks"]["google_adk"]["dynamic_toolset_count"] == 2


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

    assert report.tool_inventory[0]["name"] == "support.lookup"
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
