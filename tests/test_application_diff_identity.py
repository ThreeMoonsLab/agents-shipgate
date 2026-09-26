"""Framework identity and unobserved agents must never read as removals.

Regressions from speechmatics/speechmatics-academy#142: a LiveKit agent was
read as the OpenAI Agents SDK (its ``function_tool`` and ``Agent`` share the
SDK's spellings), and its refactor into an ``Agent`` subclass then reported
every bound tool as REMOVED under ``compared``.
"""

import pytest
from test_application_diff import SDK, commit, run
from test_application_diff import repo as repo

from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig

# Reduced from the PR's main.py: tools are closures inside the entrypoint, and
# the head passes them to an Agent subclass through ``super().__init__``.
LIVEKIT_BASE = """from livekit import agents
from livekit.agents import Agent, AgentSession, function_tool


async def entrypoint(ctx: agents.JobContext) -> None:
    @function_tool
    async def focus_on_speaker(speaker_ids: list[str]) -> str:
        return "focused"

    @function_tool
    async def ignore_speaker(speaker_id: str) -> str:
        return "ignored"

    agent = Agent(instructions="focus", tools=[focus_on_speaker, ignore_speaker])
    await AgentSession().start(agent=agent, room=ctx.room)
"""
LIVEKIT_HEAD = """from livekit import agents
from livekit.agents import Agent, AgentSession, function_tool


class FocusAgent(Agent):
    def __init__(self, tools: list) -> None:
        super().__init__(instructions="focus", tools=tools)


async def entrypoint(ctx: agents.JobContext) -> None:
    @function_tool
    async def focus_on_speaker(speaker_ids: list[str]) -> str:
        return "focused"

    @function_tool
    async def ignore_speaker(speaker_id: str) -> str:
        return "ignored"

    agent = FocusAgent(tools=[focus_on_speaker, ignore_speaker])
    await AgentSession().start(agent=agent, room=ctx.room)
"""

SDK_SUBCLASS = SDK.replace(
    'agent = Agent(name="assistant", tools=TOOLS)',
    """class SupportAgent(Agent):
    def __init__(self, tools):
        super().__init__(name="assistant", tools=tools)


agent = SupportAgent(tools=TOOLS)""",
)
# Constructions the SDK reader does not read. Each keeps the agent's identity
# (the name it is assigned to) while hiding its tool list from the reader.
UNREAD_CONSTRUCTIONS = {
    "subclass": SDK_SUBCLASS,
    "generic": SDK.replace("Agent(name=", "Agent[dict](name="),
    "factory": SDK.replace(
        'agent = Agent(name="assistant", tools=TOOLS)',
        'def build(tools):\n    return Agent(name="assistant", tools=tools)\nagent = build(TOOLS)',
    ),
    "clone": SDK.replace(
        'agent = Agent(name="assistant", tools=TOOLS)',
        'agent = Agent(name="assistant").clone(tools=TOOLS)',
    ),
}


@pytest.mark.parametrize(
    "spelling",
    [
        LIVEKIT_BASE,
        LIVEKIT_BASE.replace("@function_tool", "@agents.function_tool").replace(
            "agent = Agent(", "agent = agents.Agent("
        ),
        LIVEKIT_BASE.replace("from livekit.agents import", "from .agents import"),
    ],
    ids=["livekit-from-import", "livekit-module-attribute", "relative-agents-package"],
)
def test_other_agents_package_is_not_the_openai_sdk(tmp_path, spelling):
    (tmp_path / "main.py").write_text(spelling)
    detected = detect_workspace(tmp_path, max_python_files=10)
    assert "openai_agents_sdk" not in {f.type for f in detected.frameworks if f.candidate_files}
    # A manifest that declares the file as SDK still reads no SDK wiring from it.
    source = ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="main.py")
    loaded = load_openai_sdk_static_tools(source, None, tmp_path)
    assert loaded.tools == []
    assert loaded.binding_observations == []


def test_livekit_subclass_refactor_is_not_an_sdk_removal(repo):
    base = commit(repo, {"main.py": LIVEKIT_BASE})
    head = commit(repo, {"main.py": LIVEKIT_HEAD})
    result = run(repo, base, head)
    assert result["rows"] == []
    assert result["comparison_status"] == "not_established"
    assert result["base"]["sources"] == result["head"]["sources"] == []


def test_sdk_tools_beside_a_livekit_agent_keep_their_own_identity(tmp_path):
    (tmp_path / "main.py").write_text(
        "from livekit import agents as lk\n"
        + SDK.replace("TOOLS", "[lookup]")
        + "@lk.function_tool\ndef hang_up() -> str:\n    return 'bye'\n"
        + "voice = lk.Agent(instructions='x', tools=[hang_up])\n"
    )
    source = ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="main.py")
    loaded = load_openai_sdk_static_tools(source, None, tmp_path)
    assert sorted(t.name for t in loaded.tools) == ["execute", "lookup"]
    assert [(o.agent, o.tool_names) for o in loaded.binding_observations] == [("agent", ["lookup"])]


FACTORY = '''def {name}():
    from {module} import Agent as Builder, function_tool

    @function_tool
    def {name}_tool(query: str) -> str:
        return query

    {name}_agent = Builder(name="{name}", tools=[{name}_tool])
    return {name}_agent
'''


@pytest.mark.parametrize(
    "source",
    [
        # PR #873 review: an import in a sibling function decided identity.
        FACTORY.format(name="sdk", module="agents")
        + "\n\n"
        + FACTORY.format(name="voice", module="livekit.agents"),
        # A function's own import shadows the module's SDK import.
        "from agents import Agent as Builder, function_tool\n\n\n"
        + FACTORY.format(name="voice", module="livekit.agents")
        + "\n\n@function_tool\ndef sdk_tool(query: str) -> str:\n    return query\n"
        + 'sdk_agent = Builder(name="sdk", tools=[sdk_tool])\n',
    ],
    ids=["sibling-function-import", "inner-import-shadows-module"],
)
def test_import_provenance_is_resolved_in_the_enclosing_scope(tmp_path, source):
    (tmp_path / "main.py").write_text(source)
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="main.py"), None, tmp_path
    )
    assert [t.name for t in loaded.tools] == ["sdk_tool"]
    assert [(o.agent, o.tool_names) for o in loaded.binding_observations] == [
        ("sdk_agent", ["sdk_tool"])
    ]
    assert loaded.warnings == []


@pytest.mark.parametrize("spelling", ["agent", "exported as agent"])
def test_import_bound_agent_name_is_not_a_removal(repo, spelling):
    # PR #873 review: the head module still binds `agent`, through an import
    # of an unsupported factory's result, so absence is not established.
    exported = spelling.split()[0]
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    factory = SDK.replace(
        'agent = Agent(name="assistant", tools=TOOLS)',
        f'def build():\n    return Agent(name="assistant", tools=[lookup])\n{exported} = build()',
    )
    head = commit(
        repo,
        {"agent_factory.py": factory, "agent.py": f"from agent_factory import {spelling}\n"},
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    # #876: the factory's `return Agent(name="assistant", ...)` is itself an
    # observed construction, at a path the base does not have. `agent`, the
    # name its result is bound to, is still not a removal.
    assert [
        (r["agent"], r["agent_source"], r["tool"], r["change"], r["candidate_change"])
        for r in result["rows"]
    ] == [
        ("agent", "agent.py", "lookup", "not_established", "removed"),
        ("assistant", "agent_factory.py", "lookup", "added", None),
    ]
    assert list(result["rows"][0]["uncertainty"]) == ["head"]


@pytest.mark.parametrize("construction", sorted(UNREAD_CONSTRUCTIONS))
def test_unread_head_construction_is_not_a_removal(repo, construction):
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    head = commit(
        repo, {"agent.py": UNREAD_CONSTRUCTIONS[construction].replace("TOOLS", "[lookup, execute]")}
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [
        (r["agent"], r["tool"], r["change"], r["candidate_change"]) for r in result["rows"]
    ] == [
        ("agent", "execute", "not_established", "removed"),
        ("agent", "lookup", "not_established", "removed"),
    ]
    assert all(list(r["uncertainty"]) == ["head"] for r in result["rows"])
    assert any(
        g["agent"] == "agent" and g["source"] == "agent.py" for g in result["head"]["coverage_gaps"]
    )


def test_handoff_reference_is_not_an_observed_construction(repo):
    # `handoffs=[worker]` makes `worker` a graph agent without reading its own
    # construction, so it cannot stand in for the worker's tool list.
    triage = '\ntriage = Agent(name="triage", handoffs=[worker])\n'
    base = commit(
        repo,
        {
            "agent.py": SDK.replace("TOOLS", "[lookup]")
            + '\nworker = Agent(name="worker", tools=[execute])'
            + triage
        },
    )
    head = commit(
        repo,
        {
            "agent.py": SDK.replace("TOOLS", "[lookup]")
            + "\nclass Worker(Agent):\n    pass\n"
            + 'worker = Worker(name="worker", tools=[execute])'
            + triage
        },
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [(r["agent"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("worker", "execute", "not_established")
    ]


def test_unread_base_construction_is_not_an_addition(repo):
    base = commit(repo, {"agent.py": SDK_SUBCLASS.replace("TOOLS", "[lookup, execute]")})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert {(r["change"], r["candidate_change"]) for r in result["rows"]} == {
        ("not_established", "added")
    }
    assert all(list(r["uncertainty"]) == ["base"] for r in result["rows"])


def test_adk_subclass_refactor_is_not_a_removal(repo):
    source = """from google.adk.agents import Agent, LlmAgent

def lookup(query: str) -> str:
    return query
"""
    base = commit(
        repo, {"agent.py": source + 'root_agent = Agent(name="helper", tools=[lookup])\n'}
    )
    head = commit(
        repo,
        {
            "agent.py": source
            + "class HelperAgent(LlmAgent):\n    pass\n"
            + 'root_agent = HelperAgent(name="helper", tools=[lookup])\n'
        },
    )
    result = run(repo, base, head)
    assert result["comparison_status"] == "partial"
    assert [(r["agent"], r["change"], r["candidate_change"]) for r in result["rows"]] == [
        ("helper", "not_established", "removed")
    ]


def test_deleted_agent_is_still_an_established_removal(repo):
    worker = '\nworker = Agent(name="worker", tools=[execute])\n'
    base = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]") + worker})
    head = commit(repo, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    result = run(repo, base, head)
    assert result["comparison_status"] == "compared"
    assert [(r["agent"], r["tool"], r["change"]) for r in result["rows"]] == [
        ("worker", "execute", "removed")
    ]
