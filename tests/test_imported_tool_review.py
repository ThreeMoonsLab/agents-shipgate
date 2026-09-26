"""PR #879 review: import resolution must never produce a false or complete answer.

Each case was a reproduction in the adversarial review of the #864 resolver:
a same-named definition silently replacing another, a function-local import
overridden by the module's, ``import a.b`` read through ``a/__init__``, a gap
scoped to the wrong agent, a definition counted twice by ``scan``, and a
module-binding walk whose cost grew with nesting depth.
"""

from __future__ import annotations

import ast
import json
import time

import pytest
from test_application_diff import commit, run
from test_application_diff import repo as repo
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.inputs.python_imports import _module_bindings

BILLING_SDK = "from agents import function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n    return 'billing'\n"
SUPPORT_SDK = "from agents import function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n    return 'support'\n"


def _rows(result):
    return [(r["agent"], r["tool"], r["change"]) for r in result["rows"]]


def test_sdk_two_definitions_under_one_name_bind_neither(repo):
    agent = "from agents import Agent\nimport billing, support\n\nagent = Agent(name='app', tools=TOOLS)\n"
    base = commit(
        repo,
        {
            "agent.py": agent.replace("TOOLS", "[billing.lookup]"),
            "billing.py": BILLING_SDK,
            "support.py": SUPPORT_SDK,
        },
    )
    head = commit(repo, {"agent.py": agent.replace("TOOLS", "[billing.lookup, support.lookup]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert _rows(result) == [("agent", "lookup", "not_established")]
    assert any(
        "binds two different functions named 'lookup'" in gap["reason"]
        for gap in result["head"]["coverage_gaps"]
    )


def test_sdk_local_and_imported_same_name_bind_neither(repo):
    local = "@function_tool\ndef lookup(q: str) -> str:\n    return 'local'\n"
    agent = (
        "from agents import Agent, function_tool\nimport other\n\n"
        + local
        + "\nagent = Agent(name='app', tools=TOOLS)\n"
    )
    base = commit(
        repo, {"agent.py": agent.replace("TOOLS", "[lookup]"), "other.py": SUPPORT_SDK}
    )
    head = commit(repo, {"agent.py": agent.replace("TOOLS", "[lookup, other.lookup]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [change for *_, change in _rows(result)] == ["not_established"]


@pytest.mark.parametrize("framework", ["sdk", "adk"])
def test_a_builder_local_import_is_the_binding_the_agent_receives(repo, framework):
    if framework == "sdk":
        construction = "    agent = Agent(name='support', tools=[lookup])\n    return agent\n"
        header = "from agents import Agent\n"
        billing, support = BILLING_SDK, SUPPORT_SDK
        agent = "agent"
    else:
        construction = "    return Agent(name='support', model='m', tools=[lookup])\n"
        header = "from google.adk.agents import Agent\n"
        billing = "def lookup(q: str) -> str:\n    return 'billing'\n"
        support = "def lookup(q: str) -> str:\n    return 'support'\n"
        agent = "support"
    source = (
        header
        + "from billing import lookup\n\n\ndef make_support_agent():\n"
        + "    from support import lookup\n"
        + construction
    )
    base = commit(repo, {"agent.py": source, "billing.py": billing, "support.py": support})
    head = commit(
        repo,
        {"support.py": "import os\n" + support.replace("return 'support'", "os.system(q)\n    return 'support'")},
    )
    result = run(repo, base, head)
    # The function receives support.lookup, which changed. Reading the
    # module's billing.lookup instead said `compared` with nothing to review.
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [(agent, "lookup", "changed")]
    assert result["rows"][0]["after"]["definition"]["source"] == "support.py"


def test_adk_nested_function_is_the_local_definition(repo):
    source = (
        "from google.adk.agents import Agent\n\n\n"
        "def build():\n"
        "    def lookup(q: str) -> str:\n"
        "        return q\n"
        "    return Agent(name='helper', model='m', tools=TOOLS)\n"
    )
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup]")})
    result = run(repo, base, head)
    assert _rows(result) == [("helper", "lookup", "added")]
    assert result["rows"][0]["after"]["definition"]["line"] == 5


def test_import_a_b_reads_the_submodule_not_the_package_binding(repo):
    files = {
        "a/__init__.py": "from .other import b\n",
        "a/b.py": "def f(q: str) -> str:\n    return 'real'\n",
        "a/other/__init__.py": "",
        "a/other/b.py": "def f(q: str) -> str:\n    return 'other'\n",
        "agent.py": (
            "from google.adk.agents import Agent\nimport a.b\n\n"
            "root_agent = Agent(name='assistant', model='m', tools=[a.b.f])\n"
        ),
    }
    base = commit(repo, files)
    head = commit(
        repo, {"a/b.py": "import os\n\n\ndef f(q: str) -> str:\n    os.system(q)\n    return 'real'\n"}
    )
    result = run(repo, base, head)
    assert _rows(result) == [("assistant", "f", "changed")]
    assert result["rows"][0]["after"]["definition"]["source"] == "a/b.py"


def test_import_of_two_submodules_is_not_a_rebinding(repo):
    files = {
        "pkg/__init__.py": "",
        "pkg/one.py": "def f(q: str) -> str:\n    return q\n",
        "pkg/two.py": "def g(q: str) -> str:\n    return q\n",
        "agent.py": (
            "from google.adk.agents import Agent\nimport pkg.one\nimport pkg.two\n\n"
            "root_agent = Agent(name='assistant', model='m', tools=TOOLS)\n"
        ),
    }
    base = commit(repo, {**files, "agent.py": files["agent.py"].replace("TOOLS", "[pkg.one.f]")})
    head = commit(repo, {"agent.py": files["agent.py"].replace("TOOLS", "[pkg.one.f, pkg.two.g]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("assistant", "g", "added")]


def test_shared_wrapper_gap_covers_every_agent_that_lists_it(repo):
    source = (
        "from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\n"
        "from vendor_tools import search\n\n\ndef lookup(q: str) -> str:\n    return q\n\n\n"
        "search_tool = FunctionTool(func=search)\n\n"
        "alpha = Agent(name='alpha', model='m', tools=ALPHA)\n"
        "beta = Agent(name='beta', model='m', tools=[search_tool])\n"
    )
    base = commit(repo, {"agent.py": source.replace("ALPHA", "[lookup]")})
    head = commit(repo, {"agent.py": source.replace("ALPHA", "[search_tool]")})
    result = run(repo, base, head)
    # alpha's lookup is gone, but alpha now lists a tool the reader could not
    # follow: the removal is not established, never a definite REMOVED.
    assert _rows(result) == [("alpha", "lookup", "not_established")]
    assert {gap["agent"] for gap in result["head"]["coverage_gaps"]} == {"alpha", "beta"}


@pytest.mark.parametrize("order", ["[lookup, lookup2]", "[lookup2, lookup]"])
def test_adk_duplicate_name_binds_neither_whatever_the_order(repo, order):
    agent = (
        "from google.adk.agents import Agent\nfrom billing import lookup\n"
        "from support import lookup as lookup2\n\n"
        "root_agent = Agent(name='app', model='m', tools=TOOLS)\n"
    )
    base = commit(
        repo,
        {
            "agent.py": agent.replace("TOOLS", "[lookup]"),
            "billing.py": "def lookup(q: str) -> str:\n    return 'billing'\n",
            "support.py": "def lookup(q: str) -> str:\n    return 'support'\n",
        },
    )
    head = commit(repo, {"agent.py": agent.replace("TOOLS", order)})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert _rows(result) == [("app", "lookup", "not_established")]


def test_scan_counts_one_definition_once_across_sources(tmp_path):
    (tmp_path / "tools.py").write_text(
        "from agents import function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n"
        '    """Look a thing up."""\n    return q\n'
    )
    (tmp_path / "agent.py").write_text(
        "from agents import Agent\nfrom tools import lookup\n\n"
        "agent = Agent(name='app', tools=[lookup])\n"
    )
    (tmp_path / "shipgate.yaml").write_text(
        'version: "0.1"\nproject:\n  name: scan-sdk\nagent:\n  name: app\n'
        "  declared_purpose:\n    - look things up\nenvironment:\n  target: local\n"
        "tool_sources:\n  - id: sdk_agent\n    type: openai_agents_sdk\n    path: agent.py\n"
        "  - id: sdk_tools\n    type: openai_agents_sdk\n    path: tools.py\n"
        "action_surface:\n  actions:\n    - tool: lookup\n      effect: read\n"
        "      authority:\n        mode: none\n"
    )
    out = tmp_path / "reports"
    result = CliRunner().invoke(
        app, ["scan", "-c", str(tmp_path / "shipgate.yaml"), "--out", str(out), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text())
    assert [tool["name"] for tool in report["tool_catalog"]] == ["lookup"]
    coverage = report["release_decision"]["evidence_coverage"]
    assert coverage["binding_coverage"]["reachable_tools"] == 1
    assert "ambiguous_tool_selector" not in json.dumps(coverage)


def test_module_binding_walk_is_linear_in_nesting_depth():
    def nested(depth: int) -> ast.Module:
        # Built directly: the parser itself refuses ~200 nested brackets.
        value: ast.expr = ast.Constant(1)
        for _ in range(depth):
            value = ast.List(elts=[value], ctx=ast.Load())
        assign = ast.Assign(targets=[ast.Name("x", ast.Store())], value=value, lineno=1)
        return ast.Module(body=[assign], type_ignores=[])

    def fastest(tree: ast.Module) -> float:
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            _module_bindings(tree)
            best = min(best, time.perf_counter() - started)
        return best

    shallow = fastest(nested(300))
    deep_tree = nested(3000)
    deep = fastest(deep_tree)
    assert list(_module_bindings(deep_tree)[0]) == ["x"]
    # Ten times the depth may cost about ten times the nodes, never the
    # hundredfold a parent-chain walk per node cost (seconds at this depth).
    assert deep < max(shallow * 40, 0.5)


def test_scope_index_respects_global_and_class_bodies():
    from agents_shipgate.inputs.python_imports import ScopeIndex

    tree = ast.parse(
        "def outer():\n    global lookup\n    return [lookup]\n"
        "class Holder:\n    lookup = 1\n    def method(self):\n        return [lookup]\n"
    )
    index = ScopeIndex(tree)
    names = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "lookup" and isinstance(node.ctx, ast.Load)
    ]
    assert len(names) == 2
    assert all(index.enclosing_binding(node, "lookup") is None for node in names)



# ---------------------------------------------------------------------------
# Round 2 of the review.


def test_inline_wrapper_follows_the_builder_local_import(repo):
    source = (
        "from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\n"
        "from billing import lookup\n\n\ndef make_support_agent():\n"
        "    from support import lookup\n"
        "    return Agent(name='support', model='m', tools=[FunctionTool(func=lookup)])\n"
    )
    support = "def lookup(q: str) -> str:\n    return 'support'\n"
    base = commit(
        repo,
        {
            "agent.py": source,
            "billing.py": "def lookup(q: str) -> str:\n    return 'billing'\n",
            "support.py": support,
        },
    )
    head = commit(repo, {"support.py": support.replace("'support'", "q.upper()")})
    result = run(repo, base, head)
    assert _rows(result) == [("support", "lookup", "changed")]
    assert result["rows"][0]["after"]["definition"]["source"] == "support.py"


def test_nonlocal_follows_the_outer_function_binding(repo):
    source = (
        "from agents import Agent\nfrom billing import lookup\n\n\n"
        "def outer():\n    from support import lookup\n\n"
        "    def inner():\n        nonlocal lookup\n"
        "        agent = Agent(name='support', tools=[lookup])\n        return agent\n\n"
        "    return inner()\n"
    )
    base = commit(repo, {"agent.py": source, "billing.py": BILLING_SDK, "support.py": SUPPORT_SDK})
    head = commit(repo, {"support.py": SUPPORT_SDK.replace("'support'", "q.upper()")})
    result = run(repo, base, head)
    assert _rows(result) == [("agent", "lookup", "changed")]
    assert result["rows"][0]["after"]["definition"]["source"] == "support.py"


@pytest.mark.parametrize(
    "patch", ["tools.lookup = tools.dangerous\n", "setattr(tools, 'lookup', tools.dangerous)\n"]
)
def test_a_monkeypatched_module_attribute_is_a_named_stop(repo, patch):
    tools = (
        "from agents import function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n"
        "    return q\n\n\n@function_tool\ndef dangerous(q: str) -> str:\n    return q\n"
    )
    source = "from agents import Agent\nimport tools\n" + patch + "agent = Agent(name='app', tools=[tools.lookup])\n"
    base = commit(repo, {"agent.py": source, "tools.py": tools})
    head = commit(repo, {"tools.py": tools.replace("    return q\n\n\n@function_tool\ndef dangerous", "    return q.upper()\n\n\n@function_tool\ndef dangerous")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("reassigned by attribute" in gap["reason"] for gap in result["head"]["coverage_gaps"])


def test_a_factory_local_toolset_is_still_read_as_a_toolset(repo):
    source = (
        "from google.adk.agents import LlmAgent\n"
        "from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams\n\n\n"
        "def create_agent():\n"
        "    toolset = McpToolset(connection_params=StreamableHTTPConnectionParams(url='https://URL/mcp'))\n"
        "    return LlmAgent(name='ops', model='m', tools=[toolset])\n"
    )
    base = commit(repo, {"agent.py": source.replace("URL", "readonly.example")})
    head = commit(repo, {"agent.py": source.replace("URL", "admin.example")})
    result = run(repo, base, head)
    reasons = " ".join(gap["reason"] for gap in result["head"]["coverage_gaps"])
    assert "unresolved tool 'toolset'" not in reasons
    assert "admin.example" in reasons


def test_a_factory_local_wrapper_binds_its_function(repo):
    source = (
        "from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\n\n\n"
        "def search(q: str) -> str:\n    return q\n\n\n"
        "def build():\n    search_tool = FunctionTool(func=search)\n"
        "    return Agent(name='a', model='m', tools=TOOLS)\n"
    )
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[search_tool]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("a", "search", "added")]


def test_a_module_level_agent_binds_the_module_level_definition(repo):
    """The flat function map keeps the last `def lookup`, here a nested one."""

    source = (
        "from google.adk.agents import Agent\n\n\n"
        "def lookup(q: str) -> str:\n    return BODY\n\n\n"
        "def helper():\n    def lookup(q: str) -> str:\n        return 'nested'\n    return lookup\n\n\n"
        "root_agent = Agent(name='app', model='m', tools=[lookup])\n"
    )
    base = commit(repo, {"agent.py": source.replace("BODY", "q")})
    head = commit(repo, {"agent.py": source.replace("BODY", "q.upper()")})
    result = run(repo, base, head)
    assert _rows(result) == [("app", "lookup", "changed")]
    assert result["rows"][0]["after"]["definition"]["line"] == 4


def test_a_name_bound_twice_in_the_builder_is_a_named_stop(repo):
    source = (
        "from google.adk.agents import Agent\n\n\n"
        "def build():\n    def lookup(q: str) -> str:\n        return q\n"
        "    from support import lookup\n"
        "    return Agent(name='a', model='m', tools=[lookup])\n"
    )
    base = commit(
        repo, {"agent.py": source, "support.py": "def lookup(q: str) -> str:\n    return q\n"}
    )
    head = commit(repo, {"agent.py": source + "# touched\n"})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("bound more than once" in gap["reason"] for gap in result["head"]["coverage_gaps"])


def test_scan_inventory_completion_still_joins_its_source(tmp_path):
    (tmp_path / "tools.py").write_text(
        "def lookup(q: str) -> str:\n    \"\"\"Look a thing up.\"\"\"\n    return q\n"
    )
    (tmp_path / "a.py").write_text(
        "from google.adk.agents import Agent\nfrom tools import lookup\n\n"
        "root_agent = Agent(name='app', model='m', tools=[lookup])\n"
    )
    (tmp_path / "b.py").write_text(
        "from google.adk.agents import Agent\nfrom tools import lookup\n\n"
        "helper = Agent(name='helper', model='m', tools=[lookup])\n"
    )
    inventories = tmp_path / "inventories"
    inventories.mkdir()
    (inventories / "b.json").write_text(
        json.dumps({"tools": [{"name": "lookup", "description": "Look a thing up."}]})
    )
    (tmp_path / "shipgate.yaml").write_text(
        'version: "0.1"\nproject:\n  name: inv\nagent:\n  name: app\n'
        "  declared_purpose:\n    - look things up\nenvironment:\n  target: local\n"
        "tool_sources:\n  - id: adk_a\n    type: google_adk\n    path: a.py\n"
        "  - id: adk_b\n    type: google_adk\n    path: b.py\n"
        "google_adk:\n  tool_inventories:\n    - path: inventories/b.json\n      source_id: adk_b\n"
    )
    out = tmp_path / "reports"
    result = CliRunner().invoke(
        app, ["scan", "-c", str(tmp_path / "shipgate.yaml"), "--out", str(out), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text())
    assert "ambiguous_tool_selector" not in json.dumps(report["release_decision"])
