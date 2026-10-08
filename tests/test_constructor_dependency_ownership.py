"""Inherited hooks and eager tool metadata cannot establish unchanged wiring."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_framework_constructor_bindings import _files
from tests.test_imported_tool_bindings import _commit, _compare, _git

DEPENDENCIES = [
    ("adk", "google.adk.agents", "BaseAgent", "__init__"),
    ("adk", "google.adk.agents.base_agent", "BaseAgent", "__new__"),
    ("adk", "google.adk.agents.llm_agent", "BaseAgent", "__init__"),
    ("adk", "google.adk.workflow", "BaseNode", "__init__"),
    ("adk", "google.adk.workflow._base_node", "BaseNode", "__new__"),
    ("adk", "google.adk.agents.base_agent", "BaseNode", "__init__"),
    ("adk", "google.adk.tools", "BaseTool", "__init__"),
    ("adk", "google.adk.tools.base_tool", "BaseTool", "__new__"),
    ("adk", "google.adk.tools.function_tool", "BaseTool", "__init__"),
    ("adk", "google.adk.agents.llm_agent", "BaseTool", "__new__"),
    ("adk", "google.adk.agents.llm_agent", "FunctionTool", "__init__"),
    ("adk", "google.adk.tools.long_running_tool", "LongRunningFunctionTool", "__init__"),
    ("adk", "google.adk.agents.base_agent", "BaseModel", "__init__"),
    ("adk", "google.adk.agents.llm_agent", "BaseModel", "__new__"),
    ("adk", "google.adk.workflow._base_node", "BaseModel", "__init__"),
    ("adk", "google.adk.tools.base_tool", "BaseModel", "__new__"),
    ("adk", "google.adk.tools.base_tool", "ABC", "__new__"),
    ("adk", "abc", "ABC", "__new__"),
    ("sdk", "agents", "AgentBase", "__setattr__"),
    ("sdk", "agents.agent", "AgentBase", "__new__"),
    ("sdk", "agents", "FunctionTool", "__post_init__"),
    ("sdk", "agents.tool", "FunctionTool", "__init__"),
    ("sdk", "typing", "Generic", "__new__"),
    ("sdk", "agents.agent", "Generic", "__new__"),
    ("sdk", "agents.tool", "Generic", "__new__"),
    ("sdk", "agents.agent", "BaseModel", "model_json_schema"),
    ("sdk", "agents.tool", "BaseModel", "model_json_schema"),
    ("sdk", "agents.function_schema", "BaseModel", "model_json_schema"),
    *[(framework, namespace, symbol, hook)
      for framework in ("sdk", "adk")
      for namespace, symbol, hook in (
          ("pydantic", "BaseModel", "__init__"),
          ("pydantic.main", "BaseModel", "model_json_schema"),
          ("pydantic._internal._model_construction", "ModelMetaclass", "__new__"),
          ("pydantic._internal._model_construction", "ABCMeta", "__call__"),
          ("abc", "ABCMeta", "__call__"),
      )],
]


def _versions(framework, layout, change):
    before = _files(framework, layout, "clean", factory=False)
    after = _files(framework, layout, "clean",
                   "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   "return 2" if change == "changed" else "return 1", factory=False)
    for files in (before, after):
        if framework == "adk":
            # BaseTool hooks matter here because these are actual wrappers,
            # rather than plain functions awaiting a later runtime conversion.
            files["app.py"] = "from google.adk.tools import FunctionTool\n" + files["app.py"]
            files["app.py"] = files["app.py"].replace("[read, write]", "[FunctionTool(read), FunctionTool(write)]")
            files["app.py"] = files["app.py"].replace("[read]", "[FunctionTool(read)]")
        files["tools.py"] = files["tools.py"].replace("def read():", "def read(value: int):").replace("def write():", "def write(value: int):")
    return before, after


def _comparison(root, before, after):
    _git(root, "init", "-q", "-b", "main")
    base, head = _commit(root, before), _commit(root, after)
    return _compare(root, base, head, "--scope", ".")


def _unread(result):
    assert not result["base"]["coverage_gaps"]
    assert result["comparison_status"] == "partial"
    assert result["head"]["coverage_gaps"]
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert any("constructor" in gap["reason"] for gap in result["head"]["coverage_gaps"])


@pytest.mark.parametrize("framework,namespace,symbol,hook", DEPENDENCIES)
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_inherited_and_schema_dependency_changes_are_unread(tmp_path, framework, namespace, symbol, hook, change):
    layout = "direct" if framework == "adk" else {"added": "direct", "changed": "builder", "removed": "caller"}[change]
    before, after = _versions(framework, layout, change)
    source = "builders.py" if layout == "builder" else "app.py"
    after[source] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\nCarrier.{hook} = replacement\n" + after[source]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework,namespace,symbol", [
    ("adk", "google.adk.agents.base_agent", "BaseNode"),
    ("adk", "google.adk.agents.llm_agent", "abc"),
    ("adk", "google.adk.tools.function_tool", "pydantic"),
    ("adk", "pydantic.main", "_model_construction"),
    ("sdk", "agents.agent", "AgentBase"),
    ("sdk", "agents.tool", "typing"),
    ("sdk", "agents.function_schema", "create_model"),
    ("sdk", "agents.tool", "function_schema"),
    ("sdk", "agents.run_internal.agent_tool_configuration", "assign_agent_tools"),
])
@pytest.mark.parametrize("route", ["opaque", "local_reexport", "namespace_reexport", "saved_rebound"])
def test_dependency_retention_through_real_reexports_is_unread(tmp_path, framework, namespace, symbol, route):
    before, after = _versions(framework, "direct", "added")
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    patch = {
        "opaque": f"from {namespace} import {symbol} as Carrier\nreplacement(Carrier)\n",
        "local_reexport": "from bridge import Carrier\nreplacement(Carrier)\n",
        "namespace_reexport": "import bridge\nreplacement(bridge)\n",
        "saved_rebound": f"from {namespace} import {symbol} as Carrier\nSaved = Carrier\nCarrier = None\nreplacement(Saved)\n",
    }[route]
    after["app.py"] = "from shim import replacement\n" + patch + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework,patch", [
    ("sdk", "import agents.run_internal.agent_tool_configuration as config\nconfig.assign_agent_tools = replacement\n"),
    ("sdk", "import agents.function_schema as schema\nschema.create_model = replacement\n"),
    ("sdk", "import pydantic\npydantic.create_model = replacement\n"),
    ("sdk", "import typing\ndel typing.Generic.__class_getitem__\n"),
    ("adk", "import google.adk.agents.llm_agent as llm\nllm.BaseAgent.__init__ = replacement\n"),
    ("adk", "import pydantic.main as models\nmodels._model_construction.ModelMetaclass.__call__ = replacement\n"),
    ("adk", "import abc\nabc.ABC.__new__ = replacement\n"),
    ("adk", "import google.adk.tools.function_tool as wrappers\nwrappers.pydantic.BaseModel.__init__ = replacement\n"),
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_dependency_namespace_patches_cannot_establish_changes(tmp_path, framework, patch, change):
    before, after = _versions(framework, "direct", change)
    after["patcher.py"] = "from shim import replacement\n" + patch
    after["bridge.py"] = "import patcher\n"
    after["app.py"] = "import bridge\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework,module", [("adk", "pydantic.main"), ("adk", "abc"),
                                               ("sdk", "pydantic._internal._model_construction"), ("sdk", "typing")])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_dependency_module_table_replacement_is_unread(tmp_path, framework, module, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = f"import sys\nsys.modules['{module}'] = None\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("namespace,symbol", [("google.adk.agents", "BaseAgent"),
                                             ("google.adk.workflow", "BaseNode"), ("pydantic", "BaseModel")])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("layout", ["builder", "caller"])
def test_inherited_agent_dependencies_are_checked_in_builder_contexts(tmp_path, namespace, symbol, change, layout):
    before = _files("adk", layout, "clean", factory=False)
    after = _files("adk", layout, "clean",
                   "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   "return 2" if change == "changed" else "return 1", factory=False)
    source = "builders.py" if layout == "builder" else "app.py"
    after[source] = f"from {namespace} import {symbol} as Carrier\nfrom shim import replacement\nCarrier.__init__ = replacement\n" + after[source]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework,provider", [("sdk", "pydantic.py"), ("sdk", "pydantic/__init__.py"),
                                                ("sdk", "typing.py"), ("adk", "pydantic.py"),
                                                ("adk", "pydantic/__init__.py"), ("adk", "typing.py"),
                                                ("adk", "google/genai/__init__.py"), ("sdk", "griffe.py"),
                                                *[(framework, provider) for framework in ("sdk", "adk")
                                                  for provider in ("typing_extensions.py", "typing_extensions/__init__.py")]])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_local_dependency_provider_is_unread_without_an_application_import(tmp_path, framework, provider, change):
    before, after = _versions(framework, "direct", change)
    after[provider] = "class BaseModel:\n    pass\n"
    result = _comparison(tmp_path, before, after)
    _unread(result)
    assert any("dependency" in gap["reason"] and "supplies" in gap["reason"]
               for gap in result["head"]["coverage_gaps"])


@pytest.mark.parametrize("framework,ordinary", [
    ("adk", "from pydantic import Field\ndata = Field(default=None)\n"),
    ("adk", "from pydantic.fields import Field\ndata = Field(default=None)\n"),
    ("adk", "from typing_extensions import Any\n"),
    ("sdk", "from typing_extensions import Any\n"),
    ("adk", "from google.adk.workflow import BaseNode\n"),
    ("sdk", "from agents.agent import AgentBase\n"),
    ("sdk", "from typing import Generic\n"),
    ("sdk", "from google.adk.agents import BaseAgent\nBaseAgent.__init__ = replacement\n"),
    ("adk", "from agents.agent import AgentBase\nAgentBase.__setattr__ = replacement\n"),
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
def test_unrelated_reads_and_other_framework_patches_preserve_changes(tmp_path, framework, ordinary, change):
    before, after = _versions(framework, "direct", change)
    after["app.py"] = "from shim import replacement\n" + ordinary + after["app.py"]
    result = _comparison(tmp_path, before, after)
    assert result["comparison_status"] == "compared"
    assert not result["base"]["coverage_gaps"] and not result["head"]["coverage_gaps"]
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("a" if framework == "sdk" else "Built", "read" if change == "changed" else "write" if change == "added" else "read", change)
    ]


@pytest.mark.parametrize("framework,namespace,symbol", [
    ("sdk", "agents.tool", "inspect"),
    ("sdk", "agents.function_schema", "inspect"),
    ("sdk", "agents.agent", "inspect"),
    ("adk", "google.adk.tools.function_tool", "inspect"),
    ("sdk", "typing", "get_type_hints"),
    ("adk", "typing", "get_type_hints"),
    ("sdk", "agents.function_schema", "get_type_hints"),
    ("adk", "google.adk.tools.function_tool", "get_type_hints"),
    ("sdk", "pydantic", "Field"),
    ("sdk", "pydantic.fields", "Field"),
    ("sdk", "agents.function_schema", "Field"),
    ("adk", "google.adk.utils.context_utils", "find_context_parameter"),
    ("adk", "google.adk.tools.function_tool", "find_context_parameter"),
    ("adk", "google.adk.tools._automatic_function_calling_util", "build_function_declaration"),
    ("adk", "google.adk.tools.function_tool", "build_function_declaration"),
    ("adk", "google.genai.types", "FunctionDeclaration"),
    ("sdk", "griffe", "Docstring"),
    ("sdk", "griffe._internal.models", "Docstring"),
    ("sdk", "agents.function_schema", "Docstring"),
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("bridge", [False, True])
def test_schema_helper_carriers_cannot_establish_original_interfaces(tmp_path, framework, namespace, symbol, change, bridge):
    before, after = _versions(framework, "direct", change)
    # These shims modify the actual metadata mechanism without reflecting into
    # arbitrary globals; each import must remain an identity obligation.
    if symbol == "inspect":
        code = "def replacement(reader):\n    reader.signature = empty_signature\ndef empty_signature(*args, **kwargs):\n    return None\n"
    elif symbol == "Field":
        code = "def replacement(reader):\n    reader.__kwdefaults__['json_schema_extra'] = {'type': 'string'}\n"
    elif symbol == "find_context_parameter":
        code = "def replacement(reader):\n    reader.__wrapped__.__code__ = excluded.__code__\ndef excluded(*args, **kwargs):\n    return 'value'\n"
    elif symbol == "FunctionDeclaration":
        code = "def replacement(reader):\n    reader.model_validate = substitute\ndef substitute(*args, **kwargs):\n    return None\n"
    elif symbol == "Docstring":
        code = "def replacement(reader):\n    reader.parse = empty_doc\ndef empty_doc(*args, **kwargs):\n    return []\n"
    else:
        code = "def replacement(reader):\n    reader.__code__ = strings_only.__code__\ndef strings_only(*args, **kwargs):\n    return {'value': str}\n"
    after["shim.py"] = code
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    imported = "from bridge import Carrier\n" if bridge else f"from {namespace} import {symbol} as Carrier\n"
    after["app.py"] = imported + "from shim import replacement\nreplacement(Carrier)\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


@pytest.mark.parametrize("framework,namespace,symbol,mutation", [
    ("sdk", "typing_extensions", "get_origin", "origin"),
    ("adk", "typing_extensions", "get_args", "args"),
    ("sdk", "typing_extensions", "get_type_hints", "hints"),
    ("adk", "typing_extensions", "get_type_hints", "hints"),
    ("sdk", "typing_extensions", "typing", "typing"),
    ("adk", "typing_extensions", "typing", "typing"),
    ("sdk", "typing_extensions", "Generic", "generic"),
    *[(framework, namespace, "BaseModel", "model")
      for framework in ("sdk", "adk")
      for namespace in ("pydantic.type_adapter", "pydantic.root_model")],
    *[("adk", namespace, "types", "schema") for namespace in (
        "google.genai._automatic_function_calling_util", "google.genai._transformers", "google.genai._extra_utils",
    )],
    *[("adk", namespace, "_common", "model") for namespace in (
        "google.genai._transformers", "google.genai._extra_utils",
    )],
])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("bridge", [False, True])
def test_canonical_dependency_aliases_cannot_hide_schema_mutation(tmp_path, framework, namespace, symbol, mutation, change, bridge):
    before, after = _versions(framework, "direct", change)
    if mutation == "args":
        for files in (before, after):
            files["tools.py"] = files["tools.py"].replace("value: int", "value: list[int]")
    code = {
        "origin": "def replacement(reader):\n    from agents.run_context import RunContextWrapper\n    reader.__code__ = false_origin.__code__\n    reader.__defaults__ = (RunContextWrapper,)\ndef false_origin(annotation, context):\n    return context\n",
        "args": "def replacement(reader):\n    reader.__code__ = string_args.__code__\ndef string_args(*args, **kwargs):\n    return (str,)\n",
        "hints": "def replacement(reader):\n    reader.__code__ = string_hints.__code__\ndef string_hints(*args, **kwargs):\n    return {'value': str}\n",
        "typing": "def replacement(reader):\n    reader.get_type_hints.__code__ = string_hints.__code__\ndef string_hints(*args, **kwargs):\n    return {'value': str}\n",
        "generic": "def replacement(reader):\n    reader.__new__ = substitute\ndef substitute(*args, **kwargs):\n    return None\n",
        "model": "def replacement(reader):\n    reader.model_json_schema = string_schema\ndef string_schema(*args, **kwargs):\n    return {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}\n",
        "schema": "def replacement(reader):\n    saved = reader.Schema.__setattr__\n    def string_type(self, name, value):\n        if name == 'type':\n            value = 'STRING'\n        return saved(self, name, value)\n    reader.Schema.__setattr__ = string_type\n",
    }[mutation]
    # _common exports its actual Pydantic BaseModel, not a new class sink.
    if symbol == "_common":
        code = code.replace("reader.model_json_schema", "reader.BaseModel.model_json_schema")
    after["shim.py"] = code
    after["bridge.py"] = f"from {namespace} import {symbol} as Carrier\n"
    imported = "from bridge import Carrier\n" if bridge else f"from {namespace} import {symbol} as Carrier\n"
    after["app.py"] = imported + "from shim import replacement\nreplacement(Carrier)\n" + after["app.py"]
    _unread(_comparison(tmp_path, before, after))


def _scoped_versions(framework, change):
    before = _files(framework, "direct", "clean", factory=False)
    after = _files(framework, "direct", "clean",
                   "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]",
                   "return 2" if change == "changed" else "return 1", factory=False)
    versions = []
    for files in (before, after):
        scoped = {"pkg/__init__.py": "", "pkg/app/__init__.py": ""}
        for path, source in files.items():
            for name in ("tools", "shim", "factory", "builders", "patcher", "bridge", "foreign"):
                source = source.replace(f"from {name} import", f"from .{name} import")
            scoped[f"pkg/app/{path}"] = source
        versions.append(scoped)
    return versions


STDLIB_ABC_CARRIERS = [
    ("collections", "_collections_abc", "ABCMeta"), ("collections", "abc", None),
    ("collections.abc", "Mapping", None), ("contextlib", "abc", "ABCMeta"),
    ("numbers", "ABCMeta", None), ("io", "abc", "ABCMeta"), ("dataclasses", "abc", "ABCMeta"),
    ("_pyio", "abc", "ABCMeta"), ("fractions", "numbers", "ABCMeta"),
    ("_pydecimal", "_numbers", "ABCMeta"), ("os", "abc", "ABCMeta"),
    ("locale", "_collections_abc", "ABCMeta"), ("weakref", "_collections_abc", "ABCMeta"),
    ("selectors", "ABCMeta", None), ("shelve", "collections", "_collections_abc.ABCMeta"),
    ("traceback", "collections", "_collections_abc.ABCMeta"), ("configparser", "MutableMapping", None),
    ("pathlib", "Sequence", None), ("random", "_Sequence", None), ("tracemalloc", "Iterable", None),
]
