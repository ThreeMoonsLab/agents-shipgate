import ast
import json

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.inputs.python_static import pydantic_model_schemas
from agents_shipgate.schemas.manifest import ToolSourceConfig

FRAMEWORKS = [
    ("langchain", "langchain_core.tools", "create_react_agent(None, tools)"),
    ("crewai", "crewai.tools", "Agent(role='reader', tools=tools)"),
]


@pytest.fixture(params=FRAMEWORKS, ids=["langchain", "crewai"])
def project(tmp_path, request):
    framework, tool_module, build = request.param
    source = tmp_path / "agent.py"
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        f"""version: "0.1"
project:
  name: static-list-review
agent:
  name: root
environment:
  target: local
tool_sources:
  - id: framework
    type: {framework}
    path: agent.py
""",
        encoding="utf-8",
    )
    definitions = f"""from {tool_module} import tool

@tool
def lookup(case: str) -> str:
    \"\"\"Read support case metadata.\"\"\"
    return case

@tool
def summarize(case: str) -> str:
    \"\"\"Summarize support case metadata.\"\"\"
    return case

"""
    return source, manifest, definitions, build, framework


def scan(manifest):
    report, _ = run_scan(
        config_path=manifest,
        output_dir=manifest.parent / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    return report


@pytest.mark.parametrize(
    "change",
    ["tools.append(summarize)", "tools.extend([summarize])", "tools += [summarize]"],
)
def test_changed_tool_list_remains_partial_with_an_inventory(project, change):
    source, manifest, definitions, build, framework = project
    source.write_text(
        definitions + f"tools = [lookup]\n{change}\nroot = {build}\n",
        encoding="utf-8",
    )

    report = scan(manifest)
    assert report.frameworks[framework]["dynamic_tool_surface_count"] == 1
    assert report.binding_surface_facts.status == "partial"
    assert any(
        "changed in place" in warning or "bound more than once" in warning
        for warning in report.source_warnings
    )
    assert report.release_decision.decision != "passed"

    (manifest.parent / "tools.json").write_text(
        json.dumps({"tools": [{"name": "lookup", "annotations": {"readOnlyHint": True}}]}),
        encoding="utf-8",
    )
    with manifest.open("a", encoding="utf-8") as config:
        config.write(f"{framework}:\n  tool_inventories:\n    - tools.json\n")

    report = scan(manifest)
    assert report.binding_surface_facts.status == "partial"
    assert not report.binding_surface_facts.pass_eligible
    assert report.release_decision.decision != "passed"


def test_function_local_list_does_not_shadow_the_module_list(project):
    source, manifest, definitions, build, framework = project
    source.write_text(
        definitions
        + f"""tools = [lookup]
root = {build}

def make_other():
    tools = [summarize]
    other = {build}
    return other
""",
        encoding="utf-8",
    )
    report = scan(manifest)
    assert report.frameworks[framework]["dynamic_tool_surface_count"] == 0
    graph = report.binding_surface_facts
    agents = {agent.agent_id: agent.name for agent in graph.agents}
    tools = {tool["tool_id"]: tool["name"] for tool in report.tool_catalog}
    assert {
        (agents[edge.agent_id], tools[edge.tool_id]) for edge in graph.tool_edges
    } == {("root", "lookup"), ("other", "summarize")}


def test_openai_sdk_injected_context_is_excluded_from_input_schema(tmp_path):
    (tmp_path / "agent.py").write_text(
        """from typing import Any
from agents import RunContextWrapper as Wrapper, ToolContext, function_tool

@function_tool
def lookup(wrapper: Wrapper[Any], case: str) -> str:
    return case

@function_tool
def summarize(tool_context: "ToolContext[Any]", case: str) -> str:
    return case

@function_tool
def compare(wrapper: str, case: str) -> str:
    return case
""",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agent.py")

    loaded = load_openai_sdk_static_tools(source, None, tmp_path)
    tools = {tool.name: tool for tool in loaded.tools}
    for name in ("lookup", "summarize"):
        tool = tools[name]
        assert tool.input_schema["properties"] == {"case": {"type": "string"}}
        assert tool.input_schema["required"] == ["case"]
        assert tool.function_signature == f"{name}(case) -> str"
    assert tools["compare"].input_schema["required"] == ["wrapper", "case"]


@pytest.mark.parametrize(
    "imports",
    [
        "from agents import *\nfrom another_library import *",
        "if flag:\n    from agents import *",
    ],
)
def test_openai_sdk_reports_ambiguous_wildcard_decorator(tmp_path, imports):
    (tmp_path / "agent.py").write_text(
        imports + "\n@function_tool\ndef lookup(case: str) -> str:\n    return case\n",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agent.py")

    loaded = load_openai_sdk_static_tools(source, None, tmp_path)

    assert loaded.tools == []
    assert len(loaded.warnings) == 1
    assert "agent.py:3 has an ambiguous import" in loaded.warnings[0]


def test_pydantic_field_without_a_default_is_required():
    schema = pydantic_model_schemas(
        ast.parse(
            """class Input(BaseModel):
    required: str = Field(description="Required field")
    ellipsis: str = Field(default=...)
    no_factory: str = Field(default_factory=None)
    null: str | None = Field(default=None)
    value: str = Field("example")
    factory: list[str] = Field(default_factory=list)
"""
        )
    )["Input"]
    assert schema["required"] == ["required", "ellipsis", "no_factory"]
