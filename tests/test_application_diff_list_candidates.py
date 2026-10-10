"""An unread constructor keeps named source evidence without granting rows (#1001)."""

from __future__ import annotations

import ast

import pytest

from agents_shipgate.inputs.list_expressions import MAX_MEMBERS
from agents_shipgate.inputs.python_imports import ImportResolver
from agents_shipgate.inputs.tool_list_candidates import tool_list_candidates
from tests.test_imported_tool_bindings import _commit, _compare, _git, _write

TOOLS = "from agents import function_tool\n@function_tool\ndef read():\n    return 'before'\nBASE = [read]\n"


@pytest.mark.parametrize("shape", ["imported-list", "returned-handle"])
def test_unread_list_initializers_keep_changed_tool_candidates(tmp_path, shape):
    # CRM's imported NARZEDZIA_WYCENY and Finance's literal spread retain
    # inspectable definitions even when a constructor/handle proof refuses.
    if shape == "imported-list":
        files = {
            "agent.py": "from agents import Agent\nfrom tools import BASE\ndef build():\n    return Agent(name='Finance', tools=BASE)\n",
            "smoke.py": "import importlib, sys\nimportlib.import_module(sys.argv[1])\n",
        }
    else:
        files = {
            "agent.py": "from agents import Agent\nfrom tools import BASE\ndef build(handoffs):\n    return Agent(name='Finance', tools=[*BASE], handoffs=handoffs)\n",
            "caller.py": "from agent import build\nfrom transport import publish\npublish(build([]))\n",
        }
    files["tools.py"] = TOOLS
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {"tools.py": TOOLS.replace("'before'", "'after'")})
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "partial"
    (row,) = [row for row in result["rows"] if row["tool"] == "read"]
    assert (row["agent"], row["change"], row["candidate_change"]) == ("Finance", "not_established", "changed")
    assert row["before"] and row["after"]
    assert row["uncertainty"]
    assert any("constructor identity is not established" in limit for limit in result["head"]["limits"])


def test_mutating_a_literal_list_does_not_grant_an_established_candidate(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    files = {
        "agent.py": "from agents import Agent\nfrom tools import BASE\ndef build():\n    return Agent(name='Finance', tools=BASE)\n",
        "smoke.py": "import importlib, sys\nimportlib.import_module(sys.argv[1])\n",
        "mutator.py": "from tools import BASE\nBASE.clear()\n",
        "tools.py": TOOLS,
    }
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {"tools.py": TOOLS.replace("'before'", "'after'")})
    result = _compare(tmp_path, base, head)
    assert result["comparison_status"] == "partial"
    assert all(row["change"] == "not_established" for row in result["rows"])
    assert any("may be changed in place" in limit for limit in result["head"]["limits"])


@pytest.mark.parametrize(
    ("source", "expression", "expected"),
    [
        ("BASE = [read]\n", "[*BASE] + (extra,)", [("read", ()), ("extra", ())]),
        ("", "[read] if enabled else [extra]", [("read", ("`enabled`",)), ("extra", ("not `enabled`",))]),
        ("", "[read] if False else [extra]", [("extra", ())]),
        ("A = B\nB = A\n", "A", []),
        ("BASE = [read]\nBASE = [extra]\n", "BASE", []),
        ("", "load_tools()", []),
        ("", "[make_tool(), *load_tools(), read]", [("read", ())]),
        ("", "[" + ",".join("read" for _ in range(MAX_MEMBERS + 1)) + "]", []),
    ],
    ids=["spread-concatenation", "conditions", "constant-condition", "cycle", "rebound", "call", "unread-operands", "member-bound"],
)
def test_candidate_initializer_walk_has_bounded_source_only_behavior(tmp_path, source, expression, expected):
    _write(tmp_path, {"candidate.py": source + f"candidate = {expression}\n"})
    resolver = ImportResolver(tmp_path)
    module = resolver.module(tmp_path / "candidate.py")
    members = tool_list_candidates(module.tree.body[-1].value, module, resolver)
    assert [(ast.unparse(member.expr), member.conditions) for member in members] == expected
