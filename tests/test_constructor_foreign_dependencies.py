"""Foreign dependency providers and their actual callers."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_constructor_dependency_ownership import (
    _comparison,
    _files,
    _unread,
    _versions,
)


@pytest.mark.parametrize("framework,namespace,symbol,slot", [
    ("sdk", "agents.agent_output", "BaseModel", "model_json_schema"),
    ("sdk", "agents.items", "BaseModel", "model_json_schema"),
    ("adk", "google.adk.tools.long_running_tool", "FunctionTool", "__init__"),
    ("sdk", "google.adk.tools.base_tool", "BaseModel", "model_json_schema"),
    ("adk", "agents.agent_output", "BaseModel", "__init__"),
    ("adk", "agents.tool", "inspect", "signature"),
    ("sdk", "google.adk.tools.function_tool", "get_type_hints", "__code__"),
])
@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_framework_and_cross_family_carriers_keep_shared_dependencies_owned(tmp_path, framework, namespace, symbol, slot, bridge, change):
    before, after = _versions(framework, "direct", change)
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    imported = "from bridge import Carrier\n" if bridge else f"from {namespace} import {symbol} as Carrier\n"
    after["shim.py"] = f"def replacement(reader):\n    reader.{slot} = changed\ndef changed(*args, **kwargs):\n    return None\n"
    after["app.py"] = imported + "from shim import replacement\nreplacement(Carrier)\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework,namespace,symbol,slot", [
    ("adk", "agents", "AgentBase", "__setattr__"),
    ("adk", "agents.agent", "AgentBase", "__new__"),
    ("sdk", "google.adk.agents", "BaseAgent", "__init__"),
    ("sdk", "google.adk.agents.base_agent", "BaseAgent", "__new__"),
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_exact_independent_foreign_class_slot_writes_preserve_changes(tmp_path, framework, namespace, symbol, slot, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\nCarrier.{slot} = replacement\n" + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["head"]["coverage_gaps"]
    assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change



@pytest.mark.parametrize("framework,namespace,symbol,slot", [
    ("adk", "agents.agent", "AgentBase", "__new__"),
    ("sdk", "google.adk.agents.base_agent", "BaseAgent", "__init__"),
])
@pytest.mark.parametrize("usage", [
    "del Carrier.SLOT\n",
    "Carrier.SLOT += replacement\n",
    "Carrier.SLOT.__code__ = replacement\n",
    "saved = Carrier.SLOT\n",
    "from foreign_consumer import consume\nconsume(Carrier)\n",
    "saved = Carrier\nsaved.SLOT = replacement\n",
    "Carrier.SLOT = Carrier.other = replacement\n",
    "metadata = Carrier.__dict__\n",
    "Carrier()\n",
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_independent_foreign_slot_exception_never_grants_other_class_uses(tmp_path, framework, namespace, symbol, slot, usage, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\n" + usage.replace("SLOT", slot) + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework,namespace,symbol,slot,provider", [
    ("adk", "agents.agent", "AgentBase", "__new__", "griffe.py"),
    ("adk", "agents.agent", "AgentBase", "__new__", "griffe/__init__.py"),
    ("sdk", "google.adk.agents.base_agent", "BaseAgent", "__init__", "google/genai/__init__.py"),
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_foreign_slot_write_requires_implicit_foreign_dependency_providers(tmp_path, framework, namespace, symbol, slot, provider, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\nCarrier.{slot} = replacement\n" + after["app.py"]
    after[provider] = "# A local provider is not the trusted foreign dependency.\n"
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("scope", ["conditional", "local", "mixed", "rebound"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_foreign_slot_exception_requires_a_stable_module_import(tmp_path, scope, change):
    before, after = _versions("adk", "direct", change)
    imported = "from agents.agent import AgentBase as Carrier\n"
    patch = "Carrier.__new__ = replacement\n"
    code = {
        "conditional": "if enabled:\n    " + imported + patch,
        "local": "def patch():\n    " + imported + "    " + patch + "patch()\n",
        "mixed": imported + "if enabled:\n    from google.adk.agents import BaseAgent as Carrier\n" + patch,
        "rebound": imported + "Carrier = replacement\n" + patch,
    }[scope]
    after["app.py"] = "from shim import replacement\n" + code + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("imported", ["google", "google.cloud"])
@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_foreign_class_projection_from_an_ancestor_import_is_unread(tmp_path, imported, provider, change):
    before, after = _versions("sdk", "direct", change)
    after["app.py"] = f"import {imported}\nfrom shim import replacement\ngoogle.adk.agents.BaseAgent.__init__ = replacement\n" + after["app.py"]
    if provider:
        after["google/genai/__init__.py"] = "# The projected framework's implicit dependency is local.\n"
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("imported,receiver", [("import google.adk", "google.adk"), ("from google import adk", "adk")])
@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_explicit_foreign_namespace_import_proves_its_implicit_providers(tmp_path, imported, receiver, provider, change):
    before, after = _versions("sdk", "direct", change)
    after["app.py"] = imported + f"\nfrom shim import replacement\n{receiver}.agents.BaseAgent.__init__ = replacement\n" + after["app.py"]
    if provider:
        after["google/genai/__init__.py"] = "# The foreign dependency is not external.\n"
    result = _comparison(tmp_path, before, after)
    if provider:
        _unread(result)
    else:
        assert result["comparison_status"] == "compared"
        assert not result["head"]["coverage_gaps"]
        assert len(result["rows"]) == 1 and result["rows"][0]["change"] == change



@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("route", ["attribute", "opaque", "reexport", "namespace"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_private_class_getters_never_grant_receiving_authority(tmp_path, framework, route, change):
    before, after = _versions(framework, "direct", change)
    code = {
        "attribute": "from pydantic._internal._import_utils import import_cached_base_model as load\nload().__init__ = replacement\n",
        "opaque": "from pydantic._internal._import_utils import import_cached_base_model as load\nmodel = load()\nreplacement(model)\n",
        "reexport": "from pydantic._internal._model_construction import import_cached_base_model as load\nload().__init__ = replacement\n",
        "namespace": "import pydantic._internal._import_utils as internals\ninternals.import_cached_base_model().__init__ = replacement\n",
    }[route]
    after["app.py"] = "from shim import replacement\n" + code + after["app.py"]
    _unread(_comparison(tmp_path, before, after))



@pytest.mark.parametrize("framework,foreign,provider", [
    (framework, False, provider) for framework in ("sdk", "adk")
    for provider in ("pydantic.py", "typing.py", "typing_extensions.py", "_collections_abc.py")
] + [("sdk", False, "griffe.py"), ("adk", False, "google/genai/__init__.py"),
     ("adk", False, "mcp.py"), ("adk", False, "json.py"), ("adk", False, "yaml.py"),
     ("sdk", True, "json.py"), ("sdk", True, "yaml.py"),
     ("adk", True, "griffe.py"), ("sdk", True, "google/genai/__init__.py")])
@pytest.mark.parametrize("context", ["builder", "caller", "imported"])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_implicit_providers_are_proved_in_every_caller_and_import_context(tmp_path, framework, foreign, provider, context, change):
    # ADK wrappers returned through helpers have a separate named limit. Plain
    # functions establish the receiving Agent before we introduce a provider.
    layout = "direct" if context == "imported" else context
    before = _files(framework, layout, "clean", factory=False)
    after = _files(framework, layout, "clean",
                   "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   "return 2" if change == "changed" else "return 1", factory=False)
    for files in (before, after):
        files["apps/main.py"] = files.pop("app.py")
    if foreign:
        namespace, symbol, slot = (("agents.agent", "AgentBase", "__new__") if framework == "adk"
                                   else ("google.adk.agents.base_agent", "BaseAgent", "__init__"))
        after["apps/main.py"] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\nCarrier.{slot} = replacement\n" + after["apps/main.py"]
    if context == "imported":
        for files in (before, after):
            files["helpers/reader.py"] = "def separate():\n    return None\n"
            files["apps/main.py"] = "from helpers.reader import separate\n" + files["apps/main.py"]
        after[f"helpers/{provider}"] = "# A provider alongside an imported helper is unread.\n"
    else:
        after[f"apps/{provider}"] = "# A provider alongside the actual caller is unread.\n"
    _unread(_comparison(tmp_path, before, after))
