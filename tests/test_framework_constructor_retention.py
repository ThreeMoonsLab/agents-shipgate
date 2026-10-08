"""Constructor namespaces remain owned across retaining callbacks and values."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_framework_constructor_bindings import (
    _adk,
    _commit,
    _compare,
    _files,
    _git,
    _sdk,
)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("carrier", ["coroutine", "generator", "generator_expression", "lambda", "frame", "frame_alias"])
def test_retaining_helpers_cannot_escape_a_frame_or_callable(tmp_path, framework, carrier):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "imported", selected="[]", factory=False)
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    body = {
        "coroutine": "async def helper():\n    return 0\n", "generator": "def helper():\n    yield 0\n",
        "generator_expression": "def helper():\n    return (item for item in ())\n",
        "lambda": "def helper():\n    return lambda: None\n",
        "frame": "import sys\ndef helper():\n    return sys._getframe()\n",
        "frame_alias": "from inspect import currentframe as capture\ndef helper():\n    return capture()\n",
    }[carrier]
    after["bridge.py"] = f"from {namespace} import Agent\n" + body
    after["patcher.py"] = "from bridge import helper\nfrom shim import replacement\nreplacement(helper())\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("escape", ["", "consume(a)", "consume(a.tools[0])", "alias = a.tools"])
def test_local_tool_registration_keeps_constructor_namespace_ownership(tmp_path, framework, escape):
    from tests.test_imported_tool_bindings import _write
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    decorator = "from agents import function_tool\n@function_tool\n" if framework == "sdk" else ""
    _write(tmp_path, {"app.py": f"from {namespace} import Agent\n" + decorator + "def read():\n    return 1\na = Agent(name='Built', tools=[read])\n" + escape + "\n"})
    if framework == "sdk":
        loaded = _sdk(tmp_path, "app.py")
        observations, warnings = loaded.binding_observations, loaded.warnings
    else:
        loaded, artifacts = _adk(tmp_path, "app.py")
        observations, warnings = [item for source in loaded for item in source.binding_observations], artifacts.warnings
    assert observations
    assert all(item.tools_complete == (not escape) for item in observations)
    assert bool(warnings) == bool(escape)



@pytest.mark.parametrize("provider", ["agents.py", "agents/__init__.py"])
def test_non_git_local_sdk_provider_is_not_the_external_constructor(tmp_path, provider):
    from tests.test_imported_tool_bindings import _write
    _write(tmp_path, {"app.py": "from agents import Agent, function_tool\n@function_tool\ndef read():\n    return 1\na = Agent(name='Built', tools=[read])\n",
                      provider: "class Agent:\n    pass\ndef function_tool(fn):\n    return fn\n"}, project=False)
    loaded = _sdk(tmp_path, "app.py")
    assert loaded.warnings and loaded.binding_observations
    assert all(not item.tools_complete for item in loaded.binding_observations)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("escape", ["consume(other)", "other.__class__.__init__ = replacement", "type(other).__init__ = replacement"])
@pytest.mark.parametrize("empty", [True, False])
def test_every_agent_instance_retains_the_constructor_class(tmp_path, framework, escape, empty):
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    before = _files(framework, "direct", "clean", factory=False)
    before["app.py"] = f"from {namespace} import Agent\nfrom tools import read\nother = Agent(name='Other', tools=[])\na = Agent(name='Built', tools=[read])\n"
    after = dict(before)
    after["shim.py"] = "from tools import read\ndef replacement(self, *args, **kwargs):\n    self.tools = [read]\ndef consume(value):\n    value.__class__.__init__ = replacement\n"
    after["app.py"] = (f"from {namespace} import Agent\nfrom tools import read\nfrom shim import consume, replacement\n"
                       f"other = Agent(name='Other', tools={'[]' if empty else '[read]'})\n{escape}\na = Agent(name='Built', tools=[])\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_unrelated_empty_handle_escape_invalidates_a_safe_parent_child(tmp_path, framework):
    from tests.test_imported_tool_bindings import _write
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    field = "handoffs" if framework == "sdk" else "sub_agents"
    _write(tmp_path, {"tools.py": ("from agents import function_tool\n@function_tool\n" if framework == "sdk" else "") + "def read():\n    return 1\n",
                      "app.py": f"from {namespace} import Agent\nfrom tools import read\nchild = Agent(name='Child', tools=[read])\nroot = Agent(name='Root', {field}=[child])\nother = Agent(name='Other', tools=[])\nconsume(other)\n"})
    if framework == "sdk":
        loaded = _sdk(tmp_path, "app.py")
        observations = loaded.binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for part in loaded for item in part.binding_observations]
    assert observations and all(not item.tools_complete for item in observations)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("carrier", ["lambda: None", "(value for value in ())"])
@pytest.mark.parametrize("inline", [False, True])
def test_anonymous_namespace_carriers_never_establish_bindings(tmp_path, framework, carrier, inline):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]", factory=False)
    escape = f"replacement({carrier})\n" if inline else f"proxy = {carrier}\nreplacement(proxy)\n"
    after["app.py"] = after["app.py"].replace("a = ", escape + "a = ")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("escape, complete", [("a.tools.append(local)", True), ("a.tools = [local]", True),
                                              ("a.tools.append(local)\nconsume(a.tools[0])", False)])
def test_an_empty_agent_can_receive_only_owned_namespace_carriers(tmp_path, framework, escape, complete):
    from tests.test_imported_tool_bindings import _write
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    decorator = "from agents import function_tool\n@function_tool\n" if framework == "sdk" else ""
    _write(tmp_path, {"app.py": f"from {namespace} import Agent\n" + decorator
                      + "def local():\n    return 1\na = Agent(name='Empty', tools=[])\n" + escape
                      + "\nb = Agent(name='Other', tools=[local])\n"})
    if framework == "sdk":
        observations = _sdk(tmp_path, "app.py").binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
    peer = next(item for item in observations if item.agent == ("b" if framework == "sdk" else "Other"))
    assert peer.tool_names == ["local"]
    assert peer.tools_complete is complete



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("header", ["def invoke(Agent=Agent):", "def invoke(*, Agent=Agent):",
                                    "class Invoke:\n    def invoke(Agent=Agent):"])
def test_eager_defaults_cannot_borrow_the_parameter_scope(tmp_path, framework, header):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]", factory=False)
    indent = "        " if header.startswith("class") else "    "
    injection = header + "\n" + indent + "replacement(Agent)\n" + ("Invoke.invoke()\n" if header.startswith("class") else "invoke()\n")
    after["app.py"] = after["app.py"].replace("a = ", injection + "a = ")
    after["shim.py"] = "def changed(self, *args, **kwargs):\n    self.tools = []\ndef replacement(value):\n    value.__init__ = changed\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("injection", ["def invoke(Agent: replacement(Agent)):\n    pass\n",
                                       "values = [0 for Agent in replacement(Agent)]\n"])
def test_eager_annotations_and_first_iterables_use_the_outer_scope(tmp_path, framework, injection):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]", factory=False)
    after["app.py"] = after["app.py"].replace("a = ", injection + "a = ")
    after["shim.py"] = "def changed(self, *args, **kwargs):\n    self.tools = []\ndef replacement(value):\n    value.__init__ = changed\n    return []\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("carrier", [
    "if flag:\n    def carrier():\n        pass\nelse:\n    def carrier():\n        pass\nreplacement(carrier)\n",
    "def carrier():\n    pass\nold = carrier\ncarrier = 0\nreplacement(old)\n",
    "def invoke():\n    def carrier():\n        pass\n    old = carrier\n    carrier = 0\n    replacement(old)\ninvoke()\n",
    "class Carrier:\n    def method(self):\n        pass\nold = Carrier\nCarrier = 0\nreplacement(old)\n",
])
def test_ambiguous_and_saved_local_namespace_holders_stay_unread(tmp_path, framework, carrier):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]", factory=False)
    after["app.py"] = after["app.py"].replace("a = ", carrier + "a = ")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("provider", ["google.py", "google/__init__.py", "google/adk.py", "google/adk/__init__.py",
                                     "google/adk/agents.py", "google/adk/agents/__init__.py"])
def test_non_git_local_adk_import_ancestors_cannot_prove_an_external_class(tmp_path, provider):
    from tests.test_imported_tool_bindings import _write
    _write(tmp_path, {"app.py": "from google.adk.agents import Agent\ndef read():\n    return 1\na = Agent(name='Built', tools=[read])\n",
                      provider: "class Agent:\n    pass\n"}, project=False)
    loaded, artifacts = _adk(tmp_path, "app.py")
    observations = [item for source in loaded for item in source.binding_observations]
    assert observations and artifacts.warnings and all(not item.tools_complete for item in observations)



@pytest.mark.parametrize("framework,variant", [("sdk", "opaque"), ("adk", "opaque"),
                                              ("sdk", "namespace_patch"), ("sdk", "bridge_patch")])
def test_implicit_decorator_callbacks_cannot_escape_the_constructor_namespace(tmp_path, framework, variant):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]", factory=False)
    after["shim.py"] = "retained = []\ndef decorate(fn):\n    retained.append(fn)\n    return fn\n"
    if variant == "opaque":
        injection = "from shim import decorate\n@decorate\ndef carrier():\n    pass\n"
    elif variant == "namespace_patch":
        injection = "from shim import decorate\nfw.function_tool = decorate\n@fw.function_tool\ndef carrier():\n    pass\n"
    else:
        after["bridge.py"] = "import agents\nfrom shim import decorate\nagents.function_tool = decorate\n"
        injection = "import bridge\n@fw.function_tool\ndef carrier():\n    pass\n"
    after["app.py"] = after["app.py"].replace("a = ", injection + "a = ")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("decorator", ["function_tool", "function_tool(name_override='read')"])
def test_the_unchanged_sdk_decorator_keeps_local_registration_complete(tmp_path, decorator):
    from tests.test_imported_tool_bindings import _write
    _write(tmp_path, {"app.py": "from agents import Agent, function_tool\n@" + decorator
                     + "\ndef read():\n    return 1\na = Agent(name='Built', tools=[read])\n"})
    loaded = _sdk(tmp_path, "app.py")
    assert not loaded.warnings and loaded.binding_observations
    assert all(item.tools_complete for item in loaded.binding_observations)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("escape", ["consume(a.model_fields['tools'])", "consume(a.model_fields.copy())",
                                    "consume(a.model_fields or {})", "consume(a.unknown[0])"])
def test_indexed_and_copied_agent_metadata_is_not_a_scalar_read(tmp_path, framework, escape):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]", factory=False)
    after["app.py"] += escape + "\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_literal_default_on_a_fresh_factory_projection_keeps_namespace_ownership(tmp_path, framework):
    from tests.test_imported_tool_bindings import _write
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    decorator = "from agents import function_tool\n@function_tool\n" if framework == "sdk" else ""
    _write(tmp_path, {"app.py": f"from {namespace} import Agent\n" + decorator
                      + "def read():\n    return 1\ndef make_tools():\n    return {'selected': []}\n"
                      + "a = Agent(name='Built', tools=make_tools().get('missing', [read]))\n"})
    if framework == "sdk":
        loaded = _sdk(tmp_path, "app.py")
        observations, warnings = loaded.binding_observations, loaded.warnings
    else:
        loaded, artifacts = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
        warnings = artifacts.warnings
    assert not warnings and observations and all(item.tools_complete for item in observations)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("header", ["@hook\nclass Carrier:", "class Carrier(metaclass=hook):", "class Carrier(hook):"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_class_construction_callbacks_cannot_borrow_a_clean_constructor(tmp_path, framework, header, change):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   body="return 2" if change == "changed" else "return 1", factory=False)
    injection = "from external_package import hook\n" + header + "\n    def helper(self):\n        return 0\n"
    after["app.py"] = after["app.py"].replace("a = ", injection + "a = ")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("header", ["class Carrier:", "class Carrier(object):"])
def test_plain_unescaped_classes_do_not_create_implicit_callbacks(tmp_path, framework, header):
    from tests.test_imported_tool_bindings import _write
    files = _files(framework, "direct", "clean", factory=False)
    files["app.py"] = files["app.py"].replace("a = ", header + "\n    def helper(self):\n        return 0\na = ")
    _write(tmp_path, files)
    if framework == "sdk":
        loaded = _sdk(tmp_path, "app.py")
        observations, warnings = loaded.binding_observations, loaded.warnings
    else:
        loaded, artifacts = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
        warnings = artifacts.warnings
    assert not warnings and observations and all(item.tools_complete for item in observations)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("value", ["descriptor", "Descriptor()"])
@pytest.mark.parametrize("change", ["added", "removed"])
def test_implicit_descriptor_callbacks_cannot_escape_the_constructor_namespace(tmp_path, framework, value, change):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean", selected="[read, write]" if change == "added" else "[]", factory=False)
    injection = ("from external_package import descriptor, Descriptor\nclass Carrier:\n"
                 + f"    hook = {value}\n    def helper(self):\n        return 0\n")
    after["app.py"] = after["app.py"].replace("a = ", injection + "a = ")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("carrier", [
    "@hook\ndef helper():\n    return 0\n",
    "@hook\nclass Carrier:\n    def helper(self):\n        return 0\n",
    "class Carrier(metaclass=hook):\n    def helper(self):\n        return 0\n",
    "class Carrier(hook):\n    def helper(self):\n        return 0\n",
    "class Carrier:\n    descriptor = hook\n    def helper(self):\n        return 0\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_sdk_decorator_only_modules_are_in_the_namespace_census(tmp_path, carrier, change):
    before = _files("sdk", "direct", "clean", factory=False)
    after = _files("sdk", "direct", "clean", selected="[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   body="return 2" if change == "changed" else "return 1", factory=False)
    after["tools.py"] = after["tools.py"].replace("@function_tool\n", "from external_package import hook\n" + carrier + "@function_tool\n", 1)
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("repeat", ["identical", "namespace", "conditional", "different", "rebound", "patched", "opaque", "try"])
def test_repeated_imports_require_one_unmodified_lexical_framework_identity(tmp_path, framework, repeat):
    from tests.test_imported_tool_bindings import _write
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files = _files(framework, "direct", "clean", factory=False)
    insertion = {
        "identical": f"from {namespace} import Agent\n",
        "namespace": f"import {namespace} as fw\n",
        "conditional": f"if flag:\n    import {namespace} as fw\n",
        "different": ("from openai_agents import Agent\n" if framework == "sdk" else "from google.adk.agents import LlmAgent as Agent\n"),
        "rebound": "fw = replacement\n",
        "patched": f"import {namespace} as fw\nfw.Agent.__init__ = replacement\n",
        "opaque": f"import {namespace} as fw\nreplacement(fw)\n",
        "try": f"try:\n    import {namespace} as fw\nexcept ImportError:\n    pass\n",
    }[repeat]
    files["app.py"] = files["app.py"].replace("a = ", insertion + "a = ")
    if repeat == "different":
        files["app.py"] = files["app.py"].replace("fw.Agent(name=", "Agent(name=")
    _write(tmp_path, files)
    if framework == "sdk":
        loaded = _sdk(tmp_path, "app.py")
        observations, warnings = loaded.binding_observations, loaded.warnings
    else:
        loaded, artifacts = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
        warnings = artifacts.warnings
    complete = repeat in {"identical", "namespace"}
    assert observations and all(item.tools_complete == complete for item in observations)
    assert bool(warnings) == (not complete)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("keyword_only", [False, True])
@pytest.mark.parametrize("escape", ["", "consume(tools)", "tools.append(write)", "tools = []\n    consume(tools)"])
def test_shared_defaults_need_every_lexical_parameter_use_to_retain_ownership(tmp_path, framework, keyword_only, escape):
    from tests.test_builder_bindings import _files as builder_files
    from tests.test_builder_bindings import _read
    from tests.test_imported_tool_bindings import _write
    files = builder_files(framework, "a = build()\nb = build()\n")
    files["builders.py"] = files["builders.py"].replace("def build(tools, model=None, handoffs=None):",
        "from tools import read, write\ndef build(" + ("*, " if keyword_only else "") + "tools=[read], model=None, handoffs=None):")
    if escape:
        files["builders.py"] = files["builders.py"].replace("    return Agent", "    " + escape + "\n    return Agent")
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, framework)
    assert observations and all(item.tools_complete == (not escape) for item in observations)
    assert bool(warnings) == bool(escape)



@pytest.mark.parametrize("escape", ["metadata = build.__defaults__", "other = build", "consume(a)"])
def test_sdk_shared_default_metadata_and_result_escapes_remain_unread(tmp_path, escape):
    from tests.test_builder_bindings import _files as builder_files
    from tests.test_builder_bindings import _read
    from tests.test_imported_tool_bindings import _write
    files = builder_files("sdk", "a = build()\n" + escape + "\n")
    files["builders.py"] = files["builders.py"].replace("def build(tools,", "from tools import read\ndef build(tools=[read],")
    _write(tmp_path, files)
    observations, warnings = _read(tmp_path, "sdk")
    assert warnings and observations and all(not item.tools_complete for item in observations)



def test_an_sdk_decorated_callback_does_not_prove_ownership_of_its_default(tmp_path):
    from tests.test_imported_tool_bindings import _write
    files = _files("sdk", "direct", "clean", factory=False)
    files["app.py"] = files["app.py"].replace("a = ", "from agents import function_tool\n@function_tool\ndef callback(tools=[read]):\n    return 0\na = ")
    _write(tmp_path, files)
    observations = _sdk(tmp_path, "app.py").binding_observations
    assert observations and all(not item.tools_complete for item in observations)



@pytest.mark.parametrize("mutation, complete", [
    ("a.tools.append(write)", True), ("a.tools.clear()", True), ("a.tools.reverse()", True),
    ("a.tools.insert(0, write)", True), ("a.tools.extend([write])", True), ("a.tools = [write]", True),
    ("a.tools = replacement()\na.tools.append(write)", False),
    ("a.tools.append(write)\nconsume(a)", False), ("consume(a.tools[0])", False),
    ("consume(a.tools.pop())", False), ("a.tools.sort()", False),
    ("alias = a.tools\nconsume(alias)", False), ("result = a.tools.append(write)", False),
    ("a.tools.insert(runtime_position(), write)", False),
])
def test_sdk_list_membership_edits_preserve_only_owned_tool_objects(tmp_path, mutation, complete):
    from tests.test_imported_tool_bindings import _write
    files = _files("sdk", "direct", "clean", factory=False)
    files["app.py"] += mutation + "\nb = Agent(name='Other', tools=[read])\n"
    _write(tmp_path, files)
    observations = _sdk(tmp_path, "app.py").binding_observations
    peer = next(item for item in observations if item.agent == "b")
    assert peer.tools_complete is complete



@pytest.mark.parametrize("imported", ["from registry import SHARED", "from bridge import SHARED", "import registry as R"])
@pytest.mark.parametrize("escape", ["", "consume(SHARED[0])", "alias = SHARED\nconsume(alias)"])
def test_sdk_imported_literal_member_identity_follows_exact_borrowers(tmp_path, imported, escape):
    from tests.test_imported_tool_bindings import _write
    files = _files("sdk", "direct", "clean", factory=False)
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["bridge.py"] = "from registry import SHARED\n"
    holder = "R.SHARED" if imported.startswith("import ") else "SHARED"
    files["app.py"] = files["app.py"].replace("a = ", imported + "\na = ").replace("tools=[read]", "tools=" + holder)
    files["consumer.py"] = imported + "\n" + escape.replace("SHARED", holder) + "\n"
    _write(tmp_path, files)
    observations = _sdk(tmp_path, "app.py").binding_observations
    assert observations and all(item.tools_complete == (not escape) for item in observations)



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("escape", [False, True])
@pytest.mark.parametrize("change", ["added", "removed"])
def test_imported_callable_ownership_keeps_both_builder_caller_orders(tmp_path, framework, reverse, escape, change):
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    files = _files(framework, "direct", "clean", factory=False)
    if framework == "adk":
        files["tools.py"] = f"from {namespace} import Agent\n" + files["tools.py"]
    files["tools.py"] += "\nSHARED = [read]\n"
    files["builders.py"] = f"from {namespace} import Agent\ndef build(tools):\n    return Agent(name='Built', tools=tools)\n"

    def caller(with_write, escaped=False):
        calls = ["a = build([*SHARED])", "b = build([*SHARED" + (", write" if with_write else "") + "])"]
        if reverse:
            calls.reverse()
        return (f"from {namespace} import Agent\nfrom tools import SHARED, read, write\n"
                + "from builders import build\nfrom external import consume\n" + "\n".join(calls)
                + ("\nconsume(b)" if escaped else "") + "\n")

    _git(tmp_path, "init", "-q", "-b", "main")
    files["app.py"] = caller(change == "removed")
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {"app.py": caller(change == "added", escape)})
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["rows"]
    if escape:
        assert all(row["change"] == "not_established" for row in result["rows"])
        assert any("constructor identity is not established" in gap["reason"]
                   for gap in result["head"]["coverage_gaps"])
    else:
        assert any(row["agent"] == "Built" and row["tool"] == "write" and row["change"] == change
                   and (row["after"] or row["before"])["agent_source"] == "builders.py" for row in result["rows"])
        assert not any("constructor identity is not established" in gap["reason"]
                       for stage in ("base", "head") for gap in result[stage]["coverage_gaps"])



@pytest.mark.parametrize("field", [
    "before_agent_callback", "after_agent_callback", "before_model_callback", "after_model_callback",
    "before_tool_callback", "after_tool_callback",
])
@pytest.mark.parametrize("escape", ["", "consume(a)", "consume(a.before_tool_callback)"])
def test_adk_named_callbacks_retain_only_the_actual_owned_agent(tmp_path, field, escape):
    from tests.test_imported_tool_bindings import _write
    files = _files("adk", "direct", "clean", factory=False)
    files["app.py"] = files["app.py"].replace("a = ", "def guard(*args, **kwargs):\n    return None\na = ")
    files["app.py"] = files["app.py"].replace("tools=[read]", f"tools=[read], {field}=guard")
    files["app.py"] += escape.replace("before_tool_callback", field) + "\n"
    _write(tmp_path, files)
    loaded, _ = _adk(tmp_path, "app.py")
    observations = [item for source in loaded for item in source.binding_observations]
    assert observations and all(item.tools_complete == (not escape) for item in observations)



@pytest.mark.parametrize("escape", ["", "consume(read)", "read()", "consume(read.is_enabled)", "alias = read", "self_return"])
@pytest.mark.parametrize("reexport", [False, True])
def test_sdk_enabled_callback_needs_ownership_of_the_decorated_tool(tmp_path, escape, reexport):
    from tests.test_imported_tool_bindings import _write
    files = _files("sdk", "direct", "clean", factory=False)
    files["tools.py"] = files["tools.py"].replace("@function_tool\n", "def guard(ctx, agent):\n    return True\n@function_tool(is_enabled=guard)\n", 1)
    if escape == "self_return":
        files["tools.py"] = files["tools.py"].replace("return 1", "return read", 1)
    else:
        files["consumer.py"] = ("from bridge import read\n" if reexport else "from tools import read\n") + escape + "\n"
    files["bridge.py"] = "from tools import read\n"
    if reexport:
        files["app.py"] = files["app.py"].replace("from tools import read, write", "from bridge import read\nfrom tools import write")
    _write(tmp_path, files)
    observations = _sdk(tmp_path, "app.py").binding_observations
    assert observations and all(item.tools_complete == (not escape) for item in observations)



@pytest.mark.parametrize("kind", ["clone", "dataclasses.replace", "copy.replace"])
@pytest.mark.parametrize("use, complete", [
    ("", True), ("copied is None", True),
    ("saved = copied", False), ("consume(copied)", False),
    ("consume(copied.__class__)", False), ("copied.__class__ = replacement", False),
    ("consume(copied.tools)", False), ("consume(copied.tools[0])", False),
])
def test_sdk_copy_receiver_retains_its_constructor_only_through_an_owned_result(
    tmp_path, kind, use, complete,
):
    from tests.test_imported_tool_bindings import _write
    files = _files("sdk", "direct", "clean", factory=False)
    if kind == "clone":
        construction = "a.clone(tools=[read])"
    else:
        package = kind.partition(".")[0]
        files["app.py"] += f"from {package} import replace\n"
        construction = "replace(a, tools=[read])"
    files["app.py"] += f"copied = {construction}\n{use}\nb = Agent(name='Other', tools=[read])\n"
    _write(tmp_path, files)
    observations = _sdk(tmp_path, "app.py").binding_observations
    peer = next(item for item in observations if item.agent == "b")
    assert peer.tools_complete is complete
    assert peer.tool_names == ["read"]


@pytest.mark.parametrize("constructor_root", ["agents", "openai_agents"])
@pytest.mark.parametrize("tool_root", ["agents", "openai_agents"])
@pytest.mark.parametrize("patch", [False, True])
def test_existing_sdk_import_spellings_share_only_the_owned_constructor_role(
    tmp_path, constructor_root, tool_root, patch,
):
    from tests.test_imported_tool_bindings import _write
    files = _files("sdk", "direct", "clean", factory=False)
    files["app.py"] = files["app.py"].replace("from agents import Agent", f"from {constructor_root} import Agent")
    files["tools.py"] = files["tools.py"].replace("from agents import function_tool", f"from {tool_root} import function_tool")
    if patch:
        other = "openai_agents" if constructor_root == "agents" else "agents"
        files["app.py"] += f"from {other} import Agent as Other\nOther.__init__ = replacement\n"
    _write(tmp_path, files)
    observations = _sdk(tmp_path, "app.py").binding_observations
    assert observations and all(item.tools_complete is not patch for item in observations)
    assert all(item.tool_names == ["read"] for item in observations)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("use, complete", [
    ("", True), ("other = alias\nother.tools = [read]", True),
    ("consume(alias)", False), ("alias.__class__ = replacement", False),
    ("consume(alias.tools)", False), ("alias.tools = opaque()", False),
    ("def capture():\n    alias.tools = [read]", False),
])
def test_direct_instance_alias_field_stores_do_not_export_the_constructor(
    tmp_path, framework, use, complete,
):
    from tests.test_imported_tool_bindings import _adk, _write
    files = _files(framework, "direct", "clean", factory=False)
    files["app.py"] += f"alias = a\nalias.tools = [read]\n{use}\nb = Agent(name='Other', tools=[read])\n"
    _write(tmp_path, files)
    if framework == "sdk":
        observations = _sdk(tmp_path, "app.py").binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
    peer = next(item for item in observations if item.agent in {"b", "Other"})
    assert peer.tools_complete is complete
    assert peer.tool_names == ["read"]


def test_archived_constructor_census_does_not_bind_to_the_live_head_snapshot(tmp_path):
    from agents_shipgate.core.static_inputs import (
        StaticInputSnapshot,
        activate_static_input_snapshot,
        reset_static_input_snapshot,
    )
    from tests.test_imported_tool_bindings import _write
    head, base = tmp_path / "head", tmp_path / "base"
    head.mkdir()
    files = _files("sdk", "direct", "clean", factory=False)
    files["refund_agent/agent.py"] = files.pop("app.py")
    _write(base, files)
    snapshot = StaticInputSnapshot(head)
    token = activate_static_input_snapshot(snapshot)
    try:
        loaded = _sdk(base, "refund_agent/agent.py")
        assert loaded.warnings == []
        assert loaded.binding_observations and all(item.tools_complete for item in loaded.binding_observations)
        assert snapshot.dependency_paths() == []
        snapshot.finish()
    finally:
        reset_static_input_snapshot(token)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_capturing_a_global_instance_into_a_local_store_alias_is_unread(tmp_path, framework):
    from tests.test_imported_tool_bindings import _adk, _write
    files = _files(framework, "direct", "clean", factory=False)
    files["app.py"] += (
        "def helper():\n    alias = a\n    alias.tools = [read]\n"
        "helper()\nb = Agent(name='Other', tools=[read])\n"
    )
    _write(tmp_path, files)
    if framework == "sdk":
        observations = _sdk(tmp_path, "app.py").binding_observations
    else:
        loaded, _ = _adk(tmp_path, "app.py")
        observations = [item for source in loaded for item in source.binding_observations]
    peer = next(item for item in observations if item.agent in {"b", "Other"})
    assert not peer.tools_complete and peer.tool_names == ["read"]



@pytest.mark.parametrize("carrier", [
    "def helper():\n    return 0\nconsume(helper)\n",
    "class Carrier:\n    def helper(self):\n        return 0\nconsume(Carrier)\n",
    "from external_package import hook\n@hook\ndef helper():\n    return 0\n",
    "from external_package import hook\nclass Carrier(metaclass=hook):\n    def helper(self):\n        return 0\n",
    "def helper():\n    return 0\n",
    "import patcher\n",
])
def test_reverse_borrower_namespaces_cannot_establish_a_shared_tool_removal(tmp_path, carrier):
    files = _files("sdk", "direct", "clean", factory=False)
    files["registry.py"] = "from tools import read\nSHARED = [read]\n"
    files["app.py"] = files["app.py"].replace("a = ", "import registry as R\na = ").replace("tools=[read]", "tools=R.SHARED")
    files["escape.py"] = "import registry as R\n" + carrier
    files["patcher.py"] = "from agents import Agent\nAgent.__init__ = replacement\n"
    if carrier.endswith("return 0\n"):
        files["bridge.py"] = "from escape import helper\n"
        files["consumer.py"] = "from bridge import helper\nconsume(helper)\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {"registry.py": "from tools import read\nSHARED = []\n"})
    result = _compare(tmp_path, base, head, "--scope", ".")
    rows = [row for row in result["rows"] if row["tool"] == "read"]
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in rows)
    assert any("constructor identity is not established" in gap["reason"] for gap in result["head"]["coverage_gaps"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("data", [
    "stack = {'count': 1}\nprint(stack)\n",
    "def describe(stack):\n    print(stack)\ndescribe({})\n",
    "def describe(stack=None):\n    print(stack)\ndescribe()\n",
    "def describe():\n    stack = {}\n    print(stack)\ndescribe()\n",
    "stack = {}\nstack = {'count': 2}\nprint(stack)\n",
    "if enabled:\n    stack = {}\nelse:\n    stack = []\nprint(stack)\n",
    "stack = {}\ndef describe():\n    global stack\n    print(stack)\ndescribe()\n",
    "import traceback\ndef describe():\n    vars = traceback.extract_stack()[-2][-3]\n    print(vars)\ndescribe()\n",
])
def test_lexical_data_names_do_not_become_frame_inspection(tmp_path, framework, data):
    _git(tmp_path, "init", "-q", "-b", "main")
    files = _files(framework, "direct", "clean", factory=False)
    files["app.py"] = data + files["app.py"]
    base = _commit(tmp_path, files)
    files["tools.py"] = files["tools.py"].replace("return 1", "return 2")
    head = _commit(tmp_path, files)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared", result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("a" if framework == "sdk" else "Built", "read", "changed")
    ]



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("inspection", [
    "import inspect\nfrom external import consume\nconsume(inspect)\n",
    "import inspect as inspector\nfrom external import consume\nconsume(inspector)\n",
    "import inspect\nstack = inspect.stack\nstack()\n",
    "from inspect import stack as capture\ncapture()\n",
    "import inspect\ninspect.stack()\n",
    "saved = vars\nvars = {}\nsaved()\n",
])
def test_frame_inspectors_and_saved_builtin_names_stay_unread(tmp_path, framework, inspection):
    _git(tmp_path, "init", "-q", "-b", "main")
    files = _files(framework, "direct", "clean", factory=False)
    files["app.py"] = inspection + files["app.py"]
    base = _commit(tmp_path, files)
    files["tools.py"] = files["tools.py"].replace("return 1", "return 2")
    head = _commit(tmp_path, files)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["rows"] and all(row["change"] == "not_established" for row in result["rows"])
    assert any("constructor identity is not established" in gap["reason"] for gap in result["head"]["coverage_gaps"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("use", ["copied = TOOLS.copy()\nconsume(copied)\n",
                                  "copied = TOOLS.copy()\nconsume(copied[0])\n",
                                  "print = consume\nprint(*TOOLS)\n",
                                  "consume(*TOOLS)\n"])
def test_copy_and_starred_reads_must_prove_every_receiver_and_result(tmp_path, framework, use):
    _git(tmp_path, "init", "-q", "-b", "main")
    files = _files(framework, "direct", "clean", factory=False)
    files["app.py"] = files["app.py"].replace("a = ", "from external import consume\nTOOLS = [read]\n" + use + "a = ")
    if framework == "adk":
        files["tools.py"] = "from google.adk.agents import Agent\n" + files["tools.py"]
    base = _commit(tmp_path, files)
    files["tools.py"] = files["tools.py"].replace("return 1", "return 2")
    head = _commit(tmp_path, files)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["rows"] and all(row["change"] == "not_established" for row in result["rows"])
    assert any("constructor identity is not established" in gap["reason"] for gap in result["head"]["coverage_gaps"])



@pytest.mark.parametrize("family", ["agents", "openai_agents"])
@pytest.mark.parametrize("aliased", [False, True])
def test_external_sdk_root_does_not_borrow_namespace_only_application_folder(tmp_path, family, aliased):
    _git(tmp_path, "init", "-q", "-b", "main")
    files = _files("sdk", "direct", "clean", factory=False)
    files = {name: text.replace("from agents import", f"from {family} import")
             .replace("import agents as fw", f"import {family} as fw")
             for name, text in files.items()}
    files[f"{family}/app.py"] = files.pop("app.py")
    files[f"{family}/independent.py"] = f"import {family}" + (" as sdk" if aliased else "") + "\n"
    base = _commit(tmp_path, files)
    files["tools.py"] = files["tools.py"].replace("return 1", "return 2")
    head = _commit(tmp_path, files)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared", result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("a", "read", "changed")]



@pytest.mark.parametrize("aliased", [False, True])
def test_supported_sdk_spellings_keep_each_imports_own_ownership(tmp_path, aliased):
    _git(tmp_path, "init", "-q", "-b", "main")
    files = _files("sdk", "direct", "clean", factory=False)
    # Keep the original mixed fixture: fw.Agent is agents.Agent, while the
    # tool decorators come from the separately retained SDK namespace.
    files = {name: text.replace("from agents import", "from openai_agents import")
             for name, text in files.items()}
    files["openai_agents/app.py"] = files.pop("app.py")
    files["openai_agents/independent.py"] = "import openai_agents" + (" as sdk" if aliased else "") + "\n"
    base = _commit(tmp_path, files)
    files["tools.py"] = files["tools.py"].replace("return 1", "return 2")
    head = _commit(tmp_path, files)
    result = _compare(tmp_path, base, head, "--scope", ".")
    # Both SDK spellings are explicitly modeled; each actual import still
    # receives its own provider and mutation checks. An unused namespace
    # import does not manufacture an opaque decorator invocation.
    assert result["comparison_status"] == "compared", result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("a", "read", "changed"),
    ]



@pytest.mark.parametrize("borrower", [
    "from agent import score\nconsume(score)\n",
    "from agent import score as wrapped\nconsume(wrapped)\n",
    "from agent import score\nscore.func = other\n",
    "from agent import score\nconsume(score.func.__globals__)\n",
    "import bridge as library\nconsume(library.score)\n",
    "from agent import score\n",  # Imported handles remain a bounded limit even when unused.
])
def test_self_wrapped_callable_imports_cannot_clear_actual_retained_handle(tmp_path, borrower):
    _git(tmp_path, "init", "-q", "-b", "main")
    source = ("from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\n"
              "async def score(q: str) -> str:\n    return q\n"
              "score = FunctionTool(func=score)\n"
              "root_agent = Agent(name='app', tools=[score])\n")
    files = {"agent.py": source, "consumer.py": borrower}
    if "bridge" in borrower:
        files["bridge.py"] = "from agent import score\n"
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {"agent.py": source.replace("return q\n", "return q.upper()\n")})
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "partial"
    assert [(row["agent"], row["tool"], row["change"], row["candidate_change"])
            for row in result["rows"]] == [("app", "score", "not_established", "changed")]
    assert result["rows"][0]["after"]["definition"]["source"] == "agent.py"
    assert result["rows"][0]["after"]["definition"]["line"] == 3
    assert any("constructor identity is not established" in gap["reason"]
               for gap in result["head"]["coverage_gaps"])
    assert result == _compare(tmp_path, base, head, "--scope", ".")



def test_importing_a_different_modules_same_named_callable_does_not_export_wrapper(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    source = ("from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\n"
              "async def score(q: str) -> str:\n    return q\n"
              "score = FunctionTool(func=score)\n"
              "root_agent = Agent(name='app', tools=[score])\n")
    base = _commit(tmp_path, {"agent.py": source, "elsewhere.py": "def score():\n    return 0\n",
                              "consumer.py": "from elsewhere import score\nconsume(score)\n"})
    head = _commit(tmp_path, {"agent.py": source.replace("return q\n", "return q.upper()\n")})
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared", result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("app", "score", "changed")]
