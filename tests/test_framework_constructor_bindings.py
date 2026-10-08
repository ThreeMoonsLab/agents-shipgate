"""Changed constructors cannot establish added, changed or removed bindings."""

from __future__ import annotations

import pytest

from tests.constructor_engine_fixture import frozen_constructor_engine as frozen_constructor_engine
from tests.test_imported_tool_bindings import _adk, _commit, _compare, _git, _sdk


def _files(framework, layout, patch, selected="[read]", body="return 1", *, factory=True):
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    decorator = "from agents import function_tool\n@function_tool\n" if framework == "sdk" else ""
    files = {
        "tools.py": decorator + f"def read():\n    {body}\n" +
        ("@function_tool\n" if framework == "sdk" else "") + "def write():\n    return None\n",
        "factory.py": f"from tools import read, write\ndef make_tools():\n    return {selected}\n",
        "shim.py": "def replacement(*args, **kwargs):\n    return kwargs\n",
    }
    header = f"import {namespace} as fw\nfrom {namespace} import Agent\nfrom shim import replacement\n"
    constructor = "fw.Agent" if patch != "root_rebind" else "Agent"
    patches = {
        "clean": "", "root_rebind": "Agent = replacement\n",
        "namespace": "fw.Agent = replacement\n", "init": "fw.Agent.__init__ = replacement\n",
        "new": "Agent.__new__ = replacement\n", "metadata": "Agent.__pydantic_validator__ = replacement\n",
        "alias": "A = Agent\nB = A\nB.__init__ = replacement\n",
        "dotted_alias": "A = fw.Agent\nA.__init__ = replacement\n",
        "opaque": "replacement(Agent)\n", "namespace_opaque": "replacement(fw)\n",
        "reflection": "getattr(Agent, runtime_name()).__code__ = replacement\n",
        "setattr": "setattr(fw, 'Agent', replacement)\n",
        "table": f"import sys\nsys.modules['{namespace}'] = replacement\n",
        "imported": "import patcher\n", "transitive": "import bridge\n",
        "unrelated": "import foreign\nforeign.Widget.__init__ = replacement\n",
    }
    files["foreign.py"] = "class Widget:\n    pass\n"
    files["patcher.py"] = f"from {namespace} import Agent as A\nfrom shim import replacement\nA.__init__ = replacement\n"
    files["bridge.py"] = "import patcher\n"
    if patch in {"reexport", "reexport_metadata", "star_reexport", "bridge_opaque", "helper_opaque", "conditional_reexport", "rebound_reexport", "saved_rebound"}:
        files["bridge.py"] = f"from {namespace} import Agent as Carrier\ndef helper():\n    return None\n"
        files["patcher.py"] = {
            "reexport": "from bridge import Carrier\nCarrier.__init__ = replacement\n",
            "reexport_metadata": "from bridge import Carrier\nCarrier.__pydantic_validator__ = replacement\n",
            "star_reexport": "from bridge import *\nCarrier.__new__ = replacement\n",
            "bridge_opaque": "import bridge\nreplacement(bridge)\n",
            "helper_opaque": "from bridge import helper\nreplacement(helper)\n",
            "conditional_reexport": "from bridge import Carrier\nCarrier.__init__ = replacement\n",
            "rebound_reexport": "from bridge import Carrier\nCarrier.__init__ = replacement\n",
            "saved_rebound": f"from {namespace} import Agent\nA = Agent\nAgent = object\nA.__init__ = replacement\n",
        }[patch]
        if patch == "conditional_reexport":
            files["bridge.py"] = f"if flag:\n    from {namespace} import Agent as Carrier\n"
        elif patch == "rebound_reexport":
            files["bridge.py"] += "Carrier = object\n"
        patches[patch] = "import patcher\n"
    elif patch in {"namespace_reexport", "namespace_alias"}:
        files["bridge.py"] = f"import {namespace} as api\n"
        files["patcher.py"] = ("import bridge\nbridge.api.Agent = replacement\n" if patch == "namespace_reexport"
                               else "from bridge import api as target\ntarget.Agent = replacement\n")
        patches[patch] = "import patcher\n"
    elif patch in {"exec", "exec_alias", "builtins", "dynamic", "globals", "vars"}:
        files["patcher.py"] = header + {
            "exec": "exec(payload)\n", "exec_alias": "from builtins import exec as run\nrun(payload)\n",
            "builtins": "__builtins__['exec'](payload)\n",
            "dynamic": f"import importlib\nother = importlib.import_module('{namespace}')\nother.Agent = replacement\n",
            "globals": "globals()['Agent'].__init__ = replacement\n",
            "vars": "vars(Agent)['__init__'] = replacement\n",
        }[patch]
        patches[patch] = "import patcher\n"
    tools = "make_tools()" if factory else selected
    call_header = "from factory import make_tools\n" if factory else "from tools import read, write\n"
    if layout == "direct":
        files["app.py"] = header + patches[patch] + call_header + f"a = {constructor}(name='Built', tools={tools})\n"
    else:
        files["builders.py"] = header + (patches[patch] if layout == "builder" else "") + f"def build(tools):\n    return {constructor}(name='Built', tools=tools)\n"
        files["app.py"] = header + (patches[patch] if layout == "caller" else "") + "from builders import build\n" + call_header + f"a = build({tools})\n"
    return files


def _run(root, framework, layout, patch, change, *, factory=True):
    _git(root, "init", "-q", "-b", "main")
    before = _files(framework, layout, "clean", factory=factory)
    after = _files(framework, layout, patch, "[read, write]" if change == "added" else "[]" if change == "removed" else "[read]", "return 2" if change == "changed" else "return 1", factory=factory)
    base, head = _commit(root, before), _commit(root, after)
    result = _compare(root, base, head, "--scope", ".")
    source = "app.py" if layout == "direct" else "builders.py"
    if framework == "sdk":
        loaded = _sdk(root, source)
        observations, warnings = loaded.binding_observations, loaded.warnings
    else:
        loaded, artifacts = _adk(root, source)
        observations = [item for part in loaded for item in part.binding_observations]
        warnings = artifacts.warnings
    return result, observations, warnings


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("layout,patch", [(layout, patch) for layout in ("direct", "builder", "caller")
                                        for patch in ("root_rebind", "namespace", "init", "new", "metadata", "alias", "dotted_alias", "opaque", "namespace_opaque", "reflection", "setattr", "table", "imported", "transitive")
                                        if (layout, patch) != ("caller", "root_rebind")])
@pytest.mark.parametrize("change", ["added", "changed", "removed"])
@pytest.mark.parametrize("factory", [False, True])
def test_constructor_changes_are_binding_uncertainty(tmp_path, framework, layout, patch, change, factory):
    result, observations, warnings = _run(tmp_path, framework, layout, patch, change, factory=factory)
    assert warnings and observations
    assert all(not item.tools_complete and not item.handoffs_complete for item in observations)
    assert any("constructor" in warning for warning in warnings)
    assert result["comparison_status"] != "compared"
    assert all(row["change"] == "not_established" for row in result["rows"])


@pytest.mark.parametrize("framework", ["sdk", "adk"])
@pytest.mark.parametrize("layout,patch", [(layout, patch) for layout in ("direct", "builder", "caller") for patch in ("clean", "unrelated")]
                        + [("caller", "root_rebind")])
def test_clean_constructors_still_establish_factory_additions(tmp_path, framework, layout, patch):
    result, observations, warnings = _run(tmp_path, framework, layout, patch, "added")
    assert warnings == [] and observations and all(item.tools_complete for item in observations)
    assert result["comparison_status"] == "compared"
    assert any(row["tool"] == "write" and row["change"] == "added" for row in result["rows"])


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_unread_constructors_retain_named_handoff_candidates(tmp_path, framework):
    namespace = "agents" if framework == "sdk" else "google.adk.agents"
    source = (f"from {namespace} import Agent\n"
              "Agent.__init__ = object\n"
              "worker = Agent(name='Worker', tools=[])\n"
              "root = Agent(name='Root', "
              + ("handoffs" if framework == "sdk" else "sub_agents") + "=[worker])\n")
    (tmp_path / "agent.py").write_text(source)
    if framework == "sdk":
        observations = _sdk(tmp_path, "agent.py").binding_observations
        root = next(item for item in observations if item.agent == "root")
        assert root.handoff_names == ["worker"] and not root.handoffs_complete
        assert root.constructor_issues
    else:
        _, artifacts = _adk(tmp_path, "agent.py")
        root = next(item for item in artifacts.sub_agents if item.get("agent_name") == "Root")
        assert root["sub_agents"] == ["Worker"] and root["sub_agent_count"] == 1
        assert root["unread"]
