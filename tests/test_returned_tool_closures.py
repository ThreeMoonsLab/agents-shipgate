"""Fresh ADK closures retain factory arguments and caller provenance (#874)."""

from __future__ import annotations

import json

import pytest

from tests.test_builder_bindings import _files, _read
from tests.test_imported_tool_bindings import _adk, _commit, _compare, _git, _write

FACTORY = """def make_tools(readonly=True):
    async def read(query: str = '') -> str:
        return query if readonly else 'write'
    def write(query: str | None = None) -> str:
        return query or ''
    return RETURNED
"""


def _files_for(calls="a = build(make_tools())\n", returned="[read, write]", factory=FACTORY):
    files = _files("adk", calls)
    files["app.py"] = files["app.py"].replace("from tools import read, write", "from factory import make_tools")
    files["factory.py"] = factory.replace("RETURNED", returned)
    return files


@pytest.mark.parametrize("returned,selected", [
    ("[read, write]", "make_tools()"),
    ("(read, write)", "make_tools()"),
    ("{'calendar': [read], 'email': [write]}", "groups['calendar'] + groups.get('email')"),
])
def test_adk_reads_direct_fresh_closures(tmp_path, returned, selected):
    _write(tmp_path, _files_for("groups = make_tools()\na = build(" + selected + ")\n", returned))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings == []
    (agent,) = observations
    assert agent.tools_complete and agent.tool_names == ["read", "write"]
    for tool in agent.tool_names:
        assert "app.py:5" in agent.tool_sites[tool]
        if selected.startswith("groups"):
            assert "app.py:4" in agent.tool_sites[tool]
        assert "builders.py:3" in agent.tool_sites[tool]


@pytest.mark.parametrize("changed", [
    "groups[0].__name__ = 'renamed'",
    "consume(groups[0])",
    "alias = groups\nconsume(alias)",
])
def test_retained_closure_objects_stay_protected(tmp_path, changed):
    _write(tmp_path, _files_for("groups = make_tools()\na = build(groups)\n" + changed + "\n"))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("header", [
    "@decorate\n    def unused():",
    "def unused(value=execute()):",
    "def unused(value: execute()):",
    "def unused() -> unknown:",
    "def unused(value=[]):",
    "def unused(value: None | None):",
])
def test_every_eager_nested_header_is_checked_even_when_unselected(tmp_path, header):
    factory = FACTORY.replace("    return RETURNED", "    " + header + "\n        return None\n    return RETURNED")
    _write(tmp_path, _files_for(returned="{'chosen': [read], 'other': [write]}", factory=factory,
                              calls="groups = make_tools()\na = build(groups['chosen'])\n"))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("change", [
    "    read.__name__ = 'other'\n",
    "    alias = read\n",
    "    consume(read)\n",
])
def test_factory_preludes_cannot_mutate_or_escape_the_closure(tmp_path, change):
    _write(tmp_path, _files_for(factory=FACTORY.replace("    return RETURNED", change + "    return RETURNED")))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


def _diff(tmp_path, base, head):
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, base)
    after = _commit(tmp_path, head)
    return _compare(tmp_path, before, after)


def test_added_closure_is_a_real_application_row_with_factory_and_builder_sites(tmp_path):
    returned = "{'calendar': [read], 'email': [write]}"
    result = _diff(tmp_path, _files_for("groups = make_tools()\na = build(groups['calendar'])\n", returned),
                   _files_for("groups = make_tools()\na = build(groups['calendar'] + groups['email'])\n", returned))
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"] if row["change"] != "not_established"] == [("Built", "write", "added")]
    (row,) = [row for row in result["rows"] if row["tool"] == "write"]
    assert row["after"]["definition"]["source"] == "factory.py"
    assert row["after"]["definition"]["line"] == 4


def test_actual_call_argument_changes_the_closure_implementation(tmp_path):
    result = _diff(tmp_path, _files_for("a = build(make_tools(True))\n", "[read]"),
                   _files_for("a = build(make_tools(False))\n", "[read]"))
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("read", "changed")]


def test_unknown_capture_is_implementation_uncertainty(tmp_path):
    result = _diff(tmp_path, _files_for("a = build(make_tools(runtime_policy()))\n", "[read]"),
                   _files_for("a = build(make_tools(other_policy()))\n", "[read]"))
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("read", "not_established")]
    assert any("a value this read cannot name" in reason for reason in result["rows"][0]["uncertainty"]["head"])


@pytest.mark.parametrize("default", ["{'readonly': True}", "({'readonly': True},)"])
def test_shared_mutable_factory_defaults_are_unknown(tmp_path, default):
    files = _files_for(factory=FACTORY.replace("readonly=True", "readonly=" + default), returned="[read]")
    _write(tmp_path, files)
    loaded, _ = _adk(tmp_path, "builders.py")
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert all(item.startswith("unknown:") and "shared mutable factory default" in item
               for item in tool.extraction["factory_calls"]["Built"])


def test_sdk_still_refuses_plain_returned_closures(tmp_path):
    files = _files("sdk", "a = build(make_tools())\n")
    files["app.py"] += "from factory import make_tools\n"
    files["factory.py"] = FACTORY.replace("RETURNED", "[read]")
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "sdk")
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("other", ["consume(make_tools())", "other = make_tools()\nconsume(other)"])
def test_another_factory_result_cannot_escape_with_shared_module_globals(tmp_path, other):
    _write(tmp_path, _files_for(other + "\na = build(make_tools())\n", "[read]"))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("expression", ["str | 'Other'", "'Other' | None", "custom[str]", "custom | None", "None | None"])
def test_executable_or_invalid_annotation_protocols_are_not_proven(tmp_path, expression):
    _write(tmp_path, _files_for(factory=FACTORY.replace("query: str = ''", "query: " + expression + " = ''")))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


def test_future_annotations_keep_their_unevaluated_role(tmp_path):
    factory = "from __future__ import annotations\n" + FACTORY.replace("query: str = ''", "query: Missing[Unknown] = ''")
    _write(tmp_path, _files_for(factory=factory))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings == [] and all(item.tools_complete for item in observations)


@pytest.mark.parametrize("head_call", ["make_tools(readonly=True)", "make_tools()"])
def test_equivalent_whole_call_spellings_are_not_implementation_changes(tmp_path, head_call):
    result = _diff(tmp_path, _files_for("a = build(make_tools(True))\n", "[read]"),
                   _files_for("a = build(" + head_call + ")\n", "[read]"))
    assert not result["rows"]


def test_forwarded_capture_follows_only_its_actual_invocation(tmp_path):
    def files(flag):
        data = _files_for("def forward(flag, targets):\n    return build(make_tools(flag), handoffs=targets)\na = forward(" + flag + ", [])\n", "[read]")
        data["builders.py"] = data["builders.py"].replace("tools=tools)", "tools=tools, sub_agents=handoffs)")
        return data
    result = _diff(tmp_path, files("True"), files("False"))
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("read", "changed")]


def test_ordinary_forwarding_without_a_parent_capability_context_stays_unknown(tmp_path):
    before = "def forward(flag):\n    return build(make_tools(flag))\na = forward(True)\n"
    result = _diff(tmp_path, _files_for(before, "[read]"),
                   _files_for(before.replace("forward(True)", "forward(False)"), "[read]"))
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("read", "not_established")]


@pytest.mark.parametrize("change", [
    "formatter.__code__ = write.__code__\n",
    "consume(formatter)\n",
    "consume(tools)\n",
])
def test_captured_callbacks_never_become_known_from_code_alone(tmp_path, change):
    def files(extra):
        data = _files_for("a = build(make_tools(formatter))\n" + extra, "[read]",
                          FACTORY.replace("return query if readonly else 'write'", "return readonly(query)"))
        data["app.py"] = data["app.py"].replace("from factory import make_tools\n", "from factory import make_tools\nfrom tools import read as formatter, write\nimport tools\n")
        return data
    result = _diff(tmp_path, files(""), files(change))
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("read", "not_established")]
    assert any("a value this read cannot name" in reason for reason in result["rows"][0]["uncertainty"]["head"])


def _parent_capture_files(call, policy, change=""):
    data = _files_for("a = " + call + "\n", "[read]")
    data["builders.py"] = (
        "from google.adk.agents import Agent\nfrom factory import make_tools\n"
        + "def build(targets, policy" + policy + "):\n"
        + change
        + "    return Agent(name='Built', tools=make_tools(policy), sub_agents=targets)\n"
    )
    return data


@pytest.mark.parametrize("value", ["{'readonly': True}", "({'readonly': True},)"])
def test_parent_mutable_defaults_keep_their_shared_ownership(tmp_path, value):
    data = _parent_capture_files("build([])", "=" + value)
    _write(tmp_path, data)
    loaded, _ = _adk(tmp_path, "builders.py")
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert all(item.startswith("unknown:") and "shared mutable factory default" in item
               for item in tool.extraction["factory_calls"]["Built"])


@pytest.mark.parametrize("value", ["{'readonly': True}", "({'readonly': True},)"])
@pytest.mark.parametrize("change", [
    "    policy['readonly'] = False\n",
    "    consume(policy)\n",
    "    alias = policy\n    consume(alias)\n",
])
def test_mutable_parent_captures_cannot_hide_changes_or_escape(tmp_path, value, change):
    result = _diff(tmp_path, _parent_capture_files("build([], " + value + ")", ""),
                   _parent_capture_files("build([], " + value + ")", "", change))
    assert not any(row["change"] != "not_established" for row in result["rows"])
    assert result["comparison_status"] == "partial"
    for side in ("base", "head"):
        assert any(gap["tool"] == "read" and gap["affects"] == "implementation" for gap in result[side]["coverage_gaps"])


def test_named_mutable_parent_capture_stays_unknown(tmp_path):
    def files(extra):
        data = _parent_capture_files("call()", "", extra)
        data["app.py"] = data["app.py"].replace("a = call()", "def call():\n    policy = {'readonly': True}\n    return build([], policy)\na = call()")
        return data
    result = _diff(tmp_path, files(""), files("    consume(policy)\n"))
    assert not any(row["change"] != "not_established" for row in result["rows"])
    assert result["comparison_status"] == "partial"
    for side in ("base", "head"):
        assert any(gap["tool"] == "read" and gap["affects"] == "implementation" for gap in result[side]["coverage_gaps"])


@pytest.mark.parametrize("value", ["True", "(True, False)"])
def test_immutable_parent_defaults_are_known_data(tmp_path, value):
    result = _diff(tmp_path, _parent_capture_files("build([])", "=" + value),
                   _parent_capture_files("build([], " + value + ")", "=" + value))
    assert not result["rows"]


@pytest.mark.parametrize("change", [
    "    def retune():\n        nonlocal policy\n        policy = False\n    retune()\n",
    "    def retune():\n        nonlocal policy\n        del policy\n    retune()\n",
    "    del policy\n",
])
def test_parent_capture_cells_must_not_be_rebound_or_deleted(tmp_path, change):
    result = _diff(tmp_path, _parent_capture_files("build([], True)", ""),
                   _parent_capture_files("build([], True)", "", change))
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("read", "not_established")]
    assert any(gap["tool"] == "read" and gap["affects"] == "implementation" for gap in result["head"]["coverage_gaps"])


def test_unknown_shared_mutable_capture_does_not_become_known_data(tmp_path):
    _write(tmp_path, _files_for("policy = {'readonly': True}\na = build(make_tools(policy))\n", "[read]"))
    loaded, _ = _adk(tmp_path, "builders.py")
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert all(item.startswith("unknown:") for item in tool.extraction["factory_calls"]["Built"])


def test_different_captures_behind_one_agent_name_stay_ambiguous(tmp_path):
    _write(tmp_path, _files_for("a = build(make_tools(True))\nb = build(make_tools(False))\n", "[read]"))
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


def test_one_agents_capture_cannot_change_anothers_evidence(tmp_path):
    def files(right):
        data = _files_for("a = build(make_tools(True))\nb = other(make_tools(" + right + "))\n", "[read]")
        data["app.py"] = data["app.py"].replace("from builders import build", "from builders import build, other")
        data["builders.py"] += "def other(tools):\n    return Agent(name='Other', tools=tools)\n"
        return data
    result = _diff(tmp_path, files("True"), files("False"))
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("Other", "read", "changed")]


@pytest.mark.parametrize("code", [
    "from factory import helper\nconsume(helper)\n",
    "from factory import Helper\nconsume(Helper)\n",
    "from factory import helper\nexec('replace_factory', helper.__globals__)\n",
])
def test_factory_globals_cannot_escape_through_namespace_carriers(tmp_path, code):
    files = _files_for(returned="[read]")
    files["factory.py"] += "def helper():\n    return None\nclass Helper:\n    def run(self):\n        return None\n"
    files["borrower.py"] = code
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "adk")
    assert warnings and all(not item.tools_complete for item in observations)


def test_empty_projection_cannot_establish_removal_after_factory_namespace_escape(tmp_path):
    def files(selected, escaped=False):
        data = _files_for("groups = make_tools()\na = build(groups['selected'])\n",
                          "{'selected': " + selected + ", 'unused': [write]}")
        data["factory.py"] += "def helper():\n    return None\n"
        if escaped:
            data["borrower.py"] = "from factory import helper\nconsume(helper)\n"
        return data
    result = _diff(tmp_path, files("[read]"), files("[]", True))
    assert not any(row["change"] == "removed" for row in result["rows"])
    assert result["head"]["limits"]
    assert result["head"]["coverage_gaps"]


@pytest.mark.parametrize("change", [
    "    del policy\n",
    "    def retune():\n        nonlocal policy\n        policy = False\n    retune()\n",
])
def test_named_immutable_capture_aliases_do_not_claim_ownership(tmp_path, change):
    def files(extra):
        return _files_for("def caller():\n    policy = True\n" + extra + "    return build(make_tools(policy))\na = caller()\n", "[read]")
    result = _diff(tmp_path, files(""), files(change))
    assert result["comparison_status"] == "partial"
    assert not any(row["change"] != "not_established" for row in result["rows"])
    for side in ("base", "head"):
        assert any(gap["tool"] == "read" and gap["affects"] == "implementation" for gap in result[side]["coverage_gaps"])


@pytest.mark.parametrize("kind", ["dynamic", "default"])
def test_unknown_capture_diagnostics_do_not_publish_literal_argument_payloads(tmp_path, kind):
    marker = "PRIVATE_CAPTURE_LITERAL_DO_NOT_PUBLISH"
    if kind == "dynamic":
        data = _files_for("a = build(make_tools(runtime_policy('" + marker + "')))\n", "[read]")
    else:
        data = _files_for(returned="[read]", factory=FACTORY.replace("readonly=True", "readonly={'token': '" + marker + "'}"))
    head = {**data, "app.py": data["app.py"] + "\n# Preserve unknown capture without publishing its payload.\n"}
    result = _diff(tmp_path, data, head)
    assert result["comparison_status"] == "partial"
    for side in ("base", "head"):
        assert any(gap["tool"] == "read" and gap["affects"] == "implementation" for gap in result[side]["coverage_gaps"])
    assert marker not in json.dumps(result)
    _write(tmp_path, data)
    loaded, _ = _adk(tmp_path, "builders.py")
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert marker not in json.dumps(tool.extraction["factory_calls"])
