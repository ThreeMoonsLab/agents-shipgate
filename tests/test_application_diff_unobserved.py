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


# ---------------------------------------------------------------------------
# #876 review, round 5: a handle counts only when the list is changed through
# it in view; handing it on is an indirect helper effect, as `helper(x.tools)`.


@pytest.mark.parametrize(
    "helper",
    [
        'def to_payload(request):\n    payload = {"model": "gpt"}\n'
        '    payload["tools"] = request.tools\n    return payload\n',
        "class Client:\n    def __init__(self, request):\n        self.tools = request.tools\n"
        "    def count(self):\n        return len(self.tools)\n",
        "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef forward(request):\n    tools = request.tools\n"
        "    return client.create(tools=tools)\n",
        "def swap(request):\n    tools = request.tools\n    tools = []\n    return tools\n",
    ],
    ids=["dict-payload", "attribute-copy", "handed-to-a-call", "handle-rebound"],
)
def test_a_handle_that_is_only_handed_on_is_not_a_change(repo, helper):
    before = 'main_agent = Agent(name="Main", tools=[quote])\n' + helper
    after = 'main_agent = Agent(name="Main", tools=[quote, send_image])\n' + helper
    result = _compare(repo, before, after)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("main_agent", "send_image", "added")]


@pytest.mark.parametrize(
    "change",
    [
        'payload = {}\npayload["tools"] = quote_agent.tools\npayload["tools"].append(send_image)\n',
        "class Holder:\n    def __init__(self):\n        self.tools = quote_agent.tools\n"
        "    def add(self):\n        self.tools.append(send_image)\n",
        "handle = quote_agent.tools\nother = handle or []\nother.append(send_image)\n",
        "handle = quote_agent.tools\nhandle += [send_image]\n",
    ],
    ids=["through-a-dict", "through-self-in-another-method", "through-an-alias", "augmented"],
)
def test_a_list_changed_through_a_handle_is_a_limit_on_its_agent(repo, change):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n'
    result = _compare(repo, body, body + change)
    assert result["comparison_status"] == "partial"
    assert any(
        "agent 'quote_agent' has its tools, handoffs or MCP servers changed" in limit
        for limit in result["head"]["limits"]
    ), result["head"]["limits"]


# ---------------------------------------------------------------------------
# #876 review, round 6: an agent the file builds is followed further — its
# list handed on, kept in a container, or returned may be changed out of view.


@pytest.mark.parametrize(
    "change",
    [
        'payload = {}\npayload["tools"] = quote_agent.tools\nk = "tools"\npayload[k].append(send_image)\n',
        "class Holder:\n    pass\nholder = Holder()\nholder.ref = quote_agent.tools\n"
        'getattr(holder, "ref").append(send_image)\n',
        'cache = {}\ncache["t"] = quote_agent.tools\ncache.setdefault("t", []).append(send_image)\n',
        "def add_image(lst):\n    lst.append(send_image)\nhandle = quote_agent.tools\nadd_image(handle)\n",
        "def add_image(lst):\n    lst.append(send_image)\nadd_image(quote_agent.tools)\n",
        "handle = quote_agent.tools\nlist.append(handle, send_image)\n",
        "class Base:\n    def __init__(self):\n        self.handle = quote_agent.tools\n"
        "class Child(Base):\n    def extend(self):\n        self.handle.append(send_image)\n",
        "if (handle := quote_agent.tools):\n    handle.append(send_image)\n",
        "handle, other = quote_agent.tools, [1]\nhandle.append(send_image)\n",
        "for handle in (quote_agent.tools,):\n    handle.append(send_image)\n",
        'box = {"t": quote_agent.tools}\nbox["t"].append(send_image)\n',
        "def get_tools():\n    return quote_agent.tools\nget_tools().append(send_image)\n",
        "import contextlib\nwith contextlib.nullcontext(quote_agent.tools) as handle:\n"
        "    handle.append(send_image)\n",
    ],
    ids=[
        "subscript-key-name", "getattr-on-holder", "setdefault", "handle-to-helper",
        "attribute-to-helper", "unbound-list-method", "inherited-self-handle", "walrus",
        "tuple-unpack", "for-target", "dict-literal", "getter-function", "with-as",
    ],
)
def test_an_agents_list_handed_out_of_view_is_a_limit_on_it(repo, change):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n'
    result = _compare(repo, body, body + change)
    assert result["comparison_status"] == "partial"
    assert any(
        "agent 'quote_agent' has its tools, handoffs or MCP servers changed" in limit
        for limit in result["head"]["limits"]
    ), result["head"]["limits"]


def test_an_imported_agents_list_handed_to_a_helper_is_a_limit(repo):
    base = commit(repo, {"app/__init__.py": "", "app/agents_def.py": WIRING_AGENTS, "app/wiring.py": "x = 1\n"})
    head = commit(
        repo,
        {
            "app/wiring.py": "from app.agents_def import quote_agent, send_image\n"
            "def add_image(lst):\n    lst.append(send_image)\nadd_image(quote_agent.tools)\n"
        },
    )
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert any("wiring.py" in limit for limit in result["head"]["limits"])


def test_reading_an_agents_list_is_still_not_a_change(repo):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n'
    reads = (
        "print(len(quote_agent.tools), [tool.name for tool in quote_agent.tools])\n"
        "names = sorted(tool.name for tool in quote_agent.tools)\nhandle = quote_agent.tools\n"
        "count = len(handle)\n"
    )
    result = _compare(repo, body, body.replace("[quote]", "[quote, send_image]") + reads)
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("quote_agent", "send_image", "added")]


# ---------------------------------------------------------------------------
# #876 review, round 7: a helper of the file that changes a list it is given
# changes it, whatever the reader can say about whose list it is.

ADD_IMAGE = "def add_image(lst):\n    BODY\n"


@pytest.mark.parametrize(
    "call",
    [
        'AGENTS = [quote_agent]\nadd_image(AGENTS[0].tools)\n',
        'REG = {"a": quote_agent}\nadd_image(REG["a"].tools)\n',
        "class Registry:\n    held = Agent(name='Held', tools=[quote])\nadd_image(Registry.held.tools)\n",
        "class Holder:\n    def __init__(self, agent):\n        self.agent = agent\n"
        "    def extend(self):\n        add_image(self.agent.tools)\n",
        "def setup(agent):\n    add_image(agent.tools)\n",
        "def setup(agent):\n    handle = agent.tools\n    add_image(handle)\n",
    ],
    ids=["list-of-agents", "dict-of-agents", "class-attribute", "self-agent-parameter", "parameter", "parameter-handle"],
)
def test_a_list_handed_to_a_helper_that_changes_it_is_a_change(repo, call):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + call + ADD_IMAGE
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial"
    assert result["head"]["limits"] != result["base"]["limits"], result["head"]["limits"]


def test_a_helper_of_the_file_that_only_reads_the_list_is_a_read(repo):
    body = (
        'main_agent = Agent(name="Main", tools=TOOLS)\n'
        "def render(lst):\n    return [tool.name for tool in lst]\nrender(main_agent.tools)\n"
    )
    result = _compare(repo, body.replace("TOOLS", "[quote]"), body.replace("TOOLS", "[quote, send_image]"))
    assert result["comparison_status"] == "compared"
    assert _pairs(result) == [("main_agent", "send_image", "added")]


# ---------------------------------------------------------------------------
# #876 review, round 8: a helper the file imports is followed like its own,
# and a list reached through the file's container of agents is an agent's.

HELPERS = "def register_defaults(lst):\n    BODY\n"


@pytest.mark.parametrize(
    "wiring",
    [
        "from app.agents_def import AGENTS\nfrom app.helpers import register_defaults\n\n"
        "for agent in AGENTS:\n    register_defaults(agent.tools)\n",
    ],
    ids=["loop-in-a-module-without-the-sdk"],
)
def test_an_imported_helper_that_changes_an_agents_list_is_a_limit(repo, wiring):
    agents = WIRING_AGENTS + "AGENTS = [quote_agent, other]\n"
    base = commit(
        repo,
        {
            "app/__init__.py": "",
            "app/agents_def.py": agents,
            "app/helpers.py": HELPERS.replace("BODY", "pass"),
            "app/wiring.py": wiring,
        },
    )
    head = commit(repo, {"app/helpers.py": HELPERS.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"


@pytest.mark.parametrize(
    "use",
    [
        "from helpers import register_defaults\nfor agent in AGENTS:\n    register_defaults(agent.tools)\n",
        "add = lambda lst: lst.append(send_image)\nadd(AGENTS[0].tools)\n",
        "import functools\n\ndef extend(lst, extra):\n    lst.extend(extra)\n"
        "functools.partial(extend, extra=[send_image])(AGENTS[0].tools)\n",
        "class H:\n    def add(self, lst):\n        lst.append(send_image)\nH().add(REG['a'].tools)\n",
        "class Registry:\n    held = Agent(name='Held', tools=[quote])\n"
        "def forward(*lists):\n    lists[0].append(send_image)\nforward(Registry.held.tools)\n",
    ],
    ids=["imported-helper-in-a-loop", "lambda", "partial", "method-on-dict-item", "star-args-on-class-attribute"],
)
def test_a_list_of_the_files_agents_handed_out_of_view_is_a_limit(repo, use):
    body = (
        'quote_agent = Agent(name="Quote", tools=[quote])\nAGENTS = [quote_agent]\n'
        "REG = {'a': quote_agent}\n"
    )
    helpers = "def register_defaults(lst):\n    lst.append(len)\n"
    base = commit(repo, {"agent.py": _agents(body), "helpers.py": helpers})
    head = commit(repo, {"agent.py": _agents(body + use)})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert result["head"]["limits"] != result["base"]["limits"]


# ---------------------------------------------------------------------------
# #876 review, round 9: containers of agents however they are built, a loop at
# module level, helpers' inner calls read in their own module.

METHOD_HELPER = "class H:\n    def add(self, lst):\n        BODY\n"


@pytest.mark.parametrize(
    "use",
    [
        "AGENTS = []\nAGENTS.append(quote_agent)\nH().add(AGENTS[0].tools)\n",
        "AGENTS = [x for x in (quote_agent,)]\nfor ag in AGENTS:\n    H().add(ag.tools)\n",
        "def all_agents():\n    return [quote_agent]\nfor ag in all_agents():\n    H().add(ag.tools)\n",
        "AGENTS = [quote_agent]\nfor ag in AGENTS:\n    H().add(ag.tools)\n",
    ],
    ids=["appended", "comprehension", "returned-by-a-function", "module-level-loop"],
)
def test_a_container_of_the_files_agents_however_built_is_followed(repo, use):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + METHOD_HELPER + use
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial"
    assert any("quote_agent" in limit for limit in result["head"]["limits"]), result["head"]["limits"]


@pytest.mark.parametrize(
    "use",
    [
        "from app.registry import AGENTS\nfor ag in AGENTS:\n    H().add(ag.tools)\n",
        "from app.registry import quote_agent\nH().add(quote_agent.tools)\n",
        "from app import registry\nH().add(getattr(registry, 'quote_agent').tools)\n",
    ],
    ids=["imported-container", "imported-agent", "getattr-on-imported-module"],
)
def test_an_agent_imported_from_the_scope_into_an_sdk_file_is_followed(repo, use):
    registry = TOOLS + "from agents import Agent\nquote_agent = Agent(name='Quote', tools=[quote])\nAGENTS = [quote_agent]\n"
    wiring = "from agents import Agent\n" + METHOD_HELPER + use
    base = commit(
        repo,
        {"app/__init__.py": "", "app/registry.py": registry, "app/wiring.py": wiring.replace("BODY", "pass")},
    )
    head = commit(repo, {"app/wiring.py": wiring.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"


def test_a_helpers_inner_call_is_read_in_the_helpers_module(repo):
    """d08b: ``outer`` in ``helpers.py`` hands the list to its own ``inner``;
    resolved in the caller's module, ``inner`` was not found."""

    helpers = "def inner(lst):\n    BODY\n\n\ndef outer(lst):\n    inner(lst)\n"
    body = (
        'quote_agent = Agent(name="Quote", tools=[quote])\n'
        "from helpers import outer\n\n\ndef setup(agent):\n    outer(agent.tools)\n"
    )
    base = commit(repo, {"agent.py": _agents(body), "helpers.py": helpers.replace("BODY", "pass")})
    head = commit(repo, {"helpers.py": helpers.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("cannot identify" in limit for limit in result["head"]["limits"])


# ---------------------------------------------------------------------------
# #876 review, round 10: methods and locally imported helpers are resolved;
# containers filled or iterated in any common spelling hold their agents.


@pytest.mark.parametrize(
    "use",
    [
        "REG = {}\nREG['a'] = quote_agent\nhelper(REG['a'].tools)\n",
        "REG = {}\nREG.setdefault('a', quote_agent)\nhelper(REG['a'].tools)\n",
        "REG = {}\nREG.update({'a': quote_agent})\nhelper(REG['a'].tools)\n",
        "OTHERS = []\nAGENTS = [quote_agent] + OTHERS\nhelper(AGENTS[0].tools)\n",
        "REG = {'a': quote_agent}\nfor ag in list(REG.values()):\n    helper(ag.tools)\n",
        "AGENTS = [quote_agent]\nfor i, ag in enumerate(AGENTS):\n    helper(ag.tools)\n",
        "AGENTS = [quote_agent]\nfor name, ag in zip(['q'], AGENTS):\n    helper(ag.tools)\n",
        "GROUPS = [[quote_agent]]\nfor group in GROUPS:\n    for ag in group:\n        helper(ag.tools)\n",
        "AGENTS = [quote_agent, quote_agent]\nfor ag in sorted(AGENTS[1:], key=id):\n    helper(ag.tools)\n",
    ],
    ids=[
        "item-store", "setdefault", "update", "concatenation", "list-of-values", "enumerate",
        "zip", "nested-loops", "sorted-slice",
    ],
)
def test_a_container_filled_or_iterated_any_common_way_holds_its_agents(repo, use):
    """An unresolvable callee (a third-party ``helper``) on the file's own agents."""

    body = 'quote_agent = Agent(name="Quote", tools=[quote])\nfrom vendor import helper\n' + use
    result = _compare(repo, 'quote_agent = Agent(name="Quote", tools=[quote])\n', body)
    assert result["comparison_status"] == "partial"
    assert any("quote_agent" in limit for limit in result["head"]["limits"]), result["head"]["limits"]


@pytest.mark.parametrize(
    "use",
    [
        "class H:\n    def add(self, lst):\n        BODY\n\n\ndef wire(agents):\n    for ag in agents:\n        H().add(ag.tools)\n",
        "class H:\n    def add(self, lst):\n        BODY\n\n\nholder = H()\n\n\ndef wire(agent):\n    holder.add(agent.tools)\n",
    ],
    ids=["method-on-a-fresh-instance", "method-on-a-module-instance"],
)
def test_a_method_that_changes_the_list_it_is_given_is_a_change(repo, use):
    """Whatever the receiver — here a parameter — the resolved method changes it."""

    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + use
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial"
    assert any("cannot identify" in limit for limit in result["head"]["limits"])


def test_a_helper_imported_inside_the_function_is_resolved(repo):
    helpers = "def add_image(lst):\n    BODY\n"
    body = (
        'quote_agent = Agent(name="Quote", tools=[quote])\n\n\n'
        "def setup(agent):\n    from helpers import add_image\n\n    add_image(agent.tools)\n"
    )
    base = commit(repo, {"agent.py": _agents(body), "helpers.py": helpers.replace("BODY", "pass")})
    head = commit(repo, {"helpers.py": helpers.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"


# ---------------------------------------------------------------------------
# #876 review, round 11: a parameter's list handed to a call nothing resolves
# — an inherited method, a dispatch table, ``super()`` — limits the file.


@pytest.mark.parametrize(
    "helper",
    [
        "class Base:\n    def add(self, lst):\n        BODY\n\n\nclass H(Base):\n    pass\n\n\n"
        "def setup(agent):\n    H().add(agent.tools)\n",
        "class Base:\n    def add(self, lst):\n        BODY\n\n\nclass H(Base):\n"
        "    def add(self, lst):\n        super().add(lst)\n\n\ndef setup(agent):\n    H().add(agent.tools)\n",
        "def add_image(lst):\n    BODY\n\n\nHANDLERS = {'img': add_image}\n\n\n"
        "def setup(agent):\n    HANDLERS['img'](agent.tools)\n",
        "class Toolbox:\n    def install(self, lst):\n        BODY\n\n\nclass Service:\n"
        "    def __init__(self):\n        self.toolbox = Toolbox()\n\n    def attach(self, agent):\n"
        "        self.toolbox.install(agent.tools)\n",
    ],
    ids=["inherited-method", "super-call", "dispatch-table", "service-held-instance"],
)
def test_a_parameters_list_handed_to_an_unresolved_call_limits_the_file(repo, helper):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + helper
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial"
    assert any("cannot identify" in limit for limit in result["head"]["limits"])


# ---------------------------------------------------------------------------
# #876 review, round 12: any value the reader cannot name — a factory's
# result, a service's agent, ``self.agent`` — handed to the application's own
# code that is not read limits the file, in a module read as an SDK source or
# not; another library's function is not the application's code.

INHERITED = (
    "class Base:\n    def add(self, lst):\n        BODY\n\n    def install(self, tools):\n"
    "        lst = tools\n        BODY\n\n\nclass H(Base):\n    pass\n\n\n"
)


@pytest.mark.parametrize(
    ("wiring", "extra"),
    [
        ("from factory import build\n\nbot = build()\nH().add(bot.tools)\n", True),
        (
            "class Service:\n    def __init__(self):\n        self.agent = Agent(name='Svc', tools=[quote])\n\n\n"
            "svc = Service()\nH().add(svc.agent.tools)\n",
            False,
        ),
        (
            "class Svc:\n    def __init__(self, agent):\n        self.agent = agent\n\n"
            "    def setup(self):\n        H().add(self.agent.tools)\n\n\nSvc(quote_agent).setup()\n",
            False,
        ),
        ("def setup(agent):\n    H().install(tools=agent.tools)\n\n\nsetup(quote_agent)\n", False),
    ],
    ids=["imported-factory-result", "service-holding-an-agent", "self-agent-from-a-parameter", "capability-keyword"],
)
def test_any_value_handed_to_unread_application_code_limits_the_file(repo, wiring, extra):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + INHERITED + wiring
    files = {"factory.py": 'from agent import Agent, quote\n\n\ndef build():\n    return Agent(name="Built", tools=[quote])\n'} if extra else {}
    base = commit(repo, {**files, "agent.py": _agents(body.replace("BODY", "pass"))})
    head = commit(repo, {"agent.py": _agents(body.replace("BODY", "lst.append(send_image)"))})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("cannot identify" in limit for limit in result["head"]["limits"]), result["head"]["limits"]


@pytest.mark.parametrize(
    "wiring",
    [
        "def attach(agent):\n    H().add(agent.tools)\n",
        "from factory import build\n\nbot = build()\nH().add(bot.tools)\n",
    ],
    ids=["parameter", "factory-result"],
)
def test_a_module_that_does_not_import_the_sdk_follows_the_same_rule(repo, wiring):
    files = {
        "factory.py": 'from agent import Agent, quote\n\n\ndef build():\n    return Agent(name="Built", tools=[quote])\n',
        "agent.py": _agents('quote_agent = Agent(name="Quote", tools=[quote])\n'),
    }
    module = "from agent import send_image\n\n\n" + INHERITED + wiring
    base = commit(repo, {**files, "wiring.py": module.replace("BODY", "pass")})
    head = commit(repo, {"wiring.py": module.replace("BODY", "lst.append(send_image)")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any("wiring.py" in gap["reason"] for gap in result["head"]["coverage_gaps"])


@pytest.mark.parametrize(
    "handler",
    [
        "from somelib import validate\n\n\ndef handle(request):\n    validate(request.tools)\n",
        "from pydantic import TypeAdapter\n\nADAPTER = TypeAdapter(list)\n\n\n"
        "def handle(request):\n    return ADAPTER.validate_python(request.tools)\n",
        "from openai import AsyncOpenAI\n\nclient = AsyncOpenAI()\n\n\ndef handle(request, model: str):\n"
        "    return client.chat.completions.create(model=model, tools=request.tools)\n",
    ],
    ids=["third-party-function", "third-party-instance", "api-payload"],
)
def test_a_request_handed_to_another_library_is_not_a_limit(repo, handler):
    before = 'main_agent = Agent(name="Main", tools=[quote])\n' + handler
    result = _compare(repo, before, before.replace("tools=[quote]", "tools=[quote, send_image]"))
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert _pairs(result) == [("main_agent", "send_image", "added")]


def test_a_method_called_on_self_is_whichever_the_instance_has(repo):
    """The base's ``register`` only reads; the subclass's appends."""

    body = (
        'quote_agent = Agent(name="Quote", tools=[quote])\n\n\n'
        "class BaseRunner:\n    def setup(self, agent):\n        self.register(agent.tools)\n\n"
        "    def register(self, tools):\n        return len(tools)\n\n\n"
        "class Runner(BaseRunner):\n    def register(self, tools):\n        BODY\n\n\n"
        "Runner().setup(quote_agent)\n"
    )
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "tools.append(send_image)"))
    assert result["comparison_status"] == "partial"


# ---------------------------------------------------------------------------
# #876 review, round 13: another library is a package the repository does not
# hold, handed none of the application's functions; a builtin, the standard
# library and an unannotated parameter object are not.

ADD_IMAGE_HELPER = "def add_image(lst, extra=None):\n    BODY\n\n\n"


@pytest.mark.parametrize(
    "use",
    [
        "import functools\n\n\ndef setup(agent):\n    add = functools.partial(add_image, extra=None)\n    add(agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    add = wrap(add_image)\n    add(agent.tools)\n",
        "from somelib import apply_hooks\n\n\ndef setup(agent):\n    apply_hooks(agent.tools, add_image)\n",
        "def setup(agent):\n    list.extend(agent.tools, [send_image])\n",
        "import operator\n\n\ndef setup(agent):\n    operator.iadd(agent.tools, [send_image])\n",
        "def setup(agent, toolbox=None):\n    toolbox.install(tools=agent.tools)\n",
        "def setup(agent):\n    getattr(agent, 'tools').append(send_image)\n",
    ],
    ids=[
        "partial-of-the-apps-function", "library-wrapper-of-the-apps-function",
        "library-handed-the-apps-function", "builtin-mutator", "stdlib-mutator",
        "parameter-object-capability-keyword", "getattr-append",
    ],
)
def test_a_call_that_is_not_another_librarys_own_is_not_a_read(repo, use):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + ADD_IMAGE_HELPER + use + "\n\nsetup(quote_agent)\n"
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial", result["rows"]


def test_the_repositorys_own_code_outside_the_scope_is_not_another_library(repo):
    helper = "def register_defaults(lst):\n    BODY\n"
    agent = _agents(
        'quote_agent = Agent(name="Quote", tools=[quote])\nfrom lib.registry import register_defaults\n\n\n'
        "def setup(agent):\n    register_defaults(agent.tools)\n\n\nsetup(quote_agent)\n"
    )
    base = commit(repo, {"app/agent.py": agent, "lib/__init__.py": "", "lib/registry.py": helper.replace("BODY", "pass")})
    head = commit(repo, {"lib/registry.py": helper.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"


# ---------------------------------------------------------------------------
# #876 review, round 14: the application's callables handed to another library
# in any spelling, repository code on any path entry, and objects a subclass
# or a caller may supply are the application's.

@pytest.mark.parametrize(
    "use",
    [
        "from somelib import wrap\n\n\nclass Svc:\n    def add_image(self, lst):\n        add_image(lst)\n\n"
        "    def setup(self, agent):\n        wrap(self.add_image)(agent.tools)\n\n\ndef setup(agent):\n    Svc().setup(agent)\n",
        "from somelib import wrap\n\n\nclass Utils:\n    @staticmethod\n    def add(lst):\n        add_image(lst)\n\n\n"
        "def setup(agent):\n    wrap(Utils.add, agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    wrap(lambda lst: add_image(lst), agent.tools)\n",
        "import functools\nfrom somelib import wrap\n\n\ndef setup(agent):\n    wrap(functools.partial(add_image, extra=1), agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    fn = add_image\n    wrap(fn, agent.tools)\n",
        "from somelib import cached, wrap\n\nadd = cached(add_image)\n\n\ndef setup(agent):\n    wrap(add, agent.tools)\n",
    ],
    ids=["bound-method", "class-attribute", "inline-lambda", "nested-partial", "alias", "wrapped-at-module-level"],
)
def test_the_applications_callable_handed_to_a_library_in_any_spelling_is_not_a_read(repo, use):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + ADD_IMAGE_HELPER + use + "\n\nsetup(quote_agent)\n"
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial", result["rows"]


@pytest.mark.parametrize(
    "use",
    [
        "from openai import OpenAI\n\n\nclass Svc:\n    def __init__(self):\n        self.client = OpenAI()\n\n"
        "    def run(self, agent):\n        self.client.install(tools=agent.tools)\n\n\n"
        "class Toolbox:\n    def install(self, tools):\n        lst = tools\n        BODY\n\n\n"
        "class AppSvc(Svc):\n    def __init__(self):\n        self.client = Toolbox()\n\n\nAppSvc().run(quote_agent)\n",
        "from somelib import ToolRegistry\n\n\ndef attach(agent, registry: ToolRegistry):\n    registry.install(tools=agent.tools)\n\n\n"
        "class Toolbox(ToolRegistry):\n    def install(self, tools):\n        lst = tools\n        BODY\n\n\nattach(quote_agent, Toolbox())\n",
    ],
    ids=["subclass-reassigns-the-client", "library-annotated-parameter"],
)
def test_an_object_a_subclass_or_caller_supplies_is_not_trusted_as_a_library(repo, use):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + use
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial", result["rows"]


def test_the_repositorys_code_on_another_path_entry_is_not_another_library(repo):
    """``libs/`` on ``PYTHONPATH`` in a monorepo: ``shared`` is the repository's."""

    helper = "def register_defaults(lst):\n    BODY\n"
    agent = _agents(
        'quote_agent = Agent(name="Quote", tools=[quote])\nfrom shared.registry import register_defaults\n\n\n'
        "def setup(agent):\n    register_defaults(agent.tools)\n\n\nsetup(quote_agent)\n"
    )
    base = commit(repo, {
        "services/app/agent.py": agent,
        "libs/shared/__init__.py": "",
        "libs/shared/registry.py": helper.replace("BODY", "pass"),
    })
    head = commit(repo, {"libs/shared/registry.py": helper.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head, "--scope", "services/app")
    assert result["comparison_status"] == "partial"


# ---------------------------------------------------------------------------
# #876 review, round 15: another library's call is a read only when every
# other argument on the way is inert data; anything unrecognised may be the
# application's own callable.

@pytest.mark.parametrize(
    "use",
    [
        "from somelib import wrap\n\nHANDLERS = {'img': add_image}\n\n\ndef setup(agent):\n    wrap(HANDLERS['img'], agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    wrap([add_image], agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    wrap(hooks={'on_start': add_image}, lst=agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent, cb=add_image):\n    wrap(cb, agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    for fn in (add_image,):\n        wrap(fn, agent.tools)\n",
        "from somelib import wrap\n\n\nclass Adder:\n    def __call__(self, lst):\n        add_image(lst)\n\n\n"
        "def setup(agent):\n    wrap(Adder(), agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent):\n    wrap(add_image.__call__, agent.tools)\n",
    ],
    ids=["dispatch-table", "list-of-callables", "dict-of-hooks", "callback-parameter", "loop-bound",
         "instance-of-an-app-class", "function-attribute"],
)
def test_a_library_handed_anything_but_data_is_not_a_read(repo, use):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + ADD_IMAGE_HELPER + use + "\n\nsetup(quote_agent)\n"
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial", result["rows"]


@pytest.mark.parametrize(
    "handler",
    [
        "from openai import OpenAI\n\nMODEL = 'gpt-4o'\nclient = OpenAI()\n\n\ndef handle(req):\n"
        "    messages = [{'role': 'user', 'content': req.text}]\n"
        "    return client.chat.completions.create(model=MODEL, messages=messages, tools=req.tools, temperature=0.2)\n",
        "from somelib import validate\n\n\ndef handle(req, strict: bool = True):\n    return validate(req.tools, strict=strict)\n",
        "import os\n\nfrom openai import OpenAI\n\nclient = OpenAI(api_key=os.environ.get('KEY'))\n\n\n"
        "def handle(req):\n    return client.responses.create(input=f'{req.text}', tools=req.tools)\n",
    ],
    ids=["client-with-local-data", "annotated-flag", "client-built-from-the-environment"],
)
def test_a_library_handed_only_data_is_a_read(repo, handler):
    before = 'main_agent = Agent(name="Main", tools=[quote])\n' + handler
    result = _compare(repo, before, before.replace("tools=[quote]", "tools=[quote, send_image]"))
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert _pairs(result) == [("main_agent", "send_image", "added")]


def test_a_namespace_package_on_another_path_entry_is_the_repositorys(repo):
    """PEP 420: ``libs/sharedns/`` without ``__init__.py`` is still held."""

    helper = "def register_defaults(lst):\n    BODY\n"
    agent = _agents(
        'quote_agent = Agent(name="Quote", tools=[quote])\nfrom sharedns.registry import register_defaults\n\n\n'
        "def setup(agent):\n    register_defaults(agent.tools)\n\n\nsetup(quote_agent)\n"
    )
    base = commit(repo, {"services/app/agent.py": agent, "libs/sharedns/registry.py": helper.replace("BODY", "pass")})
    head = commit(repo, {"libs/sharedns/registry.py": helper.replace("BODY", "lst.append(len)")})
    result = run(repo, base, head, "--scope", "services/app")
    assert result["comparison_status"] == "partial"


# ---------------------------------------------------------------------------
# #876 review, round 16: the data channels are narrow — only the list's own
# object's attributes, scalar-annotated parameters, and no dynamic import.

@pytest.mark.parametrize(
    "use",
    [
        "from somelib import wrap\n\n\nclass Svc:\n    def attach(self, lst):\n        add_image(lst)\n\n\n"
        "def setup(agent, svc=Svc()):\n    wrap(svc.attach, agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent, hooks: list = [add_image]):\n    wrap(hooks, agent.tools)\n",
        "from somelib import wrap\n\n\ndef setup(agent, extra: dict = {'callback': add_image}):\n    wrap(agent.tools, **extra)\n",
        "from somelib import wrap\n\n\ndef setup(agent, hooks: list = [add_image]):\n    wrap([h for h in hooks], agent.tools)\n",
        "import importlib\n\nfrom somelib import wrap\n\n\ndef setup(agent):\n"
        "    mod = importlib.import_module('agent')\n    wrap(mod.add_image, agent.tools)\n",
        "import sys\n\nfrom somelib import wrap\n\n\ndef setup(agent):\n    wrap(sys.modules['agent'].add_image, agent.tools)\n",
    ],
    ids=["another-parameters-method", "bare-list-parameter", "bare-dict-parameter",
         "comprehension-over-a-parameter", "dynamic-import", "module-table"],
)
def test_a_channel_that_can_carry_the_applications_code_is_not_data(repo, use):
    body = 'quote_agent = Agent(name="Quote", tools=[quote])\n' + ADD_IMAGE_HELPER + use + "\n\nsetup(quote_agent)\n"
    result = _compare(repo, body.replace("BODY", "pass"), body.replace("BODY", "lst.append(send_image)"))
    assert result["comparison_status"] == "partial", result["rows"]


@pytest.mark.parametrize(
    "handler",
    [
        "from openai import OpenAI\n\nclient = OpenAI()\n\n\ndef handle(req):\n"
        "    return client.chat.completions.create(model=req.model, messages=req.messages, tools=req.tools)\n",
        "from somelib import validate\n\n\ndef handle(req, names: list[str] | None = None):\n"
        "    return validate(req.tools, names=names)\n",
        "def handle(config):\n    return getattr(config.tools, 'task_steps_manager_enabled', True)\n",
    ],
    ids=["the-requests-own-data", "annotated-list-of-strings", "config-flag-read"],
)
def test_the_lists_own_objects_data_is_still_a_read(repo, handler):
    before = 'main_agent = Agent(name="Main", tools=[quote])\n' + handler
    result = _compare(repo, before, before.replace("tools=[quote]", "tools=[quote, send_image]"))
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert _pairs(result) == [("main_agent", "send_image", "added")]


# ---------------------------------------------------------------------------
# #874 review, round 3, found in #876: a change on an agent another file
# constructs, or on a value the reader cannot identify, limited only the file
# the change is written in. The agent it changed kept an established row:
# ``send_image`` read as ``added`` in the head, though the base had bound it
# through the change.

_IMPORTED_AGENT = 'quote_agent = Agent(name="Quote", tools=TOOLS)\n'
_BUILDER = 'def build():\n    return Agent(name="Quote", tools=TOOLS)\n'


@pytest.mark.parametrize(
    ("defining", "changing", "agent"),
    [
        (
            _IMPORTED_AGENT,
            "from agents import Runner\nfrom agent import quote_agent, send_image\n\n"
            "quote_agent.tools.append(send_image)\n",
            "quote_agent",
        ),
        (
            _BUILDER,
            "from agents import Runner\nfrom agent import build, send_image\n\n"
            "support = build()\nsupport.tools.append(send_image)\n",
            "Quote",
        ),
        (
            _IMPORTED_AGENT,
            "from agents import Runner\nfrom agent import quote_agent, send_image\n\n\n"
            "def extend(agent):\n    agent.tools.append(send_image)\n\n\nextend(quote_agent)\n",
            "quote_agent",
        ),
        (
            _BUILDER,
            "from agent import build, send_image\n\n"
            "support = build()\nsupport.tools.append(send_image)\n",
            "Quote",
        ),
    ],
    ids=["imported-agent", "imported-builders-result", "a-parameter", "outside-an-sdk-source"],
)
def test_a_change_on_another_files_agent_limits_that_agent(repo, defining, changing, agent):
    base = commit(
        repo,
        {"agent.py": _agents(defining.replace("TOOLS", "[quote]")), "wiring.py": changing},
    )
    head = commit(
        repo,
        {"agent.py": _agents(defining.replace("TOOLS", "[quote, send_image]")), "wiring.py": None},
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert _pairs(result) == [(agent, "send_image", "not_established")]
    (row,) = result["rows"]
    assert row["candidate_change"] == "added"
    assert any("wiring.py:" in reason for reason in row["uncertainty"]["base"])


def test_the_changed_agents_file_is_named(repo):
    body = _IMPORTED_AGENT.replace("TOOLS", "[quote]")
    changing = (
        "from agents import Runner\nfrom agent import quote_agent, send_image\n\n"
        "quote_agent.tools.append(send_image)\n"
    )
    base = commit(repo, {"agent.py": _agents(body)})
    head = commit(repo, {"agent.py": _agents(body), "wiring.py": changing})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert any(
        "of 'quote_agent' (agent.py:" in limit
        and "changed after construction at wiring.py:4" in limit
        and "the agents constructed in agent.py are not established" in limit
        for limit in result["head"]["limits"]
    ), result["head"]["limits"]


# ---------------------------------------------------------------------------
# #876 review, round 18: a container holding another file's agent beside the
# file's own routes the change to both; a change after construction on the
# side a binding is observed on qualifies it there too, for the list it
# changes; a logger adapter is a logger.

_PLANT = 'plant_agent = Agent(name="Plant", tools=TOOLS)\n'


@pytest.mark.parametrize(
    "reset",
    [
        "AGENTS = [manager, plant_agent]\nfor each in AGENTS:\n    each.tools = [quote]\n",
        "AGENTS = [manager, plant_agent]\nAGENTS[1].tools = [quote]\n",
        "REGISTRY = {'m': manager, 'p': plant_agent}\nREGISTRY['p'].tools = [quote]\n",
    ],
    ids=["loop", "subscript", "dict"],
)
def test_a_container_of_another_files_agent_limits_it_where_it_is_built(repo, reset):
    manager = (
        "from agents import Agent\nfrom agent import plant_agent, quote\n\n"
        'manager = Agent(name="Manager", tools=[quote])\n' + reset
    )
    base = commit(repo, {"agent.py": _agents(_PLANT.replace("TOOLS", "[quote]")), "manager.py": manager})
    head = commit(repo, {"agent.py": _agents(_PLANT.replace("TOOLS", "[quote, send_image]"))})
    result = run(repo, base, head)
    assert _pairs(result) == [("plant_agent", "send_image", "not_established")]
    assert any("agent.py" in reason for reason in result["rows"][0]["uncertainty"]["head"])


@pytest.mark.parametrize(
    ("before", "after", "candidate", "side"),
    [
        (
            _PLANT.replace("TOOLS", "[quote]"),
            _PLANT.replace("TOOLS", "[quote, send_image]") + "plant_agent.tools = [quote]\n",
            "added",
            "head",
        ),
        (
            _PLANT.replace("TOOLS", "[quote, send_image]") + "plant_agent.tools = [quote]\n",
            _PLANT.replace("TOOLS", "[quote]"),
            "removed",
            "base",
        ),
    ],
    ids=["reset-in-the-head", "reset-in-the-base"],
)
def test_a_change_after_construction_qualifies_the_side_it_is_on(repo, before, after, candidate, side):
    result = _compare(repo, before, after)
    assert _pairs(result) == [("plant_agent", "send_image", "not_established")]
    (row,) = result["rows"]
    assert row["candidate_change"] == candidate
    assert any("changed after construction" in reason for reason in row["uncertainty"][side])


def test_a_handoff_change_does_not_qualify_the_tools_the_constructor_binds(repo):
    """Wiring handoffs after construction — two agents that hand off to each
    other — leaves the tools the constructor binds in the head established."""

    plant = 'billing = Agent(name="billing")\n' + _PLANT
    result = _compare(
        repo,
        plant.replace("TOOLS", "[quote]"),
        plant.replace("TOOLS", "[quote, send_image]") + "plant_agent.handoffs = [billing]\n",
    )
    assert _pairs(result) == [("plant_agent", "send_image", "added")]


def test_a_logger_adapter_is_a_logger(repo):
    handler = (
        "import logging\n\nlog = logging.LoggerAdapter(logging.getLogger(__name__), {})\n\n\n"
        "def handle(request):\n    log.info('tools %s', request.tools)\n"
    )
    before = 'main_agent = Agent(name="Main", tools=[quote])\n' + handler
    result = _compare(repo, before, before.replace("tools=[quote]", "tools=[quote, send_image]"))
    assert result["comparison_status"] == "compared", result["head"]["limits"]
    assert _pairs(result) == [("main_agent", "send_image", "added")]


# ---------------------------------------------------------------------------
# #876 review, round 19: a list changed by a name computed at run time, or
# through the agent's namespace, is changed; a container of another file's
# agents is followed through ``+``, comprehensions and wrappers; the side a
# binding is observed on honours a moved file; an ``append``, an unrelated
# list's change, or a container member that is plainly no agent qualifies
# nothing.

_MANAGER = "from agents import Agent\nfrom agent import plant_agent, quote\n\n" 'manager = Agent(name="Manager", tools=[quote])\n'


def _reset_elsewhere(repo, reset: str, extra: dict | None = None):
    base = commit(
        repo,
        {"agent.py": _agents(_PLANT.replace("TOOLS", "[quote]")), "manager.py": _MANAGER, **(extra or {})},
    )
    head = commit(
        repo,
        {"agent.py": _agents(_PLANT.replace("TOOLS", "[quote, send_image]")), "manager.py": _MANAGER + reset},
    )
    return run(repo, base, head)


@pytest.mark.parametrize(
    "reset",
    [
        "for key, value in {'tools': [quote]}.items():\n    setattr(plant_agent, key, value)\n",
        "name = 'tools'\nsetattr(plant_agent, name, [quote])\n",
        "for capability in ('tools',):\n    getattr(plant_agent, capability).clear()\n",
        "vars(plant_agent)['tools'] = [quote]\n",
        "plant_agent.__dict__['tools'] = [quote]\n",
        "plant_agent.__dict__.update({'tools': [quote]})\n",
    ],
    ids=["setattr-overrides", "setattr-computed", "getattr-computed", "vars", "dunder-dict", "dict-update"],
)
def test_a_list_changed_by_a_computed_name_is_changed(repo, reset):
    result = _reset_elsewhere(repo, reset)
    assert _pairs(result) == [("plant_agent", "send_image", "not_established")]


@pytest.mark.parametrize(
    "agents",
    ["[manager] + OTHERS", "[manager] + [each for each in OTHERS]", "[manager] + list(OTHERS)"],
    ids=["plus", "comprehension", "wrapper"],
)
def test_another_files_agents_in_a_combined_container_are_limited(repo, agents):
    registry = "from agent import plant_agent\n\nOTHERS = [plant_agent]\n"
    reset = f"from registry import OTHERS\n\nAGENTS = {agents}\nfor each in AGENTS:\n    each.tools = [quote]\n"
    result = _reset_elsewhere(repo, reset, {"registry.py": registry})
    assert _pairs(result) == [("plant_agent", "send_image", "not_established")]


@pytest.mark.parametrize(
    "change",
    [
        "PIPELINE = [manager, print]\nPIPELINE[0].tools.append(quote)\n",
        "import logging\n\nPARTS = [manager, logging.getLogger(__name__)]\nPARTS[0].tools.append(quote)\n",
        "plant_agent.tools.append(quote)\n",
        "plant_agent.handoffs = []\n",
        "plant_agent.mcp_servers = []\n",
    ],
    ids=["builtin-member", "logger-member", "append", "handoffs", "mcp-servers"],
)
def test_a_change_that_cannot_undo_the_constructor_qualifies_nothing(repo, change):
    result = _reset_elsewhere(repo, change)
    assert _pairs(result) == [("plant_agent", "send_image", "added")]


def test_the_observed_side_honours_a_moved_file(repo):
    tools = TOOLS.replace("@function_tool\ndef send_image", "@function_tool OVERRIDE\ndef send_image")
    plant = "from agents import Agent\nfrom tools import quote, send_image\n\nplant_agent = Agent(name='Plant', tools=[quote, send_image])\n"
    manager = "from agents import Agent\nfrom {home} import plant_agent\nfrom tools import quote\n\n"
    base = commit(
        repo,
        {
            "tools.py": tools.replace(" OVERRIDE", ""),
            "plant.py": plant,
            "manager.py": manager.format(home="plant") + "plant_agent.tools = [quote]\n",
        },
    )
    head = commit(
        repo,
        {
            "tools.py": tools.replace(" OVERRIDE", "(name_override='send_photo')"),
            "plant.py": None,
            "garden.py": plant,
            "manager.py": manager.format(home="garden"),
        },
    )
    result = run(repo, base, head)
    rows = {(row["tool"], row["change"]) for row in result["rows"] if row["agent"] == "plant_agent"}
    assert ("send_image", "removed") not in rows, result["rows"]
    assert ("send_image", "not_established") in rows, result["rows"]
