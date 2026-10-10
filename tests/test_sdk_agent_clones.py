"""Copies have independent bindings only through proved SDK receivers."""

import pytest

from tests.test_imported_tool_bindings import _commit, _compare, _git, _write
from tests.test_imported_tool_bindings import _sdk as _plain_sdk


def _sdk(root, path):
    from agents_shipgate.inputs.object_tools import reading_object_bindings
    with reading_object_bindings(lambda path: False):
        return _plain_sdk(root, path)


PREFIX = """from agents import Agent, function_tool
@function_tool
def read():
    return None
@function_tool
def write():
    return None
source = Agent(name='Source', tools=[read, write])
"""


def fixture(clone):
    return {"agent.py": PREFIX + clone + "\n"}


@pytest.mark.parametrize("clone,tools", [
    ("copy = source.clone()", ["read", "write"]),
    ("copy = source.clone(tools=[read])", ["read"]),
    ("copy = source.clone(tools=[t for t in source.tools if t.name == 'read'])", ["read"]),
    ("first = source.clone(tools=[read]); copy = first.clone()", ["read"]),
    ("first = source.clone(); copy = first.clone(tools=[t for t in first.tools if t.name == 'read'])", ["read"]),
])
def test_clone_bindings(tmp_path, clone, tools):
    _write(tmp_path, fixture(clone))
    loaded = _sdk(tmp_path, "agent.py")
    copy = loaded.binding_observations[-1]
    assert loaded.warnings == []
    assert copy.agent.startswith("source clone at ") and copy.tools_complete
    assert copy.tool_names == tools


def test_only_clone_is_narrowed(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(tools=[read, write])"))
    head = _commit(tmp_path, fixture("copy = source.clone(tools=[read])"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared"
    assert len(result["rows"]) == 1
    assert result["rows"][0]["change"] == "removed"
    assert result["rows"][0]["agent"].startswith("source clone at ")


@pytest.mark.parametrize("change", [
    "source.tools = []\n", "source.tools.append(read)\n", "opaque(source)\n",
    "Agent.clone = replacement\n", "source.clone = replacement\n",
])
def test_unproved_source_or_method_stays_partial(tmp_path, change):
    _write(tmp_path, fixture(change + "copy = source.clone(tools=[read])"))
    loaded = _sdk(tmp_path, "agent.py")
    copy = loaded.binding_observations[-1]
    assert loaded.warnings and not copy.tools_complete


def test_explicit_clone_name_and_inherited_handoff(tmp_path):
    _write(tmp_path, {"agent.py": PREFIX.replace("source = Agent(name='Source', tools=[read, write])", "worker = Agent(name='Worker', tools=[write])\nsource = Agent(name='Source', tools=[read], handoffs=[worker])") + "copy = source.clone(name='Copy')\n"})
    loaded = _sdk(tmp_path, "agent.py")
    copy = next(item for item in loaded.binding_observations if item.agent == "Copy")
    assert not loaded.warnings and copy.tool_names == ["read"] and copy.handoff_names == ["worker"]


def test_unknown_clone_source_keeps_a_named_limit(tmp_path):
    _write(tmp_path, fixture("copy = unknown.clone(tools=[read])"))
    loaded = _sdk(tmp_path, "agent.py")
    assert loaded.warnings
    assert any(not item.tools_complete for item in loaded.binding_observations)


def test_clone_identity_survives_line_movement(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(tools=[read])"))
    head = _commit(tmp_path, fixture("marker = 1\ncopy = source.clone(tools=[read])"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared" and result["rows"] == []


def test_clone_settings_are_facts_and_tools_remain_bound(tmp_path):
    def configured(choice):
        result = fixture(f"copy = source.clone(tool_use_behavior='stop_on_first_tool', model_settings=ModelSettings(tool_choice='{choice}'))")
        result["agent.py"] = result["agent.py"].replace("from agents import Agent,", "from agents import Agent, ModelSettings,")
        return result
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, configured("required"))
    head = _commit(tmp_path, configured("none"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared" and len(result["rows"]) == 2
    assert all(row["change"] == "changed" and row["after"]["agent_settings"] == {"tool_use_behavior":"stop_on_first_tool", "tool_choice":"none"} for row in result["rows"])


@pytest.mark.parametrize("change", [
    "copy.tools.append(write)", "copy.tools = []", "alias = copy; opaque(alias)",
    "read.name = 'other'", "source.__class__ = Fake",
])
def test_mutable_shared_members_and_copy_handles_stay_unestablished(tmp_path, change):
    _write(tmp_path, fixture("copy = source.clone()\n" + change))
    loaded = _sdk(tmp_path, "agent.py")
    copy = next(item for item in loaded.binding_observations if "clone at" in item.agent)
    assert loaded.warnings and not copy.tools_complete


@pytest.mark.parametrize("replacement", [False, True])
def test_clone_inherits_and_overrides_mcp_servers(tmp_path, replacement):
    from agents_shipgate.inputs.object_tools import reading_object_bindings
    files = fixture("copy = source.clone()")
    files["agent.py"] = files["agent.py"].replace("source = Agent(name='Source', tools=[read, write])", "from agents.mcp import MCPServerStdio\nserver = MCPServerStdio(params={'command':'npx'})\nsource = Agent(name='Source', tools=[read], mcp_servers=[server])")
    if replacement:
        files["agent.py"] = files["agent.py"].replace("copy = source.clone()", "copy = source.clone(mcp_servers=[])")
    _write(tmp_path, files)
    with reading_object_bindings(lambda path: False):
        loaded = _sdk(tmp_path, "agent.py")
    copy = next(item for item in loaded.binding_observations if "clone at" in item.agent)
    assert not loaded.warnings and copy.tools_complete
    assert bool(copy.object_bindings) != replacement


def test_filtered_clone_narrows_only_the_clone(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(tools=[t for t in source.tools if t.name == 'read'])"))
    head = _commit(tmp_path, fixture("copy = source.clone(tools=[t for t in source.tools if t.name == 'write'])"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared"
    assert sorted((row["tool"],row["change"]) for row in result["rows"]) == [("read","removed"),("write","added")]
    assert all("clone at" in row["agent"] for row in result["rows"])


@pytest.mark.parametrize("settings", [
    "ModelSettings(tool_choice=choice)", "Other(tool_choice='none')", "ModelSettings(tool_choice='none', extra=opaque())",
])
def test_unread_settings_do_not_acquire_known_sdk_facts(tmp_path, settings):
    files = fixture(f"copy = source.clone(model_settings={settings})")
    files["agent.py"] = files["agent.py"].replace("Agent, function_tool", "Agent, ModelSettings, function_tool")
    _write(tmp_path, files)
    loaded = _sdk(tmp_path, "agent.py")
    copy = next(item for item in loaded.binding_observations if "clone at" in item.agent)
    assert copy.agent_settings == {}


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("replacement", ["factory()", "wrapper(source)", "source.copy()", "source.clone(*arguments)", "provided_agent", "module.agent"])
def test_unread_replacement_at_clone_site_is_not_a_deletion(tmp_path, named, replacement):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(" + ("name='Copy'" if named else "") + ")"))
    head = _commit(tmp_path, fixture("copy = " + replacement))
    result = _compare(tmp_path, base, head, "--scope", ".")
    copy_rows = [row for row in result["rows"] if row["agent"] == "Copy" or "clone at" in row["agent"]]
    assert len(copy_rows) == 2
    assert all(row["change"] == "not_established" and row["uncertainty"].get("head") for row in copy_rows)


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("replacement", [
    "from provider import agent as copy", "import provider as copy",
    "copy = other = provided_agent", "copy, other = provided_pair",
    "(copy,) = provided_pair", "if (copy := provided_agent):\n    pass",
    "for copy in provided_agents:\n    pass", "copy: Agent",
])
def test_import_at_clone_site_is_not_a_deletion(tmp_path, named, replacement):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(" + ("name='Copy'" if named else "") + ")"))
    head = _commit(tmp_path, fixture(replacement))
    result = _compare(tmp_path, base, head, "--scope", ".")
    copy_rows = [row for row in result["rows"] if row["agent"] == "Copy" or "clone at" in row["agent"]]
    assert len(copy_rows) == 2
    assert all(row["change"] == "not_established" for row in copy_rows)


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("replacement", ["copy = provided_agent", "from provider import agent as copy"])
def test_global_replacement_at_clone_site_is_not_a_deletion(tmp_path, named, replacement):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(" + ("name='Copy'" if named else "") + ")"))
    head = _commit(tmp_path, fixture("def update():\n    global copy\n    " + replacement + "\nupdate()"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    copy_rows = [row for row in result["rows"] if row["agent"] == "Copy" or "clone at" in row["agent"]]
    assert len(copy_rows) == 2
    assert all(row["change"] == "not_established" for row in copy_rows)


@pytest.mark.parametrize("named", [False, True])
def test_unrelated_local_replacement_does_not_hide_clone_deletion(tmp_path, named):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(" + ("name='Copy'" if named else "") + ")"))
    head = _commit(tmp_path, fixture("def update():\n    copy = provided_agent\nupdate()"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared"
    assert len(result["rows"]) == 2
    assert all(row["change"] == "removed" for row in result["rows"])


@pytest.mark.parametrize("named", [False, True])
def test_global_clone_construction_keeps_its_syntax_label_for_uncertain_absence(tmp_path, named):
    _git(tmp_path, "init", "-q", "-b", "main")
    before = "def update():\n    global copy\n    copy = source.clone(" + ("name='Copy'" if named else "") + ")\nupdate()"
    base = _commit(tmp_path, fixture(before))
    head = _commit(tmp_path, fixture("def update():\n    global copy\n    copy = other = provided_agent\nupdate()"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    copy_rows = [row for row in result["rows"] if row["agent"] == "Copy" or "clone at" in row["agent"]]
    assert len(copy_rows) == 2
    assert all(row["change"] == "not_established" for row in copy_rows)


@pytest.mark.parametrize("declaration,expected", [("global copy", "copy@agent.py"), ("nonlocal copy", "outer.copy@agent.py"), ("", "outer.update.copy@agent.py")])
def test_absence_label_uses_the_declared_binding_scope(declaration, expected):
    import ast

    from agents_shipgate.inputs.agent_construction_identity import source_binding_labels
    from agents_shipgate.inputs.python_imports import ScopeIndex

    tree = ast.parse("def outer():\n    copy = first\n    def update():\n        " + (declaration + "\n        " if declaration else "") + "copy = provided_agent\n")
    target = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == "provided_agent").targets[0]
    assert source_binding_labels(target, ScopeIndex(tree), "agent.py") == {expected, "outer.update.copy@agent.py"}


@pytest.mark.parametrize("named", [False, True])
def test_deleting_clone_site_removes_its_bindings(tmp_path, named):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, fixture("copy = source.clone(" + ("name='Copy'" if named else "") + ")"))
    head = _commit(tmp_path, fixture(""))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared"
    assert len(result["rows"]) == 2
    assert all(row["change"] == "removed" for row in result["rows"])


@pytest.mark.parametrize("mutation", ["append(write)", "extend([write])", "insert(0, write)"])
@pytest.mark.parametrize("override", ["", "tools=source.tools", "tools=source.tools if enabled else []", "tools=source.tools or []", "tools=alias"])
def test_clone_in_place_additions_make_the_source_list_incomplete(tmp_path, mutation, override):
    files = fixture(("alias = source.tools\n" if override == "tools=alias" else "")
                    + "copy = source.clone(" + override + ")\ncopy.tools." + mutation)
    files["agent.py"] = files["agent.py"].replace("tools=[read, write]", "tools=[read]")
    _write(tmp_path, files)
    loaded = _sdk(tmp_path, "agent.py")
    assert all(not item.tools_complete for item in loaded.binding_observations)
    assert all(item.issues for item in loaded.binding_observations)


@pytest.mark.parametrize("override", ["[read]", "[*source.tools]", "list(source.tools)"])
def test_clone_new_literal_list_does_not_share_source_list(tmp_path, override):
    files = fixture("copy = source.clone(tools=" + override + ")\ncopy.tools.append(write)")
    files["agent.py"] = files["agent.py"].replace("tools=[read, write]", "tools=[read]")
    _write(tmp_path, files)
    loaded = _sdk(tmp_path, "agent.py")
    assert loaded.binding_observations[0].tools_complete
    assert not loaded.binding_observations[-1].tools_complete


def test_model_override_does_not_assert_inherited_model_defaults(tmp_path):
    files = fixture("first = source.clone(model_settings=ModelSettings(tool_choice='none'))\ncopy = first.clone(model='other-model')")
    files["agent.py"] = files["agent.py"].replace("Agent, function_tool", "Agent, ModelSettings, function_tool")
    _write(tmp_path, files)
    loaded = _sdk(tmp_path, "agent.py")
    assert loaded.binding_observations[-1].agent_settings == {}
    assert loaded.binding_observations[-1].agent_settings_issues


def test_unread_model_override_is_not_a_confirmed_setting_change(tmp_path):
    def configured(model):
        files = fixture("first = source.clone(model_settings=ModelSettings(tool_choice='none'))\ncopy = first.clone(" + model + ")")
        files["agent.py"] = files["agent.py"].replace("Agent, function_tool", "Agent, ModelSettings, function_tool")
        return files
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, configured(""))
    head = _commit(tmp_path, configured("model='other-model'"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "partial"
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert all(row["uncertainty"].get("head") for row in result["rows"])


@pytest.mark.parametrize("collision", ["clone", "source"])
def test_conflicting_settings_are_not_merged_or_selected(tmp_path, collision):
    def configured(choice):
        text = "from agents import ModelSettings\n"
        name = "source" if collision == "source" else "Copy"
        if collision == "clone":
            text += "a = source.clone(name='Copy', model_settings=ModelSettings(tool_choice='none'))\n"
        text += f"b = source.clone(name='{name}', model_settings=ModelSettings(tool_choice='{choice}'))\n"
        return fixture(text)
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, configured("none"))
    head = _commit(tmp_path, configured("required"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "partial"
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert any("constructed more than once" in limit for limit in result["head"]["limits"])
    assert all("agent_settings" not in row["after"] for row in result["rows"] if row["after"])


@pytest.mark.parametrize("name", ["web_search", "WebSearchTool"])
def test_object_display_name_is_not_a_filter_membership_proof(tmp_path, name):
    def hosted(clone):
        files = fixture(clone)
        files["agent.py"] = files["agent.py"].replace("Agent, function_tool", "Agent, WebSearchTool, function_tool").replace("tools=[read, write]", "tools=[WebSearchTool()]")
        return files
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, hosted("copy = source.clone()"))
    head = _commit(tmp_path, hosted(f"copy = source.clone(tools=[t for t in source.tools if t.name == '{name}'])"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "partial"
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert any("runtime name" in limit for limit in result["head"]["limits"])


def test_default_scan_does_not_expand_application_clones(tmp_path):
    _write(tmp_path, fixture("copy = source.clone(tools=[read])"))
    loaded = _plain_sdk(tmp_path, "agent.py")
    assert any("agent copy" in warning for warning in loaded.warnings)
    assert not any("clone at" in observation.agent for observation in loaded.binding_observations)
