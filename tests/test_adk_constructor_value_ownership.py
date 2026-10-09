"""ADK static value roles preserve existing sinks without granting object APIs."""

import pytest

from agents_shipgate.core.artifact_models import GoogleAdkArtifacts
from agents_shipgate.inputs.google_adk import _load_python_path

HEADER = '''from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import OpenAPIToolset
from google.adk.tools import ToolContext
from pathlib import Path
raise RuntimeError("the application must never be imported")
def lookup(q: str) -> str:
    """Look up the stored record."""
    return q
'''
MCP = 'McpToolset(inventory_path="inventory.json", tool_filter=["remote"])'
OPENAPI = 'OpenAPIToolset(spec_str=Path("spec.yaml").read_text(), spec_str_type="yaml")'


def load(tmp_path, body, extra=None):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'agent.py').write_text(HEADER + body)
    (project / 'inventory.json').write_text('{"tools":[{"name":"remote","description":"Read a remote record","inputSchema":{"type":"object","properties":{"q":{"type":"string"}}}}]}')
    (project / 'spec.yaml').write_text('openapi: 3.1.0\ninfo:\n  title: fixture\n  version: "1"\npaths:\n  /record:\n    get:\n      operationId: api.read\n      responses:\n        "200":\n          description: ok\n')
    for path, content in (extra or {}).items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    artifacts = GoogleAdkArtifacts()
    sources = _load_python_path(project / 'agent.py', project, 'adk', 'agent.py', artifacts)
    return [tool for source in sources for tool in source.tools], artifacts


def established(tools, artifacts):
    assert tools and all(tool.extraction_confidence == 'high' for tool in tools)
    assert not any('constructor identity is not established' in warning for warning in artifacts.warnings)


def limited(tools, artifacts):
    assert any('constructor identity is not established' in warning for warning in artifacts.warnings), artifacts.warnings
    assert all(tool.extraction_confidence != 'high' for tool in tools)


@pytest.mark.parametrize('value', [MCP, OPENAPI])
@pytest.mark.parametrize('cache', [False, True])
def test_resolved_toolset_keeps_the_existing_literal_agent_tools_sink(tmp_path, value, cache):
    body = ('held = ' + value + '\n' if cache else '')
    body += 'root_agent = LlmAgent(name="x", tools=[lookup, ' + ('held' if cache else value) + '])\n'
    tools, artifacts = load(tmp_path, body)
    established(tools, artifacts)
    assert {tool.name for tool in tools} == {'lookup', 'remote' if value == MCP else 'api.read'}


@pytest.mark.parametrize('value', [MCP, OPENAPI])
@pytest.mark.parametrize('use', [
    'opaque(held)', 'saved = held', 'held.copy()', 'held.count(None)', 'held.index(None)',
    'held["tools"]', 'held.metadata', 'held.__init__',
    'root_agent = LlmAgent(name="y", tools=[], before_tool_callback=held)',
])
def test_toolset_result_does_not_borrow_container_or_callback_permissions(tmp_path, value, use):
    tools, artifacts = load(tmp_path, 'held = ' + value + '\n' + use + '\nroot_agent = LlmAgent(name="x", tools=[lookup, held])\n')
    limited(tools, artifacts)


@pytest.mark.parametrize('argument', [
    'tool_filter=lookup', 'tool_filter=[lookup]', 'header_provider=lookup',
    'progress_callback=lookup', 'sampling_callback=lookup', 'require_confirmation=lookup',
    'connection_params=lookup', 'unknown_option=None', '**options', '*options',
])
def test_mcp_callback_and_unknown_configuration_remain_unread(tmp_path, argument):
    tools, artifacts = load(tmp_path, 'options = {}\nheld = McpToolset(inventory_path="inventory.json", ' + argument + ')\nroot_agent = LlmAgent(name="x", tools=[lookup, held])\n')
    limited(tools, artifacts)


@pytest.mark.parametrize('argument', [
    'header_provider=lookup', 'httpx_client_factory=lookup', 'auth_scheme=lookup',
    'auth_credential=lookup', 'tool_filter=lookup', 'unknown_option=None',
    'spec_str=read_spec()', 'spec_str=Path("spec.yaml").other()',
])
def test_openapi_only_reads_literal_or_exact_local_spec_data(tmp_path, argument):
    tools, artifacts = load(tmp_path, 'held = OpenAPIToolset(spec_path="spec.yaml", ' + argument + ')\nroot_agent = LlmAgent(name="x", tools=[lookup, held])\n')
    limited(tools, artifacts)


@pytest.mark.parametrize('provider', ['mcp.py', 'mcp/__init__.py', 'pathlib.py', 'json.py', 'yaml.py', 'yaml/__init__.py'])
def test_toolset_dependency_and_path_providers_are_proved(tmp_path, provider):
    value = OPENAPI if provider.startswith(('pathlib', 'json', 'yaml')) else MCP
    tools, artifacts = load(tmp_path, 'root_agent = LlmAgent(name="x", tools=[lookup, ' + value + '])\n', {provider: 'from abc import ABCMeta\ndef replacement(*args, **kwargs):\n    return None\nABCMeta.__call__ = replacement\n'})
    limited(tools, artifacts)


def test_toolset_instance_identity_comparison_keeps_its_data_role(tmp_path):
    tools, artifacts = load(tmp_path, 'held = ' + MCP + '\nheld is None\nroot_agent = LlmAgent(name="x", tools=[lookup, held])\n')
    established(tools, artifacts)


@pytest.mark.parametrize('path', ['google.adk.tools', 'google.adk.tools.tool_context'])
def test_exact_own_family_context_annotation_keeps_injection(tmp_path, path):
    tools, artifacts = load(tmp_path, 'from ' + path + ' import ToolContext as ContextType\ndef contextual(ctx: ContextType, q: str) -> str:\n    """Read a record in its injected context."""\n    return q\nroot_agent = LlmAgent(name="x", tools=[lookup, contextual])\n')
    established(tools, artifacts)
    contextual = next(tool for tool in tools if tool.name == 'contextual')
    assert set(contextual.input_schema['properties']) == {'q'}


@pytest.mark.parametrize('use', [
    'opaque(ToolContext)', 'saved = ToolContext', 'ToolContext()', 'saved = ToolContext.metadata',
    'values = [ToolContext]', 'ToolContext.__init__ = None',
])
def test_context_annotation_role_does_not_grant_other_class_uses(tmp_path, use):
    tools, artifacts = load(tmp_path, use + '\nroot_agent = LlmAgent(name="x", tools=[lookup])\n')
    limited(tools, artifacts)


def test_context_annotation_cannot_borrow_a_foreign_sdk_owner(tmp_path):
    tools, artifacts = load(tmp_path, 'from agents import Agent, function_tool\n@function_tool\ndef foreign(ctx: ToolContext, q: str) -> str:\n    return q\nforeign_agent = Agent(name="foreign", tools=[foreign])\nroot_agent = LlmAgent(name="x", tools=[lookup])\n')
    limited(tools, artifacts)


CONNECTIONS = (
    "import os\n"
    "from google.adk.tools.mcp_tool import StdioConnectionParams, StreamableHTTPConnectionParams\n"
    "from mcp import StdioServerParameters\n"
)
HTTP = 'StreamableHTTPConnectionParams(url="https://example.test/mcp", headers={"Authorization": os.environ["KEY"]})'
STDIO = ('StdioConnectionParams(server_params=StdioServerParameters(command="npx", args=["server"], '
         'env={"API_KEY": os.environ["KEY"]}))')


def _bound(connection, wrap='{}'):
    toolset = f'McpToolset(inventory_path="inventory.json", connection_params={wrap.format(connection)})'
    return CONNECTIONS + f'held = {toolset}\nroot_agent = LlmAgent(name="x", tools=[lookup, held])\n'


@pytest.mark.parametrize('connection', [HTTP, STDIO], ids=['headers', 'nested_stdio_env'])
def test_an_environment_read_in_connection_data_reaching_the_toolset_is_a_string(tmp_path, connection):
    tools, artifacts = load(tmp_path, _bound(connection))
    established(tools, artifacts)


@pytest.mark.parametrize('connection', [HTTP, STDIO], ids=['headers', 'nested_stdio_env'])
@pytest.mark.parametrize('route', [
    'opaque({})',  # Handed to an unrelated call before it reaches the toolset.
    '[{}][0]',  # Held in a container.
    '(lambda: {})()',  # Built where the framework could not own it.
])
def test_an_environment_read_in_connection_data_is_unread_off_the_toolset_route(tmp_path, connection, route):
    tools, artifacts = load(tmp_path, _bound(connection, route))
    limited(tools, artifacts)


def test_connection_data_is_unread_when_it_is_saved_before_the_toolset(tmp_path):
    body = CONNECTIONS + f'params = {HTTP}\nheld = McpToolset(inventory_path="inventory.json", connection_params=params)\n' \
        'root_agent = LlmAgent(name="x", tools=[lookup, held])\n'
    tools, artifacts = load(tmp_path, body)
    limited(tools, artifacts)


def test_nested_connection_data_follows_only_connection_classes(tmp_path):
    connection = 'StdioConnectionParams(server_params=wrap(StdioServerParameters(command="npx", env={"API_KEY": os.environ["KEY"]})))'
    tools, artifacts = load(tmp_path, _bound(connection))
    limited(tools, artifacts)


@pytest.mark.parametrize('keyword', ['header_provider', 'tool_filter', 'errlog'])
def test_connection_data_reaches_only_the_toolsets_connection_params(tmp_path, keyword):
    body = CONNECTIONS + f'held = McpToolset(inventory_path="inventory.json", {keyword}={STDIO})\n' \
        'root_agent = LlmAgent(name="x", tools=[lookup, held])\n'
    tools, artifacts = load(tmp_path, body)
    limited(tools, artifacts)


def test_an_mcp_connection_class_the_repository_shadows_is_not_the_packages(tmp_path):
    tools, artifacts = load(tmp_path, _bound(STDIO), extra={'mcp.py': 'class StdioServerParameters:\n    def __init__(self, **options):\n        self.options = options\n'})
    limited(tools, artifacts)


@pytest.mark.parametrize('argument', ['tool_name_prefix=os.environ["KEY"]', 'credential_key=os.environ["KEY"]'])
def test_an_environment_read_as_a_toolset_argument_stays_unread(tmp_path, argument):
    body = CONNECTIONS + f'held = McpToolset(inventory_path="inventory.json", {argument})\n' \
        'root_agent = LlmAgent(name="x", tools=[lookup, held])\n'
    tools, artifacts = load(tmp_path, body)
    limited(tools, artifacts)
