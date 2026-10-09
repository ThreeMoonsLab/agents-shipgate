"""Only an actual canonical false guard can remove executable import edges."""

import ast

import pytest

from agents_shipgate.inputs.python_imports import ImportResolver, _type_checking_only
from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_imported_tool_bindings import _commit, _compare, _git


@pytest.mark.parametrize("source,dead", [
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import patcher\n", True),
    ("import typing as t\nif t.TYPE_CHECKING:\n    import patcher\n", True),
    ("from typing_extensions import TYPE_CHECKING as TC\nif TC:\n    import patcher\n", True),
    ("def build():\n    from typing import TYPE_CHECKING\n    if TYPE_CHECKING:\n        import patcher\n", True),
    ("class Owner:\n    from typing import TYPE_CHECKING\n    if TYPE_CHECKING:\n        import patcher\n", True),
    ("from typing import TYPE_CHECKING\ndef build(TYPE_CHECKING):\n    if TYPE_CHECKING:\n        import patcher\n", False),
    ("from typing import TYPE_CHECKING\ndef build():\n    TYPE_CHECKING = True\n    if TYPE_CHECKING:\n        import patcher\n", False),
    ("from typing import TYPE_CHECKING\nclass Owner:\n    TYPE_CHECKING = True\n    if TYPE_CHECKING:\n        import patcher\n", False),
    ("from typing import TYPE_CHECKING\ndef outer():\n    TYPE_CHECKING = True\n    def inner():\n        if TYPE_CHECKING:\n            import patcher\n", False),
    ("from typing import TYPE_CHECKING\ndef outer():\n    TYPE_CHECKING = True\n    def inner():\n        nonlocal TYPE_CHECKING\n        if TYPE_CHECKING:\n            import patcher\n", False),
    ("from typing import TYPE_CHECKING\ndef build():\n    from shim import TYPE_CHECKING\n    if TYPE_CHECKING:\n        import patcher\n", False),
    ("from typing import TYPE_CHECKING\ndef build():\n    if choice:\n        from typing import TYPE_CHECKING\n    if TYPE_CHECKING:\n        import patcher\n", False),
    ("from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nif TYPE_CHECKING:\n    import patcher\n", False),
    ("import typing as t\nt.TYPE_CHECKING = True\nif t.TYPE_CHECKING:\n    import patcher\n", False),
    ("from typing import TYPE_CHECKING\nif not TYPE_CHECKING:\n    import patcher\n", False),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING is False:\n    import patcher\n", False),
    ("from typing import TYPE_CHECKING\nfrom shim import *\nif TYPE_CHECKING:\n    import patcher\n", False),
    ("if TYPE_CHECKING:\n    import patcher\nfrom typing import TYPE_CHECKING\n", False),
    ("from typing import TYPE_CHECKING\ndef build():\n    if TYPE_CHECKING:\n        TYPE_CHECKING = True\n        import patcher\n", False),
    ("import typing as t\nif t.TYPE_CHECKING:\n    t.TYPE_CHECKING = True\n    import patcher\n", False),
])
def test_false_guard_uses_the_actual_lexical_binding(tmp_path, source, dead):
    path = tmp_path / "agent.py"
    path.write_text(source)
    resolver = ImportResolver(tmp_path)
    module = resolver.module(path)
    [patcher] = [node for node in ast.walk(module.tree) if isinstance(node, ast.Import)
                 and any(alias.name == "patcher" for alias in node.names)]
    assert (id(patcher) in _type_checking_only(module, resolver)) is dead


@pytest.mark.parametrize("provider", ["typing.py", "typing/__init__.py", "typing_extensions.py"])
def test_a_local_typing_provider_does_not_prove_the_canonical_flag(tmp_path, provider):
    target = tmp_path / provider
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("TYPE_CHECKING = True\n")
    path = tmp_path / "agent.py"
    path.write_text(f"from {provider.split('/')[0].removesuffix('.py')} import TYPE_CHECKING\nif TYPE_CHECKING:\n    import patcher\n")
    resolver = ImportResolver(tmp_path)
    module = resolver.module(path)
    assert not _type_checking_only(module, resolver)


def _files(family, guard, tools):
    constructor = "Agent" if family == "sdk" else "LlmAgent"
    header = ("from agents import Agent, function_tool\n" if family == "sdk"
              else "from google.adk.agents import LlmAgent\n")
    decorator = "@function_tool\n" if family == "sdk" else ""
    return {
        "app/agent.py": header + "from typing import TYPE_CHECKING\n" + decorator
        + "def read(value: str) -> str:\n    return value\n" + decorator
        + "def write(value: str) -> str:\n    return value\n" + guard
        + f"    return {constructor}(name='Built', tools={tools})\nagent = build(True)\n",
        "patcher.py": "from abc import ABCMeta\ndef replacement(*args, **kwargs):\n    return None\nABCMeta.__call__ = replacement\n",
    }


@pytest.mark.parametrize("family", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["parameter", "local", "inverted", "identity-false"])
def test_an_executable_shadowed_guard_does_not_hide_an_outside_constructor_patch(tmp_path, family, route):
    guard = {
        "parameter": "def build(TYPE_CHECKING):\n    if TYPE_CHECKING:\n        import patcher\n",
        "local": "def build(unused):\n    TYPE_CHECKING = True\n    if TYPE_CHECKING:\n        import patcher\n",
        "inverted": "def build(unused):\n    if not TYPE_CHECKING:\n        import patcher\n",
        "identity-false": "def build(unused):\n    if TYPE_CHECKING is False:\n        import patcher\n",
    }[route]
    before = _files(family, "def build(unused):\n", "[read]")
    after = _files(family, guard, "[read, write]")
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert not result["base"]["coverage_gaps"]
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])


@pytest.mark.parametrize("family", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["module", "local", "module-alias", "extension"])
def test_the_exact_canonical_false_boolean_role_preserves_construction(tmp_path, family, route):
    guard = {
        "module": "def build(unused):\n    if TYPE_CHECKING:\n        import patcher\n",
        "local": "def build(unused):\n    from typing import TYPE_CHECKING as TC\n    if TC:\n        import patcher\n",
        "module-alias": "def build(unused):\n    import typing as t\n    if t.TYPE_CHECKING:\n        import patcher\n",
        "extension": "def build(unused):\n    from typing_extensions import TYPE_CHECKING as TC\n    if TC:\n        import patcher\n",
    }[route]
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _files(family, guard, "[read]"))
    head = _commit(tmp_path, _files(family, guard, "[read, write]"))
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared"
    assert not result["head"]["coverage_gaps"]
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("write", "added")]


_MUTATIONS = {
    "globals": "globals()['Agent'] = None\n",
    "constructor": "{constructor}.__init__ = None\n",
    "module-table": "import sys\nsys.modules['{namespace}'] = None\n",
    "aliased-table": "from sys import modules as table\ntable['{namespace}'] = None\n",
    "own-table": "import sys\nsys.modules[__name__].Agent = None\n",
    "search-path": "__path__.append('elsewhere')\n",
}


def _mutation_files(family, location, mutation, route):
    files = _files(family, "def build(unused):\n", "[read, write]")
    constructor = "Agent" if family == "sdk" else "LlmAgent"
    namespace = "agents" if family == "sdk" else "google.adk.agents"
    body = _MUTATIONS[mutation].format(constructor=constructor, namespace=namespace)
    # Above-scope and imported modules acquire the framework name only in
    # the guarded body; an unconditional protected import is its own limit.
    if location in {"outside", "enclosing"} and mutation == "constructor":
        body = f"from {namespace} import {constructor}\n" + body
    guard = {
        "dead": "if TYPE_CHECKING:\n",
        "live": "if not TYPE_CHECKING:\n",
        "shadowed": "TYPE_CHECKING = True\nif TYPE_CHECKING:\n",
    }[route]
    fragment = "from typing import TYPE_CHECKING\n" + guard + "".join(
        "    " + line + "\n" for line in body.splitlines()
    )
    if location == "agent":
        fragment = fragment.removeprefix("from typing import TYPE_CHECKING\n")
        files["app/agent.py"] = files["app/agent.py"].replace("def build(", fragment + "def build(")
    elif location == "tool":
        source = files["app/agent.py"]
        header, functions = source.split("from typing import TYPE_CHECKING\n", 1)
        functions = functions.split("def build(", 1)[0]
        files["app/tools.py"] = header + functions + fragment
        files["app/agent.py"] = source.replace(functions, "from tools import read, write\n")
    elif location == "outside":
        files["patcher.py"] = fragment
        files["__init__.py"] = "import patcher\n"
    else:
        files["__init__.py"] = fragment
    return files


@pytest.mark.parametrize("family", ["sdk", "adk"])
@pytest.mark.parametrize("location", ["agent", "tool", "outside", "enclosing"])
@pytest.mark.parametrize("mutation", _MUTATIONS)
@pytest.mark.parametrize("route", ["dead", "live", "shadowed"])
def test_only_proved_false_bodies_leave_auxiliary_ownership_maps(tmp_path, family, location, mutation, route):
    before = _files(family, "def build(unused):\n", "[read]")
    after = _mutation_files(family, location, mutation, route)
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert not result["base"]["coverage_gaps"]
    if route == "dead":
        assert result["comparison_status"] == "compared"
        assert not result["head"]["coverage_gaps"]
        assert [(row["tool"], row["change"]) for row in result["rows"]] == [("write", "added")]
    else:
        assert result["comparison_status"] == "partial"
        assert result["head"]["coverage_gaps"]
        assert all(row["change"] == "not_established" for row in result["rows"])


@pytest.mark.parametrize("mutation", _MUTATIONS)
@pytest.mark.parametrize("order", ["dead-first", "live-first"])
def test_recomputed_patch_map_keeps_a_live_store_of_the_same_key(tmp_path, mutation, order):
    body = _MUTATIONS[mutation].format(constructor="Agent", namespace="agents")
    dead = "if TYPE_CHECKING:\n" + "".join("    " + line + "\n" for line in body.splitlines())
    source = "from typing import TYPE_CHECKING\nfrom agents import Agent\n"
    source += dead + body if order == "dead-first" else body + dead
    path = tmp_path / "agent.py"
    path.write_text(source)
    resolver = ImportResolver(tmp_path)
    module = resolver.module(path)
    live_lines = {node.lineno for node in ast.walk(module.tree)
                  if id(node) not in _type_checking_only(module, resolver) and hasattr(node, "lineno")}
    patches = resolver._runtime_attribute_patches(module)
    assert patches
    assert all(line in live_lines for line in patches.values())
    assert resolver._runtime_attribute_patches(module) == patches
