"""Hidden Python bindings must not preserve a constructor or builtin proof."""

from __future__ import annotations

import ast

import pytest

from agents_shipgate.inputs.list_expressions import evaluation_site
from agents_shipgate.inputs.python_imports import ScopeIndex, _module_bindings
from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_framework_constructor_bindings import _files
from tests.test_imported_tool_bindings import _commit, _compare, _git

MODULE_BINDERS = [
    "[(target := object) for item in (0,)]",
    "{(target := object) for item in (0,)}",
    "{item: (target := object) for item in (0,)}",
    "((target := object) for item in (0,))",
    "[[(target := object) for inner in (0,)] for outer in (0,)]",
    "def initialize(value=(target := object)):\n    pass",
    "def initialize(*, value=(target := object)):\n    pass",
    "def initialize[T](value=(target := object)):\n    pass",
    "value = lambda argument=(target := object): argument",
    "value = lambda argument=[(target := object) for item in (0,)]: argument",
    "@(target := object)\ndef initialize():\n    pass",
    "def initialize(value: (target := object)):\n    pass",
    "def initialize() -> (target := object):\n    pass",
    "class Initialize((target := object)):\n    pass",
    "class Initialize(metaclass=(target := type)):\n    pass",
    "@(target := object)\nclass Initialize:\n    pass",
    "@(target := object)\nclass Initialize[T]:\n    pass",
    "def initialize():\n    global target\n    [(target := object) for item in (0,)]",
]


@pytest.mark.parametrize("source", MODULE_BINDERS)
def test_eager_or_comprehension_store_is_an_uncertain_module_binding(source):
    tree = ast.parse(source)
    compile(tree, "<binding-control>", "exec", dont_inherit=True)  # Validate scope syntax, without execution.
    bindings, _ = _module_bindings(tree)
    assert "target" in bindings
    assert all(not binding.top_level for binding in bindings["target"])
    assert any(isinstance(binding.node, ast.Name | ast.Global) for binding in bindings["target"])


LEXICAL_BINDERS = [
    "[target for target in values]",
    "[[target for target in values] for item in values]",
    "def local():\n    [(target := object) for item in (0,)]",
    "def local():\n    def initialize(value=(target := object)):\n        pass",
    "def local():\n    value = lambda argument=(target := object): argument",
    "value = lambda: (target := object)",
    "value = lambda: [(target := object) for item in (0,)]",
    "class Local:\n    target = object",
    "class Local:\n    def initialize(value=(target := object)):\n        pass",
    "def outer():\n    [(target := object) for item in (0,)]\n    def inner():\n        nonlocal target\n        target = None",
    "def outer():\n    def initialize(value=(target := object)):\n        pass\n    def inner():\n        nonlocal target\n        [(target := None) for item in (0,)]",
]


@pytest.mark.parametrize("source", LEXICAL_BINDERS)
def test_lexical_cells_and_temporary_comprehension_targets_do_not_bind_module(source):
    tree = ast.parse(source)
    compile(tree, "<binding-control>", "exec", dont_inherit=True)
    assert "target" not in _module_bindings(tree)[0]


@pytest.mark.parametrize("binding", [
    "[(target := object) for item in (0,)]",
    "def initialize(value=(target := object)):\n    pass",
    "value = lambda argument=(target := object): argument",
    "class Initialize((target := object)):\n    pass",
    "def initialize(value: (target := object)):\n    pass",
])
@pytest.mark.parametrize("nonlocal_use", [False, True])
def test_hidden_lexical_cells_are_visible_to_their_actual_reader(binding, nonlocal_use):
    body = "\n".join("    " + line for line in binding.splitlines())
    use = ("    def reader():\n        nonlocal target\n        observe(target)\n"
           if nonlocal_use else "    observe(target)\n")
    tree = ast.parse("def outer():\n" + body + "\n" + use)
    compile(tree, "<binding-control>", "exec", dont_inherit=True)
    scopes = ScopeIndex(tree)
    probe = next(node.args[0] for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "observe")
    bindings = scopes.enclosing_bindings(evaluation_site(scopes, probe), "target")
    assert len(bindings) == 1
    assert isinstance(bindings[0], ast.Name) and isinstance(bindings[0].ctx, ast.Store)
    assert "target" not in _module_bindings(tree)[0]


def test_lambda_body_walrus_stays_in_its_lambda_frame():
    tree = ast.parse("value = lambda: ((target := object), observe(target))")
    compile(tree, "<binding-control>", "exec", dont_inherit=True)
    scopes = ScopeIndex(tree)
    probe = next(node.args[0] for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "observe")
    assert len(scopes.enclosing_bindings(probe, "target")) == 1
    assert "target" not in _module_bindings(tree)[0]


@pytest.mark.parametrize("source,expected", [
    ("def build[Agent](tools):\n    return Agent(tools=tools)", "TypeVar"),
    ("class Builder[Agent]:\n    def build(self, tools):\n        return Agent(tools=tools)", "TypeVar"),
    ("class Builder:\n    Agent = replacement\n    def build(self, tools):\n        return Agent(tools=tools)", None),
])
def test_generic_annotation_cells_are_distinct_from_ordinary_class_locals(source, expected):
    tree = ast.parse("from framework import Agent\n" + source)
    compile(tree, "<binding-control>", "exec", dont_inherit=True)
    scopes = ScopeIndex(tree)
    use = next(node.func for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Agent")
    bindings = scopes.enclosing_bindings(use, "Agent")
    assert [type(binding).__name__ for binding in bindings] == ([] if expected is None else [expected])
    assert len(_module_bindings(tree)[0]["Agent"]) == 1


SHADOWS = [
    "[(TARGET := replacement) for item in (0,)]\n",
    "def initialize(value=(TARGET := replacement)):\n    pass\n",
    "[[(TARGET := replacement) for inner in (0,)] for outer in (0,)]\n",
]


def _surface(framework, name, selected="[read]", body="return 1", shadow="", *,
             replacement_used=False, mutating_replacement=True):
    files = _files(framework, "direct", "clean", selected, body, factory=False)
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    use = "print(TOOLS)\n" if name == "print" else ""
    tools = "list(TOOLS)" if name == "list" else "TOOLS"
    files["app.py"] = (f"from {namespace} import Agent\nfrom tools import read, write\n" +
                       ("from shim import replacement\n" if shadow or replacement_used else "") +
                       f"TOOLS = {selected}\n" +
                       shadow.replace("TARGET", name) + use + f"a = Agent(name='Built', tools={tools})\n")
    if name == "print":
        files["shim.py"] = ("def replacement(values):\n    values.clear()\n" if mutating_replacement
                            else "def replacement(values):\n    return None\n")
    elif name == "list":
        files["shim.py"] = "def replacement(values):\n    return []\n"
    return files


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("name", ["Agent", "print", "list"])
@pytest.mark.parametrize("shadow", SHADOWS)
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_hidden_module_substitution_withholds_added_changed_and_removed_bindings(tmp_path, framework, name, shadow, change):
    before = _surface(framework, name)
    after = _surface(framework, name,
                     "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                     "return 2" if change == "changed" else "return 1", shadow)
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert not result["base"]["coverage_gaps"]
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("name", ["Agent", "print", "list"])
@pytest.mark.parametrize("local", [
    "[TARGET for TARGET in (None,)]\n",
    "def helper():\n    [(TARGET := replacement) for item in (0,)]\n",
    "def helper():\n    def initialize(value=(TARGET := replacement)):\n        pass\n",
    "class Helper:\n    def initialize(value=(TARGET := replacement)):\n        pass\n",
    "def helper():\n    [(TARGET := replacement) for item in (0,)]\n    def inner():\n        nonlocal TARGET\n        TARGET = None\n",
])
def test_lexical_substitutions_keep_the_actual_module_constructor_and_builtin(tmp_path, framework, name, local):
    # These controls isolate lexical shadowing. An unrelated imported list
    # mutator already withholds ownership under the previous reader's census.
    before = _surface(framework, name, replacement_used=True, mutating_replacement=False)
    after = _surface(framework, name, "[read, write]", replacement_used=True, mutating_replacement=False)
    for files in (before, after):
        files["app.py"] += local.replace("TARGET", name)
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("a" if framework == "sdk" else "Built", "write", "added")
    ]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("kind", [
    "generic_builder", "generic_method", "generic_annotation", "type_alias",
    "function_bound_compare", "function_bound_print", "class_bound_compare", "function_constraints",
    "postponed_argument", "postponed_return", "postponed_module", "postponed_class",
    "local_variable", "local_attribute", "local_subscript",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_type_parameter_and_deferred_constructor_scopes_withhold_changes(tmp_path, framework, kind, change):
    before = _surface(framework, "Agent")
    after = _surface(framework, "Agent",
                     "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                     "return 2" if change == "changed" else "return 1")
    # SDK observations use the receiving variable where there is one. Keep
    # that identity equal to an unassigned constructor's literal name.
    before["app.py"] = before["app.py"].replace("a = Agent(", "Built = Agent(")
    call = "Agent(name='Built', tools=TOOLS)"
    route = {
        "generic_builder": "def build[Agent](tools):\n    return Agent(name='Built', tools=tools)\nBuilt = build(TOOLS)\n",
        "generic_method": "class Builder[Agent]:\n    def build(self, tools):\n        return Agent(name='Built', tools=tools)\nBuilt = Builder().build(TOOLS)\n",
        "generic_annotation": f"def initialize[Agent](value: {call}):\n    pass\n",
        "type_alias": f"type Held = {call}\n",
        "function_bound_compare": f"def initialize[T: ({call} is None)]():\n    pass\n",
        "function_bound_print": f"def initialize[T: print({call})]():\n    pass\n",
        "class_bound_compare": f"class Initialize[T: ({call} is None)]:\n    pass\n",
        "function_constraints": f"def initialize[T: (print({call}), int)]():\n    pass\n",
        "postponed_argument": f"def initialize(value: ({call} is None)):\n    pass\n",
        "postponed_return": f"def initialize() -> ({call} is None):\n    pass\n",
        "postponed_module": f"Built: ({call} is None)\n",
        "postponed_class": f"class Initialize:\n    Built: ({call} is None)\n",
        "local_variable": f"def initialize():\n    Built: ({call} is None)\n",
        "local_attribute": f"def initialize(owner):\n    owner.Built: ({call} is None)\n",
        "local_subscript": f"def initialize(owner):\n    owner['Built']: ({call} is None)\n",
    }[kind]
    after["app.py"] = after["app.py"].replace("a = " + call + "\n", route)
    if kind.startswith("postponed_"):
        for files in (before, after):
            files["app.py"] = "from __future__ import annotations\n" + files["app.py"]
    compile(after["app.py"], "<binding-control>", "exec", dont_inherit=True)
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert not result["base"]["coverage_gaps"]
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])
    if kind not in {"generic_builder", "generic_method"}:
        boundary = (
            "generic annotation scope" if kind == "generic_annotation" else
            "deferred type-alias scope" if kind == "type_alias" else
            "postponed annotation" if kind.startswith("postponed_") else
            "unevaluated function-local annotation" if kind.startswith("local_") else
            "deferred type-parameter scope"
        )
        assert any(boundary in gap["reason"] for gap in result["head"]["coverage_gaps"])
