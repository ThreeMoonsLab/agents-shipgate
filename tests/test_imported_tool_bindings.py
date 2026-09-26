"""Tools an agent binds from a sibling module are the agent's tools (#864).

The readers used to stop at a module boundary: ``tools=[memory_bank.remember]``
and ``from ..tools.shop import add_to_cart`` were unresolved, so a PR adding
such a binding produced no row. These tests pin the repaired behaviour at the
reader, the binding graph and ``diff --application``, and the negative cases
that must stay named rather than become an empty or complete surface.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan.source_loading import _build_canonical_tools
from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.source_warnings import unresolved_adk_tool_symbols
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig

ATTEST_INIT = '''"""Lazy package, as in jpka/attest#3."""
import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import agent, memory_bank, scorer  # noqa: F401


def __getattr__(name: str):
    module = importlib.import_module(f".{name}", __name__)
    globals()[name] = module
    return module
'''

ATTEST_AGENT = '''from google.adk.agents import Agent

from . import IMPORTS


def get_adv_ground_truth(crd: str) -> dict:
    """Ground truth for one firm."""
    return {}


def list_covered_firms() -> list:
    """Firms with ground truth."""
    return []


def append_evidence(payload: str) -> dict:
    """Append one evidence record."""
    return {}


root_agent = Agent(
    name="attest_orchestrator",
    model="gemini-2.5-flash",
    tools=[
        get_adv_ground_truth,
        list_covered_firms,
        append_evidence,
        scorer.score_answer,TOOLS
    ],
)
'''

MEMORY_BANK = '''def remember_firm_finding(crd: str, category: str, fact: str) -> dict:
    """Store one finding."""
    return {}


def recall_firm_memory(crd: str, query: str) -> dict:
    """Recall findings."""
    return {}


def purge_firm_memory(crd: str) -> dict:
    """Not bound to any agent."""
    return {}
'''

SCORER = '''def score_answer(answer: str) -> dict:
    """Score one answer."""
    return {}
'''


def _write(root: Path, files: dict[str, str | None]) -> None:
    for name, content in files.items():
        path = root / name
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _attest(head: bool) -> dict[str, str | None]:
    imports = "memory_bank, scorer" if head else "scorer"
    tools = (
        "\n        memory_bank.remember_firm_finding,\n        memory_bank.recall_firm_memory,"
        if head
        else ""
    )
    return {
        "agents/attest_orchestrator/__init__.py": ATTEST_INIT,
        "agents/attest_orchestrator/agent.py": ATTEST_AGENT.replace("IMPORTS", imports).replace(
            "TOOLS", tools
        ),
        "agents/attest_orchestrator/scorer.py": SCORER,
        "agents/attest_orchestrator/memory_bank.py": MEMORY_BANK if head else None,
    }


def _adk(root: Path, path: str = "agent.py"):
    return load_google_adk_artifacts(
        None, root, sources=[ToolSourceConfig(id="adk", type="google_adk", path=path)]
    )


def _edges(loaded, artifacts=None) -> list[tuple[str, str, str]]:
    tools, _ = _build_canonical_tools(loaded)
    bag = ArtifactBag()
    if artifacts is not None:
        bag.set("google_adk", artifacts)
    graph, _ = resolve_agent_binding_graph(None, tools, bag, loaded)
    by_id = {tool.id: tool for tool in tools}
    agents = {agent.agent_id: agent.name for agent in graph.agents}
    return sorted(
        (agents[edge.agent_id], by_id[edge.tool_id].name, by_id[edge.tool_id].source_location or "")
        for edge in graph.tool_edges
    )


# -- Google ADK ---------------------------------------------------------------


def test_adk_head_identifies_imported_memory_and_scorer_tools(tmp_path):
    root = tmp_path / "agents" / "attest_orchestrator"
    _write(tmp_path, {k: v for k, v in _attest(head=True).items() if v is not None})
    loaded, artifacts = _adk(root)

    assert artifacts is not None
    assert artifacts.warnings == []
    assert artifacts.unresolved_references == []
    assert _edges(loaded, artifacts) == [
        ("attest_orchestrator", "append_evidence", "agent.py:16"),
        ("attest_orchestrator", "get_adv_ground_truth", "agent.py:6"),
        ("attest_orchestrator", "list_covered_firms", "agent.py:11"),
        ("attest_orchestrator", "recall_firm_memory", "memory_bank.py:6"),
        ("attest_orchestrator", "remember_firm_finding", "memory_bank.py:1"),
        ("attest_orchestrator", "score_answer", "scorer.py:1"),
    ]
    tools = {tool.name: tool for source in loaded for tool in source.tools}
    # An unbound definition in the imported module is not a capability.
    assert "purge_firm_memory" not in tools
    # The package's lazy ``__getattr__`` is not evaluated, so the module is
    # named in full but does not claim a proven surface.
    assert {tool.extraction_confidence for tool in tools.values()} == {"medium"}
    assert all(
        "shadowed_tool_definition" in tool.extraction["surface_gaps"] for tool in tools.values()
    )
    memory = tools["remember_firm_finding"]
    assert memory.source_ref == "memory_bank.py"
    assert memory.input_schema["required"] == ["crd", "category", "fact"]
    (resolution,) = memory.extraction["import_resolutions"]
    assert resolution["reference"] == "memory_bank.remember_firm_finding"
    assert resolution["definition"] == "memory_bank.py:1"
    assert [step["path"] for step in resolution["steps"]] == [
        "agent.py",
        "__init__.py",
        "memory_bank.py",
    ]


def test_adk_proven_import_chain_can_reach_a_proven_surface(tmp_path):
    _write(
        tmp_path,
        {
            "tools.py": (
                "def lookup(query: str) -> str:\n"
                '    """Look up one record."""\n'
                "    return query\n"
            ),
            "agent.py": (
                "from google.adk.agents import Agent\n"
                "from tools import lookup\n\n"
                'root_agent = Agent(name="assistant", tools=[lookup])\n'
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path)

    assert artifacts is not None and artifacts.warnings == []
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert tool.extraction["surface_gaps"] == []
    assert tool.extraction_confidence == "high"


def test_adk_function_tool_wrappers_and_aliases_keep_one_identity(tmp_path):
    _write(
        tmp_path,
        {
            "tools.py": (
                "def lookup(query: str) -> str:\n    return query\n\n\n"
                "def approve(request_id: str) -> str:\n    return request_id\n"
            ),
            "agent.py": (
                "from google.adk.agents import Agent\n"
                "from google.adk.tools import FunctionTool, LongRunningFunctionTool\n"
                "from tools import lookup, approve as approve_request\n"
                "import tools as t\n\n"
                "lookup_tool = FunctionTool(func=t.lookup)\n"
                "root_agent = Agent(\n"
                '    name="assistant",\n'
                "    tools=[lookup, FunctionTool(lookup), lookup_tool, t.lookup,\n"
                "           LongRunningFunctionTool(approve_request)],\n"
                ")\n"
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path)

    assert artifacts is not None and artifacts.warnings == []
    tools = [tool for source in loaded for tool in source.tools]
    assert sorted(tool.name for tool in tools) == ["approve", "lookup"]
    by_name = {tool.name: tool for tool in tools}
    assert by_name["approve"].annotations["long_running"] is True
    assert by_name["lookup"].annotations["long_running"] is False
    assert _edges(loaded, artifacts) == [
        ("assistant", "approve", "tools.py:5"),
        ("assistant", "lookup", "tools.py:1"),
    ]


def test_adk_wrapper_built_in_the_imported_module_is_its_function(tmp_path):
    _write(
        tmp_path,
        {
            "approvals.py": (
                "from google.adk.tools import LongRunningFunctionTool\n\n\n"
                "def approve(request_id: str) -> str:\n    return request_id\n\n\n"
                "approval_tool = LongRunningFunctionTool(func=approve)\n"
            ),
            "agent.py": (
                "from google.adk.agents import Agent\n"
                "from approvals import approval_tool\n\n"
                'root_agent = Agent(name="assistant", tools=[approval_tool])\n'
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path)

    assert artifacts is not None and artifacts.warnings == []
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert (tool.name, tool.source_location) == ("approve", "approvals.py:4")
    assert tool.annotations["long_running"] is True
    (resolution,) = tool.extraction["import_resolutions"]
    assert [step["binding"] for step in resolution["steps"]] == ["import", "value", "definition"]


def test_adk_same_name_functions_in_different_modules_stay_distinct(tmp_path):
    _write(
        tmp_path,
        {
            "billing.py": "def lookup(invoice_id: str) -> str:\n    return invoice_id\n",
            "support.py": "\n\ndef lookup(ticket_id: str) -> str:\n    return ticket_id\n",
            "agent.py": (
                "from google.adk.agents import Agent\n"
                "from billing import lookup as billing_lookup\n"
                "import support\n\n"
                'billing_agent = Agent(name="billing", tools=[billing_lookup])\n'
                'support_agent = Agent(name="support", tools=[support.lookup])\n'
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path)

    assert artifacts is not None and artifacts.warnings == []
    assert _edges(loaded, artifacts) == [
        ("billing", "lookup", "billing.py:1"),
        ("support", "lookup", "support.py:3"),
    ]


def test_adk_one_agent_binding_two_same_named_definitions_is_named(tmp_path):
    _write(
        tmp_path,
        {
            "other.py": "def lookup(key: str) -> str:\n    return key\n",
            "agent.py": (
                "from google.adk.agents import Agent\n"
                "import other\n\n\n"
                "def lookup(query: str) -> str:\n    return query\n\n\n"
                'root_agent = Agent(name="assistant", tools=[lookup, other.lookup])\n'
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path)

    assert artifacts is not None
    assert any(
        "binds two different functions named 'lookup'" in warning
        for warning in artifacts.warnings
    )
    tools = [tool for source in loaded for tool in source.tools]
    assert all(tool.extraction_confidence == "medium" for tool in tools)
    assert all("duplicate_tool_name" in tool.extraction["surface_gaps"] for tool in tools)


def test_adk_unresolved_import_is_named_with_its_reason(tmp_path):
    _write(
        tmp_path,
        {
            "agent.py": (
                "from google.adk.agents import Agent\n"
                "from vendor_tools import search\n\n\n"
                "def lookup(query: str) -> str:\n    return query\n\n\n"
                'root_agent = Agent(name="assistant", tools=[lookup, search])\n'
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path)

    assert artifacts is not None
    # The warning keeps the wording every consumer decodes …
    assert unresolved_adk_tool_symbols(artifacts.warnings) == [("assistant", "search")]
    # … and the reason travels beside it.
    (record,) = artifacts.unresolved_references
    assert record["agent_name"] == "assistant"
    assert record["reference"] == "search"
    assert record["reason"] == "module_not_found"
    assert "vendor_tools" in record["detail"]
    # The local tool is still read, and cannot claim a proven surface.
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert tool.name == "lookup"
    assert tool.extraction_confidence == "medium"
    assert "unresolved_tool_reference" in tool.extraction["surface_gaps"]


def test_adk_import_leaving_the_read_scope_is_not_followed(tmp_path):
    _write(
        tmp_path,
        {
            "shared/tools.py": "def escalate(ticket: str) -> str:\n    return ticket\n",
            "service/agent.py": (
                "from google.adk.agents import Agent\n"
                "from ..shared.tools import escalate\n\n"
                'root_agent = Agent(name="assistant", tools=[escalate])\n'
            ),
        },
    )
    loaded, artifacts = _adk(tmp_path / "service")

    assert artifacts is not None
    assert [tool for source in loaded for tool in source.tools] == []
    (record,) = artifacts.unresolved_references
    assert record["reason"] == "outside_scope"
    assert unresolved_adk_tool_symbols(artifacts.warnings) == [("assistant", "escalate")]


# -- OpenAI Agents SDK --------------------------------------------------------


EMPOWER = {
    "agents/checkout_agent.py": (
        "from agents import Agent\n\n"
        "from ..tools.shop import add_to_cart, purchase\n"
        "from ..tools import shop\n\n"
        'checkout_agent = Agent(name="checkout_agent", tools=[add_to_cart, purchase, shop.view_cart])\n'
    ),
    "tools/shop/__init__.py": (
        "from .cart import add_to_cart, view_cart\nfrom .checkout import purchase\n"
    ),
    "tools/shop/cart.py": (
        "from agents import function_tool\n\n\n"
        "@function_tool\n"
        "async def add_to_cart(product_ids: list[int]) -> str:\n"
        '    """Add products to the cart."""\n'
        '    return ""\n\n\n'
        "@function_tool\n"
        "def view_cart() -> str:\n"
        '    return ""\n'
    ),
    "tools/shop/checkout.py": (
        "from agents import function_tool as ft\n\n\n"
        '@ft(name_override="place_order")\n'
        "def purchase(confirm: bool) -> str:\n"
        '    return ""\n'
    ),
}


def _sdk(root: Path, path: str):
    return load_openai_sdk_static_tools(
        ToolSourceConfig(id=f"sdk:{path}", type="openai_agents_sdk", path=path), None, root
    )


def test_sdk_binds_function_tools_imported_through_a_package(tmp_path):
    _write(tmp_path, EMPOWER)
    loaded = _sdk(tmp_path, "agents/checkout_agent.py")

    assert loaded.warnings == []
    (observation,) = loaded.binding_observations
    assert observation.tools_complete is True
    assert observation.tool_names == ["add_to_cart", "place_order", "view_cart"]
    assert _edges([loaded]) == [
        ("checkout_agent", "add_to_cart", "tools/shop/cart.py:5"),
        ("checkout_agent", "place_order", "tools/shop/checkout.py:5"),
        ("checkout_agent", "view_cart", "tools/shop/cart.py:11"),
    ]
    # Reader-owned guard evidence covers the imported definitions too.
    assert sorted(guard.tool_name for guard in loaded.guard_dependencies) == [
        "add_to_cart",
        "place_order",
        "view_cart",
    ]


def test_sdk_imported_function_without_the_decorator_is_named(tmp_path):
    _write(
        tmp_path,
        {
            "helpers.py": "def plain(query: str) -> str:\n    return query\n",
            "agent.py": (
                "from agents import Agent\nfrom helpers import plain\n\n"
                'agent = Agent(name="assistant", tools=[plain])\n'
            ),
        },
    )
    loaded = _sdk(tmp_path, "agent.py")

    (observation,) = loaded.binding_observations
    assert observation.tools_complete is False
    (issue,) = observation.issues
    assert "binds unresolved tool 'plain'" in issue
    assert "not decorated with the SDK's @function_tool" in issue
    assert loaded.tools == []


def test_sdk_directory_source_reuses_the_definition_it_already_read(tmp_path):
    _write(
        tmp_path,
        {
            "pkg/tools.py": (
                "from agents import function_tool\n\n\n"
                "@function_tool\ndef lookup(query: str) -> str:\n    return query\n"
            ),
            "pkg/root.py": (
                "from agents import Agent\nfrom tools import lookup\nfrom pkg import tools\n\n"
                'first = Agent(name="first", tools=[lookup])\n'
                'second = Agent(name="second", tools=[tools.lookup])\n'
            ),
        },
    )
    loaded = _sdk(tmp_path, "pkg")

    assert loaded.warnings == []
    assert [tool.name for tool in loaded.tools] == ["lookup"]
    assert _edges([loaded]) == [
        ("first", "lookup", "pkg/tools.py:5"),
        ("second", "lookup", "pkg/tools.py:5"),
    ]


# -- diff --application ---------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.excludesFile=/dev/null", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(root: Path, files: dict[str, str | None]) -> str:
    _write(root, files)
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-qm",
        "fixture",
    )
    return _git(root, "rev-parse", "HEAD")


def _compare(root: Path, base: str, head: str, *args: str) -> dict:
    result = CliRunner().invoke(
        app,
        [
            "diff",
            "--application",
            "--workspace",
            str(root),
            "--base",
            base,
            "--head",
            head,
            "--json",
            *args,
        ],
    )
    assert result.exit_code == 0, result.output + repr(result.exception)
    return json.loads(result.output)


def test_application_diff_shows_the_two_memory_tool_additions(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _attest(head=False))
    head = _commit(tmp_path, _attest(head=True))
    result = _compare(tmp_path, base, head, "--scope", "agents/attest_orchestrator")

    assert result["comparison_status"] == "compared"
    assert result["base"]["binding_count"] == 4
    assert result["head"]["binding_count"] == 6
    rows = [(row["agent"], row["tool"], row["change"]) for row in result["rows"]]
    # The four unchanged bindings, scorer.score_answer included, are not
    # relabelled as additions.
    assert rows == [
        ("attest_orchestrator", "recall_firm_memory", "added"),
        ("attest_orchestrator", "remember_firm_finding", "added"),
    ]
    after = result["rows"][1]["after"]
    assert after["definition"]["source"] == "agents/attest_orchestrator/memory_bank.py"
    assert after["definition"]["line"] == 1
    assert after["binding_location"] == "agents/attest_orchestrator/agent.py:21"
    (path,) = after["import_path"]
    assert path["steps"][0]["path"] == "agents/attest_orchestrator/agent.py"
    assert path["definition"] == "agents/attest_orchestrator/memory_bank.py:1"


def test_application_diff_reads_sdk_tools_imported_from_a_sibling_module(tmp_path):
    source = '''from agents import function_tool
@function_tool
def lookup(query: str) -> str:
    return query
@function_tool
def execute(code: str) -> str:
    return code
'''
    agent = 'from agents import Agent\nfrom tools import lookup, execute\nagent = Agent(name="app", tools=TOOLS)\n'
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(
        tmp_path, {"tools.py": source, "agent.py": agent.replace("TOOLS", "[lookup]")}
    )
    head = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[lookup, execute]")})
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "compared"
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("agent", "execute", "added")
    ]
    assert result["rows"][0]["after"]["definition"]["source"] == "tools.py"


def test_application_diff_scopes_an_unresolved_import_to_its_agent(tmp_path):
    agent = (
        "from google.adk.agents import Agent\n"
        "from vendor_tools import search\n\n\n"
        "def lookup(query: str) -> str:\n    return query\n\n\n"
        "def escalate(ticket: str) -> str:\n    return ticket\n\n\n"
        'researcher = Agent(name="researcher", tools=[search])\n'
        'support = Agent(name="support", tools=TOOLS)\n'
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[lookup]")})
    head = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[lookup, escalate]")})
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "partial"
    (row,) = result["rows"]
    # The other agent's addition is established, not swallowed by a file-wide gap.
    assert (row["agent"], row["tool"], row["change"]) == ("support", "escalate", "added")
    (gap,) = [gap for gap in result["head"]["coverage_gaps"] if gap["agent"] == "researcher"]
    assert "references unresolved tool 'search'" in gap["reason"]
    assert "vendor_tools" in gap["reason"]


def test_application_diff_names_an_unresolved_wrapper_once(tmp_path):
    agent = (
        "from google.adk.agents import Agent\n"
        "from google.adk.tools import FunctionTool\n"
        "from vendor_tools import search\n\n\n"
        "def lookup(query: str) -> str:\n    return query\n\n\n"
        'researcher = Agent(name="researcher", tools=[FunctionTool(func=search)])\n'
        'support = Agent(name="support", tools=TOOLS)\n'
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[]")})
    head = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[lookup]")})
    result = _compare(tmp_path, base, head)

    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("support", "lookup", "added")
    ]
    (gap,) = [gap for gap in result["head"]["coverage_gaps"] if gap["agent"] == "researcher"]
    assert "wraps a tool whose function 'search'" in gap["reason"]
    assert gap["reason"].count("vendor_tools") == 1
