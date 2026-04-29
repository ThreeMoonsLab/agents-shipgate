import json

from agents_shipgate.cli.scan import inspect_sources, run_scan


def test_langchain_static_extraction_without_importing_user_code(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        """
from langchain.tools import tool as lc_tool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

raise RuntimeError("this file must never be imported")

class LookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")

@lc_tool(args_schema=LookupInput)
def lookup_case(case_id: str) -> dict:
    \"\"\"Look up read-only metadata for an existing support case.\"\"\"
    return {"case_id": case_id}

def summarize_case(case_id: str) -> dict:
    \"\"\"Summarize read-only support case metadata.\"\"\"
    return {"case_id": case_id}

summary_tool = StructuredTool.from_function(
    func=summarize_case,
    name="summarize_case",
    description="Summarize read-only support case metadata.",
)
agent = create_agent(model=None, tools=[lookup_case, summary_tool])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: langchain-test
agent:
  name: support-agent
environment:
  target: local
tool_sources:
  - id: langchain
    type: langchain
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

    assert report.frameworks["langchain"]["function_tool_count"] == 1
    assert report.frameworks["langchain"]["structured_tool_count"] == 1
    assert report.frameworks["langchain"]["agent_tool_binding_count"] == 1
    assert report.frameworks["langchain"]["dynamic_tool_surface_count"] == 0
    assert {tool["name"] for tool in report.tool_inventory} == {
        "lookup_case",
        "summarize_case",
    }
    assert "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" not in {
        finding.check_id for finding in report.findings
    }


def test_langchain_dynamic_tool_surface_fires_without_inventory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        """
def get_tools():
    return []

agent = create_react_agent(None, tools=get_tools())
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: langchain-dynamic-test
agent:
  name: dynamic-agent
environment:
  target: local
tool_sources:
  - id: langchain
    type: langchain
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

    dynamic = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
    ]
    assert len(dynamic) == 1
    assert report.frameworks["langchain"]["dynamic_tool_surface_count"] == 1


def test_langchain_unresolved_args_schema_is_dynamic_surface(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        """
from langchain_core.tools import tool

@tool(args_schema=ExternalLookupInput)
def lookup_case(case_id: str) -> dict:
    \"\"\"Look up read-only metadata for an existing support case.\"\"\"
    return {"case_id": case_id}

agent = create_agent(model=None, tools=[lookup_case])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: langchain-unresolved-schema-test
agent:
  name: dynamic-agent
environment:
  target: local
tool_sources:
  - id: langchain
    type: langchain
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

    assert report.frameworks["langchain"]["dynamic_tool_surface_count"] == 1
    assert any("ExternalLookupInput" in warning for warning in report.source_warnings)
    assert "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" in {
        finding.check_id for finding in report.findings
    }


def test_langchain_tool_variable_shadowing_preserves_tools_and_warns(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        """
from langchain.tools import tool
from langchain_core.tools import StructuredTool

@tool
def summarize_case(case_id: str) -> dict:
    \"\"\"Summarize read-only support case metadata.\"\"\"
    return {"case_id": case_id}

def summarize_case_wrapped(case_id: str) -> dict:
    \"\"\"Summarize read-only support case metadata for reviewer handoff.\"\"\"
    return {"case_id": case_id}

summarize_case = StructuredTool.from_function(
    func=summarize_case_wrapped,
    name="summarize_case_wrapped",
    description="Summarize read-only support case metadata for reviewer handoff.",
)
agent = create_agent(model=None, tools=[summarize_case])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: langchain-shadow-test
agent:
  name: shadow-agent
environment:
  target: local
tool_sources:
  - id: langchain
    type: langchain
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

    assert {tool["name"] for tool in report.tool_inventory} == {
        "summarize_case",
        "summarize_case_wrapped",
    }
    assert report.frameworks["langchain"]["dynamic_tool_surface_count"] == 1
    assert any("tool variable 'summarize_case' is reassigned" in w for w in report.source_warnings)


def test_langchain_inventory_suppresses_dynamic_surface_finding(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text("agent = create_agent(model=None, tools=get_tools())\n", encoding="utf-8")
    (project / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "lookup_case",
                        "description": "Look up read-only metadata for an existing support case.",
                        "annotations": {"readOnlyHint": True},
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
  name: langchain-inventory-test
agent:
  name: dynamic-agent
environment:
  target: local
tool_sources:
  - id: langchain
    type: langchain
    path: agent.py
langchain:
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
    doctor = inspect_sources(config_path=project / "shipgate.yaml")

    assert report.frameworks["langchain"]["dynamic_tool_surface_count"] == 1
    assert "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" not in {
        finding.check_id for finding in report.findings
    }
    assert report.tool_inventory[0]["source_type"] == "langchain_inventory"
    assert doctor["frameworks"]["langchain"]["tool_inventory_file_count"] == 1
