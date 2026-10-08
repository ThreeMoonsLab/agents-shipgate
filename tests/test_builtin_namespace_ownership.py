"""A replaced module builtin namespace cannot support lexical builtin proofs."""
from __future__ import annotations

import ast

import pytest

from agents_shipgate.inputs.python_imports import ScopeIndex, replaces_builtin_namespace
from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_framework_constructor_bindings import _files
from tests.test_imported_tool_bindings import _commit, _compare, _git


@pytest.mark.parametrize("source", [
    "__builtins__ = {}", "__builtins__: object = {}", "__builtins__ |= {}", "del __builtins__",
    "import custom_builtins as __builtins__", "from shim import VALUE as __builtins__",
    "def __builtins__():\n    pass", "class __builtins__:\n    pass",
    "for __builtins__ in values:\n    pass",
    "try:\n    run()\nexcept Exception as __builtins__:\n    pass",
    "match value:\n    case __builtins__:\n        pass",
    "match value:\n    case {**__builtins__}:\n        pass",
    "def update(value):\n    global __builtins__\n    match value:\n        case {**__builtins__}:\n            pass",
    "def update():\n    global __builtins__\n    __builtins__ = {}",
    "def make(arg=(__builtins__ := {})):\n    __builtins__ = 1",
    "[(__builtins__ := {}) for item in (0,)]",
    "def update():\n    global __builtins__\n    [(__builtins__ := {}) for item in (0,)]",
    "def make(arg=[(__builtins__ := {}) for item in (0,)]):\n    __builtins__ = 1",
    "value = lambda arg=(__builtins__ := {}): arg",
    "value = lambda arg=[(__builtins__ := {}) for item in (0,)]: arg",
    "class Built(metaclass=[(__builtins__ := {}) for item in (0,)][0]):\n    pass",
    "@decorate([(__builtins__ := {}) for item in (0,)])\ndef built():\n    __builtins__ = 1",
])
def test_module_builtin_namespace_binding_forms_are_unread(source):
    tree = ast.parse(source)
    scopes = ScopeIndex(tree)
    assert any(replaces_builtin_namespace(node, scopes) for node in ast.walk(tree))


@pytest.mark.parametrize("source", [
    "def local():\n    __builtins__ = {}",
    "def local():\n    import custom_builtins as __builtins__",
    "def local():\n    from shim import VALUE as __builtins__",
    "class Local:\n    __builtins__ = {}",
    "class Local:\n    import custom_builtins as __builtins__",
    "def local():\n    def __builtins__():\n        pass",
    "def local():\n    del __builtins__",
    "class Local:\n    del __builtins__",
    "def local(value):\n    match value:\n        case {**__builtins__}:\n            pass",
    "class Local:\n    match value:\n        case {**__builtins__}:\n            pass",
    "def local():\n    [(__builtins__ := {}) for item in (0,)]",
    "def local():\n    {(__builtins__ := item) for item in (0,)}",
    "def local():\n    {item: (__builtins__ := item) for item in (0,)}",
    "def local():\n    ((__builtins__ := {}) for item in (0,))",
    "def local():\n    [[(__builtins__ := {}) for inner in (0,)] for outer in (0,)]",
    "value = lambda: (__builtins__ := {})",
    "value = lambda: [(__builtins__ := {}) for item in (0,)]",
    "def outer():\n    __builtins__ = {}\n    def inner():\n        nonlocal __builtins__\n        [(__builtins__ := {}) for item in (0,)]",
    "def outer():\n    def inner(arg=[(__builtins__ := {}) for item in (0,)]):\n        pass",
    "def outer():\n    [(__builtins__ := {}) for item in (0,)]\n    def inner():\n        nonlocal __builtins__\n        __builtins__ = {}",
    "def outer():\n    [(__builtins__ := {}) for item in (0,)]\n    def inner():\n        nonlocal __builtins__\n        [(__builtins__ := {}) for item in (0,)]",
    "def outer():\n    def initialize(value=(__builtins__ := {})):\n        pass\n    def inner():\n        nonlocal __builtins__\n        __builtins__ = {}",
    "def outer():\n    def initialize(value=(__builtins__ := {})):\n        pass\n    def inner():\n        nonlocal __builtins__\n        (__builtins__ := {})",
])
def test_ordinary_lexical_builtin_namespace_bindings_stay_local(source):
    tree = ast.parse(source)
    scopes = ScopeIndex(tree)
    assert not any(replaces_builtin_namespace(node, scopes) for node in ast.walk(tree))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("factory", [False, True])
@pytest.mark.parametrize("binding", ["__builtins__ = {}", "import custom_builtins as __builtins__",
                                     "from shim import VALUE as __builtins__"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_replaced_builtin_namespace_withholds_capability_changes(tmp_path, framework, factory, binding, change):
    before = _files(framework, "direct", "clean", factory=factory)
    after = _files(framework, "direct", "clean", "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   "return 2" if change == "changed" else "return 1", factory=factory)
    for files in (before, after):
        files["custom_builtins.py"] = "str = None\n"
        files["shim.py"] += "VALUE = {}\n"
    path = "factory.py" if factory else "app.py"
    after[path] = binding + "\n" + after[path]
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert not result["base"]["coverage_gaps"]
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert any("builtin namespace" in gap["reason"] for gap in result["head"]["coverage_gaps"])


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("local", [
    "def helper():\n    __builtins__ = {}\n", "class Helper:\n    __builtins__ = {}\n",
    "def helper():\n    [(__builtins__ := {}) for item in (0,)]\n",
    "def helper():\n    [(__builtins__ := {}) for item in (0,)]\n    def inner():\n        nonlocal __builtins__\n        __builtins__ = {}\n",
    "def helper():\n    def initialize(value=(__builtins__ := {})):\n        pass\n    def inner():\n        nonlocal __builtins__\n        __builtins__ = {}\n",
])
def test_local_namespace_data_does_not_hide_a_real_capability_change(tmp_path, framework, local):
    before = _files(framework, "direct", "clean")
    after = _files(framework, "direct", "clean", "[read, write]")
    for files in (before, after):
        files["factory.py"] += local
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", ".")
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("a" if framework == "sdk" else "Built", "write", "added")
    ]
