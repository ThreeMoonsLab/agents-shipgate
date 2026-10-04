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
def test_caller_local_shadow_never_resolves_to_the_module_import(tmp_path, framework):
    files = _files(
        framework,
        "def start():\n    def read():\n        return 'LOCAL'\n    return build([read])\na = start()\n",
    )
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and warnings and all(not item.tools_complete for item in observations)
    assert all("tools.py#read" not in item.tool_locators.values() for item in observations)


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
    assert warnings == [] and observations[0].tools_complete
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
