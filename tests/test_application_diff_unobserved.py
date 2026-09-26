"""#876: an agent the reader cannot see must never let a comparison read as complete.

Each case here was a silent miss before the fix: the SDK reader only saw
``name = Agent(...)``, so an agent built any other way produced no observation
and no limit, and a test double's agent was enough to establish the scope.
"""

import ast

import pytest
from test_application_diff import commit, run
from test_application_diff import repo as repo

TOOLS = '''from agents import function_tool
@function_tool
def quote(item: str) -> str:
    return item
@function_tool
def send_image(name: str) -> dict:
    return {"name": name}
'''


def _agents(body: str) -> str:
    return TOOLS + "from agents import Agent\n" + body


def _pairs(result):
    return [(r["agent"], r["tool"], r["change"]) for r in result["rows"]]


@pytest.mark.parametrize(
    ("body", "agent"),
    [
        (
            'def build():\n    return Agent(name="Quote", instructions="q", tools=TOOLS)\n',
            "Quote",
        ),
        (
            'class Holder:\n    def __init__(self):\n'
            '        self.agent = Agent(name="Held", instructions="h", tools=TOOLS)\n',
            "Held",
        ),
        ('AGENTS = [Agent(name="Listed", instructions="l", tools=TOOLS)]\n', "Listed"),
        ('typed = Agent[dict](name="Typed", instructions="t", tools=TOOLS)\n', "typed"),
        ('def build():\n    return Agent("Positional", tools=TOOLS)\n', "Positional"),
    ],
)
def test_every_agent_construction_is_observed(repo, body, agent):
    base = commit(repo, {"agent.py": _agents(body.replace("TOOLS", "[quote]"))})
    head = commit(repo, {"agent.py": _agents(body.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [(agent, "send_image", "added")]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            'def build(name):\n    return Agent(name=name, instructions="q", tools=[quote])\n',
            "has no literal name",
        ),
        (
            'class Custom(Agent):\n    pass\nhelper = Custom(name="c", tools=[quote])\n',
            "built from the subclass 'Custom'",
        ),
        (
            'base = Agent(name="base", tools=[quote])\n'
            'copy = base.clone(tools=[quote, send_image])\n',
            "agent copy at agent.py:",
        ),
        (
            "from shared import base_agent\n"
            "copy = base_agent.clone(tools=[quote, send_image])\n",
            "agent copy at agent.py:",
        ),
    ],
)
def test_an_agent_the_reader_cannot_identify_is_a_named_limit(repo, body, reason):
    base = commit(repo, {"agent.py": _agents(body)})
    head = commit(repo, {"agent.py": _agents(body) + "# touched\n"})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any(reason in limit for limit in result["head"]["limits"])
    assert all(g["source"] == "agent.py" for g in result["head"]["coverage_gaps"])


def test_a_test_double_does_not_establish_the_application(repo):
    """The kkmiecik-coder/CRM#5 shape: the real agent is built by a function."""

    real = 'def build():\n    return Agent(name="Wycena", instructions="w", tools=TOOLS)\n'
    double = (
        "from agents import Agent, function_tool\n@function_tool\n"
        "def fake(q: str) -> str:\n    return q\n"
        'agent = Agent(name="double", tools=[fake])\n'
    )
    base = commit(
        repo,
        {
            "bots/agents.py": _agents(real.replace("TOOLS", "[quote]")),
            "bots/tests/test_turn.py": double,
        },
    )
    head = commit(repo, {"bots/agents.py": _agents(real.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert _pairs(result) == [("Wycena", "send_image", "added")]
    for side in ("base", "head"):
        assert [a["name"] for a in result[side]["agents"]] == ["Wycena"]
        assert result[side]["excluded_tests"] == ["bots/tests/test_turn.py"]


def test_a_test_file_with_a_duplicate_tool_does_not_refuse_the_comparison(repo):
    duplicated = (
        "from agents import Agent, function_tool\n"
        "@function_tool\ndef _tool(q: str) -> str:\n    return q\n"
        "@function_tool\ndef _tool(q: str) -> str:\n    return q + q\n"
        'agent = Agent(name="x", tools=[_tool])\n'
    )
    body = 'assistant = Agent(name="assistant", tools=TOOLS)\n'
    base = commit(
        repo,
        {
            "agent.py": _agents(body.replace("TOOLS", "[quote]")),
            "tests/unit/test_executor.py": duplicated,
        },
    )
    head = commit(repo, {"agent.py": _agents(body.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("assistant", "send_image", "added")]


def test_a_duplicate_tool_in_application_code_limits_only_that_file(repo):
    duplicated = (
        "from agents import Agent, function_tool\n"
        "@function_tool\ndef dup(q: str) -> str:\n    return q\n"
        "@function_tool\ndef dup(q: str) -> str:\n    return q + q\n"
        'other = Agent(name="other", tools=[dup])\n'
    )
    body = 'assistant = Agent(name="assistant", tools=TOOLS)\n'
    base = commit(
        repo, {"agent.py": _agents(body.replace("TOOLS", "[quote]")), "other.py": duplicated}
    )
    head = commit(repo, {"agent.py": _agents(body.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert _pairs(result) == [("assistant", "send_image", "added")]
    assert any(
        g["source"] == "other.py" and "defines the tool 'dup' more than once" in g["reason"]
        for g in result["head"]["coverage_gaps"]
    )


def test_a_livekit_agent_subclass_is_not_the_sdk(repo):
    livekit = (
        "from livekit.agents import Agent\n"
        "class Focus(Agent):\n    pass\n"
    )
    base = commit(repo, {"agent.py": livekit})
    head = commit(repo, {"agent.py": livekit + "# touched\n"})
    result = run(repo, base, head)
    assert not any("subclass" in limit for limit in result["head"]["limits"])


# The invariant behind #876, checked over every fixture shape above: a result
# may say ``compared`` only when every ``Agent(...)`` construction in the
# scope's non-test SDK files is either an observed agent or a named limit.
_SHAPES = [
    'def build():\n    return Agent(name="Quote", tools=[quote])\n',
    'class H:\n    def __init__(self):\n        self.a = Agent(name="Held", tools=[quote])\n',
    'AGENTS = [Agent(name="Listed", tools=[quote])]\n',
    'typed = Agent[dict](name="Typed", tools=[quote])\n',
    'def build(n):\n    return Agent(name=n, tools=[quote])\n',
    'assistant = Agent(name="assistant", tools=[quote])\n',
]


@pytest.mark.parametrize("body", _SHAPES)
def test_compared_never_leaves_a_construction_site_unaccounted(repo, body):
    source = _agents(body)
    base = commit(repo, {"agent.py": source})
    head = commit(repo, {"agent.py": source + "# touched\n"})
    result = run(repo, base, head)
    sites = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "Agent")
            or (
                isinstance(node.func, ast.Subscript)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "Agent"
            )
        )
    ]
    observed = {
        int(a["location"].rsplit(":", 1)[1])
        for a in result["head"]["agents"]
        if a["location"] and ":" in a["location"]
    }
    limited = {
        line
        for line in sites
        for limit in result["head"]["limits"]
        if f"agent.py:{line}" in limit
    }
    unaccounted = [line for line in sites if line not in observed | limited]
    if result["comparison_status"] == "compared":
        assert unaccounted == []
    else:
        assert unaccounted == [] or result["head"]["limits"]


# ---------------------------------------------------------------------------
# #876 review: paths that still read `compared` with no rows, or limits that
# downgraded rows they had nothing to do with.


def _compare(repo, before: str, after: str, path: str = "agent.py"):
    base = commit(repo, {path: _agents(before)})
    head = commit(repo, {path: _agents(after)})
    return run(repo, base, head)


def test_one_identity_constructed_twice_is_not_merged(repo):
    body = (
        "def build(premium):\n"
        "    if premium:\n"
        '        return Agent(name="Quote", tools=PREMIUM)\n'
        '    return Agent(name="Quote", tools=FREE)\n'
    )
    result = _compare(
        repo,
        body.replace("PREMIUM", "[send_image]").replace("FREE", "[quote]"),
        body.replace("PREMIUM", "[quote]").replace("FREE", "[send_image]"),
    )
    assert result["comparison_status"] == "partial"
    assert any("constructed more than once" in limit for limit in result["head"]["limits"])


def test_a_literal_name_equal_to_a_variable_name_is_not_merged(repo):
    body = (
        'assistant = Agent(name="Helper", tools=HELPER)\n'
        'def build():\n    return Agent(name="assistant", tools=OTHER)\n'
    )
    result = _compare(
        repo,
        body.replace("HELPER", "[send_image]").replace("OTHER", "[quote]"),
        body.replace("HELPER", "[quote]").replace("OTHER", "[quote, send_image]"),
    )
    assert result["comparison_status"] == "partial"
    assert all(row["change"] == "not_established" for row in result["rows"])


@pytest.mark.parametrize(
    "construction",
    [
        'COMMON = {"tools": TOOLS}\ndef build():\n    return Agent(name="Quote", **COMMON)\n',
        'COMMON = {"tools": TOOLS}\nquote_agent = Agent(name="Quote", **COMMON)\n',
        'def build():\n    return Agent("Quote", "desc", TOOLS)\n',
    ],
)
def test_opaque_arguments_are_a_named_limit(repo, construction):
    result = _compare(
        repo,
        construction.replace("TOOLS", "[quote]"),
        construction.replace("TOOLS", "[quote, send_image]"),
    )
    assert result["comparison_status"] == "partial"
    assert any("so its tools and handoffs are not read" in limit for limit in result["head"]["limits"])


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            'QUOTE = [quote]\nquote_agent = Agent(name="Quote", tools=QUOTE)\n',
            'QUOTE = [quote]\nQUOTE.append(send_image)\nquote_agent = Agent(name="Quote", tools=QUOTE)\n',
        ),
        (
            'QUOTE = [quote]\ndef build():\n    return Agent(name="Quote", tools=QUOTE)\n',
            'QUOTE = [quote]\nQUOTE += [send_image]\ndef build():\n    return Agent(name="Quote", tools=QUOTE)\n',
        ),
        (
            'import os\nQUOTE = [quote]\ndef build():\n    return Agent(name="Quote", tools=QUOTE)\n',
            'import os\nif os.environ.get("X"):\n    QUOTE = [quote, send_image]\nelse:\n    QUOTE = [quote]\n'
            'def build():\n    return Agent(name="Quote", tools=QUOTE)\n',
        ),
    ],
)
def test_a_list_changed_or_bound_twice_is_dynamic(repo, before, after):
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "partial"
    assert result["rows"] == [] or all(row["change"] == "not_established" for row in result["rows"])


def test_each_builder_reads_its_own_local_list(repo):
    body = (
        'def build_a():\n    tools = [quote]\n    return Agent(name="A", tools=tools)\n'
        'def build_b():\n    tools = B_TOOLS\n    return Agent(name="B", tools=tools)\n'
    )
    result = _compare(
        repo,
        body.replace("B_TOOLS", "[quote]"),
        body.replace("B_TOOLS", "[quote, send_image]"),
    )
    assert _pairs(result) == [("B", "send_image", "added")]


@pytest.mark.parametrize(
    "mutation",
    [
        "quote_agent.tools.append(send_image)\n",
        "quote_agent.tools = [quote, send_image]\n",
    ],
)
def test_tools_changed_after_construction_is_a_named_limit(repo, mutation):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n'
    result = _compare(repo, body, body + mutation)
    assert result["comparison_status"] == "partial"
    assert any("changed after construction" in limit for limit in result["head"]["limits"])


def test_dataclasses_replace_with_tools_is_a_named_limit(repo):
    body = 'import dataclasses\nquote_agent = Agent(name="Quote", tools=[quote])\n'
    result = _compare(
        repo, body, body + "refund = dataclasses.replace(quote_agent, tools=[send_image])\n"
    )
    assert result["comparison_status"] == "partial"
    assert any("agent copy at agent.py" in limit for limit in result["head"]["limits"])


def test_a_copy_without_its_own_capabilities_is_not_a_limit(repo):
    """The SDK docs' `robot_agent = pirate_agent.clone(name=..., instructions=...)`."""

    body = (
        'pirate_agent = Agent(name="Pirate", tools=TOOLS)\n'
        'robot_agent = pirate_agent.clone(name="Robot", instructions="beep")\n'
    )
    result = _compare(
        repo, body.replace("TOOLS", "[quote]"), body.replace("TOOLS", "[quote, send_image]")
    )
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("pirate_agent", "send_image", "added")]


def test_an_unused_subclass_does_not_downgrade_other_agents(repo):
    body = (
        "class LoggingAgent(Agent):\n    pass\n"
        'triage = Agent(name="triage", tools=TOOLS)\n'
    )
    result = _compare(
        repo, body.replace("TOOLS", "[quote]"), body.replace("TOOLS", "[quote, send_image]")
    )
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("triage", "send_image", "added")]


def test_an_instantiated_subclass_limits_only_its_own_agent(repo):
    body = (
        "class LoggingAgent(Agent):\n    pass\n"
        'logged = LoggingAgent(name="logged", tools=[quote])\n'
        'triage = Agent(name="triage", tools=TOOLS)\n'
    )
    result = _compare(
        repo, body.replace("TOOLS", "[quote]"), body.replace("TOOLS", "[quote, send_image]")
    )
    assert result["comparison_status"] == "partial"
    assert _pairs(result) == [("triage", "send_image", "added")]
    assert {g["agent"] for g in result["head"]["coverage_gaps"]} == {"logged"}


def test_text_output_names_the_test_files_it_did_not_read(repo):
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    body = 'assistant = Agent(name="assistant", tools=TOOLS)\n'
    base = commit(
        repo,
        {
            "agent.py": _agents(body.replace("TOOLS", "[quote]")),
            "tests/test_agent.py": "def test_x():\n    pass\n",
        },
    )
    head = commit(repo, {"agent.py": _agents(body.replace("TOOLS", "[quote, send_image]"))})
    result = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert result.exit_code == 0, result.output
    assert "tests/test_agent.py" in result.output


def test_a_copy_in_a_module_without_the_sdk_import_is_a_named_limit(repo):
    main = 'main_agent = Agent(name="Main", tools=[quote])\n'
    base = commit(
        repo,
        {
            "app/__init__.py": "",
            "app/main.py": _agents(main),
            "app/variants.py": "from app.main import main_agent\n",
        },
    )
    head = commit(
        repo,
        {
            "app/variants.py": (
                "from app.main import main_agent\n"
                "from app.main import send_image\n"
                'refund_agent = main_agent.clone(name="Refund", tools=[send_image])\n'
            )
        },
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("app/variants.py:3" in limit for limit in result["head"]["limits"])


def test_adk_agent_name_constructed_twice_is_not_merged(repo):
    source = (
        "from google.adk.agents import LlmAgent\n\n\n"
        "def submit(q: str) -> str:\n    return q\n\n\n"
        "def submit_orchestrated(q: str) -> str:\n    return q\n\n\n"
        'root_agent = LlmAgent(name="reviewer", model="m", tools=[submit])\n\n\n'
        "def make_agent():\n"
        '    return LlmAgent(name="reviewer", model="m", tools=TOOLS)\n'
    )
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[submit_orchestrated]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("constructed more than once" in limit for limit in result["head"]["limits"])
    assert all(row["change"] == "not_established" for row in result["rows"])


# ---------------------------------------------------------------------------
# #876 review, round 2: a list or an agent changed from another scope, through
# another name or in another module; copies and changes on values that are not
# agents; and identities that were one only by their variable's spelling.


@pytest.mark.parametrize(
    "shape",
    [
        "QUOTE_TOOLS = [quote]\ndef enable():\n    QUOTE_TOOLS.append(send_image)\n",
        "QUOTE_TOOLS = [quote]\ndef enable():\n    global QUOTE_TOOLS\n"
        "    QUOTE_TOOLS = [quote, send_image]\n",
        "QUOTE_TOOLS = []\ndef register(fn):\n    QUOTE_TOOLS.append(fn)\n    return fn\n"
        "register(quote)\n",
        "QUOTE_TOOLS = [quote]\nalias = QUOTE_TOOLS\nalias.append(send_image)\n",
        "def add_images(lst):\n    lst.append(send_image)\n"
        "QUOTE_TOOLS = [quote]\nadd_images(QUOTE_TOOLS)\n",
        "QUOTE_TOOLS = [quote]\nQUOTE_TOOLS[0] = send_image\n",
    ],
    ids=["append-in-a-function", "global-rebinding", "decorator-registry", "alias", "helper", "subscript"],
)
def test_a_module_list_changed_from_anywhere_is_dynamic(repo, shape):
    after = shape + 'quote_agent = Agent(name="Quote", tools=QUOTE_TOOLS)\n'
    result = _compare(repo, 'quote_agent = Agent(name="Quote", tools=[quote])\n', after)
    assert result["comparison_status"] == "partial"
    assert any("dynamic tools expression" in limit for limit in result["head"]["limits"])


@pytest.mark.parametrize(
    "inner",
    [
        "    def extend():\n        nonlocal tools\n        tools = [quote, send_image]\n",
        "    def extend():\n        tools.append(send_image)\n",
    ],
    ids=["nonlocal", "closure"],
)
def test_a_builder_list_changed_by_a_nested_function_is_dynamic(repo, inner):
    after = (
        "def build():\n    tools = [quote]\n" + inner + "    extend()\n"
        '    return Agent(name="Quote", tools=tools)\n'
    )
    result = _compare(
        repo, 'def build():\n    return Agent(name="Quote", tools=[quote])\n', after
    )
    assert result["comparison_status"] == "partial"


def test_handoffs_changed_in_a_function_are_dynamic(repo):
    base = 'sub_a = Agent(name="SubA", tools=[quote])\ntriage = Agent(name="Triage", handoffs=[sub_a])\n'
    head = (
        'sub_a = Agent(name="SubA", tools=[quote])\nsub_b = Agent(name="SubB", tools=[send_image])\n'
        "HANDOFFS = [sub_a]\ndef enable():\n    HANDOFFS.append(sub_b)\n"
        'triage = Agent(name="Triage", handoffs=HANDOFFS)\n'
    )
    result = _compare(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("dynamic handoffs" in limit for limit in result["head"]["limits"])


@pytest.mark.parametrize(
    ("before", "after", "agent"),
    [
        (
            'class Svc:\n    def __init__(self):\n'
            '        self.agent = Agent(name="Held", tools=[quote])\n',
            'class Svc:\n    def __init__(self):\n'
            '        self.agent = Agent(name="Held", tools=[quote])\n'
            "        self.agent.tools.append(send_image)\n",
            "Held",
        ),
        (
            'def build_quote_agent():\n    return Agent(name="Quote", tools=[quote])\n'
            "quote_agent = build_quote_agent()\n",
            'def build_quote_agent():\n    return Agent(name="Quote", tools=[quote])\n'
            "quote_agent = build_quote_agent()\nquote_agent.tools.append(send_image)\n",
            "Quote",
        ),
        (
            'quote_agent = Agent(name="Quote", tools=[quote])\n',
            'quote_agent = Agent(name="Quote", tools=[quote])\nquote_agent.tools[:] = [send_image]\n',
            "quote_agent",
        ),
        (
            'quote_agent = Agent(name="Quote", tools=[quote])\n',
            'quote_agent = Agent(name="Quote", tools=[quote])\n'
            'setattr(quote_agent, "tools", [send_image])\n',
            "quote_agent",
        ),
        (
            'quote_agent = Agent(name="Quote", tools=[quote])\n',
            'quote_agent = Agent(name="Quote", tools=[quote])\n'
            "handle = quote_agent.tools\nhandle.append(send_image)\n",
            "quote_agent",
        ),
    ],
    ids=["self-attribute", "builder-result", "slice-store", "setattr", "handle"],
)
def test_an_agent_changed_after_construction_is_a_limit_on_it(repo, before, after, agent):
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "partial"
    assert any(
        f"agent {agent!r} has its tools, handoffs or MCP servers changed" in limit
        for limit in result["head"]["limits"]
    )


def test_a_change_on_a_value_the_reader_cannot_identify_limits_the_file(repo):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n'
    change = "def extend(agent):\n    agent.tools.append(send_image)\nextend(quote_agent)\n"
    result = _compare(repo, body, body + change)
    assert result["comparison_status"] == "partial"
    assert any("value this reader cannot identify" in limit for limit in result["head"]["limits"])


def test_a_change_in_a_module_without_the_sdk_import_is_a_named_limit(repo):
    agents = 'quote_agent = Agent(name="Quote", tools=[quote])\n'
    base = commit(repo, {"agent.py": _agents(agents), "wiring.py": "import agent\n"})
    head = commit(
        repo,
        {"wiring.py": "import agent\n\nagent.quote_agent.tools.append(agent.send_image)\n"},
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("changed at wiring.py:3" in limit for limit in result["head"]["limits"])


def test_copy_replace_with_tools_is_a_named_limit(repo):
    body = 'import copy\nquote_agent = Agent(name="Quote", tools=[quote])\n'
    result = _compare(repo, body, body + "refund = copy.replace(quote_agent, tools=[send_image])\n")
    assert result["comparison_status"] == "partial"
    assert any("(copy.replace) is not read" in limit for limit in result["head"]["limits"])


def test_a_subclass_used_from_another_module_is_a_named_limit(repo):
    subclass = "from agents import Agent\n\n\nclass BaseAgent(Agent):\n    pass\n"
    agents = 'main_agent = Agent(name="Main", tools=[quote])\n'
    base = commit(repo, {"base_agent.py": subclass, "agent.py": _agents(agents)})
    head = commit(
        repo,
        {
            "agent.py": _agents(
                "from base_agent import BaseAgent\n" + agents
                + 'def build():\n    return BaseAgent(name="Quote", tools=[quote, send_image])\n'
            )
        },
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any(
        "uses BaseAgent, an OpenAI Agents SDK agent subclass defined at base_agent.py:4" in limit
        for limit in result["head"]["limits"]
    )


@pytest.mark.parametrize(
    "noise",
    [
        "from dataclasses import dataclass, replace\n@dataclass\nclass Settings:\n    debug: bool = False\n"
        "SETTINGS = replace(Settings(), **{'debug': True})\n",
        "class Settings:\n    def clone(self, **kw):\n        return self\ns = Settings().clone(handoffs=3)\n",
        'def make_payload():\n    triage = type("P", (), {})()\n    triage.tools = ["x"]\n    return triage\n',
    ],
    ids=["config-replace", "non-agent-clone", "same-named-local"],
)
def test_copies_and_changes_on_values_that_are_not_agents_are_not_limits(repo, noise):
    before = 'triage = Agent(name="Triage", tools=[quote])\n'
    after = 'triage = Agent(name="Triage", tools=[quote, send_image])\n' + noise
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("triage", "send_image", "added")]


def test_two_builders_local_agent_variables_are_two_agents(repo):
    body = (
        'def build_a():\n    agent = Agent(name="A", tools=[quote])\n    return agent\n'
        'def build_b():\n    agent = Agent(name="B", tools=B_TOOLS)\n    return agent\n'
    )
    result = _compare(
        repo, body.replace("B_TOOLS", "[quote]"), body.replace("B_TOOLS", "[quote, send_image]")
    )
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("B", "send_image", "added")]


def test_a_local_handoff_target_is_named_by_its_identity(repo):
    """Two builders' ``sub`` variables are two agents, and each handoff reaches its own."""

    body = (
        'def build_a():\n    sub = Agent(name="SubA", tools=[quote])\n'
        '    return Agent(name="TriageA", handoffs=[sub])\n'
        'def build_b():\n    sub = Agent(name="SubB", tools=B_TOOLS)\n'
        '    return Agent(name="TriageB", handoffs=[sub])\n'
    )
    result = _compare(
        repo, body.replace("B_TOOLS", "[quote]"), body.replace("B_TOOLS", "[quote, send_image]")
    )
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("SubB", "send_image", "added")]


def test_a_rename_or_move_keeps_a_single_local_agents_identity(repo):
    before = 'def build():\n    agent = Agent(name="Support Bot", tools=[quote])\n    return agent\n'
    after = before.replace('"Support Bot"', '"Support Bot v2"')
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "compared"
    assert result["rows"] == []


def test_identical_constructions_of_one_identity_are_one_agent(repo):
    body = (
        "import os\nif os.environ.get('MINI'):\n"
        '    agent = Agent(name="A", model="mini", tools=TOOLS)\n'
        "else:\n"
        '    agent = Agent(name="A", model="big", tools=TOOLS)\n'
    )
    result = _compare(
        repo, body.replace("TOOLS", "[quote]"), body.replace("TOOLS", "[quote, send_image]")
    )
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("agent", "send_image", "added")]


def test_many_module_lists_are_read_in_linear_time(tmp_path):
    import time

    from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
    from agents_shipgate.schemas.manifest import ToolSourceConfig

    def fastest(count: int) -> float:
        lines = [TOOLS, "from agents import Agent\n"]
        for index in range(count):
            lines.append(f"t_{index} = [quote]\na_{index} = Agent(name='A_{index}', tools=t_{index})\n")
        path = tmp_path / f"big_{count}.py"
        path.write_text("".join(lines))
        source = ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=path.name)
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            load_openai_sdk_static_tools(source, None, tmp_path)
            best = min(best, time.perf_counter() - started)
        return best

    small, large = fastest(100), fastest(1000)
    # Ten times the lists may cost about ten times as much, never the hundred
    # times a rescan of the file per list cost (41 s at 1,500 lists).
    assert large < max(small * 40, 1.0)


# ---------------------------------------------------------------------------
# #876 review, round 3: reads that change nothing, uses spelled only in text,
# and identities that must survive a rename.


@pytest.mark.parametrize(
    "helper",
    [
        "def describe(request):\n    tools = request.tools\n    return len(tools)\n",
        "def config_tools(x):\n    return getattr(x, 'tools', None)\n",
    ],
    ids=["handle-only-read", "getattr-read"],
)
def test_reading_another_objects_tools_is_not_a_change(repo, helper):
    before = 'main_agent = Agent(name="Main", tools=[quote])\n' + helper
    after = 'main_agent = Agent(name="Main", tools=[quote, send_image])\n' + helper
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("main_agent", "send_image", "added")]


@pytest.mark.parametrize(
    "module",
    [
        "def handle(req):\n    tools = req.tools\n    return tools\n",
        "from vendor import Payload\n\np = Payload()\np.tools = ['x']\n",
        "def registered(server):\n    return getattr(server, 'tools', None)\n",
    ],
    ids=["request-handle", "library-object-store", "getattr"],
)
def test_another_librarys_tools_in_a_non_sdk_module_are_not_a_limit(repo, module):
    agents = 'main_agent = Agent(name="Main", tools=TOOLS)\n'
    base = commit(repo, {"agent.py": _agents(agents.replace("TOOLS", "[quote]")), "api.py": module})
    head = commit(repo, {"agent.py": _agents(agents.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("main_agent", "send_image", "added")]


@pytest.mark.parametrize(
    ("core", "other"),
    [
        (
            "from agents import Agent\n\n\nclass Assistant(Agent):\n    pass\n",
            "PROMPT = 'You are an Assistant.'\n",
        ),
        (
            "from agents import Agent as SdkAgent\n\n\nclass Agent(SdkAgent):\n    pass\n",
            "from agents import Agent\n\nhelper = Agent(name='Helper')\n",
        ),
    ],
    ids=["word-in-a-string", "wrapper-named-like-the-sdk-class"],
)
def test_a_subclass_is_used_only_where_code_imports_it(repo, core, other):
    agents = 'main_agent = Agent(name="Main", tools=TOOLS)\n'
    base = commit(
        repo,
        {"core.py": core, "other.py": other, "agent.py": _agents(agents.replace("TOOLS", "[quote]"))},
    )
    head = commit(repo, {"agent.py": _agents(agents.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert not any("agent subclass" in limit for limit in result["head"]["limits"])
    assert ("main_agent", "send_image", "added") in _pairs(result)


def test_an_agent_class_made_with_type_is_a_named_limit(repo):
    before = 'main_agent = Agent(name="Main", tools=[quote])\n'
    after = before + 'Dyn = type("Dyn", (Agent,), {})\nd = Dyn(name="D", tools=[quote, send_image])\n'
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "partial"
    assert any("subclass 'Dyn'" in limit for limit in result["head"]["limits"])


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            'def build():\n    agent = Agent(name="Support Bot", tools=[quote])\n    return agent\n',
            'def build():\n    agent = Agent(name="Support Bot v2", tools=[quote])\n    return agent\n',
        ),
        (
            'NAME = "Quote"\ndef build():\n    quote_agent = Agent(name=NAME, tools=[quote])\n    return quote_agent\n',
            'def build():\n    quote_agent = Agent(name="Quote", tools=[quote])\n    return quote_agent\n',
        ),
        (
            'quote_agent = Agent(name="Quote", tools=[quote])\n',
            'def build():\n    quote_agent = Agent(name="Quote", tools=[quote])\n    return quote_agent\n',
        ),
    ],
    ids=["display-rename", "constant-to-literal", "module-to-local"],
)
def test_a_refactor_keeps_a_single_agents_identity(repo, before, after):
    result = _compare(repo, before, after)
    assert result["rows"] == []


def test_a_change_repeated_on_one_agent_is_one_limit(repo):
    body = (
        'class Svc:\n    def __init__(self):\n        self.agent = Agent(name="Held", tools=[quote])\n'
        + "".join(f"        self.agent.tools.append(send_image)  # {index}\n" for index in range(50))
    )
    result = _compare(repo, 'held = Agent(name="Held2", tools=[quote])\n', body)
    changed = [limit for limit in result["head"]["limits"] if "changed after construction" in limit]
    assert len(changed) == 1


# ---------------------------------------------------------------------------
# #876 review, round 4: a module without the SDK import reaching an agent by
# any value, re-exported subclasses, and class bodies as scopes.

WIRING_AGENTS = (
    TOOLS + "from agents import Agent\n"
    'quote_agent = Agent(name="Quote", tools=[quote])\n'
    'other = Agent(name="Other", tools=[quote])\n'
    "def get_agent():\n    return quote_agent\n"
)


@pytest.mark.parametrize(
    "wiring",
    [
        "from app.agents_def import get_agent, send_image\nget_agent().tools.append(send_image)\n",
        "from app.agents_def import quote_agent, send_image\na = quote_agent\na.tools.append(send_image)\n",
        "from app.agents_def import quote_agent, other, send_image\n"
        "for a in (quote_agent, other):\n    a.tools.append(send_image)\n",
        "from app.agents_def import quote_agent, send_image\n"
        "def patch(agent):\n    agent.tools.append(send_image)\npatch(quote_agent)\n",
    ],
    ids=["call-result", "local-alias", "loop", "helper-parameter"],
)
def test_a_module_without_the_sdk_import_reaching_an_agent_is_a_limit(repo, wiring):
    base = commit(repo, {"app/__init__.py": "", "app/agents_def.py": WIRING_AGENTS, "app/wiring.py": "x = 1\n"})
    head = commit(repo, {"app/wiring.py": wiring})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert any("wiring.py" in limit for limit in result["head"]["limits"])


def test_a_scope_below_its_top_package_still_sees_its_own_imports(repo):
    files = {
        "svc/__init__.py": "",
        "svc/app/__init__.py": "",
        "svc/app/agents_def.py": WIRING_AGENTS,
        "svc/app/wiring.py": "x = 1\n",
    }
    base = commit(repo, files)
    head = commit(
        repo,
        {
            "svc/app/wiring.py": "from svc.app.agents_def import quote_agent, send_image\n"
            "quote_agent.tools.append(send_image)\n"
        },
    )
    result = run(repo, base, head, "--scope", "svc/app")
    assert result["comparison_status"] == "partial"


def test_a_model_the_scope_defines_is_not_an_agent(repo):
    schemas = "class Payload:\n    tools = None\n"
    payload = "from app.schemas import Payload\ndef build(specs):\n    p = Payload()\n    p.tools = specs\n    return p\n"
    agents = TOOLS + "from agents import Agent\nmain_agent = Agent(name='Main', tools=TOOLS_LIST)\n"
    base = commit(
        repo,
        {
            "app/__init__.py": "",
            "app/schemas.py": schemas,
            "app/payload.py": payload,
            "app/agents_def.py": agents.replace("TOOLS_LIST", "[quote]"),
        },
    )
    head = commit(repo, {"app/agents_def.py": agents.replace("TOOLS_LIST", "[quote, send_image]")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("main_agent", "send_image", "added")]


def test_a_subclass_reexported_through_a_package_star_import_is_a_limit(repo):
    base = commit(
        repo,
        {
            "app/__init__.py": "",
            "app/core.py": "from agents import Agent\n\n\nclass Assistant(Agent):\n    pass\n",
            "app/agents/__init__.py": "from app.core import *\n",
            "app/main.py": "x = 1\n",
        },
    )
    head = commit(
        repo,
        {"app/main.py": "from app.agents import Assistant\nq = Assistant(name='Q', tools=[])\n"},
    )
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert any("main.py uses Assistant" in limit for limit in result["head"]["limits"])


def test_a_same_stem_vendor_class_is_not_the_scopes_subclass(repo):
    core = "from agents import Agent\n\n\nclass Assistant(Agent):\n    pass\n"
    main = (
        TOOLS + "from agents import Agent\nfrom vendorlib.core import Assistant as VendorAssistant\n"
        "main_agent = Agent(name='Main', tools=TOOLS_LIST)\n"
    )
    base = commit(repo, {"core.py": core, "main.py": main.replace("TOOLS_LIST", "[quote]")})
    head = commit(repo, {"main.py": main.replace("TOOLS_LIST", "[quote, send_image]")})
    result = run(repo, base, head)
    assert _pairs(result) == [("main_agent", "send_image", "added")]
    assert not any("agent subclass" in limit for limit in result["head"]["limits"])


def test_two_class_bodies_with_the_same_attribute_are_two_agents(repo):
    body = (
        'class Billing:\n    agent = Agent(name="Billing", tools=[quote])\n'
        'class Support:\n    agent = Agent(name="Support", tools=SUPPORT)\n'
    )
    result = _compare(
        repo, body.replace("SUPPORT", "[quote]"), body.replace("SUPPORT", "[quote, send_image]")
    )
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("Support", "send_image", "added")]


def test_a_list_changed_through_one_agent_limits_every_agent_sharing_it(repo):
    body = (
        "SHARED = [quote]\nfirst = Agent(name='first', tools=SHARED)\n"
        "second = Agent(name='second', tools=SHARED)\nfirst.tools.append(send_image)\n"
    )
    result = _compare(repo, body + "# base\n", body)
    agents = {gap["agent"] for gap in result["head"]["coverage_gaps"] if "changed after" in gap["reason"]}
    assert agents == {"first", "second"}


def test_an_adk_agent_changed_after_construction_is_a_limit(repo):
    source = (
        "from google.adk.agents import Agent\n\n\n"
        "def lookup(q: str) -> str:\n    return q\n\n\n"
        "def other(q: str) -> str:\n    return q\n\n\n"
        "root_agent = Agent(name='app', model='m', tools=[lookup])\n"
    )
    base = commit(repo, {"agent.py": source})
    head = commit(repo, {"agent.py": source + "root_agent.tools.append(other)\n"})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("changed at agent.py" in limit for limit in result["head"]["limits"])
