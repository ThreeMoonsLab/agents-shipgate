import runpy
import sys
import types
from pathlib import Path

import pytest


def test_simple_langchain_fixture_fails_if_imported(monkeypatch):
    _install_langchain_stubs(monkeypatch)

    with pytest.raises(RuntimeError, match="must parse this file without importing"):
        runpy.run_path(str(Path("samples/simple_langchain_agent/agent.py")))


def test_simple_crewai_fixture_fails_if_imported(monkeypatch):
    _install_crewai_stubs(monkeypatch)

    with pytest.raises(RuntimeError, match="must parse this file without importing"):
        runpy.run_path(str(Path("samples/simple_crewai_agent/crew.py")))


def _install_langchain_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    langchain = types.ModuleType("langchain")
    langchain.__path__ = []
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_agents.create_agent = lambda *args, **kwargs: None
    langchain_tools = types.ModuleType("langchain.tools")
    langchain_tools.tool = lambda *args, **kwargs: (lambda fn: fn)
    langchain_core = types.ModuleType("langchain_core")
    langchain_core.__path__ = []
    langchain_core_tools = types.ModuleType("langchain_core.tools")

    class StructuredTool:
        @classmethod
        def from_function(cls, *args, **kwargs):
            return cls()

    langchain_core_tools.StructuredTool = StructuredTool
    monkeypatch.setitem(sys.modules, "langchain", langchain)
    monkeypatch.setitem(sys.modules, "langchain.agents", langchain_agents)
    monkeypatch.setitem(sys.modules, "langchain.tools", langchain_tools)
    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core)
    monkeypatch.setitem(sys.modules, "langchain_core.tools", langchain_core_tools)


def _install_crewai_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    crewai = types.ModuleType("crewai")
    crewai.Agent = lambda *args, **kwargs: None
    crewai.Crew = lambda *args, **kwargs: None
    crewai_tools_module = types.ModuleType("crewai.tools")
    crewai_tools_module.tool = lambda *args, **kwargs: (lambda fn: fn)

    class BaseTool:
        pass

    crewai_tools_module.BaseTool = BaseTool
    prebuilt_tools = types.ModuleType("crewai_tools")
    prebuilt_tools.FileReadTool = type("FileReadTool", (), {})
    monkeypatch.setitem(sys.modules, "crewai", crewai)
    monkeypatch.setitem(sys.modules, "crewai.tools", crewai_tools_module)
    monkeypatch.setitem(sys.modules, "crewai_tools", prebuilt_tools)
