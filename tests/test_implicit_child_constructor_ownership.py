"""An initializer can repeat the import system's exact own-child binding."""

import ast

import pytest

from agents_shipgate.inputs.builder_calls import BuilderCalls
from agents_shipgate.inputs.python_imports import ImportResolver
from tests.test_imported_tool_bindings import _adk, _sdk, _write


def _observations(root, framework, initializer, extra=None):
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    _write(root, {"pkg/__init__.py": initializer,
                  "pkg/agent.py": f"from {namespace} import Agent\nfrom tools import read\nroot_agent = Agent(name='App', tools=[read])\n",
                  "tools.py": ("from agents import function_tool\n@function_tool\n" if framework == "sdk" else "") + "def read():\n    return 1\n",
                  **(extra or {})})
    if framework == "sdk":
        loaded = _sdk(root, "pkg/agent.py")
        return loaded.binding_observations, loaded.warnings
    loaded, artifacts = _adk(root, "pkg/agent.py")
    return [item for source in loaded for item in source.binding_observations], artifacts.warnings


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("initializer", ["from . import agent\n", '"Package docs."\nfrom . import agent\n'])
def test_exact_unused_own_child_binding_remains_readable(tmp_path, framework, initializer):
    observations, warnings = _observations(tmp_path, framework, initializer)
    assert observations and all(item.tools_complete for item in observations)
    assert all(item.tool_names == ["read"] for item in observations)
    assert not warnings


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("initializer", ["from . import agent as retained\n",
    "from . import agent\nconsume(agent)\n", "from . import agent\nagent = replacement\n"])
def test_additional_initializer_retention_remains_unread(tmp_path, framework, initializer):
    observations, warnings = _observations(tmp_path, framework, initializer)
    assert observations and all(not item.tools_complete for item in observations)
    assert warnings


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_other_importers_still_keep_export_obligation(tmp_path, framework):
    observations, warnings = _observations(tmp_path, framework, "from . import agent\n",
        {"consumer.py": "from pkg import agent\nconsume(agent)\n"})
    assert observations and all(not item.tools_complete for item in observations)
    assert warnings


@pytest.mark.parametrize("change", [None, "initializer", "child", "new_consumer"])
def test_own_child_absence_is_fresh_and_currency_bound(tmp_path, change):
    text = "from agents import Agent\nroot_agent = Agent(name='App', tools=[])\n"
    _write(tmp_path, {"pkg/__init__.py": "from . import agent\n", "pkg/agent.py": text})
    reader = ImportResolver(tmp_path)
    module = reader.entry(tmp_path / "pkg/agent.py", ast.parse(text), text)
    calls = BuilderCalls(reader)
    call = next(item for item in ast.walk(module.tree) if isinstance(item, ast.Call))
    assert calls.exported_elsewhere(module, call) is False
    assert id(call) not in calls._exports
    if change == "initializer":
        (tmp_path / "pkg/__init__.py").write_text("from . import agent as proxy\n")
    elif change == "child":
        (tmp_path / "pkg/agent.py").write_text(text.replace("tools=[]", "tools=[unknown]"))
    elif change == "new_consumer":
        (tmp_path / "consumer.py").write_text("from pkg import agent\nconsume(agent)\n")
    assert calls.exported_elsewhere(module, call) is (change is not None)
    if change is not None:
        assert calls.export_limit(call)
    else:
        assert id(call) not in calls._exports
