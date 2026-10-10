"""Only the existing strict ownership proof can read an agent's list field."""

import pytest

from tests.test_builder_bindings import _read
from tests.test_imported_tool_bindings import _adk, _commit, _compare, _git, _write


def files(framework, *, tools="[read]", suffix="", field="tools"):
    imported = "from agents import Agent, function_tool\n" if framework == "sdk" else "from google.adk.agents import Agent\n"
    decorated = "@function_tool\n" if framework == "sdk" else ""
    text = imported + decorated + "def read():\n    return None\n" + decorated + "def write():\n    return None\n"
    return {"builders.py": text + f"base = Agent(name='Source', {field}={tools})\nroot = Agent(name='Root', {field}=base.{field})\n" + suffix}


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_existing_agent_tools_are_read_from_their_owned_constructor_list(tmp_path, framework):
    _write(tmp_path, files(framework))
    observations, warnings = _read(tmp_path, framework)
    root = next(item for item in observations if item.agent == ("root" if framework == "sdk" else "Root"))
    assert warnings == [] and root.tools_complete and root.tool_names == ["read"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_inherited_field_addition_is_an_application_row(tmp_path, framework):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, files(framework))
    head = _commit(tmp_path, files(framework, tools="[read, write]"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    root = "root" if framework == "sdk" else "Root"
    row = next(row for row in result["rows"] if row["agent"] == root and row["tool"] == "write")
    assert row["change"] == "added" and result["comparison_status"] == "compared"


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("suffix", [
    "base.tools.append(write)\n", "alias = base\nalias.tools = [write]\n",
    "base.tools = [write]\n", "base.tools = []\n", "del base.tools\n", "base.tools += [write]\n",
    "opaque(base)\n", "held = base.tools\nheld.append(write)\n",
])
def test_mutated_or_escaped_agent_field_is_not_a_complete_list(tmp_path, framework, suffix):
    _write(tmp_path, files(framework, suffix=suffix))
    observations, warnings = _read(tmp_path, framework)
    root = next(item for item in observations if item.agent == ("root" if framework == "sdk" else "Root"))
    assert warnings and not root.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_name_alike_fields_remain_unread(tmp_path, framework):
    fixture = files(framework)
    fixture["builders.py"] = fixture["builders.py"].replace("base = Agent(name='Source', tools=[read])", "class Holder:\n    tools = [read]\nbase = Holder()")
    _write(tmp_path, fixture)
    observations, warnings = _read(tmp_path, framework)
    root = next(item for item in observations if item.agent == ("root" if framework == "sdk" else "Root"))
    assert warnings and not root.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_self_field_in_an_unread_custom_instance_is_not_resolved(tmp_path, framework):
    fixture = files(framework)
    fixture["builders.py"] = fixture["builders.py"].split("base = Agent")[0] + """class Holder:
    def __init__(self):
        self.tools = [read]
    def build(self):
        return Agent(name='Root', tools=self.tools)
"""
    _write(tmp_path, fixture)
    observations, warnings = _read(tmp_path, framework)
    assert warnings and observations and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_agent_handoff_field_keeps_the_actual_target(tmp_path, framework):
    field = "handoffs" if framework == "sdk" else "sub_agents"
    fixture = files(framework, field=field, tools="[worker]")
    fixture["builders.py"] = fixture["builders.py"].replace("base = Agent", "worker = Agent(name='Worker', tools=[read])\nbase = Agent")
    _write(tmp_path, fixture)
    if framework == "adk":
        _, artifacts = _adk(tmp_path, "builders.py")
        assert artifacts.warnings == []
        assert any(item["agent_name"] == "Root" and item["sub_agents"] == ["Worker"] for item in artifacts.sub_agents)
    else:
        observations, warnings = _read(tmp_path, framework)
        root = next(item for item in observations if item.agent == "root")
        assert warnings == [] and root.handoffs_complete and root.handoff_names == ["worker"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_replaced_field_cannot_produce_a_false_application_addition(tmp_path, framework):
    def fixture(tools):
        result = files(framework, tools=tools)
        result["builders.py"] = result["builders.py"].replace("root = Agent", "base.tools = []\nroot = Agent")
        return result
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("[read]"))
    head = _commit(tmp_path, fixture("[read, write]"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert not any(row["agent"] in {"root", "Root"} and row["tool"] == "write" and row["change"] == "added" for row in result["rows"])


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("replacement", ["[]", "[worker]"])
def test_replaced_handoff_field_keeps_a_read_limit(tmp_path, framework, replacement):
    field = "handoffs" if framework == "sdk" else "sub_agents"
    fixture = files(framework, field=field, tools="[worker]")
    fixture["builders.py"] = fixture["builders.py"].replace("base = Agent", "worker = Agent(name='Worker', tools=[read])\nbase = Agent")
    fixture["builders.py"] = fixture["builders.py"].replace("root = Agent", f"base.{field} = {replacement}\nroot = Agent")
    _write(tmp_path, fixture)
    if framework == "adk":
        _, artifacts = _adk(tmp_path, "builders.py")
        root = next(item for item in artifacts.sub_agents if item["agent_name"] == "Root")
        assert root["sub_agent_count"] is None and root["sub_agents"] == [] and root["unread"]
    else:
        observations, warnings = _read(tmp_path, framework)
        root = next(item for item in observations if item.agent == "root")
        assert warnings and not root.handoffs_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("replacement", [False, True])
def test_returned_agent_field_checks_the_defining_constructor(tmp_path, framework, replacement):
    fixture = files(framework)
    fixture["builders.py"] = fixture["builders.py"].replace(
        "base = Agent(name='Source', tools=[read])",
        "def make():\n    source = Agent(name='Source', tools=[read])\n" +
        ("    source.tools = []\n" if replacement else "") + "    return source\nbase = make()",
    )
    _write(tmp_path, fixture)
    observations, warnings = _read(tmp_path, framework)
    root = next(item for item in observations if item.agent == ("root" if framework == "sdk" else "Root"))
    if replacement:
        assert warnings and not root.tools_complete
    else:
        assert not warnings and root.tools_complete and root.tool_names == ["read"]
