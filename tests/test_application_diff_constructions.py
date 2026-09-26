"""An unobserved agent is not "no change" (#876).

Regressions from kkmiecik-coder/CRM#5: an OpenAI Agents SDK agent built by
``return Agent(...)`` gained a tool, the reader never saw the builder, and a
test double with resolvable tools made the scope count as established, so the
comparison printed ``compared`` with no rows. A tool name a test file defined
twice refused whole comparisons (alliance-genome/agr_ai_curation#842,
usestrix/strix#1103).
"""

import ast
import re

import pytest
from test_application_diff import SDK, commit, run
from test_application_diff import repo as repo

from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig

# The issue's minimal reproduction: the builder imports its tool list, and a
# test module holds the only agent any reader established on main.
CRM_TOOLS = """from agents import function_tool
@function_tool
def quote(item: str) -> str:
    return item
@function_tool
def send_image(name: str) -> dict:
    return {}
QUOTE_TOOLS = [quote]
"""
CRM_AGENTS = """from agents import Agent
from app.tools import QUOTE_TOOLS, quote
def build_quote_agent():
    return Agent(name="Quote", instructions="q", tools=QUOTE_TOOLS)
"""
CRM_TEST = """from agents import Agent, function_tool
@function_tool
def fake_lookup(q: str) -> str:
    return q
agent = Agent(name="TestAgent", instructions="t", tools=[fake_lookup])
"""

# One module that defines its tools and builds its agent in a function, so the
# builder's list resolves and the gained tool can be a row.
BUILDER = """from agents import Agent, function_tool
@function_tool
def quote(item: str) -> str:
    return item
@function_tool
def send_image(name: str) -> dict:
    return {}
QUOTE_TOOLS = TOOL_LIST
def build_quote_agent():
    return Agent(name="Quote", instructions="q", tools=QUOTE_TOOLS)
"""


def rows(result):
    return [(r["agent"], r["tool"], r["change"]) for r in result["rows"]]


def test_crm_builder_with_test_double_is_not_compared_with_no_change(repo):
    base = commit(
        repo,
        {"app/tools.py": CRM_TOOLS, "app/agents_def.py": CRM_AGENTS, "app/tests/test_turn.py": CRM_TEST},
    )
    head = commit(
        repo, {"app/tools.py": CRM_TOOLS.replace("[quote]", "[quote, send_image]")}
    )
    result = run(repo, base, head, "--scope", "app")
    # Main printed `compared` with no rows: the only agent established was the
    # test double, and `Quote` was never observed.
    assert result["comparison_status"] == "partial"
    assert [a["name"] for a in result["head"]["agents"]] == ["Quote"]
    assert result["head"]["agents"][0]["location"] == "agents_def.py:4"
    assert "tests/test_turn.py" not in {s["path"] for s in result["head"]["sources"]}
    # The imported list is #864's to resolve; until then it is named on Quote.
    assert any(
        g["agent"] == "Quote" and "QUOTE_TOOLS" in g["reason"]
        for g in result["head"]["coverage_gaps"]
    )
    assert "Test code is not read as the application (1 file(s)): tests/test_turn.py" in (
        result["head"]["limits"]
    )


def test_returned_agent_gaining_a_tool_is_an_added_row(repo):
    base = commit(repo, {"agents_def.py": BUILDER.replace("TOOL_LIST", "[quote]")})
    head = commit(repo, {"agents_def.py": BUILDER.replace("TOOL_LIST", "[quote, send_image]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert rows(result) == [("Quote", "send_image", "added")]
    after = result["rows"][0]["after"]
    assert after["binding_location"] == "agents_def.py:10"
    assert after["definition"]["line"] == 6


def test_returned_agent_carries_the_evidence_an_assigned_agent_does(repo):
    assigned = BUILDER.replace(
        "def build_quote_agent():\n    return Agent(",
        "quote_agent = Agent(",
    )
    results = []
    for source in (BUILDER, assigned):
        base = commit(repo, {"agents_def.py": source.replace("TOOL_LIST", "[quote]")})
        head = commit(repo, {"agents_def.py": source.replace("TOOL_LIST", "[quote, send_image]")})
        results.append(run(repo, base, head)["rows"])
    (returned,), (assigned_row,) = results
    assert (returned["agent"], assigned_row["agent"]) == ("Quote", "quote_agent")
    for key in ("tool", "change", "why"):
        assert returned[key] == assigned_row[key]
    for key in ("edge_type", "evidence_basis", "signature", "input_schema", "definition"):
        assert returned["after"][key] == assigned_row["after"][key]


def test_module_level_agent_beside_builders_does_not_hide_them(repo):
    # Main observed only the module-level agent and silently missed Quote.
    extra = '\nsupport = Agent(name="Support", tools=[quote])\n'
    base = commit(repo, {"agents_def.py": BUILDER.replace("TOOL_LIST", "[quote]") + extra})
    head = commit(
        repo, {"agents_def.py": BUILDER.replace("TOOL_LIST", "[quote, send_image]") + extra}
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert sorted(a["name"] for a in result["head"]["agents"]) == ["Quote", "support"]
    assert rows(result) == [("Quote", "send_image", "added")]


@pytest.mark.parametrize(
    "builder",
    [
        "def build():\n    return Agent(name='Built', tools={tools})",
        "async def build():\n    return Agent(name='Built', tools={tools})",
        "class Factory:\n    def build(self):\n        return Agent(name='Built', tools={tools})",
    ],
    ids=["function", "async-function", "method"],
)
def test_every_function_form_of_return_agent_is_observed(repo, builder):
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]") + builder.format(tools="[lookup]")})
    head = commit(
        repo, {"agent.py": SDK.replace("TOOLS", "[lookup]") + builder.format(tools="[lookup, execute]")}
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert rows(result) == [("Built", "execute", "added")]


# Every construction form the guard below walks: established ones must yield
# the gained tool as a row; every other one must be named at its line.
CONSTRUCTIONS = {
    "returned": ("def build():\n    return Agent(name='built', tools={tools})\n", "built"),
    "module-attribute": ("import agents\nmod = agents.Agent(name='mod', tools={tools})\n", "mod"),
    "annotated": ("typed: Agent = Agent(name='typed', tools={tools})\n", "typed"),
    "returned-without-literal-name": (
        "def build(label):\n    return Agent(name=label, tools={tools})\n",
        None,
    ),
    "inline-handoff": (
        "triage = Agent(name='triage', handoffs=[Agent(name='inner', tools={tools})])\n",
        None,
    ),
    "list-of-agents": ("AGENTS = [Agent(name='a1', tools={tools})]\n", None),
    "dict-of-agents": ("REGISTRY = {{'x': Agent(name='x', tools={tools})}}\n", None),
    "attribute-target": (
        "class Holder:\n    def __init__(self):\n        self.agent = Agent(name='held', tools={tools})\n",
        None,
    ),
    "chained-targets": ("first = second = Agent(name='twin', tools={tools})\n", None),
    "walrus": ("if (walrus := Agent(name='w', tools={tools})):\n    pass\n", None),
    "conditional": ("picked = Agent(name='p', tools={tools}) if agent else agent\n", None),
    "lambda": ("make = lambda: Agent(name='lam', tools={tools})\n", None),
    "yield": ("def gen():\n    yield Agent(name='gen', tools={tools})\n", None),
    "argument": ("print(Agent(name='arg', tools={tools}))\n", None),
    "generic": ("typed = Agent[dict](name='typed', tools={tools})\n", None),
    "clone": ("copy = agent.clone(tools={tools})\n", None),
    "subclass": (
        "class Custom(Agent):\n    pass\n\n\ncustom = Custom(name='custom', tools={tools})\n",
        None,
    ),
    "generic-subclass": (
        "class Custom(Agent[dict]):\n    pass\n\n\ncustom = Custom(name='custom', tools={tools})\n",
        None,
    ),
}


def _construction_sites(source: str) -> set[int]:
    """Independent of the reader: every line that visibly builds an agent."""

    def agent_class(node):
        if isinstance(node, ast.Subscript):
            node = node.value
        return (isinstance(node, ast.Name) and node.id == "Agent") or (
            isinstance(node, ast.Attribute) and node.attr == "Agent"
        )

    sites = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and (
            agent_class(node.func)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "clone")
        ):
            sites.add(node.lineno)
        elif isinstance(node, ast.ClassDef) and any(agent_class(b) for b in node.bases):
            sites.add(node.lineno)
    return sites


@pytest.mark.parametrize("form", sorted(CONSTRUCTIONS))
def test_compared_requires_every_construction_site_to_be_accounted_for(repo, form):
    snippet, established = CONSTRUCTIONS[form]
    prefix = SDK.replace("TOOLS", "[lookup]") + "\n"
    base = commit(repo, {"agent.py": prefix + snippet.format(tools="[lookup]")})
    source = prefix + snippet.format(tools="[lookup, execute]")
    head = commit(repo, {"agent.py": source})
    result = run(repo, base, head)
    side = result["head"]
    located = {
        int(agent["location"].rsplit(":", 1)[1])
        for agent in side["agents"]
        if agent["location"] and agent["source"] == "agent.py"
    }
    # Only the census's own gaps account for a site: another gap that merely
    # shares the line (a parent's dynamic handoffs) proves nothing about it.
    named = {
        int(line)
        for gap in side["coverage_gaps"]
        if " is not read (" in gap["reason"] or " is constructed at " in gap["reason"]
        for line in re.findall(r"agent\.py:(\d+)", gap["reason"])
    }
    sites = _construction_sites(source)
    assert sites, form
    unaccounted = sites - located - named
    assert not unaccounted, (form, unaccounted, side["coverage_gaps"])
    if result["comparison_status"] == "compared":
        assert sites <= located, form
    if established:
        assert result["comparison_status"] == "compared"
        assert rows(result) == [(established, "execute", "added")]
    else:
        assert result["comparison_status"] == "partial"
        assert ("agent", "execute", "added") not in rows(result)


def test_builder_parameter_never_resolves_to_a_module_list_it_shadows(tmp_path):
    (tmp_path / "agent.py").write_text(
        SDK.replace('agent = Agent(name="assistant", tools=TOOLS)', "tools = [lookup]\n")
        + "def build(tools):\n    return Agent(name='built', tools=tools)\n"
    )
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agent.py"), None, tmp_path
    )
    (observation,) = loaded.binding_observations
    assert (observation.agent, observation.tool_names, observation.tools_complete) == (
        "built",
        [],
        False,
    )
    assert "dynamic tools expression" in observation.issues[0]


def test_one_identity_built_twice_is_not_a_silent_union(repo):
    variants = (
        "def small():\n    return Agent(name='Assistant', tools={small})\n"
        "def large():\n    return Agent(name='Assistant', tools={large})\n"
    )
    prefix = SDK.replace("TOOLS", "[lookup]") + "\n"
    base = commit(
        repo, {"agent.py": prefix + variants.format(small="[lookup]", large="[lookup, execute]")}
    )
    # `execute` moves from one variant to the other; the union is unchanged.
    head = commit(
        repo, {"agent.py": prefix + variants.format(small="[lookup, execute]", large="[lookup]")}
    )
    result = run(repo, base, head)
    # The union is unchanged, so there is no row, but never `compared`.
    assert result["rows"] == []
    assert result["comparison_status"] == "partial"
    assert any(
        g["agent"] == "Assistant" and "is constructed at agent.py:" in g["reason"]
        for g in result["head"]["coverage_gaps"]
    )


ADK_TWICE = """from google.adk.agents import LlmAgent
def details(pr: int) -> str:
    return str(pr)
def submit(pr: int) -> str:
    return str(pr)
def submit_orchestrated(pr: int) -> str:
    return str(pr)
root_agent = LlmAgent(name="reviewer", tools=[details, submit])
def make_review_agent():
    return LlmAgent(name="reviewer", tools=[details, submit_orchestrated])
"""
# The SDK keys an assigned agent by its variable, so its two sites that
# share a `name=` are two builders.
SDK_TWICE = (
    ADK_TWICE.replace(
        "from google.adk.agents import LlmAgent",
        "from agents import Agent as LlmAgent, function_tool",
    )
    .replace("def ", "@function_tool\ndef ")
    .replace("@function_tool\ndef make_review_agent", "def make_review_agent")
    .replace("root_agent = LlmAgent(", "def make_root():\n    return LlmAgent(")
)


@pytest.mark.parametrize("source", [ADK_TWICE, SDK_TWICE], ids=["adk", "sdk"])
def test_new_identity_built_twice_keeps_its_additions(repo, source):
    # tensorflow#128063: a root agent and a builder share one `name=`. Each
    # union binding is true of some construction; the gap names both lines,
    # so the result is `partial` rather than `compared`.
    base = commit(repo, {"README.md": "empty"})
    head = commit(repo, {"agent.py": source})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert sorted(rows(result)) == [
        ("reviewer", "details", "added"),
        ("reviewer", "submit", "added"),
        ("reviewer", "submit_orchestrated", "added"),
    ]
    sites = [
        f"agent.py:{number}"
        for number, line in enumerate(source.splitlines(), 1)
        if 'LlmAgent(name="reviewer"' in line
    ]
    assert len(sites) == 2
    assert [g["reason"] for g in result["head"]["coverage_gaps"] if g["agent"] == "reviewer"] == [
        f"Agent 'reviewer' is constructed at {', '.join(sites)}; which construction "
        "binds which tool is not established."
    ]


@pytest.mark.parametrize(
    "path",
    ["tests/test_agent.py", "test/helpers.py", "conftest.py", "agent_test.py", "test_turn.py"],
)
def test_test_code_does_not_establish_the_application(repo, path):
    base = commit(repo, {path: SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {path: SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "not_established"
    assert result["rows"] == []
    assert result["head"]["sources"] == []
    assert result["head"]["limits"] == [
        f"Test code is not read as the application (1 file(s)): {path}"
    ]


DUPLICATE = """from agents import Agent, function_tool
def one():
    @function_tool
    def _tool(q: str) -> str:
        return q
    return Agent(name="one", tools=[_tool])
def two():
    @function_tool
    def _tool(q: str) -> str:
        return q
    return Agent(name="two", tools=[_tool])
"""


def test_duplicate_tool_in_a_test_file_is_ignored(repo):
    # agr_ai_curation#842 and strix#1103 exited 2 on a test file like this one.
    base = commit(
        repo,
        {"agent.py": SDK.replace("TOOLS", "[lookup]"), "tests/test_executor.py": DUPLICATE},
    )
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert rows(result) == [("agent", "execute", "added")]
    assert result["head"]["coverage_gaps"] == []


def test_duplicate_tool_in_application_code_is_a_named_limit(repo):
    # livekit-agents#1 exited 2 on a non-test example defining one name twice.
    base = commit(
        repo, {"agent.py": SDK.replace("TOOLS", "[lookup]"), "examples/agent.py": DUPLICATE}
    )
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert rows(result) == [("agent", "execute", "added")]
    assert {
        (g["source"], g["tool"], g["reason"])
        for g in result["head"]["coverage_gaps"]
        if g["agent"] is None
    } == {
        (
            "examples/agent.py",
            "_tool",
            "examples/agent.py defines the tool '_tool' more than once; which "
            "definition an agent binds is not established.",
        )
    }


def test_adk_agent_subclass_is_named_not_silently_absent(repo):
    adk = """from google.adk.agents import LlmAgent
def lookup(query: str) -> str:
    return query
def execute(code: str) -> str:
    return code
root_agent = LlmAgent(name="root", tools=[lookup])
class Helper(LlmAgent):
    pass
helper = Helper(name="helper", tools=TOOLS)
"""
    base = commit(repo, {"agent.py": adk.replace("TOOLS", "[lookup]")})
    head = commit(repo, {"agent.py": adk.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    # Main printed `compared` with no rows: `root` was established, and the
    # helper's gained tool was never observed.
    assert result["comparison_status"] == "partial"
    assert any(
        "agent.py:7" in g["reason"] and "'Helper'" in g["reason"]
        for g in result["head"]["coverage_gaps"]
    )


@pytest.mark.parametrize(
    "call",
    ["Agent(name='cfg', **CONFIG)", "Agent(*ARGS, name='cfg')"],
    ids=["keywords", "positional"],
)
def test_unpacked_arguments_are_not_an_empty_tool_list(tmp_path, call):
    # Main read `Agent(name=..., **config)` as an agent with no tools, complete.
    (tmp_path / "agent.py").write_text(
        SDK.replace('agent = Agent(name="assistant", tools=TOOLS)', f"built = {call}\n")
    )
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agent.py"), None, tmp_path
    )
    (observation,) = loaded.binding_observations
    assert (observation.tools_complete, observation.handoffs_complete) == (False, False)
    assert "unpacks arguments that may set its tools" in observation.issues[0]


def test_scope_selected_inside_a_test_directory_is_read(repo):
    path = "tests/fixtures/app/test_agent.py"
    base = commit(repo, {path: SDK.replace("TOOLS", "[lookup]")})
    head = commit(repo, {path: SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head, "--scope", "tests/fixtures/app")
    assert result["comparison_status"] == "compared"
    assert rows(result) == [("agent", "execute", "added")]
    assert result["head"]["limits"] == []


def test_duplicate_tool_leaves_the_agents_other_tools_compared(repo):
    # Review of #880: the dropped name stayed in the agent's observation, so
    # the graph's agent-level gap made the agent's other additions uncertain.
    source = DUPLICATE.replace(
        "from agents import Agent, function_tool",
        "from agents import Agent, function_tool\n"
        "@function_tool\ndef lookup(q: str) -> str:\n    return q\n"
        "@function_tool\ndef execute(c: str) -> str:\n    return c",
    ) + "agent = Agent(name='a', tools=TOOLS)\n"
    base = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup, _tool]")})
    head = commit(repo, {"agent.py": source.replace("TOOLS", "[lookup, _tool, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert rows(result) == [("agent", "execute", "added")]
    assert [(g["agent"], g["tool"]) for g in result["head"]["coverage_gaps"]] == [(None, "_tool")]


def test_link_onto_test_code_is_a_gap(repo):
    # Review of #880: the alias's target is test code, which is never read, so
    # the link hid the application agent with no gap at all.
    base = commit(repo, {"tests/real.py": SDK.replace("TOOLS", "[lookup]")})
    (repo / "agent.py").symlink_to("tests/real.py")
    head = commit(repo, {"tests/real.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert "Linked Python input: agent.py" in result["head"]["limits"]


def test_test_rule_is_the_same_on_both_sides_of_a_move(repo):
    # Review of #880: `--base-scope tests/app` read test-named files on the base
    # only, so the two sides compared different kinds of input.
    base = commit(repo, {"tests/app/test_agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(
        repo,
        {
            "tests/app/test_agent.py": None,
            "app/test_agent.py": SDK.replace("TOOLS", "[lookup, execute]"),
        },
    )
    result = run(repo, base, head, "--base-scope", "tests/app", "--scope", "app")
    assert rows(result) == [("agent", "execute", "added")]
    assert result["base"]["limits"] == result["head"]["limits"] == []
