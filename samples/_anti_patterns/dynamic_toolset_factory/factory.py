"""Demonstration of a factory pattern that hides tools from static AST.

Static AST extraction sees the function definitions and the
``Agent(name="…")`` literal, but it cannot reach the runtime
``make_tool`` wrapper or the ``TOOLS`` list — so the OpenAI Agents SDK
adapter reports zero tools for this source. The agent has three tools
at runtime; Shipgate sees none.

This fixture exists so AI coding agents can recognize the failure mode
("0 tools extracted from a Python source") and reach for the documented
recovery: provide an explicit MCP export, OpenAPI spec, or local tool
inventory artifact (see [agent-recipes.md Recipe 2](../../../docs/agent-recipes.md#recipe-2--add-shipgate-to-a-repo-that-already-has-tool-surfaces)).
"""

# pragma: pylint=skip-file (this is a fixture, not runnable code)


def make_tool(name: str, fn):
    """Wrap fn into a tool the agent can call. Runtime-only."""
    fn.__tool_name__ = name
    return fn


def search_orders(query: str) -> str:
    """Stub — search for orders matching the query."""
    return "..."


def lookup_user(user_id: str) -> dict:
    """Stub — look up a user by id."""
    return {}


def issue_refund(amount: int) -> dict:
    """Stub — issue a refund. NB: this is the kind of high-risk tool
    that Shipgate would normally flag for missing approval policy —
    but the static extractor never sees it through the factory wrap."""
    return {"status": "ok"}


# The factory pattern that hides tools from static analysis. Each call
# to ``make_tool`` happens at module import time, after the static
# extractor has already given up.
TOOLS = [
    make_tool("search_orders", search_orders),
    make_tool("lookup_user", lookup_user),
    make_tool("issue_refund", issue_refund),
]


# Pretend this is from `agents` (the OpenAI Agents SDK), but note that it
# is a locally defined class, not an import. Name extraction resolves the
# callee through its binding, so this `Agent` is *not* read as a framework
# constructor and contributes no agent-name candidate. The failure this
# fixture isolates is still the one below: nothing can follow the
# `tools=TOOLS` reference back to the wrapped functions, so zero tools are
# enumerated.
class Agent:
    def __init__(self, name: str, tools: list) -> None:
        self.name = name
        self.tools = tools


agent = Agent(name="dynamic-toolset-agent", tools=TOOLS)
