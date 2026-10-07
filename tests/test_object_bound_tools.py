"""Tools bound as objects are bindings with an identity (#910, #866).

An MCP server, an agent exposed as a tool and a hosted or built-in tool used to
read as "unresolved tool" or "assigned a value, not a function definition", so
a PR adding one produced no row. These fixtures reproduce each pinned shape
statically — nothing is fetched, ADK and the SDK are never imported, no user
code runs — and the controls that must stay named:

- the shapes of buriro-ezekia/cinescout-ai#9 (a remote MCP toolset with an
  ``Authorization`` header, configured through a settings dataclass),
  Lling0000/OpenCMO#41 (an expert agent wrapped as a tool by a helper),
  cuppibla/adk_tutorial#7/#9 (``google_search``) and zendah21/capstone_project#3
  (``load_memory`` beside two SQL tools);
- a same-named local function, a shadowed or reassigned import, a vendored
  package and an unsupported package never acquire built-in semantics;
- a helper whose return cannot be resolved stays a named limit;
- no URL path, query string or header value is printed, in text or JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.object_tools import reading_object_bindings
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig
from tests.test_imported_tool_bindings import _commit, _compare, _git


def _repo(tmp_path: Path, base: dict[str, str | None], head: dict[str, str | None]) -> tuple[str, str]:
    _git(tmp_path, "init", "-q", "-b", "main")
    return _commit(tmp_path, base), _commit(tmp_path, head)


def _rows(result: dict) -> dict[tuple[str, str], dict]:
    return {(row["agent"], row["tool"]): row for row in result["rows"]}


def _text(root: Path, base: str, head: str, *args: str) -> str:
    outcome = CliRunner().invoke(
        app,
        ["diff", "--application", "--workspace", str(root), "--base", base, "--head", head, *args],
    )
    assert outcome.exit_code == 0, outcome.output
    return outcome.output


def _limits(result: dict) -> list[str]:
    return [*result["base"]["limits"], *result["head"]["limits"]]


# -- #866: load_memory beside two SQL tools (zendah21/capstone_project#3) ------

CAPSTONE = """from google.adk.agents import LlmAgent
from google.adk.tools import {imported}


def inspect_schema() -> dict:
    \"\"\"List the tables.\"\"\"
    return {{}}


def execute_sql(sql: str, params_json: str = "[]") -> dict:
    \"\"\"Run one statement.\"\"\"
    return {{}}


root_agent = LlmAgent(
    name="meal_planner_agent",
    model="gemini-2.0-flash",
    tools={tools},
)
"""


def _capstone(tools: str, imported: str = "load_memory") -> dict[str, str | None]:
    return {"meal_planner_agent/agent.py": CAPSTONE.format(imported=imported, tools=tools)}


def test_load_memory_is_added_beside_both_sql_tools(tmp_path):
    base, head = _repo(
        tmp_path, _capstone("[]"), _capstone("[load_memory, inspect_schema, execute_sql]")
    )
    result = _compare(tmp_path, base, head, "--scope", "meal_planner_agent")

    assert result["comparison_status"] == "compared"
    assert result["application_comparison_schema_version"] == "0.4"
    rows = _rows(result)
    assert sorted(rows) == [
        ("meal_planner_agent", "execute_sql"),
        ("meal_planner_agent", "inspect_schema"),
        ("meal_planner_agent", "load_memory"),
    ]
    assert {row["change"] for row in rows.values()} == {"added"}
    memory = rows[("meal_planner_agent", "load_memory")]["after"]
    assert memory["object"] == {"kind": "built_in_tool", "tool": "load_memory"}
    assert memory["agent_source"] == "meal_planner_agent/agent.py"
    assert memory["binding_location"] == "meal_planner_agent/agent.py:15"
    # Where the built-in comes from: its import, not a fabricated definition.
    assert memory["definition"]["source"] == "meal_planner_agent/agent.py"
    assert memory["definition"]["line"] == 2
    assert "input_schema" not in memory and "signature" not in memory
    # The SQL tools are still read as the functions they are.
    assert rows[("meal_planner_agent", "execute_sql")]["after"]["signature"].startswith("execute_sql(sql")
    # Deterministic: the same refs give the same answer.
    assert _compare(tmp_path, base, head, "--scope", "meal_planner_agent")["comparison_id"] == result["comparison_id"]
    text = _text(tmp_path, base, head, "--scope", "meal_planner_agent")
    assert "built-in tool load_memory at meal_planner_agent/agent.py:2" in text


def test_an_aliased_built_in_import_resolves(tmp_path):
    base, head = _repo(
        tmp_path,
        _capstone("[inspect_schema]", imported="load_memory as remember"),
        _capstone("[remember, inspect_schema]", imported="load_memory as remember"),
    )
    result = _compare(tmp_path, base, head, "--scope", "meal_planner_agent")
    assert result["comparison_status"] == "compared"
    (row,) = result["rows"]
    assert (row["tool"], row["change"]) == ("load_memory", "added")


SHADOWED = {
    "local function": (
        "from google.adk.agents import LlmAgent\n\n\n"
        "def load_memory(query: str) -> dict:\n    return {}\n\n\n"
        'root_agent = LlmAgent(name="planner", tools=[load_memory])\n'
    ),
    "shadowed import": (
        "from google.adk.agents import LlmAgent\nfrom google.adk.tools import load_memory\n\n\n"
        "def load_memory(query: str) -> dict:\n    return {}\n\n\n"
        'root_agent = LlmAgent(name="planner", tools=[load_memory])\n'
    ),
    "reassigned import": (
        "from google.adk.agents import LlmAgent\nfrom google.adk.tools import load_memory\n\n"
        "load_memory = wrap(load_memory)\n"
        'root_agent = LlmAgent(name="planner", tools=[load_memory])\n'
    ),
    "unsupported package": (
        "from google.adk.agents import LlmAgent\nfrom my_adk.tools import load_memory\n\n"
        'root_agent = LlmAgent(name="planner", tools=[load_memory])\n'
    ),
    "patched built-in": (
        "from google.adk.agents import LlmAgent\nfrom google.adk.tools import load_memory\n\n"
        "load_memory.func = print\n"
        'root_agent = LlmAgent(name="planner", tools=[load_memory])\n'
    ),
}


def test_a_same_name_never_acquires_built_in_semantics(tmp_path):
    for case, source in SHADOWED.items():
        root = tmp_path / case.replace(" ", "_")
        root.mkdir()
        (root / "agent.py").write_text(source)
        with reading_object_bindings(lambda path: False):
            loaded, _ = load_google_adk_artifacts(
                None, root, sources=[ToolSourceConfig(id="adk", type="google_adk", path="agent.py")]
            )
        observations = [o for item in loaded for o in item.binding_observations]
        assert all(not o.object_bindings for o in observations), case


def test_a_vendored_google_package_is_application_code(tmp_path):
    files = {
        "google/__init__.py": "",
        "google/adk/__init__.py": "",
        "google/adk/tools/__init__.py": "load_memory = object()\n",
        "app/agent.py": (
            "from google.adk.agents import LlmAgent\nfrom google.adk.tools import load_memory\n\n"
            'root_agent = LlmAgent(name="planner", tools=[load_memory])\n'
        ),
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    with reading_object_bindings(lambda path: False):
        loaded, _ = load_google_adk_artifacts(
            None, tmp_path, sources=[ToolSourceConfig(id="adk", type="google_adk", path="app/agent.py")]
        )
    assert all(not o.object_bindings for item in loaded for o in item.binding_observations)


def test_scan_reads_built_ins_as_it_did(tmp_path):
    # Object bindings are read for the comparison only: ``scan`` keeps its
    # warning, so no catalog or check moves.
    (tmp_path / "agent.py").write_text(_capstone("[load_memory]")["meal_planner_agent/agent.py"])
    loaded, artifacts = load_google_adk_artifacts(
        None, tmp_path, sources=[ToolSourceConfig(id="adk", type="google_adk", path="agent.py")]
    )
    assert artifacts is not None
    assert any("load_memory" in warning for warning in artifacts.warnings)
    assert all(not o.object_bindings for item in loaded for o in item.binding_observations)


# -- adk_tutorial#7/#9: google_search on an agent that gains a tool ------------

TUTORIAL = """from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool


def update_plan(new_plan: str) -> str:
    \"\"\"Replace the plan.\"\"\"
    return new_plan


scout = Agent(name="LocationScoutAgent", model="m", tools=[google_search])
refiner_agent = Agent(name="refiner_agent", model="m", tools={tools})
"""


def test_google_search_resolves_and_the_added_tool_is_established(tmp_path):
    base, head = _repo(
        tmp_path,
        {"loop/agents.py": TUTORIAL.format(tools="[google_search]")},
        {"loop/agents.py": TUTORIAL.format(tools="[google_search, update_plan]")},
    )
    result = _compare(tmp_path, base, head, "--scope", "loop")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert (row["agent"], row["tool"], row["change"]) == ("refiner_agent", "update_plan", "added")


def test_an_agent_tool_wraps_the_agent_it_names(tmp_path):
    base, head = _repo(
        tmp_path,
        {"loop/agents.py": TUTORIAL.format(tools="[google_search]")},
        {"loop/agents.py": TUTORIAL.format(tools="[google_search, AgentTool(agent=scout)]")},
    )
    result = _compare(tmp_path, base, head, "--scope", "loop")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert (row["tool"], row["change"]) == ("LocationScoutAgent", "added")
    assert row["after"]["object"]["kind"] == "agent_tool"
    assert row["after"]["object"]["agent"] == "LocationScoutAgent"


# -- cinescout-ai#9: a remote MCP toolset with an Authorization header --------

CONFIG = """import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    parallel_mcp_url: str = os.getenv("PARALLEL_MCP_URL", "{url}")
    parallel_api_key: str | None = os.getenv("{key}") or None


settings = Settings()
"""

CINESCOUT = """from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.{toolset_module} import McpToolset

from app.config import settings


def _parallel_headers() -> dict[str, str]:
    if not settings.parallel_api_key:
        return {{}}
    return {{"Authorization": f"Bearer {{settings.parallel_api_key}}"}}


parallel_search = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=settings.parallel_mcp_url,
        headers=_parallel_headers(),
        timeout=30,
    ),
    tool_filter=["web_search", "web_fetch"],
)

root_agent = Agent(name="cinescout_phase1", model="m", tools={tools})
"""


def _cinescout(
    tools: str = "[parallel_search]",
    *,
    url: str = "https://search.parallel.ai/mcp",
    key: str = "PARALLEL_API_KEY",
    toolset_module: str = "mcp_toolset",
) -> dict[str, str | None]:
    return {
        "app/__init__.py": "",
        "app/config.py": CONFIG.format(url=url, key=key),
        "app/agent.py": CINESCOUT.format(tools=tools, toolset_module=toolset_module),
    }


def test_a_remote_mcp_toolset_is_added_with_host_transport_and_credential(tmp_path):
    base, head = _repo(tmp_path, _cinescout("[]"), _cinescout())
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert (row["agent"], row["tool"], row["change"]) == ("cinescout_phase1", "parallel_search", "added")
    assert row["after"]["object"] == {
        "kind": "mcp_server",
        "class": "McpToolset",
        "transport": "streamable_http",
        "host": ["env PARALLEL_MCP_URL", "search.parallel.ai"],
        "command": None,
        "credential_sources": [{"header": "Authorization", "env": ["PARALLEL_API_KEY"]}],
        "tool_filter": ["web_fetch", "web_search"],
        "endpoint_sha256": row["after"]["object"]["endpoint_sha256"],
    }
    assert row["after"]["definition"]["source"] == "app/agent.py"
    text = _text(tmp_path, base, head, "--scope", "app")
    assert "MCP server (McpToolset, streamable_http); host env PARALLEL_MCP_URL or search.parallel.ai" in text
    assert "credential: env PARALLEL_API_KEY → header Authorization" in text


def test_a_changed_host_or_credential_name_is_a_changed_row(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, _cinescout())
    moved = _commit(tmp_path, _cinescout(url="https://other.example.com/mcp"))
    rekeyed = _commit(tmp_path, _cinescout(url="https://other.example.com/mcp", key="SEARCH_TOKEN"))
    imports = _commit(
        tmp_path, _cinescout(url="https://other.example.com/mcp", key="SEARCH_TOKEN", toolset_module="mcp_toolset")
    )
    (row,) = _compare(tmp_path, base, moved, "--scope", "app")["rows"]
    assert row["change"] == "changed"
    assert (row["before"]["object"]["host"], row["after"]["object"]["host"]) == (
        ["env PARALLEL_MCP_URL", "search.parallel.ai"],
        ["env PARALLEL_MCP_URL", "other.example.com"],
    )
    (row,) = _compare(tmp_path, moved, rekeyed, "--scope", "app")["rows"]
    assert row["change"] == "changed"
    assert row["after"]["object"]["credential_sources"] == [{"header": "Authorization", "env": ["SEARCH_TOKEN"]}]
    assert _compare(tmp_path, rekeyed, imports, "--scope", "app")["rows"] == []


def test_the_toolset_import_path_is_not_its_identity(tmp_path):
    base, head = _repo(tmp_path, _cinescout(toolset_module="mcp_toolset"), {
        **_cinescout(),
        "app/agent.py": CINESCOUT.format(tools="[parallel_search]", toolset_module="mcp_toolset").replace(
            "from google.adk.tools.mcp_tool.mcp_toolset import McpToolset",
            "from google.adk.tools.mcp_tool import McpToolset",
        ),
    })
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["rows"] == [] and result["comparison_status"] == "compared"


FACTORY = """from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from app.config import settings


def create_parallel_search_toolset() -> McpToolset:
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=settings.parallel_mcp_url),
        tool_filter=["web_search"],
    )
"""


def test_a_toolset_a_helper_returns_is_read_through_the_helper(tmp_path):
    agent = (
        "from google.adk.agents import Agent\n\nfrom app.tools import create_parallel_search_toolset\n\n"
        'verifier = Agent(name="evidence_verifier", model="m", tools={tools})\n'
    )
    files = {"app/__init__.py": "", "app/config.py": CONFIG.format(url="https://search.parallel.ai/mcp", key="K"),
             "app/tools.py": FACTORY}
    base, head = _repo(
        tmp_path,
        {**files, "app/agent.py": agent.format(tools="[]")},
        {**files, "app/agent.py": agent.format(tools="[create_parallel_search_toolset()]")},
    )
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert row["tool"] == "create_parallel_search_toolset()"
    assert row["after"]["object"]["host"] == ["env PARALLEL_MCP_URL", "search.parallel.ai"]
    assert row["after"]["definition"]["source"] == "app/tools.py"


def test_a_host_this_read_cannot_name_is_presence_without_identity(tmp_path):
    agent = """from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams


def build(url: str) -> Agent:
    toolset = McpToolset(connection_params=SseConnectionParams(url=url{suffix}))
    return Agent(name="remote", model="m", tools=[toolset])
"""
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"app/agent.py": agent.format(suffix="").replace("tools=[toolset]", "tools=[]")})
    head = _commit(tmp_path, {"app/agent.py": agent.format(suffix="")})
    later = _commit(tmp_path, {"app/agent.py": agent.format(suffix=' + "/v2"')})
    result = _compare(tmp_path, base, head, "--scope", "app")
    (row,) = result["rows"]
    # The binding is there: its addition is established, its host is not.
    assert (row["tool"], row["change"]) == ("toolset", "added")
    assert row["after"]["object"]["host"] is None
    assert result["comparison_status"] == "partial"
    assert any("its URL's host is not read" in limit for limit in result["head"]["limits"])
    # A change to it is a candidate, never an established answer.
    (row,) = _compare(tmp_path, head, later, "--scope", "app")["rows"]
    assert row["change"] == "not_established"
    assert row["candidate_change"] == "changed"


# -- OpenCMO#41: an expert agent wrapped as a tool by a helper -----------------

EXPERT = 'from agents import Agent\n\nblog_expert = Agent(name="Blog Expert", instructions="Write.")\n'

CMO_DIRECT = """from agents import Agent

from opencmo.agents.blog import blog_expert

blog_tool = blog_expert.as_tool(
    tool_name="generate_blog_content",
    tool_description="Generate blog content.",
)

cmo_agent = Agent(name="CMO", instructions="Lead.", tools={tools})
"""

CMO_HELPER = """from agents import Agent

from opencmo.agents.blog import blog_expert


def _multi_channel_tool(agent: Agent, *, tool_name: str, tool_description: str):
    return agent.as_tool(
        tool_name=tool_name,
        tool_description=f"Use for multi-channel requests. {tool_description}",
    )


blog_tool = _multi_channel_tool(
    blog_expert,
    tool_name="generate_blog_content",
    tool_description="Generate blog content.",
)

cmo_agent = Agent(name="CMO", instructions="Lead.", tools=[blog_tool])
"""


def _cmo(agent: str) -> dict[str, str | None]:
    return {
        "src/opencmo/__init__.py": "",
        "src/opencmo/agents/__init__.py": "",
        "src/opencmo/agents/blog.py": EXPERT,
        "src/opencmo/agents/cmo.py": agent,
    }


def test_an_agent_wrapped_as_a_tool_names_the_agent_it_wraps(tmp_path):
    base, head = _repo(tmp_path, _cmo(CMO_DIRECT.format(tools="[]")), _cmo(CMO_HELPER))
    result = _compare(tmp_path, base, head, "--scope", "src/opencmo")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert (row["agent"], row["tool"], row["change"]) == ("cmo_agent", "generate_blog_content", "added")
    assert row["after"]["object"] == {
        "kind": "agent_tool",
        "agent": "blog_expert",
        "tool": "generate_blog_content",
        "options": [],
        "options_sha256": None,
    }
    assert row["after"]["object_evidence"] == {"agent_source": "src/opencmo/agents/blog.py"}
    assert "agent as tool: wraps agent blog_expert" in _text(tmp_path, base, head, "--scope", "src/opencmo")


def test_moving_as_tool_into_a_helper_is_no_change(tmp_path):
    # OpenCMO's own refactor (cda065d): the description gains a prefix, the
    # binding is the same agent under the same tool name.
    base, head = _repo(tmp_path, _cmo(CMO_DIRECT.format(tools="[blog_tool]")), _cmo(CMO_HELPER))
    result = _compare(tmp_path, base, head, "--scope", "src/opencmo")
    assert result["comparison_status"] == "compared", _limits(result)
    assert result["rows"] == []


def test_a_helper_whose_return_is_not_resolved_stays_a_named_limit(tmp_path):
    helper = CMO_HELPER.replace(
        "    return agent.as_tool(",
        "    if not tool_name:\n        return None\n    return agent.as_tool(",
    )
    base, head = _repo(tmp_path, _cmo(CMO_DIRECT.format(tools="[]")), _cmo(helper))
    result = _compare(tmp_path, base, head, "--scope", "src/opencmo")
    assert result["comparison_status"] == "partial"
    assert any(
        "binds unresolved tool 'blog_tool'" in limit and "assigned a value" in limit
        for limit in result["head"]["limits"]
    )
    assert all(row["change"] != "added" for row in result["rows"])


def test_as_tool_on_a_value_that_is_not_an_agent_is_named(tmp_path):
    agent = CMO_DIRECT.format(tools="[blog_tool]").replace(
        "from opencmo.agents.blog import blog_expert", "blog_expert = load_expert()"
    )
    base, head = _repo(tmp_path, _cmo(CMO_DIRECT.format(tools="[]")), _cmo(agent))
    result = _compare(tmp_path, base, head, "--scope", "src/opencmo")
    assert result["comparison_status"] == "partial"
    assert any("is not one this reader identifies" in limit for limit in result["head"]["limits"])


# -- OpenAI Agents SDK hosted tools, MCP servers and function_tool(f) ----------

SDK_AGENT = """import os

from agents import Agent, {hosted}
from agents.mcp import MCPServerStdio

{local}

async def run() -> None:
    async with MCPServerStdio(
        params={{"command": "npx", "args": ["-y", "@acme/server"], "env": {{"ACME_TOKEN": os.environ["ACME_TOKEN"]}}}},
    ) as server:
        assistant = Agent(name="assistant", tools={tools}, mcp_servers={servers})
        await assistant.run()
"""


def _sdk(tools: str = "[]", servers: str = "[]", hosted: str = "WebSearchTool", local: str = "") -> dict[str, str | None]:
    return {"app/assistant.py": SDK_AGENT.format(tools=tools, servers=servers, hosted=hosted, local=local)}


def test_a_hosted_tool_and_an_mcp_server_are_added(tmp_path):
    base, head = _repo(tmp_path, _sdk(), _sdk(tools="[WebSearchTool()]", servers="[server]"))
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared", _limits(result)
    rows = _rows(result)
    assert rows[("assistant", "WebSearchTool")]["after"]["object"]["kind"] == "hosted_tool"
    server = rows[("assistant", "server")]
    assert server["change"] == "added"
    assert server["after"]["object"]["transport"] == "stdio"
    assert server["after"]["object"]["command"] == ["npx"]
    assert server["after"]["object"]["credential_sources"] == [{"server_env": "ACME_TOKEN", "env": ["ACME_TOKEN"]}]


def test_a_local_class_named_like_a_hosted_tool_is_not_one(tmp_path):
    local = "class WebSearchTool:\n    pass\n"
    files = _sdk(tools="[WebSearchTool()]", hosted="function_tool", local=local)
    with reading_object_bindings(lambda path: False):
        for name, text in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text)
        loaded = load_openai_sdk_static_tools(
            ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="app/assistant.py"), None, tmp_path
        )
    (observation,) = loaded.binding_observations
    assert observation.object_bindings == {}
    assert observation.tools_complete is False


def test_mcp_servers_this_read_cannot_enumerate_are_named(tmp_path):
    base, head = _repo(tmp_path, _sdk(), _sdk(servers="load_servers()"))
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert any("has MCP servers it reads only in part" in limit for limit in result["head"]["limits"])


FUNCTION_TOOLS = """from agents import function_tool


def list_catalog_items(category: str) -> list:
    \"\"\"List the catalog.\"\"\"
    return []


list_catalog_items_tool = function_tool(list_catalog_items)
"""


def test_a_function_wrapped_as_a_value_is_the_function_it_wraps(tmp_path):
    # lifenyan/agent-desk#8: ``x_tool = function_tool(x)`` in a tools module.
    agent = (
        "from agents import Agent\n\nfrom app.tools import list_catalog_items_tool\n\n"
        'fulfillment_agent = Agent(name="fulfillment", tools={tools})\n'
    )
    files = {"app/__init__.py": "", "app/tools.py": FUNCTION_TOOLS}
    base, head = _repo(
        tmp_path,
        {**files, "app/agent.py": agent.format(tools="[]")},
        {**files, "app/agent.py": agent.format(tools="[list_catalog_items_tool]")},
    )
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert (row["tool"], row["change"]) == ("list_catalog_items", "added")
    assert row["after"]["signature"].startswith("list_catalog_items(category")
    assert row["after"]["definition"]["source"] == "app/tools.py"
    assert "object" not in row["after"]


# -- privacy -------------------------------------------------------------------

PRIVATE = """from agents import Agent
from agents.mcp import MCPServerStreamableHttp
from google.adk.tools.mcp_tool import McpToolset

server = MCPServerStreamableHttp(
    name="billing",
    params={
        "url": "https://mcp.billing.example.com/tenant-8f3kq29xz/v1?api_key=sk-live-QUERYVALUE&region=eu",
        "headers": {"X-Api-Key": "sk-live-HEADERVALUE", "X-Tenant": "tenant-HEADER-2"},
    },
)

assistant = Agent(name="assistant", mcp_servers={servers})
"""


def test_no_url_path_query_or_header_value_is_printed(tmp_path):
    base, head = _repo(
        tmp_path,
        {"app/assistant.py": PRIVATE.replace("{servers}", "[]")},
        {"app/assistant.py": PRIVATE.replace("{servers}", "[server]")},
    )
    result = _compare(tmp_path, base, head, "--scope", "app")
    (row,) = result["rows"]
    assert row["after"]["object"]["host"] == ["mcp.billing.example.com"]
    assert row["after"]["object"]["credential_sources"] == [
        {"header": "X-Api-Key", "literal": True},
        {"literal": True, "query": "api_key"},
    ]
    outputs = [json.dumps(result), _text(tmp_path, base, head, "--scope", "app")]
    for secret in ("tenant-8f3kq29xz", "/v1", "QUERYVALUE", "api_key=", "region=eu", "HEADERVALUE", "tenant-HEADER-2"):
        for output in outputs:
            assert secret not in output, secret


def test_an_object_changed_after_it_is_built_is_a_named_limit(tmp_path):
    changed = _cinescout()
    changed["app/agent.py"] = changed["app/agent.py"].replace(
        "root_agent = Agent(", 'parallel_search.tool_filter = ["web_search"]\n\nroot_agent = Agent('
    )
    base, head = _repo(tmp_path, _cinescout("[]"), changed)
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert all(row["change"] != "added" for row in result["rows"])
    assert any("`parallel_search` is changed after it is built" in limit for limit in result["head"]["limits"])


def test_a_wrapper_argument_is_part_of_the_wrapped_function(tmp_path):
    # awslabs/agentcore-samples#1869: ``function_tool(f, needs_approval=...)``.
    agent = (
        "from agents import Agent, function_tool\n\n\n"
        "def pay() -> str:\n    return 'ok'\n\n\n"
        'payer = Agent(name="payer", tools=[function_tool(pay, needs_approval={approval})])\n'
    )
    base, head = _repo(
        tmp_path,
        {"app/agent.py": agent.format(approval="True")},
        {"app/agent.py": agent.format(approval="False")},
    )
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert (row["tool"], row["change"]) == ("pay", "changed")


def test_a_host_with_an_environment_default_names_both(tmp_path):
    # NoiseDigital/agent-platform#98: ``f"{os.getenv('URL', 'http://host:8080')}/sse"``.
    agent = """import os

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset

INTACCT_MCP_URL = os.getenv("INTACCT_MCP_URL", "http://mcp-sage-intacct:8080")

intacct_toolset = MCPToolset(connection_params=SseConnectionParams(url=f"{INTACCT_MCP_URL}/sse"))
root_agent = Agent(name="timesheet", model="m", tools={tools})
"""
    base, head = _repo(
        tmp_path,
        {"app/agent.py": agent.replace("{tools}", "[]")},
        {"app/agent.py": agent.replace("{tools}", "[intacct_toolset]")},
    )
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "compared", _limits(result)
    (row,) = result["rows"]
    assert row["after"]["object"]["class"] == "McpToolset"
    assert row["after"]["object"]["host"] == ["env INTACCT_MCP_URL", "mcp-sage-intacct:8080"]


def test_an_object_changed_through_a_method_or_a_closure_is_a_named_limit(tmp_path):
    appended = _cinescout()
    appended["app/agent.py"] = appended["app/agent.py"].replace(
        "root_agent = Agent(",
        'def widen() -> None:\n    parallel_search.tool_filter.append("admin")\n\n\nroot_agent = Agent(',
    )
    base, head = _repo(tmp_path, _cinescout("[]"), appended)
    result = _compare(tmp_path, base, head, "--scope", "app")
    assert result["comparison_status"] == "partial"
    assert any("`parallel_search` is changed after it is built" in limit for limit in result["head"]["limits"])

    closure = """from agents import Agent
from agents.mcp import MCPServerStdio


def build() -> Agent:
    server = MCPServerStdio(params={"command": "npx"})

    def swap() -> None:
        nonlocal server
        server = MCPServerStdio(params={"command": "bash"})

    return Agent(name="assistant", mcp_servers=[server])
"""
    root = tmp_path / "closure"
    root.mkdir()
    (root / "agent.py").write_text(closure)
    with reading_object_bindings(lambda path: False):
        loaded = load_openai_sdk_static_tools(
            ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agent.py"), None, root
        )
    (observation,) = loaded.binding_observations
    assert observation.object_bindings == {}
    assert observation.tools_complete is False


def test_a_command_file_name_shaped_like_a_key_is_withheld(tmp_path):
    agent = SDK_AGENT.replace('"command": "npx"', '"command": "/opt/run/tok3n9f8a7b6c5d4e3"')
    files = {"app/assistant.py": agent.format(tools="[]", servers="[server]", hosted="WebSearchTool", local="")}
    base, head = _repo(tmp_path, _sdk(), files)
    result = _compare(tmp_path, base, head, "--scope", "app")
    row = _rows(result)[("assistant", "server")]
    assert row["after"]["object"]["command"] == ["[REDACTED:sensitive_field]"]
    assert "tok3n9f8a7b6c5d4e3" not in json.dumps(result)
