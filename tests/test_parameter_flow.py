"""#874: a tool list an agent-building function receives from its caller.

``def create_x_agent(tools): return LlmAgent(tools=tools)`` names no tool; the
list is chosen where the builder is called. Each case read "uses a dynamic
tools expression" and produced no row before the fix. Anything the flow cannot
establish must stay a named limit, never an empty or complete list.
"""

from __future__ import annotations

import pytest
from test_application_diff import commit, run
from test_application_diff import repo as repo

ADK_TOOLS = '''def create_tools(user_id):
    async def get_calendar_events(days: int) -> str:
        """Read the user's calendar."""
        return str(days)

    async def check_emails(query: str) -> str:
        """Read the user's inbox."""
        return query

    return {
        "calendar": [get_calendar_events],
        "email": [check_emails],
    }
'''

ADK_BUILDER = '''from google.adk.agents import LlmAgent


def create_secretary_agent(tools, model):
    return LlmAgent(name="SecretaryAgent", model=model, tools=tools)
'''

ADK_ROOT = '''from google.adk.agents import LlmAgent


def create_root_agent(sub_agents, model):
    return LlmAgent(name="RootAgent", model=model, sub_agents=sub_agents)
'''

ADK_SERVICE = '''from google.adk.runners import InMemoryRunner

from app.agents.root import create_root_agent
from app.agents.secretary import create_secretary_agent
from app.tools import create_tools


class Service:
    def run(self, user_id):
        tool_groups = create_tools(user_id=user_id)
        secretary = create_secretary_agent(tools=TOOLS, model="m")
        root = create_root_agent(sub_agents=[secretary], model="m")
        return InMemoryRunner(agent=root)
'''


def _rows(result):
    return [(r["agent"], r["tool"], r["change"]) for r in result["rows"]]


def _adk(tools_expression: str) -> dict[str, str]:
    return {
        "app/__init__.py": "",
        "app/agents/__init__.py": "",
        "app/tools.py": ADK_TOOLS,
        "app/agents/secretary.py": ADK_BUILDER,
        "app/agents/root.py": ADK_ROOT,
        "app/service.py": ADK_SERVICE.replace("TOOLS", tools_expression),
    }


def test_a_builder_parameter_is_read_at_its_one_call_site(repo):
    """The Vesta#58 shape: a factory's dict entries, concatenated at the call."""

    base = commit(repo, _adk('tool_groups["calendar"]'))
    head = commit(repo, _adk('tool_groups["calendar"] + tool_groups["email"]'))
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("SecretaryAgent", "check_emails", "added")]
    after = result["rows"][0]["after"]
    assert after["definition"]["source"] == "app/tools.py"
    assert after["definition"]["line"] == 6
    [flow] = after["parameter_flow"]
    assert flow["call_sites"] == ["app/service.py:11"]
    assert {entry["path"] for entry in flow["inputs"]} >= {
        "app/agents/secretary.py",
        "app/service.py",
        "app/tools.py",
    }


def test_a_sub_agent_list_passed_to_a_builder_is_named(repo):
    base = commit(repo, _adk('tool_groups["calendar"]'))
    result = run(repo, base, base, "--scope", "app")
    head_agents = {agent["name"] for agent in result["head"]["agents"]}
    assert {"SecretaryAgent", "RootAgent"} <= head_agents
    assert result["head"]["binding_count"] >= 2
    assert not result["head"]["limits"]


def test_a_sdk_builder_parameter_is_read_at_its_one_call_site(repo):
    tools = (
        "from agents import function_tool\n\n\n@function_tool\ndef quote(item: str) -> str:\n"
        "    return item\n\n\n@function_tool\ndef send_image(name: str) -> str:\n    return name\n"
    )
    builder = (
        "from agents import Agent\n\n\n"
        "def build_quote_agent(tools):\n    return Agent(name='Quote', tools=tools)\n"
    )
    wiring = (
        "from builder import build_quote_agent\nfrom tools import quote, send_image\n\n"
        "QUOTE_TOOLS = LIST\nagent = build_quote_agent(QUOTE_TOOLS)\n"
    )
    base = commit(
        repo,
        {"tools.py": tools, "builder.py": builder, "wiring.py": wiring.replace("LIST", "[quote]")},
    )
    head = commit(repo, {"wiring.py": wiring.replace("LIST", "[quote, send_image]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("Quote", "send_image", "added")]
    assert result["rows"][0]["after"]["parameter_flow"][0]["call_sites"] == ["wiring.py:5"]


SDK_TOOLS = (
    "from agents import function_tool\n\n\n@function_tool\ndef quote(item: str) -> str:\n"
    "    return item\n\n\n@function_tool\ndef send_image(name: str) -> str:\n    return name\n"
)
SDK_BUILDER = (
    "from agents import Agent\nfrom tools import quote, send_image\n\n\n"
    "def build(tools):\n    return Agent(name='Quote', tools=tools)\n"
)


@pytest.mark.parametrize(
    ("wiring", "reason"),
    [
        (
            "from builder import build\nfrom tools import quote, send_image\n\n"
            "a = build([quote])\nb = build([send_image])\n",
            "is called at 2 sites",
        ),
        ("from builder import build\n\nbuilder_function = build\n", "referenced other than by a direct call"),
        ("import builder\n\nmake = getattr(builder, 'build')\n", "named in a string"),
        ("x = 1\n", "no call of 'build'"),
        (
            "from builder import build\nfrom elsewhere import load_tools\n\na = build(load_tools())\n",
            "no file inside the read scope provides",
        ),
        (
            "from builder import build\nfrom tools import quote\n\nkwargs = {'tools': [quote]}\na = build(**kwargs)\n",
            "passes arguments through * or **",
        ),
    ],
    ids=["two-call-sites", "value-reference", "string-reference", "no-call-site", "unresolved-argument", "unpacked"],
)
def test_what_the_flow_cannot_establish_is_a_named_limit(repo, wiring, reason):
    base = commit(repo, {"tools.py": SDK_TOOLS, "builder.py": SDK_BUILDER, "wiring.py": "x = 0\n"})
    head = commit(repo, {"wiring.py": wiring})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert result["rows"] == []
    assert any(
        "dynamic tools expression" in limit and reason in limit
        for limit in result["head"]["limits"]
    ), result["head"]["limits"]


def test_a_decorated_builder_is_a_named_limit(repo):
    builder = SDK_BUILDER.replace(
        "def build(tools):", "import functools\n\n\n@functools.lru_cache\ndef build(tools):"
    )
    base = commit(repo, {"tools.py": SDK_TOOLS, "builder.py": builder, "wiring.py": "x = 0\n"})
    head = commit(
        repo,
        {"wiring.py": "from builder import build\nfrom tools import quote\n\na = build((quote,))\n"},
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("is decorated" in limit for limit in result["head"]["limits"])


def test_a_builder_that_changes_its_parameter_is_a_named_limit(repo):
    builder = SDK_BUILDER.replace(
        "    return Agent(", "    tools.append(send_image)\n    return Agent("
    )
    wiring = "from builder import build\nfrom tools import quote\n\na = build([quote])\n"
    base = commit(repo, {"tools.py": SDK_TOOLS, "builder.py": builder, "wiring.py": wiring})
    head = commit(repo, {"wiring.py": wiring + "# touched\n"})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("can be changed at builder.py" in limit for limit in result["head"]["limits"])


def test_a_default_or_fallback_list_is_read_by_truthiness(repo):
    builder = (
        "from agents import Agent\nfrom tools import quote, send_image\n\n\n"
        "def build(tools=None):\n    return Agent(name='Quote', tools=tools or [quote])\n"
    )
    base = commit(
        repo,
        {"tools.py": SDK_TOOLS, "builder.py": builder, "wiring.py": "from builder import build\n\na = build()\n"},
    )
    head = commit(
        repo,
        {"wiring.py": "from builder import build\nfrom tools import send_image\n\na = build(tools=[send_image])\n"},
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("Quote", "quote", "removed"), ("Quote", "send_image", "added")]


def test_a_test_files_call_is_not_a_call_site(repo):
    wiring = "from builder import build\nfrom tools import quote\n\na = build([quote])\n"
    test = "from builder import build\nfrom tools import send_image\n\nfake = build([send_image])\n"
    base = commit(repo, {"tools.py": SDK_TOOLS, "builder.py": SDK_BUILDER, "wiring.py": wiring})
    head = commit(repo, {"tests/test_wiring.py": test})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert result["rows"] == []
    assert result["head"]["binding_count"] == 1


def test_recursion_and_long_chains_stop_with_a_name(repo):
    chain = "from agents import Agent\nfrom tools import quote\n\n\n"
    for index in range(6):
        chain += f"def build_{index}(tools):\n    return build_{index + 1}(tools)\n\n\n"
    chain += "def build_6(tools):\n    return Agent(name='Deep', tools=tools)\n\n\nagent = build_0([quote])\n"
    recursive = (
        "from agents import Agent\nfrom tools import quote\n\n\n"
        "def build(tools, depth=0):\n    if depth:\n        return build(tools, depth - 1)\n"
        "    return Agent(name='Loop', tools=tools)\n\n\nagent = build([quote], 2)\n"
    )
    base = commit(repo, {"tools.py": SDK_TOOLS, "chain.py": chain, "loop.py": recursive})
    result = run(repo, base, base)
    assert result["comparison_status"] == "partial"
    limits = " ".join(result["head"]["limits"])
    assert "Deep" in limits and "Loop" in limits


def test_the_answer_is_deterministic(repo):
    base = commit(repo, _adk('tool_groups["calendar"]'))
    head = commit(repo, _adk('tool_groups["calendar"] + tool_groups["email"]'))
    first = run(repo, base, head, "--scope", "app")
    second = run(repo, base, head, "--scope", "app")
    assert first["rows"] == second["rows"]
    assert first["comparison_id"] == second["comparison_id"]


def test_a_dict_key_written_twice_keeps_its_last_value(repo):
    agents = (
        "from agents import Agent\nfrom tools import quote, send_image\n\n"
        "GROUPS = {'q': [quote], 'q': LAST}\nagent = Agent(name='Quote', tools=GROUPS['q'])\n"
    )
    base = commit(repo, {"tools.py": SDK_TOOLS, "agents_def.py": agents.replace("LAST", "[quote]")})
    head = commit(repo, {"agents_def.py": agents.replace("LAST", "[quote, send_image]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("agent", "send_image", "added")]


def test_a_factory_reads_its_parameters_at_the_call_it_was_reached_through(repo):
    agents = (
        "from agents import Agent\nfrom tools import quote, send_image\n\n\n"
        "def groups(extra):\n    return {'q': [quote, *extra]}\n\n\n"
        "one = Agent(name='One', tools=groups([])['q'])\n"
        "two = Agent(name='Two', tools=groups(EXTRA)['q'])\n"
    )
    base = commit(repo, {"tools.py": SDK_TOOLS, "agents_def.py": agents.replace("EXTRA", "[]")})
    head = commit(repo, {"agents_def.py": agents.replace("EXTRA", "[send_image]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("two", "send_image", "added")]
