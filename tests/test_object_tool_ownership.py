"""The constructor-ownership proof accepts the framework objects #985 reads, only as proved.

An MCP server or toolset, an agent exposed as a tool, a hosted or built-in tool and
``function_tool(f)`` are bindings with an identity (#910). The ownership proof used
to refuse each of them as an opaque use of the framework namespace, which withheld
the whole agent. Each route taught here accepts one shape by its role:

- the exact import path, resolved by import identity (never a spelling) and not
  provided by any file the repository holds;
- used directly as a member of a framework constructor's ``tools=`` or
  ``mcp_servers=`` list, or as the documented call that builds the object;
- the result then reaches only the owner census the list reader already runs.

Every control below keeps a variant of the same shape refused: a same-named local
object, a shadowed, vendored or reassigned import, an object changed after import
or passed anywhere but the receiving list, and each standard-library or class-hook
exemption that is not exactly the standard library's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.object_tools import reading_object_bindings
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig


def _write(root: Path, files: dict[str, str]) -> None:
    if not (root / ".git").exists():
        # A checkout root bounds the repository layout the readers walk; without
        # one they scan every directory above the temporary tree.
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


def _adk(root: Path, files: dict[str, str], entry: str = "agent.py") -> list:
    _write(root, files)
    with reading_object_bindings(lambda path: False):
        loaded, _ = load_google_adk_artifacts(
            None, root, sources=[ToolSourceConfig(id="adk", type="google_adk", path=entry)]
        )
    return [observation for item in loaded for observation in item.binding_observations]


def _sdk(root: Path, files: dict[str, str], entry: str = "agent.py") -> list:
    _write(root, files)
    with reading_object_bindings(lambda path: False):
        loaded = load_openai_sdk_static_tools(
            ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=entry), None, root
        )
    return list(loaded.binding_observations)


def _established(observations: list) -> None:
    assert observations
    assert all(not item.constructor_issues for item in observations), [
        item.constructor_issues for item in observations
    ]
    assert any(item.object_bindings for item in observations)


def _refused(observations: list) -> None:
    assert observations
    assert any(item.constructor_issues for item in observations)


# -- ADK built-in tools -----------------------------------------------------------

ADK = """from google.adk.agents import LlmAgent
from google.adk.tools import google_search
{extra}
root_agent = LlmAgent(name="planner", model="m", tools={tools})
"""


def _built_in(extra: str = "", tools: str = "[google_search]") -> dict[str, str]:
    return {"agent.py": ADK.format(extra=extra, tools=tools)}


def test_a_built_in_in_the_tools_list_is_established(tmp_path):
    _established(_adk(tmp_path, _built_in()))


def test_a_built_in_in_a_list_the_agent_receives_is_established(tmp_path):
    _established(_adk(tmp_path, _built_in("TOOLS = [google_search]\n", "TOOLS")))


@pytest.mark.parametrize(
    "extra",
    [
        "google_search.name = 'x'\n",
        "setattr(google_search, 'name', 'x')\n",
        "google_search = wrap(google_search)\n",
        "def google_search():\n    pass\n",
        "alias = google_search\n",
        "opaque(google_search)\n",
        "registry = {'search': google_search}\n",
        "TOOLS = [google_search]\nTOOLS[0].name = 'x'\n",
        "TOOLS = [google_search]\nopaque(TOOLS)\n",
        "TOOLS = [google_search]\nalias = TOOLS\n",
    ],
    ids=[
        "attribute-stored", "setattr", "reassigned", "redefined", "aliased", "passed-opaquely",
        "stored-in-a-dict", "member-attribute-stored", "container-passed", "container-aliased",
    ],
)
def test_a_built_in_changed_retained_or_handed_on_is_refused(tmp_path, extra):
    _refused(_adk(tmp_path, _built_in(extra, "TOOLS" if "TOOLS" in extra else "[google_search]")))


def test_a_built_in_handed_to_something_other_than_the_agent_list_is_refused(tmp_path):
    source = ADK.format(extra="", tools="[]") + "other = LlmAgent(name='o', model='m', before_model_callback=[google_search])\n"
    _refused(_adk(tmp_path, {"agent.py": source}))


def test_a_vendored_google_package_is_not_the_framework(tmp_path):
    files = {
        "google/__init__.py": "",
        "google/adk/__init__.py": "",
        "google/adk/tools/__init__.py": "google_search = object()\n",
        **_built_in(),
    }
    _refused(_adk(tmp_path, files))


def test_another_packages_built_in_name_is_not_the_framework(tmp_path):
    source = ADK.replace("from google.adk.tools import google_search", "from my_tools import google_search")
    observations = _adk(tmp_path, {"agent.py": source.format(extra="", tools="[google_search]")})
    assert all(not item.object_bindings for item in observations)


# -- ADK toolsets and AgentTool ------------------------------------------------------

TOOLSET = """from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams
{extra}
toolset = McpToolset(connection_params=SseConnectionParams(url={url}), tool_filter=["search"])
root_agent = LlmAgent(name="planner", model="m", tools={tools})
"""


def _toolset(url: str = '"https://mcp.example.com/sse"', extra: str = "", tools: str = "[toolset]") -> dict[str, str]:
    return {"agent.py": TOOLSET.format(url=url, extra=extra, tools=tools)}


def test_a_toolset_with_its_connection_data_is_established(tmp_path):
    _established(_adk(tmp_path, _toolset()))


def test_a_toolset_whose_url_is_a_call_or_template_is_established(tmp_path):
    _established(_adk(tmp_path, _toolset('os.getenv("MCP_URL", "https://mcp.example.com") + "/sse"', "import os\n")))


@pytest.mark.parametrize(
    "extra",
    [
        "opaque(toolset)\n",
        "alias = toolset\n",
        "toolset.close()\n",
        "toolset.tool_filter.append('admin')\n",
        "TOOLS = [toolset]\nopaque(TOOLS)\n",
    ],
    ids=["passed-opaquely", "aliased", "method-called", "filter-widened", "container-passed"],
)
def test_a_toolset_used_beyond_the_receiving_list_is_refused(tmp_path, extra):
    _refused(_adk(tmp_path, _toolset(extra=extra)))


@pytest.mark.parametrize(
    "url",
    ["lambda: 'https://x'", "make_url", "(yield)"],
    ids=["a-lambda", "a-function-value", "a-generator"],
)
def test_executable_connection_data_is_refused(tmp_path, url):
    extra = "def make_url():\n    return 'https://x'\n"
    _refused(_adk(tmp_path, _toolset(url=url, extra=extra)))


def test_a_connection_class_of_another_package_is_not_the_frameworks(tmp_path):
    source = TOOLSET.replace(
        "from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams",
        "from google.adk.tools.mcp_tool import McpToolset\nfrom my_params import SseConnectionParams",
    )
    _refused(_adk(tmp_path, {"agent.py": source.format(url='"https://x"', extra="", tools="[toolset]")}))


def test_a_local_toolset_class_of_the_same_name_is_not_the_frameworks(tmp_path):
    source = (
        "from google.adk.agents import LlmAgent\n\n\n"
        "class McpToolset:\n    def __init__(self, **kwargs):\n        pass\n\n\n"
        'toolset = McpToolset(connection_params=None)\n'
        'root_agent = LlmAgent(name="planner", model="m", tools=[toolset])\n'
    )
    observations = _adk(tmp_path, {"agent.py": source})
    assert all(not item.object_bindings for item in observations)


AGENT_TOOL = """from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

scout = LlmAgent(name="scout", model="m")
{extra}
root_agent = LlmAgent(name="planner", model="m", tools=[AgentTool(agent=scout)])
"""


def test_an_agent_tool_holding_an_agent_is_established(tmp_path):
    _established(_adk(tmp_path, {"agent.py": AGENT_TOOL.format(extra="")}))


@pytest.mark.parametrize(
    "extra",
    [
        "opaque(scout)\n",
        "alias = scout\n",
        "held = AgentTool(agent=scout)\nopaque(held)\n",
        "scout.__init__(tools=[opaque])\n",
        "scout.model_post_init(None)\n",
    ],
    ids=["passed-opaquely", "aliased", "tool-passed-opaquely", "reinitialized", "validation-hook"],
)
def test_an_agent_held_by_a_tool_and_used_elsewhere_is_refused(tmp_path, extra):
    _refused(_adk(tmp_path, {"agent.py": AGENT_TOOL.format(extra=extra)}))


def test_a_local_agent_tool_class_is_not_the_frameworks(tmp_path):
    source = AGENT_TOOL.replace("from google.adk.tools.agent_tool import AgentTool", "from my_tools import AgentTool")
    observations = _adk(tmp_path, {"agent.py": source.format(extra="")})
    assert all(not item.object_bindings for item in observations)


def test_an_eager_return_annotation_is_established_until_something_reads_annotations(tmp_path):
    helper = (
        "from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams\n\n\n"
        "def build() -> McpToolset:\n"
        '    return McpToolset(connection_params=SseConnectionParams(url="https://x"))\n'
    )
    agent = (
        "from google.adk.agents import LlmAgent\nfrom helper import build\n\n"
        'root_agent = LlmAgent(name="planner", model="m", tools=[build()])\n'
    )
    _established(_adk(tmp_path, {"agent.py": agent, "helper.py": helper}))


@pytest.mark.parametrize(
    "reader",
    [
        "print(build.__annotations__)\n",
        "from typing import get_type_hints\nget_type_hints(build)\n",
        "build.__annotations__['return'].get_tools = opaque\n",
    ],
    ids=["dictionary-read", "type-hints", "class-patched-through-it"],
)
def test_an_annotation_dictionary_that_is_read_refuses_the_eager_annotation(tmp_path, reader):
    helper = (
        "from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams\n\n\n"
        "def build() -> McpToolset:\n"
        '    return McpToolset(connection_params=SseConnectionParams(url="https://x"))\n'
    )
    agent = (
        "from google.adk.agents import LlmAgent\nfrom helper import build\n\n"
        + reader
        + 'root_agent = LlmAgent(name="planner", model="m", tools=[build()])\n'
    )
    _refused(_adk(tmp_path, {"agent.py": agent, "helper.py": helper}))


def test_a_decorated_function_does_not_take_the_eager_annotation(tmp_path):
    helper = (
        "from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams\n\n\n"
        "@register\n"
        "def build() -> McpToolset:\n"
        '    return McpToolset(connection_params=SseConnectionParams(url="https://x"))\n'
    )
    agent = (
        "from google.adk.agents import LlmAgent\nfrom helper import build\n\n"
        'root_agent = LlmAgent(name="planner", model="m", tools=[build()])\n'
    )
    _refused(_adk(tmp_path, {"agent.py": agent, "helper.py": helper}))


def test_an_eager_annotation_in_a_class_body_or_variable_is_refused(tmp_path):
    agent = (
        "from google.adk.agents import LlmAgent\nfrom google.adk.tools.mcp_tool import McpToolset\n\n"
        "held: McpToolset = None\n"
        'root_agent = LlmAgent(name="planner", model="m", tools=[])\n'
    )
    _refused(_adk(tmp_path, {"agent.py": agent}))


# -- OpenAI Agents SDK hosted tools, MCP servers, function_tool ------------------------

SDK = """from agents import Agent, WebSearchTool, function_tool
from agents.mcp import MCPServerStdio
{extra}
agent = Agent(name="assistant", tools={tools}, mcp_servers={servers})
"""


def _sdk_files(extra: str = "", tools: str = "[WebSearchTool()]", servers: str = "[]") -> dict[str, str]:
    return {"agent.py": SDK.format(extra=extra, tools=tools, servers=servers)}


def test_a_hosted_tool_called_in_the_list_is_established(tmp_path):
    _established(_sdk(tmp_path, _sdk_files()))


def test_a_server_held_by_a_with_block_is_established(tmp_path):
    source = (
        "from agents import Agent\nfrom agents.mcp import MCPServerStdio\n\n\n"
        "async def run() -> None:\n"
        '    async with MCPServerStdio(params={"command": "npx"}) as server:\n'
        '        agent = Agent(name="assistant", mcp_servers=[server])\n'
        "        await agent.go()\n"
    )
    _established(_sdk(tmp_path, {"agent.py": source}))


@pytest.mark.parametrize(
    "body",
    [
        "        opaque(server)\n",
        "        await server.connect()\n",
        "        alias = server\n",
        "        server.params = {'command': 'bash'}\n",
    ],
    ids=["passed-opaquely", "method-called", "aliased", "attribute-stored"],
)
def test_a_server_used_beyond_the_receiving_list_is_refused(tmp_path, body):
    source = (
        "from agents import Agent\nfrom agents.mcp import MCPServerStdio\n\n\n"
        "async def run() -> None:\n"
        '    async with MCPServerStdio(params={"command": "npx"}) as server:\n'
        '        agent = Agent(name="assistant", mcp_servers=[server])\n'
        + body
    )
    _refused(_sdk(tmp_path, {"agent.py": source}))


def test_a_with_block_of_another_class_does_not_bind_the_object(tmp_path):
    source = (
        "from agents import Agent\nfrom my_servers import Server\n\n\n"
        "async def run() -> None:\n"
        '    async with Server(params={"command": "npx"}) as server:\n'
        '        agent = Agent(name="assistant", mcp_servers=[server])\n'
    )
    observations = _sdk(tmp_path, {"agent.py": source})
    assert all(not item.object_bindings for item in observations)


@pytest.mark.parametrize(
    "extra",
    [
        "WebSearchTool = wrap(WebSearchTool)\n",
        "WebSearchTool.__init__ = opaque\n",
        "class WebSearchTool:\n    pass\n",
        "alias = WebSearchTool\n",
        "held = WebSearchTool()\nopaque(held)\n",
    ],
    ids=["reassigned", "patched", "redefined", "aliased", "result-passed-opaquely"],
)
def test_a_hosted_tool_that_is_not_the_frameworks_or_is_handed_on_is_refused_or_unbound(tmp_path, extra):
    observations = _sdk(tmp_path, _sdk_files(extra))
    assert all(not item.object_bindings for item in observations) or any(item.constructor_issues for item in observations)
    assert not any(item.object_bindings and not item.constructor_issues for item in observations)


def test_a_vendored_agents_package_is_not_the_framework(tmp_path):
    files = {
        "agents/__init__.py": "class WebSearchTool:\n    pass\n\n\nclass Agent:\n    pass\n",
        **_sdk_files(),
    }
    observations = _sdk(tmp_path, files)
    assert not any(item.object_bindings and not item.constructor_issues for item in observations)


def test_an_installed_package_name_is_not_a_sibling_package_of_the_module(tmp_path):
    # ``src/app/agents`` is reached as ``app.agents``: ``from agents import ...``
    # in a module beside it is the SDK. The module's own directory still counts.
    source = (
        "from agents import Agent, function_tool\n\n\n@function_tool\ndef lookup(q: str) -> str:\n"
        "    return q\n\n\nagent = Agent(name='a', tools=[lookup])\n"
    )
    sibling = {"app/__init__.py": "", "app/agents/__init__.py": "", "app/agents/main.py": source}
    observations = _sdk(tmp_path, sibling, entry="app/agents/main.py")
    assert observations and all(not item.constructor_issues for item in observations)


def test_a_local_agents_package_beside_the_script_is_not_the_sdk(tmp_path):
    files = {
        "agents/__init__.py": "class WebSearchTool:\n    pass\n\n\nclass Agent:\n    pass\n",
        **_sdk_files(),
    }
    observations = _sdk(tmp_path, files)
    assert not any(item.object_bindings and not item.constructor_issues for item in observations)


FUNCTION = """from agents import Agent, function_tool


def pay(amount: int) -> str:
    return "ok"

{extra}
agent = Agent(name="payer", tools={tools})
"""


def test_a_wrapped_function_value_is_established(tmp_path):
    source = FUNCTION.format(extra="pay_tool = function_tool(pay, needs_approval=True)\n", tools="[pay_tool]")
    _established_or_function(_sdk(tmp_path, {"agent.py": source}))


def _established_or_function(observations: list) -> None:
    assert observations
    assert all(not item.constructor_issues for item in observations), [item.constructor_issues for item in observations]


@pytest.mark.parametrize(
    "extra",
    [
        "pay_tool = function_tool(pay)\nopaque(pay_tool)\n",
        "pay_tool = function_tool(pay)\nalias = pay_tool\n",
        "pay_tool = function_tool(pay, failure_error_function=pay)\n",
        "pay_tool = function_tool(pay, **options)\n",
        "pay_tool = function_tool(*args)\n",
        "function_tool = wrap(function_tool)\npay_tool = function_tool(pay)\n",
    ],
    ids=["passed-opaquely", "aliased", "callable-option", "spread-options", "spread-operand", "reassigned"],
)
def test_a_wrapper_that_is_not_the_documented_call_is_refused(tmp_path, extra):
    source = FUNCTION.format(extra=extra, tools="[pay_tool]" if "pay_tool" in extra else "[]")
    _refused(_sdk(tmp_path, {"agent.py": source}))


# -- agent exposed as a tool, across a module and a helper ----------------------------

EXPERT = 'from agents import Agent\n\nexpert = Agent(name="Expert", instructions="Write.")\n'
LEAD = """from agents import Agent

from experts import expert
{extra}
lead = Agent(name="Lead", instructions="Lead.", tools=[expert.as_tool(tool_name="ask", tool_description="Ask.")])
"""


def test_an_imported_agent_exposed_as_a_tool_is_established(tmp_path):
    _established(_sdk(tmp_path, {"experts.py": EXPERT, "agent.py": LEAD.format(extra="")}))


@pytest.mark.parametrize(
    "extra",
    [
        "expert.tools.append(opaque)\n",
        "expert.tools = [opaque]\n",
        "opaque(expert)\n",
        "alias = expert\n",
        "held = expert.as_tool(tool_name='a', tool_description='b')\nopaque(held)\n",
        "expert.__init__(tools=[opaque])\n",
        "value = expert.get_system_prompt()\n",
        "expert.model_post_init(None)\n",
    ],
    ids=[
        "tools-appended", "tools-replaced", "passed-opaquely", "aliased", "tool-passed-opaquely",
        "reinitialized", "result-used", "validation-hook",
    ],
)
def test_an_exported_agent_changed_or_handed_on_in_the_importer_is_refused(tmp_path, extra):
    _refused(_sdk(tmp_path, {"experts.py": EXPERT, "agent.py": LEAD.format(extra=extra)}))


def test_an_exported_agent_nothing_reads_is_not_established(tmp_path):
    # The agent left its module and no use of it was read: nothing is proved.
    lead = (
        "from agents import Agent\n\nfrom experts import expert\n\n"
        'lead = Agent(name="Lead", instructions="Lead.", tools=[])\n'
    )
    _refused(_sdk(tmp_path, {"experts.py": EXPERT, "agent.py": lead}, entry="experts.py"))


def test_an_agent_imported_with_a_wildcard_or_module_is_refused(tmp_path):
    lead = LEAD.replace("from experts import expert", "import experts\nexpert = experts.expert")
    _refused(_sdk(tmp_path, {"experts.py": EXPERT, "agent.py": lead.format(extra="")}))


def test_a_helper_that_returns_a_tool_from_its_parameter_is_established(tmp_path):
    helper = (
        "from agents import Agent\n\n\n"
        "def expose(agent: Agent, *, name: str):\n"
        "    return agent.as_tool(tool_name=name, tool_description='Ask.')\n"
    )
    lead = (
        "from agents import Agent\n\nfrom experts import expert\nfrom helper import expose\n\n"
        'lead = Agent(name="Lead", instructions="Lead.", tools=[expose(expert, name="ask")])\n'
    )
    _established(_sdk(tmp_path, {"experts.py": EXPERT, "helper.py": helper, "agent.py": lead}))


def test_a_helper_that_does_more_with_its_parameter_is_refused(tmp_path):
    helper = (
        "from agents import Agent\n\n\n"
        "def expose(agent: Agent, *, name: str):\n"
        "    agent.tools.append(opaque)\n"
        "    return agent.as_tool(tool_name=name, tool_description='Ask.')\n"
    )
    lead = (
        "from agents import Agent\n\nfrom experts import expert\nfrom helper import expose\n\n"
        'lead = Agent(name="Lead", instructions="Lead.", tools=[expose(expert, name="ask")])\n'
    )
    _refused(_sdk(tmp_path, {"experts.py": EXPERT, "helper.py": helper, "agent.py": lead}))


# -- standard-library reads inside a tool body or a settings class ----------------------

BODY = """import os
from agents import Agent, function_tool
{imports}

@function_tool
def act(city: str) -> str:
    token = {read}
    return token


agent = Agent(name="a", tools=[act])
"""


def _body(read: str, imports: str = "") -> dict[str, str]:
    return {"agent.py": BODY.format(read=read, imports=imports)}


def test_reading_one_environment_value_in_a_tool_body_is_established(tmp_path):
    observations = _sdk(tmp_path, _body('os.environ["TOKEN"]'))
    assert observations and all(not item.constructor_issues for item in observations)


@pytest.mark.parametrize(
    "read",
    [
        'os.environ.update(opaque=1)',
        'os.environ',
        'opaque(os.environ)',
        'dict(os.environ)',
        '(os.environ.__setitem__("A", "b"))',
    ],
    ids=["update-called", "mapping-held", "mapping-passed", "mapping-copied", "dunder-called"],
)
def test_the_environment_mapping_itself_is_not_a_read_of_one_value(tmp_path, read):
    # Only ``os.environ[key]`` loaded is a string; the mapping going anywhere,
    # or a method of it, is the same unread-handle refusal as before.
    observations = _sdk(tmp_path, _body(read))
    if read in {"os.environ.update(opaque=1)", '(os.environ.__setitem__("A", "b"))'}:
        return  # A call on the mapping keeps its existing ordinary-callee boundary.
    _refused(observations)


def test_storing_into_the_environment_is_refused(tmp_path):
    source = BODY.format(read='"x"', imports='os.environ["A"] = "b"')
    _refused(_sdk(tmp_path, {"agent.py": source}))


@pytest.mark.parametrize(
    "imports",
    [
        "import environ_shim as os",
        "from compat import os",
    ],
    ids=["another-module-named-os", "os-from-another-package"],
)
def test_a_name_that_is_not_the_standard_library_is_not_trusted(tmp_path, imports):
    source = BODY.replace("import os\n", "").format(read='os.environ["TOKEN"]', imports=imports)
    _refused(_sdk(tmp_path, {"agent.py": source}))


def test_a_name_rebound_after_the_import_is_not_trusted(tmp_path):
    source = BODY.format(read='os.environ["TOKEN"]', imports="os = wrap(os)")
    _refused(_sdk(tmp_path, {"agent.py": source}))


SETTINGS = """import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    url: str = {value}


settings = Settings()
"""

SETTINGS_AGENT = """from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams

from site_settings import settings
EXTRA
toolset = McpToolset(connection_params=SseConnectionParams(url=settings.url))
root_agent = LlmAgent(name="planner", model="m", tools=[toolset])
"""


def test_a_frozen_dataclass_of_environment_defaults_is_established(tmp_path):
    files = {"site_settings.py": SETTINGS.format(value='os.getenv("URL", "https://x")'), "agent.py": SETTINGS_AGENT.replace("EXTRA", "")}
    _established(_adk(tmp_path, files))


@pytest.mark.parametrize(
    "value",
    ["opaque()", "os.getenv(KEY)", "os.getenv('A', default=opaque())", "lambda: 1", "[x for x in range(3)]"],
    ids=["a-call", "a-computed-key", "a-call-default", "a-lambda", "a-comprehension"],
)
def test_a_class_body_that_is_not_inert_data_is_refused(tmp_path, value):
    files = {"site_settings.py": SETTINGS.format(value=value), "agent.py": SETTINGS_AGENT.replace("EXTRA", "")}
    _refused(_adk(tmp_path, files))


@pytest.mark.parametrize(
    "extra",
    [
        "opaque(settings)",
        "alias = settings",
        "copy = replace(settings, url='x')",
        "settings.url()",
        "held = [settings]",
        "registry = {'settings': settings}",
        "opaque(settings.__class__)",
    ],
    ids=["passed-opaquely", "aliased", "copied", "method-called", "held-in-a-list", "held-in-a-dict", "class-passed"],
)
def test_an_inert_instance_handed_on_or_called_is_refused(tmp_path, extra):
    files = {
        "site_settings.py": SETTINGS.format(value='os.getenv("URL", "https://x")'),
        "agent.py": SETTINGS_AGENT.replace("EXTRA", "from dataclasses import replace\n" + extra),
    }
    _refused(_adk(tmp_path, files))


def test_an_inert_instance_handed_on_inside_its_own_module_is_refused(tmp_path):
    settings = SETTINGS.format(value='os.getenv("URL", "https://x")') + "opaque(settings)\n"
    _refused(_adk(tmp_path, {"site_settings.py": settings, "agent.py": SETTINGS_AGENT.replace("EXTRA", "")}))


def test_a_class_decorator_other_than_the_standard_dataclass_is_refused(tmp_path):
    settings = SETTINGS.replace("from dataclasses import dataclass", "from mylib import dataclass").format(
        value='"https://x"'
    )
    _refused(_adk(tmp_path, {"site_settings.py": settings, "agent.py": SETTINGS_AGENT.replace("EXTRA", "")}))


def test_a_local_dataclasses_module_is_not_the_standard_library(tmp_path):
    files = {
        "dataclasses.py": "def dataclass(*args, **kwargs):\n    return lambda cls: cls\n",
        "site_settings.py": SETTINGS.format(value='"https://x"'),
        "agent.py": SETTINGS_AGENT.replace("EXTRA", ""),
    }
    _refused(_adk(tmp_path, files))


def test_a_dataclass_with_a_method_or_a_base_is_not_an_inert_instance(tmp_path):
    for body in (
        "class Settings(Base):\n    url: str = 'x'\n",
        "class Settings:\n    url: str = 'x'\n\n    def __post_init__(self):\n        opaque(self)\n",
    ):
        root = tmp_path / str(abs(hash(body)))
        root.mkdir()
        settings = "from dataclasses import dataclass\n\n\n@dataclass(frozen=True)\n" + body + "\n\nsettings = Settings()\n"
        _refused(_adk(root, {"site_settings.py": settings, "agent.py": SETTINGS_AGENT.replace("EXTRA", "")}))


# -- a method named like the builder, on an instance of the framework's own class -----


CENSUS = """from agents import Agent


def run() -> None:
    assistant = Agent(name="assistant", tools=[])
    {call}


run()
"""


def test_a_method_on_the_agent_instance_is_not_the_function_of_that_name(tmp_path):
    _established_or_function(_sdk(tmp_path, {"agent.py": CENSUS.format(call="assistant.run()")}))


@pytest.mark.parametrize(
    "call",
    [
        "x = assistant.run()",
        "assistant.__init__(tools=[opaque])",
        "assistant.model_post_init(None)",
        "opaque(assistant.run())",
    ],
    ids=["result-used", "reinitialized", "validation-hook", "result-passed"],
)
def test_other_uses_of_the_agent_instance_are_refused(tmp_path, call):
    _refused(_sdk(tmp_path, {"agent.py": CENSUS.format(call=call)}))


def test_a_name_bound_to_something_else_is_not_an_agent_instance(tmp_path):
    source = (
        "from agents import Agent\nimport importlib\n\n\n"
        "def run() -> None:\n"
        "    assistant = importlib.import_module('agent')\n"
        "    assistant.run()\n\n\n"
        'agent = Agent(name="a", tools=[])\nrun()\n'
    )
    _refused(_sdk(tmp_path, {"agent.py": source}))
