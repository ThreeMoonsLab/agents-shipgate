"""Caller-supplied capability fields retain pairing, locations and uncertainty."""

from __future__ import annotations

import pytest

from tests.test_imported_tool_bindings import _adk, _sdk, _write


def _read(root, framework):
    if framework == "sdk":
        result = _sdk(root, "builders.py")
        return result.binding_observations, result.warnings
    loaded, artifacts = _adk(root, "builders.py")
    return [item for source in loaded for item in source.binding_observations], artifacts.warnings


def _files(framework, calls, tools="tools", handoffs=None, body=None):
    imported = (
        "from agents import Agent, function_tool\n"
        if framework == "sdk"
        else "from google.adk.agents import Agent\n"
    )
    decorator = "@function_tool\n" if framework == "sdk" else ""
    second = (
        f", {'handoffs' if framework == 'sdk' else 'sub_agents'}={handoffs}" if handoffs else ""
    )
    construction = f"Agent(name='Built', tools={tools}{second})"
    return {
        "builders.py": imported
        + "def build(tools, model=None, handoffs=None):\n"
        + (body or f"    return {construction}\n"),
        "app.py": imported + "from builders import build\nfrom tools import read, write\n" + calls,
        "tools.py": ("from agents import function_tool\n" if framework == "sdk" else "")
        + decorator
        + "def read():\n    return None\n"
        + decorator
        + "def write():\n    return None\n",
    }


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_imported_builder_reads_the_actual_list_and_call_site(tmp_path, framework):
    _write(tmp_path, _files(framework, "a = build([read], runtime_model())\n"))
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observation,) = observations
    assert observation.tools_complete and observation.tool_names == ["read"]
    assert observation.tool_locators["read"].startswith("tools.py#")
    assert observation.tool_sites["read"] == [
        "app.py:4",
        "builders.py:3",
    ] or observation.tool_sites["read"] == ["builders.py:3", "app.py:4"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("spelling", ["from", "from_alias", "module_alias"])
def test_read_only_constructor_uses_its_actual_local_import(
    tmp_path, framework, canonical, spelling
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    actual = package if canonical else "fake"
    if spelling == "from":
        local, sibling, call = f"from {actual} import Agent", f"from {package} import Agent", "Agent"
    elif spelling == "from_alias":
        local = f"from {actual} import Agent as Constructor"
        sibling, call = f"from {package} import Agent as Constructor", "Constructor"
    else:
        local, sibling, call = f"import {actual} as framework", f"import {package} as framework", "framework.Agent"
    files = _files(
        framework,
        "SHARED = [read]\nfrom helper import touch\ntouch(SHARED)\na = build(SHARED)\n",
    )
    files["helper.py"] = (
        f"def touch(tools):\n    {local}\n    {call}(name='Ignored', tools=tools)\n"
        f"def unrelated():\n    {sibling}\n"
    )
    files["fake.py"] = (
        "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if canonical:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)
    else:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("header", ["default", "keyword_default", "decorator", "class_base"])
def test_read_only_constructor_import_is_resolved_where_a_header_is_evaluated(
    tmp_path, framework, canonical, header
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    actual = package if canonical else "fake"
    constructor = "Agent(name='Ignored', tools=SHARED)"
    headers = {
        "default": f"def unused(value={constructor}):\n",
        "keyword_default": f"def unused(*, value={constructor}):\n",
        "decorator": f"@{constructor}\ndef unused():\n",
        "class_base": f"class Unused({constructor}):\n",
    }
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nfrom {actual} import Agent\n"
        + headers[header]
        + f"    from {package} import Agent\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if canonical:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)
    else:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("keyword_only", [False, True])
@pytest.mark.parametrize("use", ["unused", "called", "metadata", "opaque_consumer", "reflection", "mutating_parameter"])
def test_constructor_default_retains_function_and_parameter_use_checks(tmp_path, framework, keyword_only, use):
    declaration = "*, value" if keyword_only else "value"
    body = "    value.tools.append(write)\n" if use == "mutating_parameter" else "    return None\n"
    suffix = {
        "unused": "",
        "called": "unused()\n",
        "metadata": "saved = unused.__kwdefaults__\n" if keyword_only else "saved = unused.__defaults__\n",
        "opaque_consumer": "consumer(unused)\n",
        "reflection": "globals()\n",
        "mutating_parameter": "unused()\n",
    }[use]
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, "from tools import write\n"
        + f"def unused({declaration}=framework.Agent(name='Default', tools=SHARED)):\n"
        + body + suffix,
    )
    _assert_namespace_mutation_result(built, warnings, use not in {"unused", "called"})


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("aliased", [False, True])
@pytest.mark.parametrize("patch", ["clean", "attribute", "setattr", "unrelated"])
def test_replaced_framework_constructor_is_not_a_read_only_list_borrower(
    tmp_path, framework, aliased, patch
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    imported = f"import {package}" + (" as framework" if aliased else "")
    namespace = "framework" if aliased else package
    changes = {
        "clean": "",
        "attribute": f"{namespace}.Agent = fake.Agent\n",
        "setattr": f"setattr({namespace}, 'Agent', fake.Agent)\n",
        "unrelated": f"{namespace}.unrelated = fake.Agent\n",
    }
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\n{imported}\nimport fake\n"
        + changes[patch]
        + f"{namespace}.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if patch in {"clean", "unrelated"}:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)
    else:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)


@pytest.mark.parametrize("prefix", ["google.adk", "google.adk.agents"])
@pytest.mark.parametrize("setter", ["attribute", "setattr"])
def test_replaced_constructor_namespace_prefix_is_not_read_only(tmp_path, prefix, setter):
    receiver, _, attribute = prefix.rpartition(".")
    change = (
        f"{prefix} = fake\n" if setter == "attribute"
        else f"setattr({receiver}, '{attribute}', fake)\n"
    )
    files = _files("adk", "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        "from registry import SHARED\nimport google.adk.agents\nimport fake\n"
        + change
        + "google.adk.agents.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "adk")
    built = [item for item in observations if item.agent == "Built"]
    assert built and warnings
    assert all(not item.tools_complete and item.issues for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("patch", ["attribute", "setattr", "dict", "vars"])
def test_constructor_replacement_through_another_namespace_alias(
    tmp_path, framework, same_namespace, patch
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    other = package if same_namespace else "fake"
    changes = {
        "attribute": "other.Agent = fake.Agent\n",
        "setattr": "setattr(other, 'Agent', fake.Agent)\n",
        "dict": "other.__dict__['Agent'] = fake.Agent\n",
        "vars": "vars(other)['Agent'] = fake.Agent\n",
    }
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\n"
        f"import {other} as other\nimport fake\n"
        + changes[patch]
        + "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if same_namespace:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
def test_constructor_patch_alias_uses_its_lexical_import(tmp_path, framework, same_namespace):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    local, outer = (package, "fake") if same_namespace else ("fake", package)
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\n"
        f"import {outer} as other\nimport fake\n"
        f"def touch(tools):\n    import {local} as other\n"
        "    other.Agent = fake.Agent\n    framework.Agent(name='Ignored', tools=tools)\n"
        "touch(SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if same_namespace:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("patch", ["clean", "attribute", "setattr", "dict", "vars", "unrelated"])
def test_saved_namespace_alias_retains_constructor_patch_ownership(
    tmp_path, framework, same_namespace, patch
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    saved = "framework" if same_namespace else "fake"
    changes = {
        "clean": "",
        "attribute": "other.Agent = fake.Agent\n",
        "setattr": "setattr(other, 'Agent', fake.Agent)\n",
        "dict": "other.__dict__['Agent'] = fake.Agent\n",
        "vars": "vars(other)['Agent'] = fake.Agent\n",
        "unrelated": "other.unrelated = fake.Agent\n",
    }
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\nimport fake\n"
        f"other = {saved}\n" + changes[patch]
        + "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if same_namespace and patch not in {"clean", "unrelated"}:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
def test_saved_namespace_assignment_uses_its_lexical_import(tmp_path, framework, same_namespace):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    local, outer = (package, "fake") if same_namespace else ("fake", package)
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\n"
        f"import {outer} as namespace\nimport fake\n"
        f"def touch(tools):\n    import {local} as namespace\n"
        "    other: object = namespace\n    other.Agent = fake.Agent\n"
        "    framework.Agent(name='Ignored', tools=tools)\ntouch(SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if same_namespace:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_namespace_alias_proof_limit_cannot_grant_a_read_only_constructor(tmp_path, framework):
    from agents_shipgate.inputs.list_expressions import _NAMESPACE_ALIAS_LIMIT

    package = "agents" if framework == "sdk" else "google.adk.agents"
    aliases = "".join(
        f"alias_{index} = " + ("framework" if index == 0 else f"alias_{index - 1}") + "\n"
        for index in range(_NAMESPACE_ALIAS_LIMIT + 1)
    )
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\nimport fake\n"
        + aliases + f"alias_{_NAMESPACE_ALIAS_LIMIT}.Agent = fake.Agent\n"
        + "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built and warnings
    assert all(not item.tools_complete and item.issues for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("carrier", ["list", "tuple", "dict", "opaque"])
def test_retained_namespace_cannot_hide_constructor_replacement(
    tmp_path, framework, same_namespace, carrier
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    namespace = "framework" if same_namespace else "fake"
    retained = {
        "list": f"[{namespace}][0]",
        "tuple": f"({namespace},)[0]",
        "dict": f"{{'namespace': {namespace}}}['namespace']",
        "opaque": f"retain({namespace})",
    }
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\nimport fake\n"
        "def retain(namespace):\n    return namespace\n"
        f"other = {retained[carrier]}\nother.Agent = fake.Agent\n"
        "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if same_namespace or carrier == "opaque":
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("attribute", ["Agent", "unrelated"])
def test_unread_namespace_producer_cannot_prove_a_constructor_unchanged(
    tmp_path, framework, attribute
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\nimport fake\n"
        "def retain():\n    return framework\n"
        f"other = retain()\nother.{attribute} = fake.Agent\n"
        "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if attribute == "Agent":
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("patch", ["attribute", "setattr", "dict", "vars", "dynamic_field", "unrelated"])
def test_inline_namespace_holder_store_cannot_leave_the_constructor_read_only(
    tmp_path, framework, same_namespace, patch
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    namespace = "framework" if same_namespace else "fake"
    changes = {
        "attribute": "holders[0].Agent = fake.Agent\n",
        "setattr": "setattr(holders[0], 'Agent', fake.Agent)\n",
        "dict": "holders[0].__dict__['Agent'] = fake.Agent\n",
        "vars": "vars(holders[0])['Agent'] = fake.Agent\n",
        "dynamic_field": "setattr(holders[0], str('Agent'), fake.Agent)\n",
        "unrelated": "holders[0].unrelated = fake.Agent\n",
    }
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\nimport fake\n"
        f"holders = [{namespace}]\n" + changes[patch]
        + "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    if same_namespace and patch != "unrelated":
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


def _namespace_mutation_observations(tmp_path, framework, helper, extra=None):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from registry import SHARED\nimport {package} as framework\nimport fake\n"
        + helper + "framework.Agent(name='Ignored', tools=SHARED)\n"
    )
    files["fake.py"] = "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\n"
    files.update(extra or {})
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    built = [item for item in observations if item.agent == "Built"]
    assert built
    return built, warnings


def _assert_namespace_mutation_result(built, warnings, incomplete):
    if incomplete:
        assert warnings
        assert all(not item.tools_complete and item.issues for item in built)
    else:
        assert warnings == []
        assert all(item.tools_complete and item.tool_names == ["read"] for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("getter", ["attribute", "vars", "getattr"])
@pytest.mark.parametrize("patch", ["store", "update_keyword", "update_mapping", "ior", "unrelated"])
def test_saved_namespace_dictionary_retains_its_write_owner(
    tmp_path, framework, same_namespace, getter, patch
):
    namespace = "framework" if same_namespace else "fake"
    getters = {"attribute": f"{namespace}.__dict__", "vars": f"vars({namespace})",
               "getattr": f"getattr({namespace}, '__dict__')"}
    changes = {
        "store": "slots['Agent'] = fake.Agent\n",
        "update_keyword": "slots.update(Agent=fake.Agent)\n",
        "update_mapping": "slots.update({'Agent': fake.Agent})\n",
        "ior": "slots |= {'Agent': fake.Agent}\n",
        "unrelated": "slots.update(unrelated=fake.Agent)\n",
    }
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, f"slots = {getters[getter]}\n" + changes[patch]
    )
    _assert_namespace_mutation_result(built, warnings, same_namespace and patch != "unrelated")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("spelling", ["qualified", "from_alias", "saved_alias", "operator"])
def test_qualified_namespace_setter_retains_its_write_owner(
    tmp_path, framework, same_namespace, spelling
):
    namespace = "framework" if same_namespace else "fake"
    changes = {
        "qualified": f"import builtins\nbuiltins.setattr({namespace}, 'Agent', fake.Agent)\n",
        "from_alias": f"from builtins import setattr as replace\nreplace({namespace}, 'Agent', fake.Agent)\n",
        "saved_alias": f"import builtins\nreplace = builtins.setattr\nreplace({namespace}, 'Agent', fake.Agent)\n",
        "operator": f"import operator\noperator.setitem({namespace}.__dict__, 'Agent', fake.Agent)\n",
    }
    built, warnings = _namespace_mutation_observations(tmp_path, framework, changes[spelling])
    _assert_namespace_mutation_result(built, warnings, same_namespace)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("returned_namespace", ["canonical", "foreign"])
@pytest.mark.parametrize("receiver", ["saved", "inline", "container"])
@pytest.mark.parametrize("field", ["Agent", "unrelated"])
def test_imported_producer_symbol_cannot_prove_result_namespace_ownership(
    tmp_path, framework, returned_namespace, receiver, field
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    namespace = package if returned_namespace == "canonical" else "fake"
    changes = {"saved": f"other = retain()\nother.{field} = fake.Agent\n",
               "inline": f"retain().{field} = fake.Agent\n",
               "container": f"other = [retain()][0]\nother.{field} = fake.Agent\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, "from producer import retain\n" + changes[receiver],
        {"producer.py": f"import {namespace} as namespace\ndef retain():\n    return namespace\n"},
    )
    # Neither factory result is inferred, even when its definition is visible.
    _assert_namespace_mutation_result(built, warnings, field == "Agent")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("construction", ["literal", "copy", "alias"])
@pytest.mark.parametrize("patch", ["store", "update", "ior"])
def test_fresh_dictionary_key_writes_do_not_replace_namespace_slots(
    tmp_path, framework, construction, patch
):
    declarations = {"literal": "slots = {'Agent': framework.Agent}\n",
                    "copy": "slots = dict(vars(framework))\n",
                    "alias": "original = {}\nslots = original\n"}
    changes = {"store": "slots['Agent'] = fake.Agent\n",
               "update": "slots.update(Agent=fake.Agent)\n", "ior": "slots |= {'Agent': fake.Agent}\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, declarations[construction] + changes[patch]
    )
    _assert_namespace_mutation_result(built, warnings, False)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("use,extra", [
    ("", None),
    ("saved = slots['Agent']\n", None),
    ("slots['Agent'](name='Used', tools=SHARED)\n", None),
    ("def capture():\n    return slots\n", None),
    ("__all__ = ['slots']\n", None),
    ("", {"consumer.py": "from helper import slots\n"}),
    ("framework.Agent.__init__ = fake.Agent\n", None),
    ("framework.Agent = fake.Agent\n", None),
])
def test_literal_constructor_dictionary_grants_only_confined_data(tmp_path, framework, use, extra):
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, "slots = {'Agent': framework.Agent}\nslots['Agent'] = fake.Agent\n" + use, extra,
    )
    _assert_namespace_mutation_result(built, warnings, bool(use or extra))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("use", ["", "fake.Agent(name='Used', tools=SHARED)\n", "saved = fake.Agent\n"])
def test_literal_function_dictionary_keeps_the_original_callable_census(tmp_path, framework, use):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    # Both fixtures carry an actual constructor namespace. Plain ADK tools
    # do not acquire one merely by being imported from tools.py.
    extra = {"fake.py": f"import {package} as canonical\nfrom tools import write\n"
             "def Agent(name, tools):\n    tools.append(write)\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, "slots = {'Agent': fake.Agent}\nslots['Agent'] = fake.Agent\n" + use,
        extra,
    )
    _assert_namespace_mutation_result(built, warnings, bool(use))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("spelling", ["export", "attribute"])
@pytest.mark.parametrize("field", ["Agent", "unrelated"])
def test_exported_namespace_identity_cannot_be_proved_by_its_import_path(
    tmp_path, framework, spelling, field
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    changes = {"export": f"from bridge import namespace\nnamespace.{field} = fake.Agent\n",
               "attribute": f"import bridge\nbridge.namespace.{field} = fake.Agent\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, changes[spelling], {"bridge.py": f"import {package} as namespace\n"}
    )
    _assert_namespace_mutation_result(built, warnings, field == "Agent")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("field", ["__init__", "__call__", "model_post_init"])
def test_constructor_implementation_replacement_cannot_leave_a_read_only_proof(tmp_path, framework, field):
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, f"framework.Agent.{field} = fake.Agent\n"
    )
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("spelling", ["local_getter", "builtin_getter", "builtin_copy", "builtin_setter_alias"])
def test_changed_namespace_builtins_cannot_prove_receiver_independence(tmp_path, framework, spelling):
    changes = {
        "local_getter": "def vars(owner):\n    return framework.__dict__\nslots=vars(fake)\nslots['Agent']=fake.Agent\n",
        "builtin_getter": "import builtins\nbuiltins.vars=lambda owner: framework.__dict__\nslots=vars(fake)\nslots['Agent']=fake.Agent\n",
        "builtin_copy": "import builtins\nbuiltins.dict=lambda owner: framework.__dict__\nslots=dict(vars(fake))\nslots['Agent']=fake.Agent\n",
        "builtin_setter_alias": "import builtins\ndef mutate(owner, field, value):\n    framework.Agent=value\nbuiltins.setattr=mutate\nfrom builtins import setattr as replace\nreplace(fake, 'Agent', fake.Agent)\n",
    }
    built, warnings = _namespace_mutation_observations(tmp_path, framework, changes[spelling])
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("exceeds_bound", [False, True])
def test_fresh_dictionary_provenance_stops_at_its_proof_bound(tmp_path, framework, exceeds_bound):
    from agents_shipgate.inputs.list_expressions import _NAMESPACE_ALIAS_LIMIT

    length = _NAMESPACE_ALIAS_LIMIT + 2 if exceeds_bound else 4
    declarations = "slots_0 = {}\n" + "".join(
        f"slots_{index} = slots_{index - 1}\n" for index in range(1, length)
    )
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, declarations + f"slots_{length - 1}['Agent'] = fake.Agent\n"
    )
    _assert_namespace_mutation_result(built, warnings, exceeds_bound)


@pytest.mark.parametrize("use,extra", [
    ("slots['Agent'](name='Used', tools=SHARED)\n", None),
    ("saved = slots['Agent']\n", None),
    ("consumer(slots)\n", None),
    ("def capture():\n    return slots\n", None),
    ("", {"consumer.py": "from helper import slots\n"}),
    ("fake.Agent(name='Used', tools=SHARED)\n", None),
    ("fake.Agent.__globals__['changed'] = framework\n", None),
])
def test_fresh_dictionary_function_sink_keeps_other_namespace_uses_partial(tmp_path, use, extra):
    built, warnings = _namespace_mutation_observations(
        tmp_path, "sdk", "slots = {}\nslots['Agent'] = fake.Agent\n" + use, extra,
    )
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("write", ["slots.update(Agent=fake.Agent)", "slots.update({'Agent': fake.Agent})",
                                  "slots |= {'Agent': fake.Agent}"])
@pytest.mark.parametrize("use", ["", "saved = slots['Agent']\n", "fake.Agent(name='Used', tools=SHARED)\n"])
def test_fresh_dictionary_update_sink_keeps_function_use_ownership(tmp_path, write, use):
    built, warnings = _namespace_mutation_observations(
        tmp_path, "sdk", "original = {}\nslots = original\n" + write + "\n" + use,
    )
    _assert_namespace_mutation_result(built, warnings, bool(use))


@pytest.mark.parametrize("write", [
    "saved = slots.update(Agent=fake.Agent)",
    "saved = slots.update\nsaved(Agent=fake.Agent)",
    "slots.update(**{'Agent': fake.Agent})",
    "slots.update(*[{'Agent': fake.Agent}])",
    "slots.update({key(): fake.Agent})",
    "slots |= {key(): fake.Agent}",
    "slots.update(Agent=fake)",
    "slots.update(Agent=fake.Replacement)",
    "slots |= {'Agent': fake.Replacement}",
    "slots.update(Agent=lambda: fake.Agent)",
])
def test_fresh_dictionary_update_sink_does_not_grant_protocol_or_nonfunction_values(tmp_path, write):
    built, warnings = _namespace_mutation_observations(
        tmp_path, "sdk", "slots = {}\n" + write + "\n",
        {"fake.py": "from tools import write\ndef Agent(name, tools):\n    tools.append(write)\nclass Replacement:\n    pass\n"},
    )
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("use,extra", [
    ("", None),
    ("consumer(other)\n", None),
    ("saved = other.Agent\n", None),
    ("def capture():\n    return other\n", None),
    ("del other\n", None),
    ("", {"consumer.py": "from helper import other\n"}),
])
def test_unused_namespace_copy_has_no_read_capture_or_export_authority(tmp_path, framework, use, extra):
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, "other = framework\n" + use, extra,
    )
    _assert_namespace_mutation_result(built, warnings, bool(use or extra))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("primitive", ["vars", "dict"])
@pytest.mark.parametrize("owner", ["builtin", "foreign", "fresh"])
@pytest.mark.parametrize("patch", ["dynamic_key", "saved_mapping", "ior", "method_ior", "operator_ior", "pop_setdefault"])
def test_builtin_stability_follows_the_owner_of_dictionary_writes(
    tmp_path, framework, primitive, owner, patch
):
    declarations = {"builtin": "namespace = builtins.__dict__\n",
                    "foreign": "namespace = fake.__dict__\n", "fresh": "namespace = {}\n"}
    replacement = "lambda value: framework.__dict__"
    changes = {
        "dynamic_key": f"namespace[str('{primitive}')] = {replacement}\n",
        "saved_mapping": f"changes = {{'{primitive}': {replacement}}}\nnamespace.update(changes)\n",
        "ior": f"namespace |= {{'{primitive}': {replacement}}}\n",
        "method_ior": f"namespace.__ior__({{'{primitive}': {replacement}}})\n",
        "operator_ior": f"import operator\noperator.ior(namespace, {{'{primitive}': {replacement}}})\n",
        "pop_setdefault": f"namespace.pop('{primitive}', None)\nnamespace.setdefault('{primitive}', {replacement})\n",
    }
    getter = "vars(fake)" if primitive == "vars" else "dict(vars(fake))"
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, "import builtins\n" + declarations[owner] + changes[patch]
        + f"slots = {getter}\nslots['Agent'] = fake.Agent\n",
    )
    _assert_namespace_mutation_result(built, warnings, owner == "builtin")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("spelling", ["operator", "operator_alias", "method", "method_alias", "unbound_update", "unbound_setitem"])
def test_dictionary_protocol_mutators_retain_the_actual_receiver(
    tmp_path, framework, same_namespace, spelling
):
    namespace = "framework" if same_namespace else "fake"
    changes = {
        "operator": "import operator\noperator.ior(slots, {'Agent': fake.Agent})\n",
        "operator_alias": "from operator import ior as combine\ncombine(slots, {'Agent': fake.Agent})\n",
        "method": "slots.__ior__({'Agent': fake.Agent})\n",
        "method_alias": "combine = slots.__ior__\ncombine({'Agent': fake.Agent})\n",
        "unbound_update": "import builtins\nbuiltins.dict.update(slots, Agent=fake.Agent)\n",
        "unbound_setitem": "dict.__setitem__(slots, 'Agent', fake.Agent)\n",
    }
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, f"slots = {namespace}.__dict__\n" + changes[spelling],
    )
    _assert_namespace_mutation_result(built, warnings, same_namespace)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("projection", ["vars", "attribute"])
def test_dictionary_projection_cannot_erase_an_opaque_module_call_result(tmp_path, framework, projection):
    # The opaque input is not granted a runtime callable-module interpretation.
    # Even that unknown result must not acquire owner proof from its import.
    getter = "vars(producer())" if projection == "vars" else "producer().__dict__"
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, f"import producer\nslots = {getter}\nslots['Agent'] = fake.Agent\n",
        {"producer.py": "value = None\n"},
    )
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", ["fresh_store", "fresh_update", "foreign_attribute"])
def test_unrelated_primitive_field_names_do_not_poison_builtin_copy_proof(tmp_path, framework, patch):
    changes = {"fresh_store": "ordinary = {}\nordinary['dict'] = None\n",
               "fresh_update": "ordinary = {}\nordinary.update(dict=None)\n",
               "foreign_attribute": "fake.dict = None\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, changes[patch] + "slots = dict(vars(framework))\nslots['Agent'] = fake.Agent\n",
    )
    _assert_namespace_mutation_result(built, warnings, False)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("owner", ["framework", "foreign", "fresh"])
@pytest.mark.parametrize("spelling", ["method", "unbound", "qualified"])
@pytest.mark.parametrize("field", ["Agent", "unrelated"])
def test_dictionary_initialization_retains_its_namespace_write_owner(
    tmp_path, framework, owner, spelling, field
):
    declarations = {"framework": "slots = vars(framework)\n",
                    "foreign": "slots = vars(fake)\n", "fresh": "slots = {}\n"}
    mutations = {"method": f"slots.__init__({field}=fake.Agent)\n",
                 "unbound": f"dict.__init__(slots, {field}=fake.Agent)\n",
                 "qualified": f"import builtins\nbuiltins.dict.__init__(slots, {field}=fake.Agent)\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, declarations[owner] + mutations[spelling]
    )
    _assert_namespace_mutation_result(built, warnings, owner == "framework" and field == "Agent")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("primitive", ["setattr", "delattr", "vars"])
def test_saved_bare_builtin_alias_keeps_its_lexical_terminal(
    tmp_path, framework, same_namespace, primitive
):
    namespace = "framework" if same_namespace else "fake"
    mutations = {"setattr": f"replace({namespace}, 'Agent', fake.Agent)\n",
                 "delattr": f"replace({namespace}, 'Agent')\n",
                 "vars": f"slots = replace({namespace})\nslots['Agent'] = fake.Agent\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, f"replace = {primitive}\n" + mutations[primitive]
    )
    # Deletion has no same-function source-slot proof: destroying a function
    # may release metadata or callbacks. A foreign receiver alone does not
    # establish this operation; setters and mapping writes keep their roles.
    _assert_namespace_mutation_result(built, warnings, same_namespace or primitive == "delattr")


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_constructor", [False, True])
@pytest.mark.parametrize("spelling", ["bare", "qualified", "saved_alias"])
def test_metaclass_setter_retains_constructor_implementation_ownership(
    tmp_path, framework, same_constructor, spelling
):
    owner = "framework.Agent" if same_constructor else "fake.Agent"
    mutations = {"bare": f"type.__setattr__({owner}, '__init__', fake.initializer)\n",
                 "qualified": f"import builtins\nbuiltins.type.__setattr__({owner}, '__init__', fake.initializer)\n",
                 "saved_alias": f"replace = type.__setattr__\nreplace({owner}, '__init__', fake.initializer)\n"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, mutations[spelling],
        {"fake.py": "from tools import write\nclass Agent:\n    pass\ndef initializer(self, name, tools):\n    tools.append(write)\n"},
    )
    # An exported class may alias another constructor. Its implementation
    # receiver remains unread, even when the local source defines a class.
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("spelling", ["bare", "qualified", "saved_alias"])
def test_object_setter_retains_the_actual_module_namespace(
    tmp_path, framework, same_namespace, spelling
):
    namespace = "framework" if same_namespace else "fake"
    mutations = {"bare": f"object.__setattr__({namespace}, 'Agent', fake.Agent)\n",
                 "qualified": f"import builtins\nbuiltins.object.__setattr__({namespace}, 'Agent', fake.Agent)\n",
                 "saved_alias": f"replace = object.__setattr__\nreplace({namespace}, 'Agent', fake.Agent)\n"}
    built, warnings = _namespace_mutation_observations(tmp_path, framework, mutations[spelling])
    _assert_namespace_mutation_result(built, warnings, same_namespace)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("method", ["setitem", "delitem", "ior"])
@pytest.mark.parametrize("spelling", ["module", "from_alias", "saved_alias"])
def test_repository_operator_provider_cannot_use_the_standard_library_receiver_model(
    tmp_path, framework, method, spelling
):
    package = "agents" if framework == "sdk" else "google.adk.agents"
    imports = {"module": "import operator\n", "from_alias": f"from operator import {method} as mutate\n",
               "saved_alias": f"import operator\nmutate = operator.{method}\n"}
    callee = f"operator.{method}" if spelling == "module" else "mutate"
    arguments = {"setitem": "fake.__dict__, 'Agent', fake.Agent",
                 "delitem": "fake.__dict__, 'Agent'",
                 "ior": "fake.__dict__, {'Agent': fake.Agent}"}
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, imports[spelling] + f"{callee}({arguments[method]})\n",
        {"operator.py": f"import {package} as framework\nimport fake\ndef {method}(owner, value, *rest):\n    framework.Agent = fake.Agent\n"},
    )
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("primitive", ["vars", "dict", "getattr", "setattr"])
def test_operator_fields_cannot_replace_builtin_namespace_primitives(tmp_path, framework, primitive):
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework,
        f"import operator\noperator.{primitive} = None\nslots = vars(fake)\nslots['Agent'] = fake.Agent\n",
    )
    _assert_namespace_mutation_result(built, warnings, False)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("import_root", ["absent", "empty", "linked", "local_provider", "case_root", "case_provider"])
def test_operator_receiver_proof_reads_import_roots_above_the_caller_scope(
    tmp_path, framework, import_root
):
    (tmp_path / ".git").mkdir()
    package = "agents" if framework == "sdk" else "google.adk.agents"
    source = tmp_path / ("Src" if import_root == "case_root" else "src")
    if import_root == "linked":
        target = tmp_path / "unread"
        target.mkdir()
        source.symlink_to(target, target_is_directory=True)
    elif import_root != "absent":
        source.mkdir()
    if import_root in {"linked", "local_provider", "case_root", "case_provider"}:
        filename = "Operator.py" if import_root == "case_provider" else "operator.py"
        (source / filename).write_text(
            f"import {package} as framework\nimport fake\ndef ior(owner, values):\n    framework.Agent = fake.Agent\n"
        )
    built, warnings = _namespace_mutation_observations(
        tmp_path / "app", framework,
        "import operator\noperator.ior(fake.__dict__, {'Agent': fake.Agent})\n",
    )
    # The exact named lookup must establish absence on this filesystem. Both
    # case-sensitive and case-insensitive filesystems exercise the same test.
    provider_reachable = (tmp_path / "src" / "operator.py").exists()
    _assert_namespace_mutation_result(built, warnings, import_root == "linked" or provider_reachable)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("mutator", ["setter", "saved_setter", "operator_setitem", "operator_ior", "object_setter", "type_setter", "dict_init", "dict_update"])
def test_unpacked_mutation_receivers_cannot_acquire_namespace_ownership_proof(
    tmp_path, framework, same_namespace, mutator
):
    namespace = "framework" if same_namespace else "fake"
    arguments = f"[{namespace}, 'Agent', fake.Agent]"
    mapping = f"[{namespace}.__dict__, {{'Agent': fake.Agent}}]"
    mutations = {
        "setter": f"setattr(*{arguments})\n",
        "saved_setter": f"payload = {arguments}\nreplace = setattr\nreplace(*payload)\n",
        "operator_setitem": f"import operator\noperator.setitem(*[{namespace}.__dict__, 'Agent', fake.Agent])\n",
        "operator_ior": f"import operator\noperator.ior(*{mapping})\n",
        "object_setter": f"object.__setattr__(*{arguments})\n",
        "type_setter": f"type.__setattr__(*[{namespace}.Agent, '__init__', fake.initializer])\n",
        "dict_init": f"dict.__init__(*{mapping})\n",
        "dict_update": f"dict.update(*{mapping})\n",
    }
    built, warnings = _namespace_mutation_observations(
        tmp_path, framework, mutations[mutator],
        {"fake.py": "from tools import write\nclass Agent:\n    def __init__(self, name, tools):\n        tools.append(write)\ndef initializer(self, name, tools):\n    tools.append(write)\n"},
    )
    # Argument unpacking is not an ownership proof, including a payload that
    # looks foreign. No receiver or argument values are inferred from it.
    _assert_namespace_mutation_result(built, warnings, True)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("same_namespace", [False, True])
@pytest.mark.parametrize("mutator", ["setter", "operator_ior", "dict_update"])
def test_an_explicit_mutation_receiver_keeps_ownership_when_later_arguments_are_unpacked(
    tmp_path, framework, same_namespace, mutator
):
    namespace = "framework" if same_namespace else "fake"
    mutations = {
        "setter": f"setattr({namespace}, *['Agent', fake.Agent])\n",
        "operator_ior": f"import operator\noperator.ior({namespace}.__dict__, *[{{'Agent': fake.Agent}}])\n",
        "dict_update": f"dict.update({namespace}.__dict__, *[{{'Agent': fake.Agent}}])\n",
    }
    built, warnings = _namespace_mutation_observations(tmp_path, framework, mutations[mutator])
    _assert_namespace_mutation_result(built, warnings, same_namespace)


def test_adk_returned_builder_with_a_dynamic_name_is_not_complete(tmp_path):
    files = _files("adk", "a = build([read], 'Chosen')\n")
    files["builders.py"] = files["builders.py"].replace("name='Built'", "name=model")
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "adk")
    assert any("identity" in warning and "caller-supplied name" in warning for warning in warnings)
    assert observations and all(
        not item.tools_complete and not item.handoffs_complete for item in observations
    )


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_distinct_callers_with_different_lists_are_explicitly_ambiguous(tmp_path, framework):
    _write(tmp_path, _files(framework, "a = build([read])\nb = build([write])\n"))
    observations, warnings = _read(tmp_path, framework)
    assert warnings and observations
    assert all(not observation.tools_complete for observation in observations)
    sites = {
        name: sorted({site for item in observations for site in item.tool_sites.get(name, [])})
        for name in ("read", "write")
    }
    assert "app.py:4" in sites["read"] and "app.py:5" not in sites["read"]
    assert "app.py:5" in sites["write"] and "app.py:4" not in sites["write"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_equivalent_callers_keep_every_location(tmp_path, framework):
    _write(tmp_path, _files(framework, "a = build([read])\nb = build([read])\n"))
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observation,) = observations
    assert observation.tools_complete
    assert sorted(observation.tool_sites["read"]) == ["app.py:4", "app.py:5", "builders.py:3"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "call",
    [
        "a = build([read], typo=True)",
        "a = build(*options)",
        "a = build(**options)",
        "callback = build",
    ],
)
def test_opaque_or_escaped_caller_is_a_named_limit(tmp_path, framework, call):
    _write(tmp_path, _files(framework, call + "\n"))
    observations, warnings = _read(tmp_path, framework)
    assert warnings and observations and all(not item.tools_complete for item in observations)
    assert any(item.issues for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_an_unavailable_third_party_builder_is_a_different_callable(tmp_path, framework):
    files = _files(framework, "a = build([read])\n")
    files["other.py"] = "from external_library import build\nb = build([other])\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    assert observations and all(item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_condition_from_the_call_site_is_preserved(tmp_path, framework):
    _write(tmp_path, _files(framework, "a = build([read] + ([write] if wanted else []))\n"))
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    (observation,) = observations
    assert observation.tools_complete
    assert observation.tool_names == ["read", "write"]
    assert observation.tool_conditions == {"write": ["`wanted`"]}


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "change",
    [
        "a.tools.append(write)",
        "a.tools += [write]",
        "getattr(a, 'tools').append(write)",
        "alias = a\nalias.tools.append(write)",
        "alias = a.tools\nalias.append(write)",
    ],
)
def test_returned_handle_changes_do_not_establish_the_passed_list(tmp_path, framework, change):
    _write(tmp_path, _files(framework, "a = build([read])\n" + change + "\n"))
    observations, warnings = _read(tmp_path, framework)
    assert warnings and observations and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("inner", [False, True])
def test_borrowed_list_mutation_reaches_another_holder(tmp_path, framework, inner):
    files = _files(
        framework,
        "SHARED = [read]\na = build(SHARED)\nb = Agent(name='Other', tools=SHARED)\n"
        + ("" if inner else "a.tools.append(write)\n"),
        body="    agent = Agent(name='Built', tools=tools)\n    agent.tools.append(write)\n    return agent\n"
        if inner
        else None,
    )
    if inner:
        files["builders.py"] = files["builders.py"].replace(
            "def build", "from tools import write\ndef build"
        )
    _write(tmp_path, files)
    if framework == "sdk":
        result = _sdk(tmp_path, "app.py")
        observations = result.binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
    other = next(item for item in observations if item.agent in {"Other", "b"})
    assert not other.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("copy", ["SHARED + []", "[*SHARED]", "list(SHARED)"])
def test_fresh_list_does_not_taint_an_independent_borrowed_holder(tmp_path, framework, copy):
    _write(
        tmp_path,
        _files(
            framework,
            f"SHARED = [read]\na = build({copy})\nb = Agent(name='Other', tools=SHARED)\na.tools.append(write)\n",
        ),
    )
    if framework == "sdk":
        observations = _sdk(tmp_path, "app.py").binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
    other = next(item for item in observations if item.agent in {"Other", "b"})
    assert other.tools_complete and other.tool_names == ["read"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_literal_default_uses_the_defining_module_not_the_callers(tmp_path, framework):
    files = _files(framework, "a = build()\n")
    files["builders.py"] = files["builders.py"].replace(
        "def build(tools,", "from tools import read\ndef build(tools=[read],"
    )
    files["app.py"] = files["app.py"].replace(
        "from tools import read, write", "from tools import write as read"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    assert observations[0].tools_complete and observations[0].tool_names == ["read"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "use",
    [
        "unknown(a)",
        "registry = [a]",
        "a.tools = [write]",
        "a.handoffs = [other]",
        "a.some_method()",
        "a.__dict__['tools'] = [write]",
    ],
)
def test_retained_or_escaped_returned_handle_remains_incomplete(tmp_path, framework, use):
    field = "handoffs" if framework == "sdk" else "sub_agents"
    use = use.replace("handoffs", field)
    _write(
        tmp_path,
        _files(framework, "a = build([read], handoffs=[])\n" + use + "\n", handoffs="handoffs"),
    )
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings
    assert all(not item.tools_complete or not item.handoffs_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_inline_returned_handle_passed_to_unknown_consumer_is_a_limit(tmp_path, framework):
    _write(tmp_path, _files(framework, "registry.register(build([read]))\n"))
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_replacing_returned_field_does_not_change_another_holder_list(tmp_path, framework):
    _write(
        tmp_path,
        _files(
            framework,
            "SHARED = [read]\na = build(SHARED)\nb = Agent(name='Other', tools=SHARED)\na.tools = [write]\n",
        ),
    )
    if framework == "sdk":
        observations = _sdk(tmp_path, "app.py").binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
    other = next(item for item in observations if item.agent in {"Other", "b"})
    assert other.tools_complete and other.tool_names == ["read"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("scope", ["module", "function"])
@pytest.mark.parametrize("field", ["tools", "targets"])
@pytest.mark.parametrize("receiver", ["direct", "alias", "annotated_alias"])
def test_replacing_a_known_instance_field_preserves_sibling_list_membership(
    tmp_path, framework, scope, field, receiver,
):
    target_field = "handoffs" if framework == "sdk" else "sub_agents"
    actual_field = "tools" if field == "tools" else target_field
    body = (
        "shared = [read, write]\n"
        "first = Agent(name='First', tools=shared)\n"
        "sibling = Agent(name='Sibling', tools=shared)\n"
        "target = Agent(name='Target', tools=[])\n"
    )
    if receiver == "direct":
        owner = "first"
    else:
        annotation = ": object" if receiver == "annotated_alias" else ""
        body += f"alias{annotation} = first\n"
        owner = "alias"
    body += f"{owner}.{actual_field} = [{'write' if field == 'tools' else 'target'}]\n"
    if scope == "function":
        body = "def build_instances():\n" + "".join("    " + line + "\n" for line in body.splitlines())
        body += "    return sibling\nroot = build_instances()\n"
    _write(tmp_path, _files(framework, body))
    observations, _ = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"sibling", "Sibling"})
    assert sibling.tools_complete
    assert sibling.tool_names == ["read", "write"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "mutation",
    ["constructor_class", "instance_class", "type_class", "opaque_producer", "local_provider", "ambiguous_alias"],
)
def test_instance_field_role_cannot_hide_constructor_or_unknown_receiver_writes(
    tmp_path, framework, mutation,
):
    body = "shared = [read]\nfirst = Agent(name='First', tools=shared)\nsibling = Agent(name='Sibling', tools=shared)\n"
    mutations = {
        "constructor_class": "Agent.tools = [write]\n",
        "instance_class": "first.__class__.tools = [write]\n",
        "type_class": "type(first).tools = [write]\n",
        "opaque_producer": "from factory import produce\ncarrier = produce()\ncarrier.__init__ = write\n",
        "local_provider": "first.tools = [write]\n",
        "ambiguous_alias": "alias = first\nif flag:\n    alias = unknown()\nalias.__init__ = write\n",
    }
    files = _files(framework, body + mutations[mutation])
    if mutation == "opaque_producer":
        imported = "from agents import Agent" if framework == "sdk" else "from google.adk.agents import Agent"
        files["factory.py"] = imported + "\ndef produce():\n    return Agent\n"
    elif mutation == "local_provider":
        files["agents.py" if framework == "sdk" else "google.py"] = "Agent = object\n"
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"sibling", "Sibling"})
    assert warnings
    assert not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_mutating_a_known_instance_list_still_reaches_its_sibling_holder(tmp_path, framework):
    _write(
        tmp_path,
        _files(
            framework,
            "shared = [read]\nfirst = Agent(name='First', tools=shared)\n"
            "sibling = Agent(name='Sibling', tools=shared)\nfirst.tools.append(write)\n",
        ),
    )
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"sibling", "Sibling"})
    assert warnings
    assert not sibling.tools_complete


def _read_instance_app(root, framework):
    if framework == "sdk":
        result = _sdk(root, "app.py")
        return result.binding_observations, result.warnings
    loaded, artifacts = _adk(root, "app.py")
    return [item for source in loaded for item in source.binding_observations], artifacts.warnings


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("field", ["tools", "handoffs", "sub_agents", "mcp_servers"])
@pytest.mark.parametrize("receiver", ["producer", "parameter", "conditional_alias", "inline"])
def test_unknown_capability_field_owner_cannot_leave_constructor_read_only(
    tmp_path, framework, field, receiver,
):
    changes = {
        "producer": f"from producer import retain\ncarrier = retain()\ncarrier.{field} = []\n",
        "parameter": f"def patch(carrier):\n    carrier.{field} = []\npatch(Agent)\n",
        "conditional_alias": f"carrier = first\nif flag:\n    carrier = Agent\ncarrier.{field} = []\n",
        "inline": f"from producer import retain\nretain().{field} = []\n",
    }
    body = "shared = [read]\nfirst = Agent(name='First', tools=shared)\n" + changes[receiver]
    body += "sibling = Agent(name='Sibling', tools=shared)\n"
    files = _files(framework, body)
    imported = "from agents import Agent" if framework == "sdk" else "from google.adk.agents import Agent"
    files["producer.py"] = imported + "\ndef retain():\n    return Agent\n"
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"sibling", "Sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("field", ["tools", "handoffs", "sub_agents", "mcp_servers"])
@pytest.mark.parametrize("caller", ["imported", "local", "qualified"])
def test_exact_returned_builder_instance_keeps_another_holder_independent(
    tmp_path, framework, field, caller,
):
    body = "shared = [read]\n"
    if caller == "local":
        body += "def local_build(tools):\n    return Agent(name='Local', tools=tools)\n"
        call = "local_build"
    elif caller == "qualified":
        body += "import builders\n"
        call = "builders.build"
    else:
        call = "build"
    body += f"first = {call}(shared)\nfirst.{field} = []\n"
    body += "sibling = Agent(name='Sibling', tools=shared)\n"
    _write(tmp_path, _files(framework, body))
    observations, _ = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"sibling", "Sibling"})
    assert sibling.tools_complete and sibling.tool_names == ["read"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "limit",
    ["class", "conditional", "async", "decorated", "variadic", "invalid_call", "local_provider", "patched_constructor", "star_import"],
)
def test_returned_instance_role_requires_exact_builder_and_constructor_evidence(
    tmp_path, framework, limit,
):
    files = _files(
        framework,
        "shared = [read]\nfirst = build(shared)\nfirst.tools = []\n"
        "sibling = Agent(name='Sibling', tools=shared)\n",
    )
    if limit == "class":
        files["builders.py"] = files["builders.py"].replace("return Agent(name='Built', tools=tools)", "return Agent")
    elif limit == "conditional":
        files["builders.py"] = files["builders.py"].replace(
            "    return Agent(name='Built', tools=tools)",
            "    if flag:\n        return Agent\n    return Agent(name='Built', tools=tools)",
        )
    elif limit == "async":
        files["builders.py"] = files["builders.py"].replace("def build(", "async def build(")
    elif limit == "decorated":
        files["builders.py"] = files["builders.py"].replace("def build(", "@unknown\ndef build(")
    elif limit == "variadic":
        files["builders.py"] = files["builders.py"].replace("def build(tools,", "def build(tools, *args,")
    elif limit == "invalid_call":
        files["app.py"] = files["app.py"].replace("build(shared)", "build(shared, unexpected=True)")
    elif limit == "local_provider":
        files["agents.py" if framework == "sdk" else "google.py"] = "Agent = object\n"
    elif limit == "patched_constructor":
        files["builders.py"] += "Agent.__new__ = unknown\n"
    elif limit == "star_import":
        files["builders.py"] += "from unknown import *\n"
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"sibling", "Sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_caller_local_shadow_never_resolves_to_the_module_import(tmp_path, framework):
    files = _files(
        framework,
        "def start():\n    def read():\n        return 'LOCAL'\n    return build([read])\na = start()\n",
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)
    assert all("tools.py#read" not in item.tool_locators.values() for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["builder", "caller", "transitive", "package", "package_initializer"])
@pytest.mark.parametrize("patch", ["new", "init", "setter"])
def test_imported_constructor_patch_cannot_establish_a_returned_instance(
    tmp_path, framework, route, patch,
):
    files = _files(
        framework,
        "shared = [read]\nfirst = build(shared)\nfirst.tools = []\n"
        "sibling = Agent(name='Sibling', tools=shared)\n",
    )
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    changes = {
        "new": "def replacement(cls, *args, **kwargs):\n    return Agent\nAgent.__new__ = staticmethod(replacement)\n",
        "init": "original = Agent.__init__\ndef replacement(self, *args, **kwargs):\n    kwargs['tools'].append(write)\n    original(self, *args, **kwargs)\nAgent.__init__ = replacement\n",
        "setter": "import builtins\nbuiltins.setattr(Agent, '__init__', unknown)\n",
    }
    provider = f"from {namespace} import Agent\nfrom tools import write\n" + changes[patch]
    if route == "caller":
        files["app.py"] = "import provider\n" + files["app.py"]
        files["provider.py"] = provider
    elif route == "transitive":
        files["builders.py"] += "import bridge\n"
        files["bridge.py"] = "import provider\n"
        files["provider.py"] = provider
    elif route == "package":
        files["builders.py"] += "from bridge import provider\n"
        files["bridge/__init__.py"] = ""
        files["bridge/provider.py"] = provider
    elif route == "package_initializer":
        files["builders.py"] += "import bridge.child\n"
        files["bridge/__init__.py"] = provider
        files["bridge/child.py"] = ""
    else:
        files["builders.py"] += "import provider\n"
        files["provider.py"] = provider
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["builder", "caller", "transitive", "package_initializer"])
@pytest.mark.parametrize("patch", ["clean", "foreign_module"])
def test_readable_imports_and_known_foreign_module_writes_keep_the_sibling_independent(
    tmp_path, framework, route, patch,
):
    files = _files(
        framework,
        "shared = [read]\nfirst = build(shared)\nfirst.tools = []\n"
        "sibling = Agent(name='Sibling', tools=shared)\n",
    )
    provider = "sentinel = None\n" if patch == "clean" else "import fake\nfake.Agent = object\n"
    files["fake.py"] = "class Agent:\n    pass\n"
    if route == "caller":
        files["app.py"] = "import provider\n" + files["app.py"]
        files["provider.py"] = provider
    elif route == "transitive":
        files["builders.py"] += "import bridge\n"
        files["bridge.py"] = "import provider\n"
        files["provider.py"] = provider
    elif route == "package_initializer":
        files["builders.py"] += "import bridge.child\n"
        files["bridge/__init__.py"] = provider
        files["bridge/child.py"] = ""
    else:
        files["builders.py"] += "import provider\n"
        files["provider.py"] = provider
    _write(tmp_path, files)
    observations, _ = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert sibling.tools_complete and sibling.tool_names == ["read"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_an_imported_foreign_class_slot_is_not_proved_independent_by_its_path(tmp_path, framework):
    files = _files(
        framework,
        "shared = [read]\nfirst = build(shared)\nfirst.tools = []\n"
        "sibling = Agent(name='Sibling', tools=shared)\n",
    )
    files["builders.py"] += "import provider\n"
    files["provider.py"] = "from fake import Agent\nAgent.__new__ = unknown\n"
    files["fake.py"] = "class Agent:\n    pass\n"
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("field", ["name", "instructions"])
def test_unknown_class_writes_to_supplied_constructor_fields_withhold_reads(tmp_path, framework, field):
    files = _files(
        framework,
        f"from producer import retain\ncarrier = retain()\ncarrier.{field} = unknown\n"
        "shared = [read]\nsibling = Agent(name='Sibling', tools=shared, instructions='Help')\n",
    )
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files["producer.py"] = f"from {namespace} import Agent\ndef retain():\n    return Agent\n"
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("field", [
    "instructions", "instruction", "model", "hooks", "description", "input_guardrails",
    "before_agent_callback", "model_config", "model_fields", "__post_init__", "__setattr__",
])
@pytest.mark.parametrize("route", ["local", "imported"])
def test_unknown_default_field_and_lifecycle_writes_withhold_constructor_reads(
    tmp_path, framework, field, route,
):
    files = _files(
        framework,
        "shared = [read]\nfirst = build(shared)\nfirst.tools = []\n"
        "sibling = Agent(name='Sibling', tools=shared)\n",
    )
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    patch = (
        "from producer import retain\nfrom tools import write\n"
        "def setter(self, value):\n    self.tools.append(write)\n"
        f"owner = retain()\nowner.{field} = property(lambda self: 'Help', setter)\n"
    )
    files["producer.py"] = f"from {namespace} import Agent\ndef retain():\n    return Agent\n"
    if route == "local":
        files["builders.py"] += patch
    else:
        files["builders.py"] += "import provider\n"
        files["provider.py"] = patch
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["builder", "caller", "direct", "helper"])
@pytest.mark.parametrize("patch", ["new", "init", "default_field"])
def test_ordinary_constructor_reads_check_imported_patchers_without_a_field_store(
    tmp_path, framework, route, patch,
):
    body = "shared = [read]\n"
    if route in {"builder", "caller"}:
        body += "first = build(shared)\n"
    elif route == "direct":
        body += "first = Agent(name='First', tools=shared)\n"
    else:
        body += "from helper import touch\ntouch(shared)\n"
    body += "sibling = Agent(name='Sibling', tools=shared)\n"
    files = _files(framework, body)
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    if route == "builder":
        files["builders.py"] += "import provider\n"
    elif route in {"caller", "direct"}:
        files["app.py"] = "import provider\n" + files["app.py"]
    else:
        files["helper.py"] = (
            f"from {namespace} import Agent\nimport provider\n"
            "def touch(tools):\n    Agent(name='Ignored', tools=tools)\n"
        )
    provider = f"from {namespace} import Agent\nfrom tools import write\n"
    if patch == "new":
        provider += "def replacement(cls, *args, **kwargs):\n    return Agent\nAgent.__new__ = staticmethod(replacement)\n"
    elif patch == "init":
        provider += (
            "old = Agent.__init__\ndef replacement(self, *args, **kwargs):\n"
            "    kwargs['tools'].append(write)\n    old(self, *args, **kwargs)\n"
            "Agent.__init__ = replacement\n"
        )
    else:
        field = "instructions" if framework == "sdk" else "instruction"
        provider = (
            "from producer import retain\nfrom tools import write\n"
            "def setter(self, value):\n    self.tools.append(write)\n"
            f"owner = retain()\nowner.{field} = property(lambda self: 'Help', setter)\n"
        )
        files["producer.py"] = f"from {namespace} import Agent\ndef retain():\n    return Agent\n"
    files["provider.py"] = provider
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert warnings and not sibling.tools_complete
    if route in {"builder", "caller"}:
        built, built_warnings = _read(tmp_path, framework)
        assert built and built_warnings and all(not item.tools_complete for item in built)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_many_plain_constructor_reads_do_not_become_an_unread_cache_limit(tmp_path, framework):
    files = _files(
        framework,
        "shared = [read]\nfrom helper import touch\ntouch(shared)\na = build(shared)\n",
    )
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files["helper.py"] = f"from {namespace} import Agent\ndef touch(tools):\n" + "".join(
        f"    Agent(name='Reader{i}', tools=tools)\n" for i in range(160)
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and not warnings
    assert all(item.tools_complete and item.tool_names == ["read"] for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("field", ["_BaseAgent__set_parent_agent_for_sub_agents", "_initialization_hook"])
@pytest.mark.parametrize("returned_store", [False, True])
def test_unknown_private_construction_hooks_cannot_establish_read_only_lists(
    tmp_path, framework, field, returned_store,
):
    body = "shared = [read]\nfirst = build(shared)\n"
    if returned_store:
        body += "first.tools = []\n"
    body += "sibling = Agent(name='Sibling', tools=shared)\n"
    files = _files(framework, body)
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files["builders.py"] += "import provider\n"
    files["producer.py"] = f"from {namespace} import Agent\ndef retain():\n    return Agent\n"
    files["provider.py"] = (
        "from producer import retain\nfrom tools import write\n"
        "def replacement(self, *args, **kwargs):\n    self.tools.append(write)\n"
        f"owner = retain()\nowner.{field} = replacement\n"
    )
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    sibling = next(item for item in observations if item.agent in {"Sibling", "sibling"})
    assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", [False, True])
def test_forwarded_constructor_reads_include_the_outer_caller_imports(tmp_path, framework, patch):
    files = _files(framework, "from forwarder import outer\na = outer([read])\n")
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files["app.py"] = "import provider\n" + files["app.py"]
    files["forwarder.py"] = "from builders import build\ndef outer(tools):\n    return build(tools)\n"
    files["provider.py"] = f"from {namespace} import Agent\nfrom tools import write\n"
    if patch:
        files["provider.py"] += (
            "old = Agent.__init__\ndef replacement(self, *args, **kwargs):\n"
            "    kwargs['tools'].append(write)\n    old(self, *args, **kwargs)\n"
            "Agent.__init__ = replacement\n"
        )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations
    if patch:
        assert warnings and all(not item.tools_complete for item in observations)
    else:
        assert not warnings and all(item.tools_complete and item.tool_names == ["read"] for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("shape", ["parameter", "literal", "empty", "omitted"])
@pytest.mark.parametrize("patch", [False, True])
def test_bound_constructor_import_proof_covers_literal_and_omitted_fields(
    tmp_path, framework, shape, patch,
):
    fields = {"parameter": ", tools=tools", "literal": ", tools=[read]", "empty": ", tools=[]", "omitted": ""}
    handoff_field = "handoffs" if framework == "sdk" else "sub_agents"
    files = _files(
        framework, "a = build([read], handoffs=[])\n",
        body=f"    return Agent(name='Built'{fields[shape]}, {handoff_field}=handoffs)\n",
    )
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files["builders.py"] += "from tools import read\n"
    files["app.py"] = "import provider\n" + files["app.py"]
    files["provider.py"] = f"from {namespace} import Agent\nfrom tools import write\n"
    if patch:
        files["provider.py"] += (
            "old = Agent.__init__\ndef replacement(self, *args, **kwargs):\n"
            "    old(self, *args, **kwargs)\n    self.tools.append(write)\n"
            "Agent.__init__ = replacement\n"
        )
    _write(tmp_path, files)
    if framework == "adk" and shape in {"empty", "omitted"} and not patch:
        loaded, artifacts = _adk(tmp_path, "builders.py")
        assert artifacts.warnings == []
        assert len(artifacts.agents) == 1
        assert artifacts.agents[0]["name"] == "Built"
        assert artifacts.agents[0]["tool_count"] == 0
        assert all(not source.binding_observations for source in loaded)
        return
    observations, warnings = _read(tmp_path, framework)
    assert observations
    if patch:
        assert warnings and all(not item.tools_complete and not item.handoffs_complete for item in observations)
    else:
        assert not warnings and all(item.tools_complete and item.handoffs_complete for item in observations)
        assert all(item.tool_names == (["read"] if shape in {"parameter", "literal"} else []) for item in observations)


@pytest.mark.parametrize("patch", [False, True])
def test_bound_generic_sdk_constructor_keeps_its_existing_read_limit(tmp_path, patch):
    files = _files("sdk", "a = build([read])\n")
    files["builders.py"] = files["builders.py"].replace("return Agent(", "return Agent[object](")
    files["app.py"] = "import provider\n" + files["app.py"]
    files["provider.py"] = "from agents import Agent\n"
    if patch:
        files["provider.py"] += "Agent.__class_getitem__ = unknown\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "sdk")
    assert observations
    assert warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("patch", [False, True])
@pytest.mark.parametrize("forwarded", [False, True])
@pytest.mark.parametrize("other_name", ["aaa_other.py", "zzz_other.py"])
def test_shared_list_helper_proof_keeps_each_borrowers_actual_invocation(
    tmp_path, framework, patch, forwarded, other_name,
):
    files = _files(framework, "")
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        f"from {namespace} import Agent\ndef touch(tools):\n"
        "    Agent(name='Borrower', tools=tools)\n"
    )
    files["app.py"] = (
        f"from {namespace} import Agent\nfrom registry import SHARED\n"
        "from helper import touch\ntouch(SHARED)\n"
        "sibling = Agent(name='Sibling', tools=SHARED)\n"
    )
    if forwarded:
        files["forwarder.py"] = "from helper import touch\ndef outer(tools):\n    touch(tools)\n"
        other_call = "from forwarder import outer\nouter(SHARED)\n"
    else:
        other_call = "from helper import touch\ntouch(SHARED)\n"
    files[other_name] = "from registry import SHARED\nimport provider\n" + other_call
    files["provider.py"] = f"from {namespace} import Agent\nfrom tools import write\n"
    if patch:
        files["provider.py"] += (
            "old = Agent.__init__\ndef replacement(self, *args, **kwargs):\n"
            "    old(self, *args, **kwargs)\n    self.tools.append(write)\n"
            "Agent.__init__ = replacement\n"
        )
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, framework)
    (sibling,) = [item for item in observations if item.agent in {"Sibling", "sibling"}]
    if patch:
        assert warnings and not sibling.tools_complete
    else:
        assert not warnings and sibling.tools_complete and sibling.tool_names == ["read"]


@pytest.mark.parametrize("forwarded", [False, True])
@pytest.mark.parametrize(
    "patch",
    ["clean", "local_class", "imported_class", "instance", "opaque_owner",
     "constructor_hook", "fake_constructor", "reassigned_instance"],
)
def test_sdk_clone_borrowing_requires_its_actual_receiver_and_caller_imports(
    tmp_path, forwarded, patch,
):
    files = _files("sdk", "")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["helper.py"] = (
        "from agents import Agent as CopyAgent\nfrom tools import write\n"
        "def mutate(self, **kwargs):\n    kwargs['tools'].append(write)\n"
        "def touch(tools):\n    base = CopyAgent(name='Base', tools=[])\n"
        "    base.clone(tools=tools)\n"
    )
    local_changes = {
        "instance": "    base.clone = mutate\n",
        "reassigned_instance": "    base = opaque()\n",
    }
    if patch in local_changes:
        files["helper.py"] = files["helper.py"].replace(
            "    base.clone", local_changes[patch] + "    base.clone"
        )
    if patch == "local_class":
        files["helper.py"] += "CopyAgent.clone = mutate\n"
    if patch == "fake_constructor":
        files["helper.py"] = files["helper.py"].replace("from agents", "from fake")
        files["fake.py"] = "def Agent(**kwargs):\n    return opaque()\n"
    files["patcher.py"] = "from agents import Agent\nfrom tools import write\n"
    if patch == "imported_class":
        files["patcher.py"] += (
            "def mutate(self, **kwargs):\n    kwargs['tools'].append(write)\n"
            "Agent.clone = mutate\n"
        )
    elif patch == "opaque_owner":
        files["patcher.py"] += "owner = opaque()\nowner.clone = replacement\n"
    elif patch == "constructor_hook":
        files["patcher.py"] += "owner = opaque()\nowner._construction_hook = replacement\n"
    files["app.py"] = (
        "from agents import Agent\nfrom registry import SHARED\nimport patcher\n"
        + ("from forwarder import outer\nouter(SHARED)\n" if forwarded
           else "from helper import touch\ntouch(SHARED)\n")
        + "sibling = Agent(name='Sibling', tools=SHARED)\n"
    )
    if forwarded:
        files["forwarder.py"] = "from helper import touch\ndef outer(tools):\n    touch(tools)\n"
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, "sdk")
    (sibling,) = [item for item in observations if item.agent == "sibling"]
    if patch == "clean":
        assert not warnings and sibling.tools_complete and sibling.tool_names == ["read"]
    else:
        assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("namespace", ["agents", "openai_agents"])
@pytest.mark.parametrize("borrower", ["constructor", "clone", "dataclasses.replace", "copy.replace"])
@pytest.mark.parametrize("provider", ["absent", "function", "class", "package", "link"])
def test_sdk_clone_borrowing_cannot_assume_a_repository_provider_is_the_sdk(
    tmp_path, namespace, borrower, provider,
):
    external = "openai_agents" if namespace == "agents" else "agents"
    construction = (
        "    CopyAgent(name='Borrower', tools=tools)\n" if borrower == "constructor"
        else "    base = CopyAgent(name='Base', tools=[])\n    base.clone(tools=tools)\n"
    )
    if borrower.endswith(".replace"):
        package = borrower.partition(".")[0]
        construction = (
            f"    from {package} import replace\n"
            "    base = CopyAgent(name='Base', tools=[])\n    replace(base, tools=tools)\n"
        )
    files = {
        "tools.py": (
            f"from {external} import function_tool\n@function_tool\n"
            "def read():\n    return None\n@function_tool\ndef write():\n    return None\n"
        ),
        "registry.py": "from tools import read\nSHARED = [read]\n",
        "helper.py": (
            f"from {namespace} import Agent as CopyAgent\n"
            "def touch(tools):\n" + construction
        ),
        "app.py": (
            f"from {external} import Agent\nfrom registry import SHARED\n"
            "from helper import touch\ntouch(SHARED)\n"
            "sibling = Agent(name='Sibling', tools=SHARED)\n"
        ),
    }
    supplied = (
        "from tools import write\nclass Agent:\n"
        "    def __init__(self, **kwargs):\n        kwargs['tools'].append(write)\n"
        "    def clone(self, tools):\n        tools.append(write)\n"
    )
    if provider == "function":
        supplied = supplied.replace("class Agent:", "class Foreign:")
        supplied += "def Agent(**kwargs):\n    return Foreign(**kwargs)\n"
    path = f"{namespace}/__init__.py" if provider == "package" else f"{namespace}.py"
    if provider != "absent":
        files["foreign.py" if provider == "link" else path] = supplied
    _write(tmp_path, files)
    if provider == "link":
        (tmp_path / path).symlink_to("foreign.py")
    observations, warnings = _read_instance_app(tmp_path, "sdk")
    (sibling,) = [item for item in observations if item.agent == "sibling"]
    if provider == "absent":
        assert not warnings and sibling.tools_complete and sibling.tool_names == ["read"]
    else:
        assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("provider", ["absent", "function", "class", "package", "link"])
def test_adk_constructor_borrowing_requires_external_provider_absence(tmp_path, provider):
    files = {
        "tools.py": "def read():\n    return None\ndef write():\n    return None\n",
        "registry.py": "from tools import read\nSHARED = [read]\n",
        "helper.py": (
            "from google.adk.agents import Agent as Borrower\n"
            "def touch(tools):\n    Borrower(name='Borrower', tools=tools)\n"
        ),
        "app.py": (
            "from google.adk.agents import Agent\nfrom registry import SHARED\n"
            "from helper import touch\ntouch(SHARED)\n"
            "sibling = Agent(name='Sibling', tools=SHARED)\n"
        ),
    }
    supplied = (
        "from tools import write\nclass Agent:\n"
        "    def __init__(self, **kwargs):\n        kwargs['tools'].append(write)\n"
    )
    if provider == "function":
        supplied = supplied.replace("class Agent:", "class Foreign:")
        supplied += "def Agent(**kwargs):\n    return Foreign(**kwargs)\n"
    path = (
        "google/adk/agents/__init__.py" if provider == "package" else "google/adk/agents.py"
    )
    if provider != "absent":
        files["foreign.py" if provider == "link" else path] = supplied
    _write(tmp_path, files)
    if provider == "link":
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).symlink_to("../../foreign.py")
    observations, warnings = _read_instance_app(tmp_path, "adk")
    (sibling,) = [item for item in observations if item.agent in {"Sibling", "sibling"}]
    if provider == "absent":
        assert not warnings and sibling.tools_complete and sibling.tool_names == ["read"]
    else:
        assert warnings and not sibling.tools_complete


@pytest.mark.parametrize("package", ["dataclasses", "copy"])
@pytest.mark.parametrize("spelling", ["bare", "qualified"])
@pytest.mark.parametrize(
    "owner", ["sdk", "opaque", "dataclass", "protocol", "returned_sdk", "alias",
              "constructor_patch", "protocol_patch", "unpacked", "extra_argument"],
)
def test_sdk_replace_borrowing_requires_a_direct_sdk_receiver(
    tmp_path, package, spelling, owner,
):
    imported = f"from {package} import replace\n" if spelling == "bare" else f"import {package}\n"
    replacement = "replace" if spelling == "bare" else f"{package}.replace"
    definition = ""
    construction = "    base = Agent(name='Base', tools=[])\n"
    receiver = "base"
    if owner == "opaque":
        construction = "    base = opaque()\n"
    elif owner == "dataclass":
        definition = (
            "from dataclasses import dataclass\n@dataclass\nclass Foreign:\n"
            "    tools: list\n"
            "    def __post_init__(self):\n        self.tools.append(write)\n"
        )
        construction = "    base = Foreign([])\n"
    elif owner == "protocol":
        definition = (
            "class Foreign:\n    def __replace__(self, **changes):\n"
            "        changes['tools'].append(write)\n        return self\n"
        )
        construction = "    base = Foreign()\n"
    elif owner == "returned_sdk":
        definition = "def make():\n    return Agent(name='Base', tools=[])\n"
        construction = "    base = make()\n"
    elif owner == "alias":
        construction += "    other = base\n"
        receiver = "other"
    elif owner in {"constructor_patch", "protocol_patch"}:
        definition = (
            "def mutate(self, **changes):\n    changes['tools'].append(write)\n"
            f"Agent.{'__init__' if owner == 'constructor_patch' else '__replace__'} = mutate\n"
        )
    elif owner == "unpacked":
        receiver = "*[base]"
    elif owner == "extra_argument":
        receiver = "base, opaque()"
    files = {
        "tools.py": (
            "from openai_agents import function_tool\n@function_tool\n"
            "def read():\n    return None\n@function_tool\ndef write():\n    return None\n"
        ),
        "registry.py": "from tools import read\nSHARED = [read]\n",
        "helper.py": (
            "from agents import Agent\nfrom tools import write\n" + imported + definition
            + "def touch(tools):\n" + construction + f"    {replacement}({receiver}, tools=tools)\n"
        ),
        "app.py": (
            "from openai_agents import Agent\nfrom registry import SHARED\n"
            "from helper import touch\ntouch(SHARED)\n"
            "sibling = Agent(name='Sibling', tools=SHARED)\n"
        ),
    }
    _write(tmp_path, files)
    observations, warnings = _read_instance_app(tmp_path, "sdk")
    (sibling,) = [item for item in observations if item.agent == "sibling"]
    if owner == "sdk":
        assert not warnings and sibling.tools_complete and sibling.tool_names == ["read"]
    else:
        assert warnings and not sibling.tools_complete


@pytest.mark.parametrize(
    "borrower", ["constructor", "clone", "dataclasses.replace", "copy.replace",
                 "foreign_dataclass", "foreign_protocol"],
)
@pytest.mark.parametrize("boundary", ["in_scope", "in_scope_snapshot", "linked", "linked_snapshot"])
def test_sdk_borrowing_requires_an_import_scope(tmp_path, borrower, boundary):
    from agents_shipgate.core.errors import InputParseError
    from agents_shipgate.core.static_inputs import (
        StaticInputSnapshot,
        activate_static_input_snapshot,
        reset_static_input_snapshot,
    )

    root = tmp_path / "workspace"
    (root / ".git").mkdir(parents=True)
    bundle = root / "bundle"
    bundle.mkdir()
    declaration = ""
    use = "base = Agent(name='Borrower', tools=SHARED)\n"
    if borrower == "clone":
        use = "base = Agent(name='Base', tools=[])\nbase.clone(tools=SHARED)\n"
    elif borrower.endswith(".replace"):
        package = borrower.partition(".")[0]
        declaration = f"from {package} import replace\n"
        use = "base = Agent(name='Base', tools=[])\nreplace(base, tools=SHARED)\n"
    elif borrower == "foreign_dataclass":
        declaration = (
            "from dataclasses import dataclass, replace\n@dataclass\nclass Foreign:\n"
            "    tools: list\n"
            "    def __post_init__(self):\n        self.tools.append(write)\n"
        )
        use = "base = Foreign([])\nreplace(base, tools=SHARED)\n"
    elif borrower == "foreign_protocol":
        declaration = (
            "from copy import replace\nclass Foreign:\n"
            "    def __replace__(self, **changes):\n"
            "        changes['tools'].append(write)\n        return self\n"
        )
        use = "base = Foreign()\nreplace(base, tools=SHARED)\n"
    source = (
        "from agents import Agent, function_tool\n"
        "@function_tool\ndef read():\n    return None\n"
        "@function_tool\ndef write():\n    return None\n"
        + declaration + "SHARED = [read]\n" + use
        + "sibling = Agent(name='Sibling', tools=SHARED)\n"
    )
    if boundary.startswith("linked"):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "app.py").write_text(source)
        (bundle / "app.py").symlink_to("../../outside/app.py")
    else:
        (bundle / "app.py").write_text(source)
    snapshot = StaticInputSnapshot(root) if boundary.endswith("snapshot") else None
    token = activate_static_input_snapshot(snapshot) if snapshot is not None else None
    try:
        if boundary == "linked_snapshot":
            with pytest.raises(InputParseError, match="symlink or reparse point"):
                _sdk(root, "bundle")
            return
        loaded = _sdk(root, "bundle")
        if snapshot is not None:
            snapshot.finish()
    finally:
        if token is not None:
            reset_static_input_snapshot(token)
    (sibling,) = [item for item in loaded.binding_observations if item.agent == "sibling"]
    if boundary.startswith("in_scope") and not borrower.startswith("foreign_"):
        assert sibling.tools_complete and sibling.tool_names == ["read"] and not sibling.issues
        if borrower == "constructor":
            assert not loaded.warnings
        else:
            # The copied agent's own graph remains unread in this increment.
            assert loaded.warnings and all(
                "agent copy" in warning and "is not read" in warning
                for warning in loaded.warnings
            )
    else:
        assert loaded.warnings and not sibling.tools_complete and sibling.issues


@pytest.mark.parametrize("boundary", ["in_scope", "in_scope_snapshot", "linked", "linked_snapshot"])
def test_adk_borrowing_requires_an_import_scope(tmp_path, boundary):
    from agents_shipgate.core.errors import InputParseError
    from agents_shipgate.core.static_inputs import (
        StaticInputSnapshot,
        activate_static_input_snapshot,
        reset_static_input_snapshot,
    )

    root = tmp_path / "workspace"
    (root / ".git").mkdir(parents=True)
    bundle = root / "bundle"
    bundle.mkdir()
    source = (
        "from google.adk.agents import Agent\n"
        "def read():\n    return None\nSHARED = [read]\n"
        "first = Agent(name='Borrower', tools=SHARED)\n"
        "sibling = Agent(name='Sibling', tools=SHARED)\n"
    )
    if boundary.startswith("linked"):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "agent.py").write_text(source)
        (bundle / "agent.py").symlink_to("../../outside/agent.py")
    else:
        (bundle / "agent.py").write_text(source)
    snapshot = StaticInputSnapshot(root) if boundary.endswith("snapshot") else None
    token = activate_static_input_snapshot(snapshot) if snapshot is not None else None
    try:
        if boundary == "linked_snapshot":
            with pytest.raises(InputParseError, match="symlink or reparse point"):
                _adk(root, "bundle")
            return
        loaded, artifacts = _adk(root, "bundle")
        if snapshot is not None:
            snapshot.finish()
    finally:
        if token is not None:
            reset_static_input_snapshot(token)
    (sibling,) = [
        item for loaded_source in loaded for item in loaded_source.binding_observations
        if item.agent == "Sibling"
    ]
    if boundary.startswith("in_scope"):
        assert not artifacts.warnings and not sibling.issues
        assert sibling.tools_complete and sibling.tool_names == ["read"]
    else:
        assert artifacts.warnings and sibling.issues and not sibling.tools_complete


def test_sdk_same_named_caller_local_functions_never_collapse_as_equivalent(tmp_path):
    files = _files(
        "sdk",
        "def start1():\n    @function_tool\n    def read():\n        return 'READ'\n    return build([read])\ndef start2():\n    @function_tool\n    def read():\n        return 'WRITE'\n    return build([read])\na = start1()\nb = start2()\n",
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "sdk")
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_mutable_default_is_shared_by_omitting_callers(tmp_path, framework):
    files = _files(framework, "a = build()\nb = build()\na.tools.append(write)\n")
    files["builders.py"] = files["builders.py"].replace(
        "def build(tools,", "from tools import read\ndef build(tools=[read],"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_mutated_explicit_fresh_list_does_not_taint_default_callers(tmp_path, framework):
    files = _files(framework, "a = build([write])\nb = build()\na.tools.append(read)\n")
    files["builders.py"] = files["builders.py"].replace(
        "def build(tools,", "from tools import read\ndef build(tools=[read],"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings
    # Shared identity remains ambiguous, but the default's member is kept.
    assert any("read" in item.tool_names for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "access", ["globals()['a'].tools.append(write)", "sys.modules[__name__].a.tools.append(write)"]
)
def test_caller_namespace_reflection_is_a_limit(tmp_path, framework, access):
    _write(tmp_path, _files(framework, "import sys\na = build([read])\n" + access + "\n"))
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "consumer",
    [
        "from app import a\nfrom tools import write\na.tools.append(write)\n",
        "import app as other\nfrom tools import write\nother.a.tools.append(write)\n",
    ],
)
def test_imported_returned_handle_is_a_named_limit(tmp_path, framework, consumer):
    files = _files(framework, "a = build([read])\n")
    files["consumer.py"] = consumer
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_handoff_only_equivalent_callers_keep_provenance(tmp_path, framework):
    files = _files(framework, "", handoffs="handoffs")
    files["builders.py"] += (
        "from tools import read\nreader = Agent(name='Reader')\na = build([], handoffs=[reader])\nb = build([], handoffs=[reader])\n"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings == []
    observation = next(item for item in observations if item.agent == "Built")
    name = "reader" if framework == "sdk" else "Reader"
    assert sorted(observation.handoff_sites[name]) == [
        "builders.py:3",
        "builders.py:6",
        "builders.py:7",
    ]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_package_exported_returned_handle_is_a_limit(tmp_path, framework):
    files = _files(framework, "")
    files["package/__init__.py"] = (
        "from builders import build\nfrom tools import read\na = build([read])\n"
    )
    files["consumer.py"] = "from package import a\nfrom tools import write\na.tools.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("local_holder", [False, True])
@pytest.mark.parametrize("forwarded", [False, True])
def test_mutation_through_a_borrower_taints_other_module_holders(
    tmp_path, framework, local_holder, forwarded
):
    files = _files(
        framework,
        "from registry import SHARED\n"
        + (
            "def forward(tools):\n    return build(tools)\na = forward(SHARED)\n"
            if forwarded
            else "a = build(SHARED)\n"
        )
        + "a.tools.append(write)\n",
    )
    imported = (
        "from agents import Agent\n"
        if framework == "sdk"
        else "from google.adk.agents import Agent\n"
    )
    files["registry.py"] = "from tools import read, write\nSHARED = [read]\n"
    holder = imported + "b = Agent(name='Other', tools=SHARED)\n"
    if local_holder:
        files["registry.py"] += holder
        path = "registry.py"
    else:
        files["other.py"] = "from registry import SHARED\n" + holder
        path = "other.py"
    _write(tmp_path, files)
    if framework == "sdk":
        observations = _sdk(tmp_path, path).binding_observations
    else:
        loaded, _ = _adk(tmp_path, path)
        observations = [item for source in loaded for item in source.binding_observations]
    other = next(item for item in observations if item.agent in {"b", "Other"})
    assert not other.tools_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "mutator",
    [
        "import bridge\nbridge.RENAMED.append(write)\n",
        "from bridge import RENAMED as B\nB.append(write)\n",
        "import bridge\ngetattr(bridge, 'RENAMED').append(write)\n",
    ],
)
def test_renamed_shared_list_mutation_is_visible_through_a_namespace(tmp_path, framework, mutator):
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["bridge.py"] = "from registry import SHARED as RENAMED\n"
    files["mutator.py"] = "from tools import write\n" + mutator
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "mutation",
    [
        "bridge.lib.SHARED.append(write)",
        "getattr(bridge.lib, 'SHARED').append(write)",
        "getattr(getattr(bridge, 'lib'), 'SHARED').append(write)",
        "getattr(bridge, 'lib').SHARED.append(write)",
        "consume(bridge)",
    ],
)
def test_nested_namespace_shared_list_mutation_is_visible(tmp_path, framework, mutation):
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["bridge.py"] = "import registry as lib\n"
    files["mutator.py"] = "import bridge\nfrom tools import write\n" + mutation + "\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_an_ambiguous_changed_import_cannot_prove_a_shared_list_unchanged(tmp_path, framework):
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["sub/registry.py"] = "SHARED = []\n"
    files["sub/mutator.py"] = "from registry import SHARED as B\nB.append(write)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "mutation",
    [
        "consume(bridge)",
        "getattr(getattr(bridge, 'pkg'), 'registry').SHARED.append(write)",
        "globals()['bridge'].pkg.registry.SHARED.append(write)",
    ],
)
def test_parent_package_retains_shared_list_namespace(tmp_path, framework, mutation):
    files = _files(framework, "from pkg.registry import SHARED\na = build(SHARED)\n")
    files["pkg/__init__.py"] = ""
    files["pkg/registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["bridge.py"] = "import pkg\n"
    files["mutator.py"] = "import bridge\nfrom tools import write\n" + mutation + "\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_parent_package_cannot_hide_an_exported_agent_handle(tmp_path, framework):
    files = _files(framework, "a = build([read])\n")
    files["pkg/__init__.py"] = ""
    files["pkg/app.py"] = files.pop("app.py")
    files["escape.py"] = "import pkg\nconsume(pkg)\n"
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_unrelated_text_matches_do_not_expand_shared_list_borrowers(
    tmp_path, framework, monkeypatch
):
    from agents_shipgate.inputs import builder_calls

    monkeypatch.setattr(builder_calls, "MAX_CANDIDATES", 4)
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["utility.py"] = "# SHARED\nimport json\n"
    files.update({f"noise{index}.py": "# utility\n" for index in range(40)})
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    assert observations and all(item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_same_named_schema_module_does_not_expand_shared_list_borrowers(
    tmp_path, framework, monkeypatch
):
    from agents_shipgate.inputs import builder_calls

    monkeypatch.setattr(builder_calls, "MAX_CANDIDATES", 8)
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["schema/registry.py"] = "class Schema:\n    pass\n"
    files["utility.py"] = "from schema.registry import Schema\n"
    files.update({f"noise{index}.py": "from utility import Schema\n" for index in range(40)})
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert warnings == []
    assert observations and all(item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize(
    "other, complete",
    [("[read]", True), ("[]", True), ("[SHARED]", False), ("SHARED or []", False)],
)
def test_independent_literal_list_mutation_does_not_taint_a_sibling(
    tmp_path, framework, other, complete
):
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read\nSHARED = [read]\nOTHER = " + other + "\n"
    files["mutator.py"] = (
        "from registry import SHARED, OTHER\nfrom tools import write\nOTHER.append(write)\n"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and all(item.tools_complete is complete for item in observations)
    assert bool(warnings) is not complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_aliased_dynamic_import_cannot_hide_a_caller(tmp_path, framework):
    files = _files(framework, "a = build([read])\n")
    files["other.py"] = (
        "from importlib import import_module as load\nmodule = load('builders')\ngetattr(module, 'bu' + 'ild')([write])\n"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_foreign_caller_handoff_needs_its_actual_surface(tmp_path, framework):
    _write(
        tmp_path,
        _files(
            framework,
            "reader = Agent(name='Reader', tools=[write])\na = build([read], handoffs=[reader])\n",
            handoffs="handoffs",
        ),
    )
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.handoffs_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_an_omitted_field_cannot_be_added_through_the_returned_handle(tmp_path, framework):
    files = _files(framework, "a = build([], handoffs=[])\na.tools = [read]\n", handoffs="handoffs")
    files["builders.py"] = files["builders.py"].replace("tools=tools, ", "")
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_a_literal_field_is_guarded_when_another_field_is_forwarded(tmp_path, framework):
    files = _files(
        framework,
        "a = build([], handoffs=[])\na.tools = [write]\n",
        tools="[read]",
        handoffs="handoffs",
    )
    files["builders.py"] = files["builders.py"].replace(
        "def build", "from tools import read\ndef build"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_class_body_caller_is_not_a_plain_name_handle(tmp_path, framework):
    _write(
        tmp_path,
        _files(
            framework,
            "class Holder:\n    agent = build([read])\nHolder.agent.tools.append(write)\n",
        ),
    )
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


def test_sdk_directory_read_reuses_the_resolvers_canonical_ast(tmp_path):
    _write(
        tmp_path,
        {
            "a.py": "from agents import Agent\nfrom b import build_b\ndef build_a(tools):\n    return Agent(name='A', tools=tools)\nx = build_b([])\n",
            "b.py": "from agents import Agent\ndef build_b(tools):\n    return Agent(name='B', tools=tools)\n",
            "app.py": "from a import build_a\nfrom b import build_b\na = build_a([])\nb = build_b([])\n",
        },
    )
    result = _sdk(tmp_path, ".")
    b = next(item for item in result.binding_observations if item.agent == "B")
    assert b.tools_complete and b.handoffs_complete


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_filter_predicate_parameters_do_not_create_builder_dependencies(tmp_path, framework):
    files = _files(framework, "")
    files["builders.py"] = (
        files["builders.py"].split("def build")[0]
        + "from tools import read\na = Agent(name='Filtered', tools=list(filter(lambda tool: tool.allowed, [read])))\n"
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert any("anonymous callable or generator" in warning for warning in warnings)
    assert not observations[0].tools_complete and not observations[0].handoffs_complete
    assert not any("builder dependenc" in warning or "missing argument" in warning for warning in warnings)
    assert observations[0].tool_names == ["read"]
    assert observations[0].tool_conditions


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_package_qualified_computed_caller_is_a_limit(tmp_path, framework):
    files = _files(framework, "")
    files["package/builders.py"] = files.pop("builders.py")
    files["package/__init__.py"] = "from . import builders\n"
    files["app.py"] = (
        "from package.builders import build\nfrom tools import read\na = build([read])\n"
    )
    files["other.py"] = "import package as p\nx = getattr(p.builders, 'bu' + 'ild')([write])\n"
    _write(tmp_path, files)
    if framework == "sdk":
        result = _sdk(tmp_path, "package/builders.py")
        observations, warnings = result.binding_observations, result.warnings
    else:
        loaded, artifacts = _adk(tmp_path, "package/builders.py")
        observations = [item for source in loaded for item in source.binding_observations]
        warnings = artifacts.warnings
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("local_holder", [False, True])
def test_application_diff_does_not_publish_a_false_borrowed_removal(
    tmp_path, framework, local_holder
):
    from tests.test_imported_tool_bindings import _commit, _compare, _git

    imported = (
        "from agents import Agent\n"
        if framework == "sdk"
        else "from google.adk.agents import Agent\n"
    )
    files = _files(framework, "from registry import SHARED\na = build(SHARED)\n")
    files["registry.py"] = "from tools import read, write\nSHARED = [read, write]\n"
    holder = imported + "b = Agent(name='Other', tools=SHARED)\n"
    if local_holder:
        files["registry.py"] += holder
    else:
        files["other.py"] = "from registry import SHARED\n" + holder
    files["unrelated.py"] = (
        imported + "from tools import read, write\nextra = Agent(name='Extra', tools=[read])\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, files)
    head = _commit(
        tmp_path,
        {
            "registry.py": files["registry.py"].replace(
                "SHARED = [read, write]", "SHARED = [read]"
            ),
            "app.py": files["app.py"] + "a.tools.append(write)\n",
            "unrelated.py": files["unrelated.py"].replace("tools=[read]", "tools=[read, write]"),
        },
    )
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "partial"
    other = "b" if framework == "sdk" else "Other"
    rows = [row for row in result["rows"] if row["agent"] == other and row["tool"] == "write"]
    assert rows and all(row["change"] == "not_established" for row in rows)
    extra = "extra" if framework == "sdk" else "Extra"
    assert any(
        row["agent"] == extra and row["tool"] == "write" and row["change"] == "added"
        for row in result["rows"]
    )


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_application_diff_parameter_addition_carries_caller_and_definition(tmp_path, framework):
    from tests.test_imported_tool_bindings import _commit, _compare, _git

    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _files(framework, "a = build([read])\n"))
    head = _commit(tmp_path, _files(framework, "a = build([read, write])\n"))
    result = _compare(tmp_path, base, head, "--scope", ".")
    row = next(row for row in result["rows"] if row["agent"] == "Built" and row["tool"] == "write")
    assert row["change"] == "added"
    assert "app.py:4" in row["after"]["construction_sites"]
    assert row["after"]["definition"]["source"] == "tools.py"


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_unreachable_construction_does_not_bind_the_callers_tools(tmp_path, framework):
    files = _files(
        framework,
        "a = build([read])\n",
        body="    if False:\n        return Agent(name='Built', tools=tools)\n    return None\n",
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_unknown_caller_condition_stays_conditional(tmp_path, framework):
    _write(tmp_path, _files(framework, "if enabled:\n    a = build([read])\n"))
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings == []
    assert observations[0].tool_conditions["read"] == ["the caller's condition `enabled` holds"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_statically_unreachable_caller_is_a_named_limit(tmp_path, framework):
    _write(tmp_path, _files(framework, "if False:\n    a = build([read])\n"))
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)
