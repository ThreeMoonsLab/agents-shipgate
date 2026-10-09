"""Typing and ABC carriers preserve their constructor boundaries."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_constructor_dependency_ownership import (
    STDLIB_ABC_CARRIERS,
    _commit,
    _compare,
    _comparison,
    _files,
    _git,
    _scoped_versions,
    _unread,
    _versions,
)


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("layout", ["builder", "caller"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_external_dependencies_preserve_nested_caller_controls(tmp_path, framework, layout, change):
    before = _files(framework, layout, "clean", factory=False)
    after = _files(framework, layout, "clean",
                   "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   "return 2" if change == "changed" else "return 1", factory=False)
    for files in (before, after):
        files["apps/main.py"] = files.pop("app.py")
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("namespace,symbol,patch", [
    ("typing", "Literal", "reader._getitem = changed"),
    ("typing", "_LiteralGenericAlias", "reader.__init__.__code__ = changed.__code__"),
    ("_collections_abc", "ABCMeta", "reader.__call__ = changed"),
])
@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_shared_typing_helpers_and_abc_aliases_cannot_escape_ownership(tmp_path, framework, namespace, symbol, patch, bridge, change):
    before, after = _versions(framework, "direct", change)
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    after["shim.py"] = f"def replacement(reader):\n    {patch}\ndef changed(*args, **kwargs):\n    return int\n"
    imported = "from bridge import Carrier\n" if bridge else f"from {namespace} import {symbol} as Carrier\n"
    after["app.py"] = imported + "from shim import replacement\nreplacement(Carrier)\n" + after["app.py"]
    # The SDK annotations exercise the actual eager schema reader while the
    # ADK case owns a concrete FunctionTool handle and its shared metaclass.
    if framework == "sdk" and symbol in {"Literal", "_LiteralGenericAlias"}:
        annotation = "Literal['read', 'write']" if symbol == "Literal" else "List[float]"
        for files in (before, after):
            files["tools.py"] = "from typing import Literal, List\n" + files["tools.py"].replace("value: int", f"value: {annotation}")
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("annotation", ["Any", "List[float]", "Literal['read', 'write']", "Optional[int]", "Annotated[int, 'value']", "Callable[[int], str]"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_canonical_public_typing_values_keep_their_annotation_role(tmp_path, framework, annotation, change):
    before, after = _versions(framework, "direct", change)
    for files in (before, after):
        files["tools.py"] = "from typing import Any, List, Literal, Optional, Annotated, Callable\n" + files["tools.py"].replace("value: int", f"value: {annotation}")
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("usage", [
    "saved = Any\n", "from foreign_consumer import consume\nconsume(Any)\n",
    "metadata = Any.__dict__\n", "Any()\n", "saved = Literal['read', 'write']\n",
    "from foreign_consumer import consume\ndef separate(value: Any):\n    return None\nconsume(separate)\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_typing_annotation_exception_never_authorizes_other_value_roles(tmp_path, framework, usage, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = "from typing import Any, Literal\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("route", ["direct", "bridge", "transitive", "namespace", "conditional", "star", "ancestor", "unused"])
def test_above_scope_dependencies_retain_a_named_constructor_limit(tmp_path, framework, change, route):
    before, after = _scoped_versions(framework, change)
    carrier = "from pydantic import BaseModel\ndef replacement(reader):\n    reader.model_json_schema = changed\ndef changed(*args, **kwargs):\n    return None\nreplacement(BaseModel)\n"
    if route == "direct":
        after["pkg/__init__.py"] = carrier
    elif route in {"bridge", "transitive", "namespace"}:
        after["pkg/carrier.py"] = carrier
        after["pkg/bridge.py"] = "from .carrier import replacement\n"
        after["pkg/__init__.py"] = ("from .carrier import replacement\n" if route == "bridge" else
                                    "from .bridge import replacement\n" if route == "transitive" else
                                    "from . import carrier\n")
    else:
        after["pkg/__init__.py"] = {
            "conditional": "if enabled:\n    from typing import get_origin\n",
            "star": "from typing import *\n",
            "ancestor": "import google.cloud\n",
            "unused": "from _collections_abc import ABCMeta\n",
        }[route]
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    _unread(result)
    assert any("above the read scope" in gap["reason"] for gap in result["head"]["coverage_gaps"])



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("initialization", ["", "value = 1\n", "import google.cloud as cloud\n", "from .helper import separate\n"])
def test_plain_above_scope_initialization_preserves_changes(tmp_path, framework, initialization):
    before, after = _scoped_versions(framework, "added")
    after["pkg/__init__.py"] = initialization
    after["pkg/helper.py"] = "def separate():\n    return None\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    base, head = _commit(tmp_path, before), _commit(tmp_path, after)
    result = _compare(tmp_path, base, head, "--scope", "pkg/app")
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == "added"



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("namespace,symbol", [("abc", "abstractmethod"), ("_collections_abc", "Mapping"), ("_collections_abc", "Iterable")])
@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_abc_function_globals_and_class_metaclass_carriers_remain_owned(tmp_path, framework, namespace, symbol, bridge, change):
    before, after = _versions(framework, "direct", change)
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    imported = "from bridge import Carrier\n" if bridge else f"from {namespace} import {symbol} as Carrier\n"
    after["app.py"] = imported + "from foreign_consumer import consume\nconsume(Carrier)\n" + after["app.py"]
    # abstractmethod carries abc's globals; Mapping and Iterable are concrete
    # ABCMeta instances. An unread consumer cannot establish their ownership.
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("namespace,symbol,member", STDLIB_ABC_CARRIERS)
@pytest.mark.parametrize("bridge", [False, True])
def test_primary_stdlib_abc_reexports_never_prove_unchanged_construction(tmp_path, framework, namespace, symbol, member, bridge):
    before, after = _versions(framework, "direct", "added")
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    imported = "from bridge import Carrier\n" if bridge else f"from {namespace} import {symbol} as Carrier\n"
    usage = f"Carrier.{member}.__new__ = replacement\n" if member else "from foreign_consumer import consume\nconsume(Carrier)\n"
    after["app.py"] = imported + "from shim import replacement\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["changed", "removed"])
@pytest.mark.parametrize("namespace,symbol", [("collections", "_collections_abc"), ("contextlib", "abc"), ("os", "abc")])
def test_primary_stdlib_reexports_also_withhold_changes_and_removals(tmp_path, framework, change, namespace, symbol):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\nCarrier.ABCMeta.__call__ = replacement\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("namespace,symbol", [("io", "IOBase"), ("os", "PathLike"), ("weakref", "WeakKeyDictionary"),
                                            ("contextlib", "suppress"), ("contextlib", "ExitStack"),
                                            ("selectors", "DefaultSelector"), ("collections", "UserDict")])
@pytest.mark.parametrize("usage", ["from foreign_consumer import consume\nconsume(Carrier)\n", "Carrier()\n"])
def test_actual_inherited_abc_classes_gain_no_receiving_or_getter_authority(tmp_path, framework, namespace, symbol, usage):
    before, after = _versions(framework, "direct", "added")
    after["app.py"] = f"from {namespace} import {symbol} as Carrier\n" + usage + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("spelling", ["module", "from", "bridge", "local"])
def test_module_type_class_gains_no_receiving_or_getter_authority(tmp_path, framework, change, spelling):
    before, after = _versions(framework, "direct", change)
    uses = {
        "module": "import types\ntypes.ModuleType('scratch')\n",
        "from": "from types import ModuleType as Carrier\nCarrier('scratch')\n",
        "bridge": "from bridge import Carrier\nCarrier('scratch')\n",
        "local": "def touch():\n    from types import ModuleType as Carrier\n    Carrier('scratch')\ntouch()\n",
    }
    after["bridge.py"] = "from types import ModuleType as Carrier\n"
    after["app.py"] = uses[spelling] + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_module_type_dependency_leaves_ordinary_types_calls_readable(tmp_path, framework, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = "import types\ntypes.SimpleNamespace(value=1)\n" + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("provider", ["types.py", "types/__init__.py"])
@pytest.mark.parametrize("bridge", [False, True])
def test_module_type_identity_requires_the_actual_import_provider(tmp_path, framework, provider, bridge):
    before, after = _versions(framework, "direct", "added")
    after[provider] = "class ModuleType:\n    pass\n"
    after["bridge.py"] = "from types import ModuleType as Carrier\n"
    imported = "from bridge import Carrier\n" if bridge else "from types import ModuleType as Carrier\n"
    after["app.py"] = imported + "Carrier()\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))
