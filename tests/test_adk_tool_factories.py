"""A Google ADK tool built by a factory is the function the factory wraps (#865).

``save_memory_tool = create_save_memory_tool()`` in visulate/visulate-for-oracle#526
was an unresolved tool: the reader stopped at the local assignment. The factory's
one unconditional ``return FunctionTool(save_memory_record)`` names the tool, so
the reader follows it — read, never run — and names every factory it cannot
follow, with why.
"""

from __future__ import annotations

import pytest
from test_imported_tool_bindings import _adk, _commit, _compare, _edges, _git, _write

AGENT = '''from google.adk.agents import LlmAgent
from .remote_tool import create_remote_delegate_tool
from common.tools import create_save_memory_tool, create_read_memory_tool


def create_root_agent() -> LlmAgent:
    nl2sql_tool = create_remote_delegate_tool("nl2sql_agent", "http://localhost:10001")
    save_memory_tool = create_save_memory_tool()
    read_memory_tool = create_read_memory_tool()
    return LlmAgent(name="visulate_root_agent", tools=[TOOLS])
'''
REMOTE = '''from google.adk.tools.function_tool import FunctionTool


def create_remote_delegate_tool(agent_name: str, endpoint_url: str) -> FunctionTool:
    async def _delegate(message: str) -> str:
        return message

    _delegate.__name__ = f"delegate_to_{agent_name}"
    return FunctionTool(_delegate)
'''
MEMORY = '''from google.adk.tools.function_tool import FunctionTool


def create_save_memory_tool() -> FunctionTool:
    async def save_memory_record(content: str, filename: str = "") -> str:
        return content

    return FunctionTool(save_memory_record)


def create_read_memory_tool() -> FunctionTool:
    async def read_memory_record(filename: str) -> str:
        return filename

    return FunctionTool(read_memory_record)
'''


def _visulate(tools: str = "nl2sql_tool, read_memory_tool, save_memory_tool") -> dict[str, str | None]:
    return {
        "ai-agent/root_agent/__init__.py": "",
        "ai-agent/root_agent/agent.py": AGENT.replace("TOOLS", tools),
        "ai-agent/root_agent/remote_tool.py": REMOTE,
        "ai-agent/common/__init__.py": "",
        "ai-agent/common/tools.py": MEMORY,
    }


def _why(artifacts) -> dict[str, tuple[str, str]]:
    return {item["reference"]: (item["reason"], item["detail"]) for item in artifacts.unresolved_references}


def test_the_visulate_memory_factories_resolve_to_the_functions_they_wrap(tmp_path):
    (tmp_path / ".git").mkdir()
    _write(tmp_path, _visulate())
    loaded, artifacts = _adk(tmp_path / "ai-agent", "root_agent/agent.py")

    assert _edges(loaded, artifacts) == [
        ("visulate_root_agent", "read_memory_record", "common/tools.py:12"),
        ("visulate_root_agent", "save_memory_record", "common/tools.py:5"),
    ]
    tools = {tool.name: tool for source in loaded for tool in source.tools}
    assert tools["save_memory_record"].function_signature == "save_memory_record(content, filename) -> str"
    (resolution,) = tools["save_memory_record"].extraction["import_resolutions"]
    # Every module read on the way, and what each hop is.
    assert [(step["path"], step["binding"]) for step in resolution["steps"]] == [
        ("root_agent/agent.py", "import"),
        ("common/tools.py", "definition"),
        ("common/tools.py", "factory"),
        ("common/tools.py", "definition"),
    ]
    assert resolution["definition"] == "common/tools.py:5"
    assert {item["path"] for item in resolution["inputs"]} == {"root_agent/agent.py", "common/tools.py"}
    # The remote delegate renames the function it returns: named, with why.
    reason, detail = _why(artifacts)["nl2sql_tool"]
    assert reason == "factory_return"
    assert "'_delegate' is changed or handed on at root_agent/remote_tool.py:8" in detail
    assert "Google ADK agent 'visulate_root_agent' references unresolved tool 'nl2sql_tool'." in artifacts.warnings


def test_a_factory_outside_the_read_scope_is_named_with_the_scope_that_reads_it(tmp_path):
    (tmp_path / ".git").mkdir()
    _write(tmp_path, _visulate())
    loaded, artifacts = _adk(tmp_path / "ai-agent" / "root_agent")

    assert _edges(loaded, artifacts) == []
    reason, detail = _why(artifacts)["save_memory_tool"]
    assert reason == "module_not_found"
    assert detail.startswith("'save_memory_tool' is the tool 'create_save_memory_tool' returns at agent.py:8")
    assert "the repository holds it outside the read scope" in detail


FACTORY = "from google.adk.tools import FunctionTool, LongRunningFunctionTool\n\n\n"
LOOKUP = "def lookup(query: str) -> str:\n    return query\n"


@pytest.mark.parametrize(
    ("factory", "location"),
    [
        ("def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n", "tools.py:5"),
        ("def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(func=inner)\n", "tools.py:5"),
        (
            "def make():\n    def inner(query: str) -> str:\n        return query\n\n    tool = FunctionTool(inner)\n    return tool\n",
            "tools.py:5",
        ),
        ("def make():\n    def inner(query: str) -> str:\n        return query\n\n    return inner\n", "tools.py:5"),
        (LOOKUP + "\n\ndef make():\n    return FunctionTool(lookup)\n", "tools.py:4"),
        (
            "def _make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n\n\n"
            "def make():\n    return _make()\n",
            "tools.py:5",
        ),
    ],
    ids=["wrapped", "wrapped-by-keyword", "wrapped-then-named", "plain-function", "module-function", "factory-of-factory"],
)
def test_a_factory_s_one_return_is_its_tool(tmp_path, factory, location):
    agent = "from google.adk.agents import Agent\nfrom tools import make\n\nTOOL\n\nroot = Agent(name='root', tools=[ITEM])\n"
    for tool, item in (("tool = make()", "tool"), ("", "make()"), ("def build():\n    return make()\n\n\ntool = build()", "tool")):
        root = tmp_path / item.replace("()", "_call") / tool[:4].strip()
        _write(root, {"tools.py": FACTORY + factory, "agent.py": agent.replace("TOOL", tool).replace("ITEM", item)})
        loaded, artifacts = _adk(root)
        assert [location for _, _, location in _edges(loaded, artifacts)] == [location], (tool, artifacts.warnings)
        assert artifacts.warnings == []


def test_a_long_running_factory_tool_is_long_running(tmp_path):
    factory = "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return LongRunningFunctionTool(inner)\n"
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n"})
    loaded, _ = _adk(tmp_path)
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert tool.annotations.get("long_running") is True


@pytest.mark.parametrize(
    ("factory", "why"),
    [
        (
            "def make(fast):\n    def inner(query: str) -> str:\n        return query\n\n    def other(query: str) -> str:\n        return query\n\n"
            "    if fast:\n        return FunctionTool(inner)\n    return FunctionTool(other)\n",
            "it returns from 2 places",
        ),
        (
            "def make(fast):\n    def inner(query: str) -> str:\n        return query\n\n    if fast:\n        return FunctionTool(inner)\n",
            "it returns only under a condition",
        ),
        ("def make():\n    return make()\n", "it calls itself on the way"),
        (
            "import functools\n\n\ndef make():\n    @functools.lru_cache\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n",
            "'inner' (tools.py:9) is decorated",
        ),
        (
            "def make(name):\n    def inner(query: str) -> str:\n        return query\n\n    setattr(inner, '__name__', name)\n    return FunctionTool(inner)\n",
            "'inner' is changed or handed on at tools.py:8",
        ),
        (
            "def rename(function):\n    function.__name__ = 'other'\n\n\ndef make():\n    def inner(query: str) -> str:\n        return query\n\n"
            "    rename(inner)\n    return FunctionTool(inner)\n",
            "'inner' is changed or handed on at tools.py:12",
        ),
        (
            "def make():\n    def inner(query: str) -> str:\n        return query\n\n    tool = FunctionTool(inner)\n    tool.name = 'other'\n    return tool\n",
            "it changes the 'tool' it returns at tools.py:9",
        ),
        (
            "def make(inner):\n    return FunctionTool(inner)\n",
            "'inner' is bound by a parameter",
        ),
        ("def make():\n    yield FunctionTool(print)\n", "it is a generator"),
        ("def make():\n    return [FunctionTool(print)]\n", "returns an expression"),
    ],
    ids=[
        "two-returns",
        "conditional-return",
        "recursive",
        "decorated-function",
        "renamed-by-setattr",
        "handed-to-a-helper",
        "tool-renamed",
        "parameter",
        "generator",
        "list",
    ],
)
def test_a_factory_this_read_cannot_follow_is_named_with_why(tmp_path, factory, why):
    agent = "from google.adk.agents import Agent\nfrom tools import make\n\n\ndef build():\n    tool = make(True)\n    return Agent(name='root', tools=[tool])\n"
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)

    assert _edges(loaded, artifacts) == []
    assert "Google ADK agent 'root' references unresolved tool 'tool'." in artifacts.warnings
    reason, detail = _why(artifacts)["tool"]
    assert reason == "factory_return", detail
    assert why in detail


@pytest.mark.parametrize(
    ("agent", "reason"),
    [
        (
            # Bound only under a condition: which tool, if any, is not known.
            "def build(flag):\n    if flag:\n        tool = make()\n    return Agent(name='root', tools=[tool])\n",
            "local_binding",
        ),
        (
            # The agent's module changes the tool it was handed.
            "def build():\n    tool = make()\n    tool.name = 'other'\n    return Agent(name='root', tools=[tool])\n",
            "factory_return",
        ),
    ],
    ids=["conditional-binding", "changed-at-the-call-site"],
)
def test_the_call_site_of_a_factory_is_read_too(tmp_path, agent, reason):
    factory = "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    _write(
        tmp_path,
        {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\n\n" + agent},
    )
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert _why(artifacts)["tool"][0] == reason


def test_a_third_party_factory_keeps_the_answer_it_had(tmp_path):
    _write(
        tmp_path,
        {
            "agent.py": "from google.adk.agents import Agent\nfrom vendor.tools import make_search\n\n\n"
            "def build():\n    tool = make_search()\n    return Agent(name='root', tools=[tool, make_search()])\n"
        },
    )
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    # The local binding stays the named stop; the inline call the expression it was.
    assert _why(artifacts)["tool"][0] == "local_binding"
    assert "Google ADK agent 'root' has a tool expression that could not be statically resolved." in artifacts.warnings
    assert "make_search()" not in _why(artifacts)


def test_application_diff_shows_the_memory_tools_a_factory_adds(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _visulate("read_memory_tool"))
    head = _commit(tmp_path, _visulate("read_memory_tool, save_memory_tool"))
    result = _compare(tmp_path, base, head, "--scope", "ai-agent")

    assert result["comparison_status"] == "compared"
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [
        ("visulate_root_agent", "save_memory_record", "added"),
    ]
    after = result["rows"][0]["after"]
    assert after["definition"]["source"] == "ai-agent/common/tools.py"
    assert after["definition"]["line"] == 5


def test_resolving_two_tools_does_not_complete_the_visulate_surface(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _visulate("nl2sql_tool"))
    head = _commit(tmp_path, _visulate())
    result = _compare(tmp_path, base, head, "--scope", "ai-agent")

    # The base's delegate is not read, so it may have been either memory tool:
    # the two are candidate additions, and the delegate stays named.
    assert result["comparison_status"] == "partial"
    assert [(row["tool"], row["change"], row["candidate_change"]) for row in result["rows"]] == [
        ("read_memory_record", "not_established", "added"),
        ("save_memory_record", "not_established", "added"),
    ]
    assert any(
        "nl2sql_tool" in gap["reason"] for gap in result["head"]["coverage_gaps"]
    ), result["head"]["coverage_gaps"]


def test_a_factory_s_function_changing_is_an_implementation_change(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _visulate())
    head = _commit(tmp_path, {"ai-agent/common/tools.py": MEMORY.replace("return content", "return content.upper()")})
    result = _compare(tmp_path, base, head, "--scope", "ai-agent")
    rows = [(row["agent"], row["tool"], row["change"]) for row in result["rows"]]
    assert ("visulate_root_agent", "save_memory_record", "changed") in rows


# -- a tools list built in the agent's function ---------------------------------

LIST_TOOLS = (
    "from google.adk.tools import FunctionTool\n\n\n"
    "def intake(name: str) -> str:\n    return name\n\n\n"
    "def route(zone: str) -> str:\n    return zone\n\n\n"
    "def triage(case: str) -> str:\n    return case\n"
)


def _list_agent(body: str) -> dict[str, str]:
    return {
        "tools.py": LIST_TOOLS,
        "agent.py": "from google.adk.agents import LlmAgent\nfrom google.adk.tools import FunctionTool\n"
        "from tools import intake, route, triage\n\n\ndef build(enabled):\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
        + "    return LlmAgent(name='assign', tools=tools)\n",
    }


@pytest.mark.parametrize(
    ("body", "bound"),
    [
        ("tools = [FunctionTool(intake)]\ntools.append(FunctionTool(route))", ["intake", "route"]),
        ("tools = [intake]\ntools.extend([route, triage])", ["intake", "route", "triage"]),
        ("tools = [intake]\ntools.insert(0, route)", ["intake", "route"]),
        ("tools = [intake]\ntools += [route]", ["intake", "route"]),
        ("tools: list = (intake, route)", ["intake", "route"]),
    ],
    ids=["append", "extend", "insert", "augmented", "annotated-tuple"],
)
def test_a_tools_list_built_in_the_agent_s_function_is_read(tmp_path, body, bound):
    _write(tmp_path, _list_agent(body))
    loaded, artifacts = _adk(tmp_path)
    assert [tool for _, tool, _ in _edges(loaded, artifacts)] == bound
    assert artifacts.warnings == []


def test_a_tool_added_under_a_condition_is_named_beside_the_list(tmp_path):
    # MuhammadVT/smart-assignment#46: the triage tool joins only when enabled.
    _write(tmp_path, _list_agent("tools = [FunctionTool(intake), route]\nif enabled:\n    tools.append(FunctionTool(triage))"))
    loaded, artifacts = _adk(tmp_path)

    assert [tool for _, tool, _ in _edges(loaded, artifacts)] == ["intake", "route"]
    message = (
        "Google ADK agent 'assign' adds a tool to its tools list only under a condition or in a loop "
        "at agent.py:9, which is not established."
    )
    assert message in artifacts.warnings
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tools_complete is False
    assert observation.issues == [message]


@pytest.mark.parametrize(
    "body",
    [
        "tools = [intake]\nregister(tools)",
        "tools = [intake]\ntools.remove(intake)",
        "tools = [intake]\nother = tools\nother.append(route)",
        "tools = [intake]\nhelper = lambda: tools.append(route)",
        "tools = [intake]\ntools.extend(more_tools())",
        "tools = [*base_tools(), intake]",
        "if enabled:\n    tools = [intake]\nelse:\n    tools = [route]",
    ],
    ids=["handed-to-a-call", "removed", "aliased", "nested-function", "extended-by-a-call", "starred", "rebound"],
)
def test_any_other_use_of_the_list_keeps_it_dynamic(tmp_path, body):
    _write(tmp_path, _list_agent(body))
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert "Google ADK agent 'assign' uses a dynamic tools expression." in artifacts.warnings


def test_a_module_level_tools_list_stays_dynamic(tmp_path):
    # Another module may change a module's list: not read here.
    _write(
        tmp_path,
        {
            "tools.py": LIST_TOOLS,
            "agent.py": "from google.adk.agents import LlmAgent\nfrom tools import intake\n\n"
            "TOOLS = [intake]\nroot = LlmAgent(name='assign', tools=TOOLS)\n",
        },
    )
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert "Google ADK agent 'assign' uses a dynamic tools expression." in artifacts.warnings


def test_application_diff_does_not_add_a_conditional_tool(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _list_agent("tools = [FunctionTool(intake), route]"))
    head = _commit(
        tmp_path, _list_agent("tools = [FunctionTool(intake), route]\nif enabled:\n    tools.append(FunctionTool(triage))")
    )
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "partial"
    assert [row["tool"] for row in result["rows"] if row["change"] == "added"] == []
    assert any("only under a condition" in gap["reason"] for gap in result["head"]["coverage_gaps"])


# -- #865 review, round 1 -------------------------------------------------------


SQL = (
    "from google.adk.tools import FunctionTool\n\n\n"
    "def make_sql_tool(readonly):\n    target = 'TARGET'\n\n"
    "    def run_sql(query: str) -> str:\n        if readonly and query.lower().startswith('drop'):\n"
    "            raise ValueError(target)\n        return query\n\n    return FunctionTool(run_sql)\n"
)


@pytest.mark.parametrize(
    ("base", "head"),
    [
        ({"READONLY": "True", "TARGET": "staging"}, {"READONLY": "False", "TARGET": "staging"}),
        ({"READONLY": "True", "TARGET": "staging"}, {"READONLY": "True", "TARGET": "production"}),
    ],
    ids=["call-argument", "closure-value"],
)
def test_what_a_factory_closes_over_is_part_of_the_tool(tmp_path, base, head):
    def files(values):
        return {
            "tools.py": SQL.replace("TARGET", values["TARGET"]),
            "agent.py": "from google.adk.agents import Agent\nfrom tools import make_sql_tool\n\n"
            f"root = Agent(name='root', tools=[make_sql_tool(readonly={values['READONLY']})])\n",
        }

    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, files(base))
    after = _commit(tmp_path, files(head))
    result = _compare(tmp_path, before, after)
    assert [(row["tool"], row["change"]) for row in result["rows"]] == [("run_sql", "changed")]


def test_a_factory_docstring_is_not_a_change(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, _visulate())
    after = _commit(
        tmp_path,
        {"ai-agent/common/tools.py": MEMORY.replace("return content", '"""Save a record."""\n        return content')},
    )
    result = _compare(tmp_path, before, after, "--scope", "ai-agent")
    assert [row for row in result["rows"] if row["tool"] == "save_memory_record"] == []


@pytest.mark.parametrize(
    ("body", "bound"),
    [
        (
            "tools = [intake]\nhelper = LlmAgent(name='helper', tools=tools)\ntools.append(route)\n"
            "return LlmAgent(name='assign', tools=tools, sub_agents=[helper])",
            {"helper": ["intake"], "assign": ["intake", "route"]},
        ),
        (
            "tools = [intake]\nif enabled:\n    return LlmAgent(name='fast', tools=tools)\ntools.append(route)\n"
            "return LlmAgent(name='assign', tools=tools)",
            {"fast": ["intake"], "assign": ["intake", "route"]},
        ),
        ("tools = [intake]\nreturn LlmAgent(name='assign', tools=tools)\ntools.append(route)", {"assign": ["intake"]}),
    ],
    ids=["built-before-the-append", "early-return", "after-return"],
)
def test_an_addition_after_the_agent_is_built_is_not_its_tool(tmp_path, body, bound):
    files = _list_agent(body)
    # The body builds its own agents: drop the trailing one ``_list_agent`` adds.
    head, _, tail = files["agent.py"].rpartition("    return LlmAgent(name='assign', tools=tools)\n")
    files["agent.py"] = head + tail
    _write(tmp_path, files)
    loaded, artifacts = _adk(tmp_path)
    edges: dict[str, list[str]] = {}
    for agent, tool, _ in _edges(loaded, artifacts):
        edges.setdefault(agent, []).append(tool)
    assert edges == bound
    assert not [warning for warning in artifacts.warnings if "only under a condition" in warning]


@pytest.mark.parametrize(
    "factory",
    [
        LOOKUP + "\n\ndef make():\n    lookup.__name__ = 'search'\n    return FunctionTool(lookup)\n",
        LOOKUP + "\n\ndef make():\n    setattr(lookup, '__name__', 'search')\n    return FunctionTool(lookup)\n",
    ],
    ids=["attribute", "setattr"],
)
def test_a_module_function_renamed_in_the_factory_is_named(tmp_path, factory):
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n"})
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert "'lookup' is changed or handed on at tools.py" in _why(artifacts)["make()"][1]


def test_two_factories_nesting_one_name_are_named_not_a_crash(tmp_path):
    factories = (
        "def make_a():\n    def run(query: str) -> str:\n        return query\n\n    return FunctionTool(run)\n\n\n"
        "def make_b():\n    def run(order: str) -> str:\n        return order\n\n    return FunctionTool(run)\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    agent = (
        "from google.adk.agents import Agent\nfrom tools import make_a, make_b\n\n"
        "a = Agent(name='a', tools=[make_a()])\nb = Agent(name='b', tools=[make_b()])\n"
    )
    before = _commit(tmp_path, {"tools.py": FACTORY + LOOKUP, "agent.py": agent.replace("make_a()", "").replace("make_b()", "")})
    after = _commit(tmp_path, {"tools.py": FACTORY + factories, "agent.py": agent})
    result = _compare(tmp_path, before, after)
    assert result["comparison_status"] == "partial"
    assert any("defines 'run' more than once" in gap["reason"] for gap in result["head"]["coverage_gaps"])


def test_an_async_factory_is_named(tmp_path):
    factory = "async def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n"})
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert "coroutine" in _why(artifacts)["make()"][1]


def test_a_shadowed_function_tool_in_the_factory_is_not_proven(tmp_path):
    factory = (
        "from wrappers import Wrapped\n\nFunctionTool = Wrapped\n\n\n"
        "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n"})
    loaded, _ = _adk(tmp_path)
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert tool.extraction_confidence != "high"


@pytest.mark.parametrize(
    "imports",
    [
        "try:\n    from vendor.tools import SearchTool\nexcept ImportError:\n    from vendor.legacy import SearchTool\n",
        "from vendor.tools import *\n",
        "class SearchTool:\n    pass\n",
    ],
    ids=["try-except-import", "wildcard", "class"],
)
def test_a_call_that_is_not_an_application_factory_keeps_its_answer(tmp_path, imports):
    _write(tmp_path, {"agent.py": "from google.adk.agents import Agent\n" + imports + "\nroot = Agent(name='root', tools=[SearchTool()])\n"})
    loaded, artifacts = _adk(tmp_path)
    assert "Google ADK agent 'root' has a tool expression that could not be statically resolved." in artifacts.warnings
    assert artifacts.unresolved_references == []


@pytest.mark.parametrize(
    "use",
    ["rename(tool)", "setattr(tool.func, '__name__', 'other')"],
    ids=["handed-to-a-helper", "function-renamed"],
)
def test_a_factory_tool_changed_at_the_call_site_is_named(tmp_path, use):
    factory = "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    agent = (
        "from google.adk.agents import Agent\nfrom tools import make, rename\n\n\n"
        f"def build():\n    tool = make()\n    {use}\n    return Agent(name='root', tools=[tool])\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory + "\n\ndef rename(tool):\n    tool.name = 'other'\n", "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert _why(artifacts)["tool"][0] == "factory_return"


def test_one_record_per_unresolved_factory_call(tmp_path):
    factory = "def make():\n    return make()\n"
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make(), make(), make()])\n"})
    _, artifacts = _adk(tmp_path)
    assert len(artifacts.unresolved_references) == 1


# -- #865 review, round 2 -------------------------------------------------------

SQL_FACTORY = (
    "from google.adk.tools import FunctionTool\n\n\n"
    "def make_sql_tool(readonly=True):\n    def run_sql(query: str) -> str:\n"
    "        if readonly and query.lower().startswith('drop'):\n            raise ValueError(query)\n"
    "        return query\n\n    return FunctionTool(run_sql)\n"
)


def _sql_rows(tmp_path, base: str, head: str, extra: dict | None = None):
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, {"tools.py": SQL_FACTORY, **(extra or {}), "agent.py": base})
    after = _commit(tmp_path, {"agent.py": head})
    result = _compare(tmp_path, before, after)
    return result, [(row["agent"], row["tool"], row["change"]) for row in result["rows"]]


AGENT_HEAD = "from google.adk.agents import Agent\nfrom tools import make_sql_tool\n"


def test_a_local_value_the_factory_is_given_is_part_of_the_tool(tmp_path):
    body = AGENT_HEAD + "\n\ndef build():\n    ro = RO\n    return Agent(name='root', tools=[make_sql_tool(readonly=ro)])\n"
    _, rows = _sql_rows(tmp_path, body.replace("RO", "True"), body.replace("RO", "False"))
    assert rows == [("root", "run_sql", "changed")]


def test_an_imported_constant_the_factory_is_given_is_part_of_the_tool(tmp_path):
    agent = AGENT_HEAD + "from config import READONLY\n\nroot = Agent(name='root', tools=[make_sql_tool(readonly=READONLY)])\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, {"tools.py": SQL_FACTORY, "config.py": "READONLY = True\n", "agent.py": agent})
    after = _commit(tmp_path, {"config.py": "READONLY = False\n"})
    result = _compare(tmp_path, before, after)
    assert [(row["agent"], row["tool"], row["change"]) for row in result["rows"]] == [("root", "run_sql", "changed")]


def test_a_value_this_read_cannot_name_leaves_the_implementation_unknown(tmp_path):
    body = AGENT_HEAD + "\n\ndef build(ro):\n    return Agent(name='root', tools=[make_sql_tool(readonly=ro)])\n\n\nroot = build(RO)\n"
    result, rows = _sql_rows(tmp_path, body.replace("RO", "True"), body.replace("RO", "False"))
    assert rows == [("root", "run_sql", "not_established")]
    assert result["comparison_status"] == "partial"
    (row,) = result["rows"]
    assert any(
        "a value this read cannot name (readonly=ro at agent.py:" in reason for reason in row["uncertainty"]["head"]
    ), row["uncertainty"]


@pytest.mark.parametrize(
    "head",
    [
        "from google.adk.agents import Agent\nfrom tools import make_sql_tool as mk\n\nroot = Agent(name='root', tools=[mk(readonly=True)])\n",
        AGENT_HEAD + "\nroot = Agent(name='root', tools=[make_sql_tool(True)])\n",
        AGENT_HEAD + "\nroot = Agent(name='root', tools=[make_sql_tool()])\n",
        AGENT_HEAD + "\nroot = Agent(name='root', tools=[make_sql_tool(readonly=True)])\nother = Agent(name='other', tools=[make_sql_tool(readonly=False)])\n",
    ],
    ids=["alias", "positional", "default", "another-agent-calls-it"],
)
def test_the_same_call_spelled_otherwise_is_not_a_change(tmp_path, head):
    base = AGENT_HEAD + "\nroot = Agent(name='root', tools=[make_sql_tool(readonly=True)])\n"
    _, rows = _sql_rows(tmp_path, base, head)
    assert [row for row in rows if row[0] == "root"] == []


@pytest.mark.parametrize(
    "factory",
    [
        "import impl\n\n\ndef make():\n    impl.search.__name__ = 'lookup'\n    return FunctionTool(impl.search)\n",
        "import impl\n\n\ndef make():\n    setattr(impl.search, '__name__', 'lookup')\n    return impl.search\n",
        "def make():\n    from impl import search\n\n    search.__name__ = 'lookup'\n    return FunctionTool(search)\n",
    ],
    ids=["module-attribute", "setattr-on-module-attribute", "local-import"],
)
def test_a_function_renamed_in_the_factory_however_spelled_is_named(tmp_path, factory):
    _write(
        tmp_path,
        {
            "impl.py": "def search(query: str) -> str:\n    return query\n",
            "tools.py": FACTORY + factory,
            "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n",
        },
    )
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []
    assert "is changed or handed on" in _why(artifacts)["make()"][1]


@pytest.mark.parametrize(
    "body",
    [
        "tool = make()\n    tools = [tool]\n    return Agent(name='root', tools=tools)",
        "tool = make()\n    logging.info('bound %s', tool.name)\n    return Agent(name='root', tools=[tool])",
        "tool = make()\n    return Agent(name='root', instruction=f'Use {tool.name}', tools=[tool])",
        "fn = make_plain()\n    return Agent(name='root', tools=[FunctionTool(func=fn, require_confirmation=True)])",
    ],
    ids=["local-list", "logged", "instruction", "wrapped-for-approval"],
)
def test_reading_a_factory_tool_where_it_is_bound_is_not_a_change(tmp_path, body):
    factory = (
        "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n\n\n"
        "def make_plain():\n    def plain(query: str) -> str:\n        return query\n\n    return plain\n"
    )
    agent = (
        "import logging\n\nfrom google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\n"
        f"from tools import make, make_plain\n\n\ndef build():\n    {body}\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    expected = ["plain"] if "make_plain" in body else ["inner"]
    assert [tool for _, tool, _ in _edges(loaded, artifacts)] == expected, artifacts.unresolved_references


def test_a_class_method_of_the_same_name_is_not_a_second_tool(tmp_path):
    factory = (
        "class Runner:\n    def run(self):\n        return None\n\n\n"
        "def make():\n    def run(query: str) -> str:\n        return query\n\n    return FunctionTool(run)\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n"})
    loaded, artifacts = _adk(tmp_path)
    assert [tool for _, tool, _ in _edges(loaded, artifacts)] == ["run"]


def test_a_function_local_adk_import_in_the_factory_is_proven(tmp_path):
    factory = (
        "def make():\n    from google.adk.tools import FunctionTool\n\n"
        "    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    )
    _write(tmp_path, {"tools.py": factory, "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n"})
    loaded, _ = _adk(tmp_path)
    (tool,) = [tool for source in loaded for tool in source.tools]
    assert tool.extraction_confidence == "high"


# -- #865 review, round 3 -------------------------------------------------------


@pytest.mark.parametrize(
    ("base_prefix", "head_prefix", "argument"),
    [
        ("policy = {'readonly': True}\n    ", "policy = {'readonly': True}\n    policy['readonly'] = False\n    ", "policy"),
        ("allowed = ['select']\n    ", "allowed = ['select']\n    allowed.append('delete')\n    ", "allowed"),
    ],
    ids=["dict-item-changed", "list-appended"],
)
def test_a_mutable_value_changed_after_it_is_bound_is_not_data(tmp_path, base_prefix, head_prefix, argument):
    body = AGENT_HEAD + "\n\ndef build():\n    PREFIXreturn Agent(name='root', tools=[make_sql_tool(readonly=ARG)])\n"
    result, rows = _sql_rows(
        tmp_path,
        body.replace("PREFIX", base_prefix).replace("ARG", argument),
        body.replace("PREFIX", head_prefix).replace("ARG", argument),
    )
    assert rows == [("root", "run_sql", "not_established")], rows


def test_a_module_level_mutable_constant_is_not_data(tmp_path):
    agent = AGENT_HEAD + "from config import POLICY\n\nroot = Agent(name='root', tools=[make_sql_tool(readonly=POLICY)])\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, {"tools.py": SQL_FACTORY, "config.py": "POLICY = {'readonly': True}\n", "agent.py": agent})
    after = _commit(tmp_path, {"config.py": "POLICY = {'readonly': True}\nPOLICY['readonly'] = False\n"})
    result = _compare(tmp_path, before, after)
    assert result["comparison_status"] == "partial"
    assert any("cannot name" in gap["reason"] for gap in result["head"]["coverage_gaps"])


def test_an_unnamed_value_in_an_untouched_module_is_a_gap_not_a_row(tmp_path):
    body = AGENT_HEAD + "\n\ndef build(ro):\n    return Agent(name='root', tools=[make_sql_tool(readonly=ro)])\n\n\nroot = build(True)\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, {"tools.py": SQL_FACTORY, "agent.py": body, "README.md": "a\n"})
    after = _commit(tmp_path, {"README.md": "b\n"})
    result = _compare(tmp_path, before, after)
    assert result["rows"] == []
    assert result["comparison_status"] == "partial"
    assert any("readonly=ro at agent.py:" in gap["reason"] for gap in result["head"]["coverage_gaps"])


@pytest.mark.parametrize(
    ("factory", "call"),
    [
        (
            "def upper(text: str) -> str:\n    return text.upper()\n\n\ndef make(transform):\n"
            "    def run(query: str) -> str:\n        return transform(query)\n\n    return FunctionTool(run)\n",
            "make(upper)",
        ),
        (
            SQL_FACTORY.replace("from google.adk.tools import FunctionTool\n\n\n", "")
            + "\n\ndef make_named(readonly):\n    return make_sql_tool(readonly=readonly)\n",
            "make_named(True)",
        ),
    ],
    ids=["function-argument", "forwarding-factory"],
)
def test_a_value_the_read_can_follow_is_data(tmp_path, factory, call):
    names = call.replace("(", ", ").replace(")", "").replace(", True", "")
    agent = f"from google.adk.agents import Agent\nfrom tools import {names}\n\nroot = Agent(name='root', tools=[{call}])\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, {"tools.py": FACTORY + factory, "agent.py": agent, "README.md": "a\n"})
    after = _commit(tmp_path, {"README.md": "b\n"})
    result = _compare(tmp_path, before, after)
    assert result["rows"] == [] and result["comparison_status"] == "compared", result["head"]["coverage_gaps"]


@pytest.mark.parametrize(
    "use",
    [
        "rename_fn(tool.func)",
        "bucket = [tool]\n    for each in bucket:\n        each.name = 'renamed'",
        "rename_all([tool])",
    ],
    ids=["part-handed-on", "local-list-looped", "list-handed-on"],
)
def test_a_factory_tool_handed_on_at_the_call_site_is_named(tmp_path, use):
    factory = "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    helpers = "\n\ndef rename_fn(fn):\n    fn.__name__ = 'renamed'\n\n\ndef rename_all(tools):\n    for tool in tools:\n        tool.name = 'renamed'\n"
    agent = (
        "from google.adk.agents import Agent\nfrom tools import make, rename_fn, rename_all\n\n\n"
        f"def build():\n    tool = make()\n    {use}\n    return Agent(name='root', tools=[tool])\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory + helpers, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []


def test_another_name_for_a_module_function_in_the_factory_is_named(tmp_path):
    factory = "import impl\n\n\ndef make():\n    f = impl.search\n    f.__name__ = 'lookup'\n    return FunctionTool(impl.search)\n"
    _write(
        tmp_path,
        {
            "impl.py": "def search(query: str) -> str:\n    return query\n",
            "tools.py": FACTORY + factory,
            "agent.py": "from google.adk.agents import Agent\nfrom tools import make\n\nroot = Agent(name='root', tools=[make()])\n",
        },
    )
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []


def test_wrapping_a_factory_tool_again_is_not_a_tool(tmp_path):
    factory = "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    agent = (
        "from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\nfrom tools import make\n\n\n"
        "def build():\n    tool = make()\n    return Agent(name='root', tools=[FunctionTool(func=tool)])\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []


# -- #865 review, round 4 -------------------------------------------------------


@pytest.mark.parametrize(
    ("base_prefix", "head_prefix", "argument"),
    [
        (
            "base = {'readonly': True}\n    policy = base\n    ",
            "base = {'readonly': True}\n    policy = base\n    policy['readonly'] = False\n    ",
            "policy",
        ),
        (
            "policy = ({'readonly': True},)\n    ",
            "policy = ({'readonly': True},)\n    policy[0]['readonly'] = False\n    ",
            "policy",
        ),
    ],
    ids=["through-an-alias", "inside-a-tuple"],
)
def test_a_mutable_value_reached_another_way_is_not_data(tmp_path, base_prefix, head_prefix, argument):
    body = AGENT_HEAD + "\n\ndef build():\n    PREFIXreturn Agent(name='root', tools=[make_sql_tool(readonly=ARG)])\n"
    _, rows = _sql_rows(
        tmp_path,
        body.replace("PREFIX", base_prefix).replace("ARG", argument),
        body.replace("PREFIX", head_prefix).replace("ARG", argument),
    )
    assert rows == [("root", "run_sql", "not_established")], rows


def test_a_module_tuple_holding_a_dict_is_not_data(tmp_path):
    agent = AGENT_HEAD + "from config import POLICY\n\nroot = Agent(name='root', tools=[make_sql_tool(readonly=POLICY)])\n"
    _git(tmp_path, "init", "-q", "-b", "main")
    before = _commit(tmp_path, {"tools.py": SQL_FACTORY, "config.py": "POLICY = ({'readonly': True},)\n", "agent.py": agent})
    after = _commit(tmp_path, {"config.py": "POLICY = ({'readonly': True},)\nPOLICY[0]['readonly'] = False\n"})
    result = _compare(tmp_path, before, after)
    assert result["comparison_status"] == "partial"
    assert any("cannot name" in gap["reason"] for gap in result["head"]["coverage_gaps"])


@pytest.mark.parametrize(
    "use",
    [
        "f = tool.func\n    f.__name__ = 'renamed'",
        "rename_all([tool.func])",
        "if True:\n        bucket = [tool]\n    for each in bucket:\n        each.name = 'renamed'",
    ],
    ids=["part-aliased", "part-in-a-container", "list-in-a-block"],
)
def test_a_factory_tool_handed_on_another_way_is_named(tmp_path, use):
    factory = "def make():\n    def inner(query: str) -> str:\n        return query\n\n    return FunctionTool(inner)\n"
    helpers = "\n\ndef rename_all(items):\n    for item in items:\n        item.__name__ = 'renamed'\n"
    agent = (
        "from google.adk.agents import Agent\nfrom tools import make, rename_all\n\n\n"
        f"def build():\n    tool = make()\n    {use}\n    return Agent(name='root', tools=[tool])\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory + helpers, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == []


@pytest.mark.parametrize(
    ("factory", "wrapped"),
    [
        ("def make():\n    def run(query: str) -> str:\n        return query.upper()\n\n    return run\n", True),
        (
            "def make():\n    def run(query: str) -> str:\n        return query\n\n"
            "    tool: FunctionTool = FunctionTool(run)\n    return tool\n",
            False,
        ),
    ],
    ids=["plain-function-with-an-expression-inside", "annotated-tool"],
)
def test_only_a_plain_function_a_factory_returns_is_wrapped(tmp_path, factory, wrapped):
    agent = (
        "from google.adk.agents import Agent\nfrom google.adk.tools import FunctionTool\nfrom tools import make\n\n\n"
        "def build():\n    fn = make()\n    return Agent(name='root', tools=[FunctionTool(func=fn, require_confirmation=True)])\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    assert [tool for _, tool, _ in _edges(loaded, artifacts)] == (["run"] if wrapped else [])


@pytest.mark.parametrize(
    "append",
    ["tools.append(route)", "if enabled:\n        tools.append(route)"],
    ids=["unconditional", "conditional"],
)
def test_a_factory_tool_in_a_list_that_grows_is_read(tmp_path, append):
    factory = (
        "def make():\n    def run(query: str) -> str:\n        return query\n\n    return FunctionTool(run)\n\n\n"
        "def route(zone: str) -> str:\n    return zone\n"
    )
    agent = (
        "from google.adk.agents import Agent\nfrom tools import make, route\n\n\n"
        f"def build(enabled):\n    tool = make()\n    tools = [tool]\n    {append}\n    return Agent(name='root', tools=tools)\n"
    )
    _write(tmp_path, {"tools.py": FACTORY + factory, "agent.py": agent})
    loaded, artifacts = _adk(tmp_path)
    assert "run" in [tool for _, tool, _ in _edges(loaded, artifacts)]
