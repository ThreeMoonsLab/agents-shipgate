from agents_shipgate.cli.scan import inspect_sources, run_scan


def test_crewai_static_extraction_without_importing_user_code(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "crew.py").write_text(
        """
from crewai import Agent, Crew
from crewai.tools import BaseTool, tool
from crewai_tools import FileReadTool
from pydantic import BaseModel, Field

raise RuntimeError("this file must never be imported")

class LookupInput(BaseModel):
    case_id: str = Field(..., description="Support case identifier.")

@tool("summarize_case")
def summarize_case(case_id: str) -> dict:
    \"\"\"Summarize read-only support case metadata.\"\"\"
    return {"case_id": case_id}

class LookupTool(BaseTool):
    name: str = "lookup_case"
    description: str = "Look up read-only metadata for an existing support case."
    args_schema = LookupInput

    def _run(self, case_id: str) -> dict:
        return {"case_id": case_id}

file_tool = FileReadTool()
lookup_tool = LookupTool()
researcher = Agent(
    role="reader",
    goal="read case metadata",
    backstory="reads cases",
    tools=[summarize_case, lookup_tool, file_tool],
)
crew = Crew(agents=[researcher])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: crewai-test
agent:
  name: crew-agent
environment:
  target: local
tool_sources:
  - id: crewai
    type: crewai
    path: crew.py
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["crewai"]["agent_count"] == 1
    assert report.frameworks["crewai"]["crew_count"] == 1
    assert report.frameworks["crewai"]["function_tool_count"] == 1
    assert report.frameworks["crewai"]["class_tool_count"] == 1
    assert report.frameworks["crewai"]["prebuilt_tool_count"] == 1
    assert report.frameworks["crewai"]["dynamic_tool_surface_count"] == 0
    inventory = {tool["name"]: tool for tool in report.tool_inventory}
    assert {"summarize_case", "lookup_case", "FileReadTool"} <= set(inventory)
    assert inventory["FileReadTool"]["confidence"] == "low"
    assert "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" not in {
        finding.check_id for finding in report.findings
    }


def test_crewai_dynamic_tool_surface_fires_without_inventory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "crew.py").write_text(
        """
from crewai import Agent

def get_tools():
    return []

researcher = Agent(role="reader", goal="read", backstory="", tools=get_tools())
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: crewai-dynamic-test
agent:
  name: dynamic-crew
environment:
  target: local
tool_sources:
  - id: crewai
    type: crewai
    path: crew.py
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
        if finding.check_id == "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
    ]
    assert len(dynamic) == 1
    assert report.frameworks["crewai"]["dynamic_tool_surface_count"] == 1


def test_crewai_unresolved_args_schema_is_dynamic_surface(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "crew.py").write_text(
        """
from crewai import Agent
from crewai.tools import BaseTool

class LookupTool(BaseTool):
    name: str = "lookup_case"
    description: str = "Look up read-only metadata for an existing support case."
    args_schema = ExternalLookupInput

    def _run(self, case_id: str) -> dict:
        return {"case_id": case_id}

lookup_tool = LookupTool()
researcher = Agent(role="reader", goal="read", backstory="", tools=[lookup_tool])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: crewai-unresolved-schema-test
agent:
  name: dynamic-crew
environment:
  target: local
tool_sources:
  - id: crewai
    type: crewai
    path: crew.py
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.frameworks["crewai"]["dynamic_tool_surface_count"] == 1
    assert any("ExternalLookupInput" in warning for warning in report.source_warnings)
    assert "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" in {
        finding.check_id for finding in report.findings
    }


def test_crewai_basetool_field_default_description_is_extracted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "crew.py").write_text(
        """
from crewai import Agent
from crewai.tools import BaseTool
from pydantic import Field

class LookupTool(BaseTool):
    name: str = Field(default="lookup_case", description="Tool name.")
    description: str = Field(
        default="Look up read-only metadata for an existing support case.",
        description="Tool description.",
    )

    def _run(self, case_id: str) -> dict:
        return {"case_id": case_id}

lookup_tool = LookupTool()
researcher = Agent(role="reader", goal="read", backstory="", tools=[lookup_tool])
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: crewai-field-description-test
agent:
  name: field-description-crew
environment:
  target: local
tool_sources:
  - id: crewai
    type: crewai
    path: crew.py
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
    assert "lookup_case" in inventory
    assert "SHIP-CREWAI-FUNCTION-TOOL-METADATA-MISSING" not in {
        finding.check_id for finding in report.findings
    }


def test_crewai_inventory_suppresses_dynamic_surface_finding(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "crew.py").write_text(
        'researcher = Agent(role="reader", goal="read", backstory="", tools=get_tools())\n',
        encoding="utf-8",
    )
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "lookup_case",
      "description": "Look up read-only metadata for an existing support case.",
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
  name: crewai-inventory-test
agent:
  name: dynamic-crew
environment:
  target: local
tool_sources:
  - id: crewai
    type: crewai
    path: crew.py
crewai:
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

    assert report.frameworks["crewai"]["dynamic_tool_surface_count"] == 1
    assert "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE" not in {
        finding.check_id for finding in report.findings
    }
    assert report.tool_inventory[0]["source_type"] == "crewai_inventory"
    assert doctor["frameworks"]["crewai"]["tool_inventory_file_count"] == 1
