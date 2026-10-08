"""Tools lists built by an expression are read member by member (#909).

An agent whose ``tools=`` is ``[*BASE, *([extra] if wanted else [])]``,
``base + (extra or [])`` or a filter over a list used to read as "uses a
dynamic tools expression": none of its bindings were compared, even when every
member was a plain tool the reader already understood. These tests pin the
shared resolver (``inputs/list_expressions.py``) per shape, each with the
negative case where an operand cannot be read, then the two readers and
``diff --application``:

- a member that cannot be read keeps the agent incomplete and is named where
  it is, and resolving the rest never makes the agent complete;
- a member held only under a condition is never shown as unconditional, and a
  change to the condition alone is a ``changed`` row whose direction is not
  established.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.inputs.list_expressions import MAX_DEPTH, ListExpressions
from agents_shipgate.inputs.python_imports import ScopeIndex, _module_bindings
from tests.test_imported_tool_bindings import _adk, _commit, _compare, _edges, _git, _sdk, _write

# -- the resolver, one shape at a time ------------------------------------------


def _resolve(source: str, expression: str = "TOOLS"):
    """Resolve ``expression`` as the last line of ``source`` would read it."""

    tree = ast.parse(f"{source}\nagent = Agent(tools={expression})\n")
    lists = ListExpressions(
        ref="agent.py",
        tree=tree,
        scopes=ScopeIndex(tree),
        bindings=_module_bindings(tree)[0],
        module=None,
        resolver=None,
        agent_reads=lambda call, keyword: keyword == "tools",
    )
    call = tree.body[-1].value
    assert isinstance(call, ast.Call)
    return lists.resolve(call.keywords[0].value)


def _members(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(ast.unparse(member.expr), member.conditions) for member in result.members]


def _unread(result) -> list[str]:
    return [f"{part.reason} ({part.location})" for part in result.unresolved]


def test_a_literal_list_and_its_spread_are_read():
    result = _resolve("BASE = [a, b]", "[*BASE, c]")
    assert _members(result) == [("a", ()), ("b", ()), ("c", ())]
    assert result.complete
    assert result.members[0].via == ("BASE (agent.py:1)",)


def test_a_spread_of_a_call_is_named_and_the_rest_kept():
    result = _resolve("", "[*load_tools(), c]")
    assert _members(result) == [("c", ())]
    assert _unread(result) == ["a call to `load_tools`, whose result is not read (agent.py:2)"]


def test_concatenation_reads_both_operands():
    result = _resolve("BASE = [a]", "BASE + [b] + (c,)")
    assert _members(result) == [("a", ()), ("b", ()), ("c", ())]
    assert result.complete


def test_concatenation_with_an_unread_operand_names_it():
    result = _resolve("BASE = [a]", "BASE + extra_tools()")
    assert _members(result) == [("a", ())]
    assert _unread(result) == ["a call to `extra_tools`, whose result is not read (agent.py:2)"]


def test_a_conditional_member_carries_its_condition():
    result = _resolve("", "[a, *([b] if wanted else [c])]")
    assert _members(result) == [("a", ()), ("b", ("`wanted`",)), ("c", ("not `wanted`",))]
    assert result.complete


def test_a_constant_condition_picks_its_branch():
    assert _members(_resolve("", "[a] + ([b] if True else [c])")) == [("a", ()), ("b", ())]


def test_a_conditional_branch_that_cannot_be_read_is_named():
    result = _resolve("", "[a] + ([b] if wanted else more())")
    assert _members(result) == [("a", ()), ("b", ("`wanted`",))]
    assert _unread(result) == ["a call to `more`, whose result is not read (agent.py:2)"]


def test_or_takes_the_first_operand_known_not_empty():
    assert _members(_resolve("EXTRA = [b]", "[a] + (EXTRA or [c])")) == [("a", ()), ("b", ())]
    # Known empty: never the value.
    assert _members(_resolve("EXTRA = None", "[a] + (EXTRA or [c])")) == [("a", ()), ("c", ())]
    # Not known: each operand, under the condition that selects it.
    assert _members(_resolve("EXTRA = [b] if wanted else []", "[a] + (EXTRA or [c])")) == [
        ("a", ()),
        ("b", ("`EXTRA` is not empty", "`wanted`")),
        ("c", ("`EXTRA` is empty",)),
    ]


def test_or_with_a_parameter_names_the_parameter():
    # mosnin/realestatecrm#612: ``base_tools + (extra_tools or [])``.
    tree = ast.parse(
        "def build(extra_tools):\n    base_tools = [a]\n    return Agent(tools=base_tools + (extra_tools or []))\n"
    )
    lists = ListExpressions(
        ref="chippi.py", tree=tree, scopes=ScopeIndex(tree), bindings=_module_bindings(tree)[0],
        module=None, resolver=None, agent_reads=lambda call, keyword: keyword == "tools",
    )
    call = tree.body[0].body[-1].value
    result = lists.resolve(call.keywords[0].value)
    assert _members(result) == [("a", ())]
    assert _unread(result) == [
        "`extra_tools` is a parameter of `build`, so its value comes from a caller (chippi.py:3)"
    ]


def test_a_filter_over_a_list_keeps_each_member_conditional():
    result = _resolve("BROKER_TOOLS = [a, b]", "[tool for tool in BROKER_TOOLS if tool.name in ALLOWED]")
    condition = "the filter `tool.name in ALLOWED` keeps it"
    assert _members(result) == [("a", (condition,)), ("b", (condition,))]
    result = _resolve("BROKER_TOOLS = [a, b]", "list(filter(is_allowed, BROKER_TOOLS))")
    assert _members(result) == [("a", ("the filter `is_allowed` keeps it",)), ("b", ("the filter `is_allowed` keeps it",))]
    # ``list``, ``tuple`` and ``sorted`` hold the same members.
    assert _members(_resolve("BASE = [a]", "sorted(tuple(BASE))")) == [("a", ())]


def test_a_comprehension_that_builds_new_elements_is_named():
    result = _resolve("BASE = [a, b]", "[wrap(tool) for tool in BASE]")
    assert result.members == ()
    assert _unread(result) == ["a comprehension that builds new elements is not followed (agent.py:2)"]


def test_a_filter_over_an_unread_list_is_named():
    result = _resolve("", "[tool for tool in registry() if tool.enabled]")
    assert _unread(result) == ["a call to `registry`, whose result is not read (agent.py:2)"]


def test_a_name_bound_once_in_the_function_is_read():
    tree = ast.parse(
        "def build(wanted):\n"
        "    tools = [a, *([b] if wanted else [])]\n"
        "    return Agent(tools=tools)\n"
    )
    lists = ListExpressions(
        ref="agent.py", tree=tree, scopes=ScopeIndex(tree), bindings=_module_bindings(tree)[0],
        module=None, resolver=None, agent_reads=lambda call, keyword: keyword == "tools",
    )
    result = lists.resolve(tree.body[0].body[-1].value.keywords[0].value)
    assert _members(result) == [("a", ()), ("b", ("`wanted`",))]
    assert result.members[0].via == ("tools (agent.py:2)",)


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("TOOLS = [a]\nTOOLS = [b]", "`TOOLS` is bound more than once, or conditionally, in agent.py (agent.py:3)"),
        ("if wanted:\n    TOOLS = [a]", "`TOOLS` is bound more than once, or conditionally, in agent.py (agent.py:3)"),
        ("TOOLS = [a]\nTOOLS.append(b)", "`TOOLS` may be changed in place after it is built (agent.py:1)"),
        ("TOOLS = [a]\nregister(TOOLS)", "`TOOLS` may be changed in place after it is built (agent.py:1)"),
        ("TOOLS = [a]\nglobals()['TOOLS'] = [b]", "`TOOLS` may be changed in place after it is built (agent.py:1)"),
        ("def TOOLS():\n    return [a]", "`TOOLS` is a function, not a list of tools (agent.py:3)"),
        ("", "`TOOLS` is not bound where the list is read (agent.py:2)"),
    ],
    ids=["rebound", "conditional", "appended", "handed-on", "reflective", "function", "unbound"],
)
def test_a_name_the_reader_cannot_hold_still_is_named(source, reason):
    result = _resolve(source)
    assert result.members == ()
    assert _unread(result) == [reason]


def test_a_list_only_read_stays_readable():
    # Iterated, measured and logged: none of these change it.
    result = _resolve("TOOLS = [a]\nfor tool in TOOLS:\n    print(tool)\nCOUNT = len(TOOLS)")
    assert _members(result) == [("a", ())]


def test_an_attribute_of_the_object_is_named():
    tree = ast.parse("class Bot:\n    def build(self):\n        return Agent(tools=self.tools)\n")
    lists = ListExpressions(
        ref="bot.py", tree=tree, scopes=ScopeIndex(tree), bindings=_module_bindings(tree)[0],
        module=None, resolver=None, agent_reads=lambda call, keyword: keyword == "tools",
    )
    result = lists.resolve(tree.body[0].body[0].body[0].value.keywords[0].value)
    assert _unread(result) == ["`self.tools` is an attribute of the object, set elsewhere (bot.py:3)"]


def test_nesting_and_self_reference_are_bounded():
    deep = "[a]"
    for _ in range(MAX_DEPTH + 2):
        deep = f"({deep} + [])"
    result = _resolve("", deep)
    assert any("nests further than the reader follows" in reason for reason in _unread(result))
    # ``A = B`` alone is a second name, which may change the list; a spread
    # only reads it, so two lists spreading each other reach the cycle bound.
    result = _resolve("A = [*B]\nB = [*A]", "A")
    assert result.members == ()
    assert _unread(result) == ["`A` refers to itself (agent.py:1)"]


# -- the OpenAI Agents SDK reader -----------------------------------------------

SDK_TOOLS = '''from agents import function_tool


@function_tool
def load_and_validate(path: str) -> str:
    """Load and validate."""
    return path


@function_tool
def summarize(text: str) -> str:
    """Summarize."""
    return text


@function_tool
def prepare_finance_handoff(note: str) -> str:
    """Prepare a handoff."""
    return note


FINANCE_TOOLS = [load_and_validate, summarize]
'''


def _sdk_agent(tools: str, preamble: str = "") -> dict[str, str]:
    return {
        "tools.py": SDK_TOOLS,
        "agent.py": "from agents import Agent\n"
        "from tools import FINANCE_TOOLS, load_and_validate, prepare_finance_handoff, summarize\n"
        f"{preamble}\nfinance = Agent(name='Finance', tools={tools})\n",
    }


def _observation(loaded):
    (observation,) = loaded.binding_observations
    return observation


def test_sdk_reads_a_spread_of_another_module_s_list(tmp_path):
    # Tiendat2703/MIS_TALENT#7: ``[*FINANCE_TOOLS, *([handoff] if wanted else [])]``.
    _write(
        tmp_path,
        _sdk_agent(
            "[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]",
            "want_handoff_tool = True\n",
        ),
    )
    loaded = _sdk(tmp_path, "agent.py")
    observation = _observation(loaded)
    assert observation.tools_complete is True
    assert observation.tool_names == ["load_and_validate", "summarize", "prepare_finance_handoff"]
    assert observation.tool_conditions == {"prepare_finance_handoff": ["`want_handoff_tool`"]}
    assert not any("tools expression" in warning for warning in loaded.warnings)
    # Each member is the definition its own module names.
    assert _edges([loaded]) == [
        ("finance", "load_and_validate", "tools.py:5"),
        ("finance", "prepare_finance_handoff", "tools.py:17"),
        ("finance", "summarize", "tools.py:11"),
    ]


def test_sdk_concatenation_binds_what_the_one_list_form_binds(tmp_path):
    # #584: ``[a] + [b]`` yields the edges ``[a, b]`` does, with no warning.
    _write(tmp_path / "joined", _sdk_agent("[load_and_validate] + [summarize]"))
    _write(tmp_path / "single", _sdk_agent("[load_and_validate, summarize]"))
    joined, single = _sdk(tmp_path / "joined", "agent.py"), _sdk(tmp_path / "single", "agent.py")
    assert _edges([joined]) == _edges([single])
    assert joined.warnings == single.warnings == []
    assert joined.recovery_evidence == []


def test_sdk_partly_read_list_keeps_the_agent_incomplete(tmp_path):
    _write(tmp_path, _sdk_agent("[*FINANCE_TOOLS, *plugin_tools()]"))
    loaded = _sdk(tmp_path, "agent.py")
    observation = _observation(loaded)
    assert observation.tools_complete is False
    assert observation.tool_names == ["load_and_validate", "summarize"]
    (reason,) = observation.issues
    assert reason == (
        "OpenAI Agents SDK agent 'finance' at agent.py:4 has a tools list it reads only in part; its "
        "binding graph is incomplete. Not read: a call to `plugin_tools`, whose result is not read "
        "(agent.py:4)."
    )
    (fact,) = loaded.recovery_evidence
    assert (fact.recovery.kind, fact.recovery.reason) == ("unresolved", "sdk_tools_expression_unresolved")


def test_sdk_imported_list_changed_in_its_module_is_named(tmp_path):
    files = _sdk_agent("[*FINANCE_TOOLS]")
    files["tools.py"] += "FINANCE_TOOLS.append(prepare_finance_handoff)\n"
    _write(tmp_path, files)
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tools_complete is False
    assert observation.tool_names == []
    assert "`FINANCE_TOOLS` in tools.py may be changed in place or rebound" in observation.issues[0]


def test_sdk_imported_handoff_list_names_its_agents_not_itself(tmp_path):
    # ``handoffs=SPECIALISTS`` used to bind one handoff named ``SPECIALISTS``.
    _write(
        tmp_path,
        {
            "specialists.py": "from agents import Agent\n"
            "billing = Agent(name='Billing')\nrefunds = Agent(name='Refunds')\n"
            "SPECIALISTS = [billing, *([refunds] if REFUNDS_ENABLED else [])]\n",
            "triage.py": "from agents import Agent\nfrom specialists import SPECIALISTS\n"
            "triage = Agent(name='Triage', handoffs=SPECIALISTS)\n",
        },
    )
    observation = _observation(_sdk(tmp_path, "triage.py"))
    assert observation.handoff_names == ["billing", "refunds"]
    assert observation.handoff_conditions == {"refunds": ["`REFUNDS_ENABLED`"]}
    assert observation.handoffs_complete is True


def test_sdk_unread_handoffs_are_named_beside_the_read_ones(tmp_path):
    _write(
        tmp_path,
        {
            "triage.py": "from agents import Agent, handoff\n"
            "billing = Agent(name='Billing')\n"
            "triage = Agent(name='Triage', handoffs=[billing, handoff(billing), *more_agents()])\n",
        },
    )
    (observation,) = [item for item in _sdk(tmp_path, "triage.py").binding_observations if item.agent == "triage"]
    assert observation.handoff_names == []
    assert observation.handoffs_complete is False
    assert any("constructor identity is not established" in issue for issue in observation.issues)
    for issue in [
        "OpenAI Agents SDK agent 'triage' has dynamic handoffs at triage.py:3. Not read: a call to "
        "`more_agents`, whose result is not read (triage.py:3).",
        "OpenAI Agents SDK agent 'triage' at triage.py:3 hands off to `handoff(billing)`, which is not "
        "resolved to an agent: it is not a name.",
    ]:
        assert issue in observation.issues


def test_sdk_a_change_through_one_agent_reaches_every_agent_sharing_the_list(tmp_path):
    # ``BASE or []`` is BASE itself when BASE is not empty: the same object.
    _write(
        tmp_path,
        {
            "tools.py": SDK_TOOLS,
            "agent.py": "from agents import Agent\nfrom tools import load_and_validate, summarize\n"
            "BASE = [load_and_validate]\n"
            "first = Agent(name='First', tools=BASE or [])\n"
            "second = Agent(name='Second', tools=BASE)\n"
            "second.tools.append(summarize)\n",
        },
    )
    observations = {item.agent: item for item in _sdk(tmp_path, "agent.py").binding_observations}
    assert observations["second"].tools_complete is False
    assert observations["first"].tools_complete is False


def test_sdk_constructions_that_differ_only_in_a_condition_are_not_one_agent(tmp_path):
    _write(
        tmp_path,
        {
            "tools.py": SDK_TOOLS,
            "agent.py": "from agents import Agent\nfrom tools import load_and_validate\n"
            "def build(premium):\n"
            "    if premium:\n"
            "        return Agent(name='Quote', tools=[load_and_validate])\n"
            "    return Agent(name='Quote', tools=[*([load_and_validate] if premium else [])])\n",
        },
    )
    observations = _sdk(tmp_path, "agent.py").binding_observations
    assert len(observations) == 2
    assert all("constructed more than once" in item.issues[-1] for item in observations)


# -- the Google ADK reader ------------------------------------------------------

ADK_TOOLS = '''def load_and_validate(path: str) -> str:
    """Load and validate."""
    return path


def summarize(text: str) -> str:
    """Summarize."""
    return text


FINANCE_TOOLS = [load_and_validate, summarize]
'''


def test_adk_reads_another_module_s_list_with_that_module_s_names(tmp_path):
    _write(
        tmp_path,
        {
            "tools.py": ADK_TOOLS,
            "agent.py": "from google.adk.agents import Agent\nfrom tools import FINANCE_TOOLS\n\n"
            "def escalate(case: str) -> str:\n    return case\n\n"
            "root_agent = Agent(name='finance', tools=[*FINANCE_TOOLS, *([escalate] if URGENT else [])])\n",
        },
    )
    loaded, artifacts = _adk(tmp_path)
    assert _edges(loaded, artifacts) == [
        ("finance", "escalate", "agent.py:4"),
        ("finance", "load_and_validate", "tools.py:1"),
        ("finance", "summarize", "tools.py:6"),
    ]
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tools_complete is True
    assert observation.tool_conditions == {"escalate": ["`URGENT`"]}
    assert artifacts.warnings == []
    # Read, never proven for ``scan``: another module could change the list.
    for source in loaded:
        for tool in source.tools:
            assert "dynamic_tools_expression" in tool.extraction["surface_gaps"]


def test_adk_unread_part_is_a_limit_on_its_agent_with_its_location(tmp_path):
    _write(
        tmp_path,
        {
            "tools.py": ADK_TOOLS,
            "agent.py": "from google.adk.agents import Agent\nfrom tools import FINANCE_TOOLS\n\n"
            "root_agent = Agent(name='finance', tools=FINANCE_TOOLS + plugin_tools())\n",
        },
    )
    loaded, artifacts = _adk(tmp_path)
    assert [tool for _, tool, _ in _edges(loaded, artifacts)] == ["load_and_validate", "summarize"]
    message = (
        "Google ADK agent 'finance' at agent.py:4 has a tools list it reads only in part; its binding "
        "graph is incomplete. Not read: a call to `plugin_tools`, whose result is not read (agent.py:4)."
    )
    assert message in artifacts.warnings
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tools_complete is False
    assert observation.issues == [message]


def test_adk_starred_element_no_longer_reads_complete(tmp_path):
    # ``tools=[a, *TOOLS]`` with TOOLS unbound used to bind ``a`` and call the
    # agent's list complete, naming the starred element only on the file.
    _write(
        tmp_path,
        {
            "agent.py": "from google.adk.agents import Agent\n\n"
            "def lookup(q: str) -> str:\n    return q\n\n"
            "root_agent = Agent(name='helper', tools=[lookup, *EXTRA_TOOLS])\n",
        },
    )
    loaded, artifacts = _adk(tmp_path)
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tool_names == ["lookup"]
    assert observation.tools_complete is False
    assert "`EXTRA_TOOLS` is not bound where the list is read (agent.py:6)" in observation.issues[0]


def test_adk_sub_agents_spread_and_condition(tmp_path):
    _write(
        tmp_path,
        {
            "agent.py": "from google.adk.agents import Agent\n\n"
            "billing = Agent(name='billing')\nrefunds = Agent(name='refunds')\n"
            "SPECIALISTS = [billing]\n"
            "root_agent = Agent(name='triage', sub_agents=[*SPECIALISTS, *([refunds] if REFUNDS else [])])\n",
        },
    )
    _, artifacts = _adk(tmp_path)
    (record,) = [record for record in artifacts.sub_agents if record["agent_name"] == "triage"]
    assert record["sub_agent_count"] == 2
    assert record["sub_agents"] == ["billing", "refunds"]
    assert record["conditions"] == {"refunds": ["`REFUNDS`"]}


def test_adk_sub_agents_partly_read_stay_unnamed_in_count(tmp_path):
    _write(
        tmp_path,
        {
            "agent.py": "from google.adk.agents import Agent\n\n"
            "billing = Agent(name='billing')\n"
            "root_agent = Agent(name='triage', sub_agents=[billing, *more_agents()])\n",
        },
    )
    _, artifacts = _adk(tmp_path)
    (record,) = [record for record in artifacts.sub_agents if record["agent_name"] == "triage"]
    # The graph reads a None count as "has sub-agents that were not statically named".
    assert record["sub_agent_count"] is None
    assert record["sub_agents"] == ["billing"]


# -- diff --application -----------------------------------------------------------


def _rows(result) -> list[tuple[str, str, str]]:
    return [(row["agent"], row["tool"], row["change"]) for row in result["rows"]]


def test_application_diff_reports_a_changed_tool_held_through_a_spread(tmp_path):
    # MIS_TALENT#7: ``load_and_validate`` changes, and ``Finance_Agent`` holds it
    # through ``[*FINANCE_TOOLS, ...]``: its row now appears.
    files = _sdk_agent(
        "[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]",
        "want_handoff_tool = True\n",
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, files)
    head = _commit(tmp_path, {"tools.py": SDK_TOOLS.replace("    return path", "    return path.strip()")})
    result = _compare(tmp_path, base, head)

    assert result["application_comparison_schema_version"] == "0.3"
    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("finance", "load_and_validate", "changed")]


def test_application_diff_shows_a_conditional_addition_with_its_condition(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _sdk_agent("[*FINANCE_TOOLS]", "want_handoff_tool = True\n"))
    head = _commit(
        tmp_path,
        _sdk_agent(
            "[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]",
            "want_handoff_tool = True\n",
        ),
    )
    result = _compare(tmp_path, base, head)

    (row,) = result["rows"]
    assert (row["tool"], row["change"]) == ("prepare_finance_handoff", "added")
    assert row["after"]["bound_when"] == ["`want_handoff_tool`"]
    assert row["why"] == (
        "The source now binds this callable to this agent, only when `want_handoff_tool`."
    )


@pytest.mark.parametrize(
    ("head_tools", "after", "why"),
    [
        (
            "[*FINANCE_TOOLS, *([prepare_finance_handoff] if not want_handoff_tool else [])]",
            ["`not want_handoff_tool`"],
            "only when `not want_handoff_tool` at the head",
        ),
        ("[*FINANCE_TOOLS, prepare_finance_handoff]", None, "unconditionally at the head"),
    ],
    ids=["condition-changed", "now-unconditional"],
)
def test_application_diff_a_condition_change_alone_is_a_change_without_direction(
    tmp_path, head_tools, after, why
):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(
        tmp_path,
        _sdk_agent(
            "[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]",
            "want_handoff_tool = True\n",
        ),
    )
    head = _commit(tmp_path, _sdk_agent(head_tools, "want_handoff_tool = True\n"))
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "compared"
    (row,) = result["rows"]
    assert (row["tool"], row["change"], row["uncertainty"]) == ("prepare_finance_handoff", "changed", {})
    assert row["before"]["bound_when"] == ["`want_handoff_tool`"]
    assert row["after"].get("bound_when") == after
    assert row["why"].startswith(
        "Only the condition changed: bound only when `want_handoff_tool` at the base and "
    )
    assert why in row["why"]
    assert "is not established" in row["why"]
    assert row["review_question"].startswith("Should finance hold prepare_finance_handoff ")


def test_application_diff_text_prints_the_condition(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _sdk_agent("[*FINANCE_TOOLS]", "want_handoff_tool = True\n"))
    head = _commit(
        tmp_path,
        _sdk_agent(
            "[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]",
            "want_handoff_tool = True\n",
        ),
    )
    result = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(tmp_path), "--base", base, "--head", head]
    )
    assert result.exit_code == 0, result.output
    assert "    bound only when `want_handoff_tool`" in result.output


def test_application_diff_a_partly_read_list_is_never_compared(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _sdk_agent("[*FINANCE_TOOLS, *plugin_tools()]"))
    head = _commit(tmp_path, {"tools.py": SDK_TOOLS.replace("    return text", "    return text[:80]")})
    result = _compare(tmp_path, base, head)

    # Never ``compared`` while a member is unread, and the unread part is
    # named on its agent. A tool both sides bind is still a row: what the
    # unread part holds cannot unbind it.
    assert result["comparison_status"] == "partial"
    assert _rows(result) == [("finance", "summarize", "changed")]
    assert any(
        gap["agent"] == "finance"
        and "a call to `plugin_tools`, whose result is not read (agent.py:4)" in gap["reason"]
        for gap in result["head"]["coverage_gaps"]
    )


def test_application_diff_handoff_through_an_imported_list(tmp_path):
    specialists = (
        "from agents import Agent\nbilling = Agent(name='Billing')\nrefunds = Agent(name='Refunds')\n"
        "SPECIALISTS = [billing]\n"
    )
    triage = (
        "from agents import Agent\nfrom specialists import SPECIALISTS\n"
        "triage = Agent(name='Triage', handoffs=SPECIALISTS)\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"specialists.py": specialists, "triage.py": triage})
    head = _commit(
        tmp_path,
        {"specialists.py": specialists.replace("[billing]", "[billing, *([refunds] if REFUNDS else [])]")},
    )
    result = _compare(tmp_path, base, head)

    (row,) = result["rows"]
    assert (row["agent"], row["change"]) == ("triage", "added")
    assert row["tool"].endswith(":refunds")
    assert row["after"]["bound_when"] == ["`REFUNDS`"]


def test_application_diff_adk_sub_agent_condition(tmp_path):
    agent = (
        "from google.adk.agents import Agent\n\n"
        "billing = Agent(name='billing')\nrefunds = Agent(name='refunds')\n"
        "root_agent = Agent(name='triage', sub_agents=SUBS)\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"agent.py": agent.replace("SUBS", "[billing]")})
    head = _commit(tmp_path, {"agent.py": agent.replace("SUBS", "[billing, *([refunds] if REFUNDS else [])]")})
    result = _compare(tmp_path, base, head)

    added = [row for row in result["rows"] if row["change"] == "added"]
    assert [row["tool"].rsplit(":", 1)[-1] for row in added] == ["refunds"]
    assert added[0]["after"]["bound_when"] == ["`REFUNDS`"]


def test_application_diff_reads_the_concatenation_without_a_recovery_gap(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _sdk_agent("[load_and_validate]"))
    head = _commit(tmp_path, _sdk_agent("[load_and_validate] + [summarize]"))
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "compared"
    assert _rows(result) == [("finance", "summarize", "added")]


def test_the_comparison_never_runs_the_list(tmp_path):
    # A list built by running code is named, never evaluated.
    _write(tmp_path, _sdk_agent("[load_and_validate, *eval('[summarize]')]"))
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tool_names == ["load_and_validate"]
    assert observation.tools_complete is False
    assert any("a call to `eval`, whose result is not read (agent.py:4)" in issue for issue in observation.issues)
    assert any("constructor identity is not established" in issue for issue in observation.issues)


def test_sdk_reader_writes_nothing(tmp_path):
    _write(tmp_path, _sdk_agent("[*FINANCE_TOOLS]"))
    before = sorted(path.name for path in Path(tmp_path).rglob("*"))
    _sdk(tmp_path, "agent.py")
    assert sorted(path.name for path in Path(tmp_path).rglob("*")) == before


# -- #909 review: every way a list could change without the reader seeing it -----------


@pytest.mark.parametrize(
    "source",
    [
        # Defaults, decorators and class bodies run in the scope around them.
        "TOOLS = [a]\ndef register(tool, TOOLS=TOOLS):\n    TOOLS.append(tool)\nregister(b)",
        "TOOLS = [a]\nadd = lambda tool, TOOLS=TOOLS: TOOLS.append(tool)\nadd(b)",
        "TOOLS = [a]\ndef register(tool, *, TOOLS=TOOLS):\n    TOOLS.append(tool)",
        "TOOLS = [a]\n@extend(TOOLS)\ndef helper(TOOLS):\n    pass",
        "TOOLS = [a]\nclass Config:\n    TOOLS = TOOLS\n    TOOLS.append(b)",
        # A walrus in a comprehension rebinds the name around it.
        "TOOLS = [a]\n_ = [(TOOLS := [b]) for _ in range(1)]",
        # A wildcard import after the list may rebind it.
        "TOOLS = [a]\nfrom helpers import *",
        # A list handed to a function past a spread lands on another parameter.
        "TOOLS = [a]\nNO_ARGS = ()\ndef add(target, label=None):\n    target.append(b)\nadd(*NO_ARGS, TOOLS)",
        # A decorated helper may hand the list to code that changes it.
        "TOOLS = [a]\n@wrap\ndef show(items):\n    print(items)\nshow(TOOLS)",
    ],
    ids=["default", "lambda-default", "kw-default", "decorator", "class-body", "walrus",
         "star-import", "spread-argument", "decorated-callee"],
)
def test_a_change_the_old_index_missed_keeps_the_list_unread(source):
    result = _resolve(source)
    assert result.members == ()
    assert not result.complete


def test_a_condition_is_compared_whole_however_long():
    condition = "os.environ.get('X', '') in (" + ", ".join(f"'region-{n}'" for n in range(30)) + ")"
    result = _resolve("", f"[a, *([b] if {condition} else [])]")
    assert result.members[1].conditions == (f"`{condition}`",)
    assert len(result.members[1].conditions[0]) > 160


def test_a_list_spread_many_times_is_read_once():
    lines = ["L0 = [a]"] + [f"L{k} = [" + ", ".join([f"*L{k - 1}"] * 10) + "]" for k in range(1, 7)]
    result = _resolve("\n".join(lines), "L6")
    assert _members(result) == [("a", ())]


def test_a_class_body_comprehension_reads_the_class_list():
    tree = ast.parse(
        "TOOLS = [a]\nclass Config:\n    TOOLS = [b]\n    agent = Agent(tools=[t for t in TOOLS if t])\n"
    )
    lists = ListExpressions(
        ref="agent.py", tree=tree, scopes=ScopeIndex(tree), bindings=_module_bindings(tree)[0],
        module=None, resolver=None, agent_reads=lambda call, keyword: keyword == "tools",
    )
    call = tree.body[1].body[1].value
    assert [ast.unparse(member.expr) for member in lists.resolve(call.keywords[0].value).members] == ["b"]


def test_a_filter_object_is_true_however_empty():
    # ``filter(...) or [b]`` is the filter object, never ``b``.
    assert _members(_resolve("", "list(filter(keep, []) or [b])")) == []


def test_a_list_bound_in_a_with_block_is_read():
    tree = ast.parse(
        "async def main():\n"
        "    async with server() as s:\n"
        "        tools = [a]\n"
        "        return Agent(tools=tools)\n"
    )
    lists = ListExpressions(
        ref="agent.py", tree=tree, scopes=ScopeIndex(tree), bindings=_module_bindings(tree)[0],
        module=None, resolver=None, agent_reads=lambda call, keyword: keyword == "tools",
    )
    call = tree.body[0].body[0].body[1].value
    assert _members(lists.resolve(call.keywords[0].value)) == [("a", ())]


def test_a_list_bound_in_a_branch_is_read_only_inside_that_branch():
    tree = ast.parse(
        "def build(premium):\n"
        "    if premium:\n"
        "        tools = [a]\n"
        "        first = Agent(tools=tools)\n"
        "    return Agent(tools=tools)\n"
    )
    lists = ListExpressions(
        ref="agent.py", tree=tree, scopes=ScopeIndex(tree), bindings=_module_bindings(tree)[0],
        module=None, resolver=None, agent_reads=lambda call, keyword: keyword == "tools",
    )
    inside = tree.body[0].body[0].body[1].value
    after = tree.body[0].body[1].value
    assert _members(lists.resolve(inside.keywords[0].value)) == [("a", ())]
    assert not lists.resolve(after.keywords[0].value).complete


@pytest.mark.parametrize(
    ("files", "agent_file"),
    [
        (   # A builder's own import of the list, changed in place.
            {"agent.py": "from agents import Agent\ndef build():\n    from tools import FINANCE_TOOLS, prepare_finance_handoff\n"
             "    FINANCE_TOOLS.append(prepare_finance_handoff)\n    return Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"},
            "agent.py",
        ),
        (   # The same list changed through the module's own spelling.
            {"agent.py": "import tools\nfrom agents import Agent\nfrom tools import FINANCE_TOOLS\n"
             "tools.FINANCE_TOOLS.append(tools.prepare_finance_handoff)\nfinance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"},
            "agent.py",
        ),
        (   # A package that re-exports the list changes it when imported.
            {"pkg/__init__.py": "from pkg.tools import FINANCE_TOOLS, prepare_finance_handoff\nFINANCE_TOOLS.append(prepare_finance_handoff)\n",
             "pkg/tools.py": SDK_TOOLS,
             "agent.py": "from agents import Agent\nfrom pkg import FINANCE_TOOLS\nfinance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"},
            "agent.py",
        ),
        (   # The list's own module changes it through an agent built with it.
            {"tools.py": SDK_TOOLS + "from agents import Agent\nhelper = Agent(name='Helper', tools=FINANCE_TOOLS)\n"
             "helper.tools.append(prepare_finance_handoff)\n",
             "agent.py": "from agents import Agent\nfrom tools import FINANCE_TOOLS\nfinance = Agent(name='Finance', tools=FINANCE_TOOLS)\n"},
            "agent.py",
        ),
        (   # Another module's own ``Agent`` class is not the SDK's.
            {"tools.py": SDK_TOOLS + "class Agent:\n    def __init__(self, name, tools):\n        tools.append(prepare_finance_handoff)\n"
             "legacy = Agent(name='legacy', tools=FINANCE_TOOLS)\n",
             "agent.py": "from agents import Agent\nfrom tools import FINANCE_TOOLS\nfinance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"},
            "agent.py",
        ),
    ],
    ids=["local-import", "module-spelling", "package-init", "through-an-agent", "foreign-agent-class"],
)
def test_sdk_an_imported_list_changed_elsewhere_is_named(tmp_path, files, agent_file):
    _write(tmp_path, {"tools.py": SDK_TOOLS, **files})
    loaded = _sdk(tmp_path, agent_file)
    (observation,) = [item for item in loaded.binding_observations if item.agent.lower() == "finance"]
    assert observation.tools_complete is False
    assert "prepare_finance_handoff" not in observation.tool_names


@pytest.mark.parametrize(
    "call",
    [
        "replace(tools=FINANCE_TOOLS)",  # the application's own ``replace``
        "Registry().clone(tools=FINANCE_TOOLS)",  # a ``clone`` of something not an agent
    ],
    ids=["user-replace", "non-agent-clone"],
)
def test_sdk_only_a_proven_agent_copy_reads_its_list(tmp_path, call):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent\nfrom tools import FINANCE_TOOLS\n"
                      "def replace(tools):\n    tools.append(1)\n\nclass Registry:\n    def clone(self, tools):\n        tools.append(1)\n\n"
                      f"{call}\nfinance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"})
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tools_complete is False


def test_sdk_a_module_attribute_is_read_when_only_read(tmp_path):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "import tools\nfrom agents import Agent\n"
                      "finance = Agent(name='Finance', tools=tools.FINANCE_TOOLS)\n"})
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tools_complete is True
    assert observation.tool_names == ["load_and_validate", "summarize"]


def test_sdk_a_parameter_shadowing_a_module_is_not_the_module(tmp_path):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "import tools\nfrom agents import Agent\n"
                      "def build(tools):\n    return Agent(name='Finance', tools=tools.FINANCE_TOOLS)\n"})
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tools_complete is False
    assert observation.tool_names == []


def test_sdk_a_copy_made_after_a_change_through_an_agent_is_unread(tmp_path):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent\n"
                      "from tools import load_and_validate, prepare_finance_handoff\nBASE = [load_and_validate]\n"
                      "first = Agent(name='First', tools=BASE)\nfirst.tools.append(prepare_finance_handoff)\n"
                      "second = Agent(name='Second', tools=[*BASE])\n"})
    observations = {item.agent: item for item in _sdk(tmp_path, "agent.py").binding_observations}
    assert observations["second"].tools_complete is False


def test_sdk_a_hosted_tool_keeps_its_recovery_evidence(tmp_path):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent, WebSearchTool\n"
                      "from tools import summarize\nfinance = Agent(name='Finance', tools=[WebSearchTool(), summarize])\n"})
    loaded = _sdk(tmp_path, "agent.py")
    (fact,) = loaded.recovery_evidence
    assert (fact.recovery.kind, fact.recovery.reason) == ("unresolved", "sdk_tools_expression_unresolved")
    assert fact.source_ref == "agent.py:3"


def test_sdk_an_unread_member_of_another_module_is_not_bound_by_name(tmp_path):
    # ``lookup`` in the other module's list is extlib's, not this module's.
    _write(tmp_path, {
        "registry.py": SDK_TOOLS + "from extlib import lookup\nTOOLS = [summarize, lookup]\n",
        "agent.py": "from agents import Agent, function_tool\nfrom registry import TOOLS\n\n"
        "@function_tool\ndef lookup(q: str) -> str:\n    return q\n\nfinance = Agent(name='Finance', tools=[*TOOLS])\n",
    })
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tool_names == ["summarize"]
    assert observation.tools_complete is False


def test_sdk_a_handoff_spelled_like_a_local_agent_is_not_that_agent(tmp_path):
    _write(tmp_path, {
        "specialists.py": "from agents import Agent\nrefunds = Agent(name='Refunds')\nSPECIALISTS = [refunds]\n",
        "triage.py": "from agents import Agent\nfrom specialists import SPECIALISTS\n"
        "refunds = Agent(name='Refunds')\ntriage = Agent(name='Triage', handoffs=[*SPECIALISTS])\n",
    })
    (observation,) = [item for item in _sdk(tmp_path, "triage.py").binding_observations if item.agent == "triage"]
    assert observation.handoff_names == []
    assert observation.handoffs_complete is False


def test_adk_a_sub_agent_list_read_from_an_expression_is_not_proven(tmp_path):
    _write(tmp_path, {"agent.py": "from google.adk.agents import LlmAgent\n\n"
                      "def lookup(q: str) -> dict:\n    return {}\n\n"
                      "helper = LlmAgent(name='helper', model='m', tools=[lookup])\n"
                      "SUBS = [helper]\nroot_agent = LlmAgent(name='root', model='m', sub_agents=SUBS)\n"})
    loaded, artifacts = _adk(tmp_path)
    (record,) = [record for record in artifacts.sub_agents if record["agent_name"] == "root"]
    assert record["sub_agents"] == ["helper"]
    tools = [tool for source in loaded for tool in source.tools]
    assert tools and all("dynamic_tools_expression" in tool.extraction["surface_gaps"] for tool in tools)


def test_adk_twins_differing_in_what_they_hold_unconditionally_are_two(tmp_path):
    _write(tmp_path, {"agent.py": "from google.adk.agents import Agent\n\n"
                      "def lookup(q: str) -> str:\n    return q\n\ndef search(q: str) -> str:\n    return q\n\n"
                      "def export(q: str) -> str:\n    return q\n\n"
                      "COMMON = [lookup, search]\nPREMIUM = [lookup, export]\n\n"
                      "def build(premium):\n    return Agent(name='assistant', tools=[*COMMON, *(PREMIUM if premium else [])])\n\n"
                      "def other(premium):\n    return Agent(name='assistant', tools=[search, *(PREMIUM if premium else [])])\n"})
    loaded, _ = _adk(tmp_path)
    (observation,) = [item for source in loaded for item in source.binding_observations if item.agent == "assistant"]
    assert observation.tools_complete is False
    assert any("constructed more than once" in issue for issue in observation.issues)


def test_application_diff_an_adk_list_read_in_part_limits_only_its_agent(tmp_path):
    agent = (
        "from google.adk.agents import Agent\n\n"
        "def lookup(q: str) -> str:\n    return q\n\ndef search(q: str) -> str:\n    return q\n\n"
        "a = Agent(name='a', tools=[lookup, *get_more()])\nb = Agent(name='b', tools=TOOLS)\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[search]")})
    head = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[]")})
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "partial"
    assert _rows(result) == [("b", "search", "removed")]


def test_application_diff_a_condition_change_beside_an_unread_part_is_not_established(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _sdk_agent("[load_and_validate, summarize, *plugin_tools()]"))
    head = _commit(tmp_path, _sdk_agent("[load_and_validate, *([summarize] if ADMIN else []), *plugin_tools()]"))
    result = _compare(tmp_path, base, head)

    (row,) = result["rows"]
    assert (row["tool"], row["change"], row["candidate_change"]) == ("summarize", "not_established", "changed")


def test_application_diff_a_change_past_160_characters_of_a_condition_is_seen(tmp_path):
    regions = ", ".join(f"'region-{n}'" for n in range(20))
    tools = "[*FINANCE_TOOLS, *([prepare_finance_handoff] if os.environ.get('X', '') in (REGIONS) else [])]"
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _sdk_agent(tools.replace("REGIONS", regions), "import os\n"))
    head = _commit(tmp_path, _sdk_agent(tools.replace("REGIONS", regions + ", '*'"), "import os\n"))
    result = _compare(tmp_path, base, head)

    assert _rows(result) == [("finance", "prepare_finance_handoff", "changed")]


def test_adk_a_literal_spread_keeps_the_surface_proven(tmp_path):
    # No name another module could change: nothing puts the proof at risk.
    _write(tmp_path, {"agent.py": "from google.adk.agents import Agent\n\n"
                      "def lookup(q: str) -> str:\n    return q\n\ndef search(q: str) -> str:\n    return q\n\n"
                      "root_agent = Agent(name='root', tools=[lookup] + [*[search]])\n"})
    loaded, _ = _adk(tmp_path)
    tools = [tool for source in loaded for tool in source.tools]
    assert sorted(tool.name for tool in tools) == ["lookup", "search"]
    assert all("dynamic_tools_expression" not in tool.extraction["surface_gaps"] for tool in tools)


def test_sdk_one_load_indexes_an_imported_module_once(tmp_path, monkeypatch):
    files = {"tools.py": SDK_TOOLS}
    for index in range(5):
        files[f"agents/agent_{index}.py"] = (
            "from agents import Agent\nfrom tools import FINANCE_TOOLS\n"
            f"agent_{index} = Agent(name='a{index}', tools=[*FINANCE_TOOLS])\n"
        )
    _write(tmp_path, files)
    indexed: list[str] = []
    original = ListExpressions._index_changes

    def counting(self, view):
        indexed.append(view.ref)
        return original(self, view)

    monkeypatch.setattr(ListExpressions, "_index_changes", counting)
    from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
    from agents_shipgate.schemas.manifest import ToolSourceConfig

    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="."), None, tmp_path
    )
    assert indexed.count("tools.py") <= 2  # once as an imported module, once if read as an entry
    assert all(observation.tools_complete for observation in loaded.binding_observations)


# -- #909 review: the guards, the condition rules and each reader's shapes ---------------


def test_sdk_a_list_appended_through_a_module_level_import_is_named(tmp_path):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent\n"
                      "from tools import FINANCE_TOOLS, prepare_finance_handoff\n"
                      "FINANCE_TOOLS.append(prepare_finance_handoff)\n"
                      "finance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"})
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tools_complete is False
    assert observation.tool_names == []


def test_sdk_a_list_whose_module_runs_unread_code_is_not_established(tmp_path):
    # ``tools.py`` imports code above the read scope, which could rebind its list.
    _write(tmp_path, {
        "svc/common.py": "settings = {}\n",
        "svc/app/__init__.py": "",
        "svc/app/tools.py": "from ..common import settings  # noqa: F401\n" + SDK_TOOLS,
        "svc/app/agent.py": "from agents import Agent\nfrom .tools import FINANCE_TOOLS\n"
        "finance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n",
    })
    observation = _observation(_sdk(tmp_path / "svc" / "app", "agent.py"))
    assert observation.tools_complete is False
    assert "is not established" in observation.issues[0]


@pytest.mark.parametrize(
    "first",
    ["Agent(name='First', tools=BASE if FLAG else [])", "Agent(name='First', handoffs=SPECIALISTS)"],
    ids=["conditional-holder", "handoffs-holder"],
)
def test_sdk_a_change_through_one_holder_reaches_the_other(tmp_path, first):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent\n"
                      "from tools import load_and_validate, prepare_finance_handoff\n"
                      "BASE = [load_and_validate]\nhelper = Agent(name='Helper')\nSPECIALISTS = [helper]\nFLAG = True\n"
                      f"first = {first}\n"
                      "second = Agent(name='Second', tools=BASE, handoffs=SPECIALISTS)\n"
                      "second.tools.append(prepare_finance_handoff)\nsecond.handoffs.append(helper)\n"})
    observations = {item.agent: item for item in _sdk(tmp_path, "agent.py").binding_observations}
    assert observations["first"].tools_complete is False or observations["first"].handoffs_complete is False


def test_sdk_a_member_of_another_module_is_that_module_s_definition(tmp_path):
    # agent.py's own ``summarize`` is another function: the list's is tools.py's.
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent, function_tool\n"
                      "from tools import FINANCE_TOOLS\n\n"
                      "@function_tool\ndef summarize(text: str) -> str:\n    return text.upper()\n\n"
                      "finance = Agent(name='Finance', tools=[*FINANCE_TOOLS])\n"})
    loaded = _sdk(tmp_path, "agent.py")
    assert _edges([loaded]) == [
        ("finance", "load_and_validate", "tools.py:5"),
        ("finance", "summarize", "tools.py:11"),
    ]


def test_sdk_a_list_imported_inside_the_builder_is_read(tmp_path):
    _write(tmp_path, {"tools.py": SDK_TOOLS, "agent.py": "from agents import Agent\n"
                      "def build():\n    from tools import FINANCE_TOOLS\n"
                      "    return Agent(name='Finance', tools=FINANCE_TOOLS)\n"})
    (observation,) = _sdk(tmp_path, "agent.py").binding_observations
    assert observation.tools_complete is True
    assert observation.tool_names == ["load_and_validate", "summarize"]


def test_adk_a_member_of_another_module_that_is_not_a_reference_is_named(tmp_path):
    _write(tmp_path, {
        "tools.py": "from google.adk.tools import FunctionTool\n\n" + ADK_TOOLS
        + "WRAPPED = [FunctionTool(func=summarize)]\n",
        "agent.py": "from google.adk.agents import Agent\nfrom tools import WRAPPED\n\n"
        "root_agent = Agent(name='finance', tools=[*WRAPPED])\n",
    })
    loaded, _ = _adk(tmp_path)
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tools_complete is False
    assert "binds `FunctionTool(func=summarize)`, written in tools.py" in observation.issues[0]


def test_a_tool_the_list_also_holds_unconditionally_has_no_condition(tmp_path):
    _write(tmp_path, _sdk_agent("[load_and_validate, *([load_and_validate] if X else [])]"))
    observation = _observation(_sdk(tmp_path, "agent.py"))
    assert observation.tool_conditions == {}


def test_hold_merges_constructions_unconditional_first():
    from agents_shipgate.cli.application_diff import _hold

    held: dict[str, list[str] | None] = {}
    _hold(held, "t", ["`a`"])
    _hold(held, "t", ["`b`"])
    _hold(held, "u", ["`a`"])
    _hold(held, "u", None)
    _hold(held, "u", ["`c`"])
    assert held == {"t": ["`a`", "`b`"], "u": None}


def test_sdk_handoff_twins_differing_only_in_a_condition_are_two(tmp_path):
    _write(tmp_path, {"triage.py": "from agents import Agent\nbilling = Agent(name='Billing')\n"
                      "def build(premium):\n"
                      "    if premium:\n        return Agent(name='Triage', handoffs=[billing])\n"
                      "    return Agent(name='Triage', handoffs=[*([billing] if premium else [])])\n"})
    observations = [item for item in _sdk(tmp_path, "triage.py").binding_observations if item.agent == "Triage"]
    assert observations and all(not item.handoffs_complete or not item.tools_complete for item in observations)


def test_adk_twins_differing_only_in_a_condition_are_two(tmp_path):
    _write(tmp_path, {"agent.py": "from google.adk.agents import Agent\n\n"
                      "def lookup(q: str) -> str:\n    return q\n\n"
                      "def build(premium):\n    if premium:\n        return Agent(name='assistant', tools=[lookup])\n"
                      "    return Agent(name='assistant', tools=[*([lookup] if premium else [])])\n"})
    loaded, _ = _adk(tmp_path)
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tools_complete is False


@pytest.mark.parametrize("expression", ["filter(keep, plugin_tools())", "list(plugin_tools())", "sorted(get())"])
def test_a_filter_or_copy_of_an_unread_list_is_named(expression):
    result = _resolve("", expression)
    assert result.members == ()
    assert not result.complete


@pytest.mark.parametrize(
    ("expression", "names", "conditions"),
    [
        ("[lookup] + [search]", ["lookup", "search"], {}),
        ("EXTRA or [lookup]", ["search", "lookup"],
         {"search": ["`EXTRA` is not empty and `FLAG`"], "lookup": ["`EXTRA` is empty"]}),
        ("[t for t in BOTH if keep(t)]", ["lookup", "search"],
         {"lookup": ["the filter `keep(t)` keeps it"], "search": ["the filter `keep(t)` keeps it"]}),
    ],
    ids=["concatenation", "or", "filter"],
)
def test_adk_tools_shapes(tmp_path, expression, names, conditions):
    _write(tmp_path, {"agent.py": "from google.adk.agents import Agent\n\n"
                      "def lookup(q: str) -> str:\n    return q\n\ndef search(q: str) -> str:\n    return q\n\n"
                      "EXTRA = [search] if FLAG else []\nBOTH = [lookup, search]\n"
                      f"root_agent = Agent(name='root', tools={expression})\n"})
    loaded, _ = _adk(tmp_path)
    (observation,) = [item for source in loaded for item in source.binding_observations]
    assert observation.tool_names == names
    assert observation.tool_conditions == conditions
    if "for t in" in expression:
        assert not observation.tools_complete
        assert any("constructor identity is not established" in issue for issue in observation.issues)
    else:
        assert observation.tools_complete is True


@pytest.mark.parametrize(
    ("expression", "names", "conditions"),
    [
        ("[billing] + [refunds]", ["billing", "refunds"], {}),
        ("EXTRA or [billing]", ["refunds", "billing"],
         {"refunds": ["`EXTRA` is not empty and `FLAG`"], "billing": ["`EXTRA` is empty"]}),
        ("[a for a in BOTH if a]", ["billing", "refunds"],
         {"billing": ["the filter `a` keeps it"], "refunds": ["the filter `a` keeps it"]}),
    ],
    ids=["concatenation", "or", "filter"],
)
def test_adk_sub_agents_shapes(tmp_path, expression, names, conditions):
    _write(tmp_path, {"agent.py": "from google.adk.agents import Agent\n\n"
                      "billing = Agent(name='billing')\nrefunds = Agent(name='refunds')\n"
                      "EXTRA = [refunds] if FLAG else []\nBOTH = [billing, refunds]\n"
                      f"root_agent = Agent(name='triage', sub_agents={expression})\n"})
    _, artifacts = _adk(tmp_path)
    (record,) = [record for record in artifacts.sub_agents if record["agent_name"] == "triage"]
    assert record["conditions"] == conditions
    if "for a in" in expression:
        assert record["sub_agents"] == [] and record["sub_agent_count"] is None
        assert record["unresolved_sub_agents"] == names
        assert any("constructor identity is not established" in warning for warning in artifacts.warnings)
    else:
        assert record["sub_agents"] == names
        assert record["sub_agent_count"] == len(names)


def test_adk_sub_agents_from_another_module_are_not_this_module_s(tmp_path):
    _write(tmp_path, {
        "specialists.py": "from google.adk.agents import Agent\nbilling = Agent(name='billing')\nSPECIALISTS = [billing]\n",
        "agent.py": "from google.adk.agents import Agent\nfrom specialists import SPECIALISTS\n\n"
        "root_agent = Agent(name='triage', sub_agents=[*SPECIALISTS])\n",
    })
    _, artifacts = _adk(tmp_path)
    (record,) = [record for record in artifacts.sub_agents if record["agent_name"] == "triage"]
    assert record["sub_agents"] == []
    assert record["unresolved_sub_agents"] == ["billing"]


def test_application_diff_an_unread_sub_agent_part_is_named_on_its_agent(tmp_path):
    agent = (
        "from google.adk.agents import Agent\n\n"
        "def lookup(q: str) -> str:\n    return q\n\ndef search(q: str) -> str:\n    return q\n\n"
        "billing = Agent(name='billing')\n"
        "root_agent = Agent(name='triage', sub_agents=[billing, *more_agents()])\n"
        "helper = Agent(name='helper', tools=TOOLS)\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[lookup]")})
    head = _commit(tmp_path, {"agent.py": agent.replace("TOOLS", "[lookup, search]")})
    result = _compare(tmp_path, base, head)

    assert result["comparison_status"] == "partial"
    # The other agent's addition stands; the limit is the triage agent's.
    assert ("helper", "search", "added") in _rows(result)
    (gap,) = [gap for gap in result["head"]["coverage_gaps"] if "sub-agents" in gap["reason"]]
    assert gap["agent"] == "triage"
    assert "Not read: a call to `more_agents`, whose result is not read (agent.py:10)." in gap["reason"]


@pytest.mark.parametrize(
    ("expression", "names"),
    [("[billing] + [refunds]", ["billing", "refunds"]), ("EXTRA or [billing]", ["refunds", "billing"]),
     ("[a for a in BOTH if a]", ["billing", "refunds"])],
    ids=["concatenation", "or", "filter"],
)
def test_sdk_handoffs_shapes(tmp_path, expression, names):
    _write(tmp_path, {"triage.py": "from agents import Agent\n"
                      "billing = Agent(name='Billing')\nrefunds = Agent(name='Refunds')\n"
                      "EXTRA = [refunds] if FLAG else []\nBOTH = [billing, refunds]\n"
                      f"triage = Agent(name='Triage', handoffs={expression})\n"})
    (observation,) = [item for item in _sdk(tmp_path, "triage.py").binding_observations if item.agent == "triage"]
    if "for a in" in expression:
        assert observation.handoff_names == [] and not observation.handoffs_complete
        assert any("constructor identity is not established" in issue for issue in observation.issues)
    else:
        assert observation.handoff_names == names
        assert observation.handoffs_complete is True
