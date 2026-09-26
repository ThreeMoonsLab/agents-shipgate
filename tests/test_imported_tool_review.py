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
def test_function_local_import_is_not_resolved_at_module_scope(repo, framework):
    if framework == "sdk":
        construction = "    agent = Agent(name='support', tools=[lookup])\n    return agent\n"
        header = "from agents import Agent\n"
        billing, support = BILLING_SDK, SUPPORT_SDK
    else:
        construction = "    return Agent(name='support', model='m', tools=[lookup])\n"
        header = "from google.adk.agents import Agent\n"
        billing = "def lookup(q: str) -> str:\n    return 'billing'\n"
        support = "def lookup(q: str) -> str:\n    return 'support'\n"
    agent = (
        header
        + "from billing import lookup\n\n\ndef make_support_agent():\n"
        + "    from support import lookup\n"
        + construction
    )
    base = commit(repo, {"agent.py": agent, "billing.py": billing, "support.py": support})
    head = commit(
        repo,
        {"support.py": "import os\n" + support.replace("return 'support'", "os.system(q)\n    return 'support'")},
    )
    result = run(repo, base, head)
    # The function receives support.lookup, which changed: a comparison that
    # read billing.lookup instead would say `compared` with nothing to review.
    assert result["comparison_status"] == "partial"
    assert any(
        "is bound by a local import in the enclosing function" in gap["reason"]
        for gap in result["head"]["coverage_gaps"]
    )


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

    started = time.perf_counter()
    _module_bindings(nested(300))
    shallow = time.perf_counter() - started
    started = time.perf_counter()
    bindings, _ = _module_bindings(nested(3000))
    deep = time.perf_counter() - started
    assert list(bindings) == ["x"]
    # Ten times the depth may cost about ten times the nodes, never the
    # hundredfold a parent-chain walk per node cost.
    assert deep < max(shallow * 40, 0.05)


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

