from pathlib import Path

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_structured_file
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.inputs.openapi import load_openapi_tools

BASE = Path("samples/support_refund_agent")


def test_openapi_loader_extracts_operations():
    manifest = load_manifest(BASE / "shipgate.yaml")
    source = next(item for item in manifest.tool_sources if item.id == "support_openapi")
    loaded = load_openapi_tools(source, BASE)

    names = {tool.name for tool in loaded.tools}
    assert "stripe.create_refund" in names
    assert "refund_status_lookup" in names

    refund = next(tool for tool in loaded.tools if tool.name == "stripe.create_refund")
    assert refund.auth.scopes == ["stripe:refunds:write"]
    assert any(parameter.name == "amount" and parameter.maximum is None for parameter in refund.parameters)


def test_mcp_loader_extracts_tools_and_wildcard():
    manifest = load_manifest(BASE / "shipgate.yaml")
    mcp_source = next(item for item in manifest.tool_sources if item.id == "support_mcp_tools")
    wildcard_source = next(item for item in manifest.tool_sources if item.id == "wildcard_mcp_tools")

    loaded = load_mcp_tools(mcp_source, BASE)
    wildcard = load_mcp_tools(wildcard_source, BASE)

    assert {tool.name for tool in loaded.tools} == {
        "support.search_kb",
        "gmail.send_customer_email",
    }
    assert wildcard.tools[0].annotations["wildcard_tools"] is True


def test_mcp_loader_accepts_array_root(tmp_path):
    tools_path = tmp_path / "tools.json"
    tools_path.write_text(
        """
[
  {
    "name": "support.lookup",
    "description": "Look up support metadata.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "ticket_id": {"type": "string"}
      }
    },
    "annotations": {"readOnlyHint": true}
  }
]
""",
        encoding="utf-8",
    )
    loaded = load_mcp_tools(
        ToolSourceConfig(id="array_tools", type="mcp", path="tools.json"),
        tmp_path,
    )

    assert [tool.name for tool in loaded.tools] == ["support.lookup"]


def test_mcp_wildcard_with_tools_is_rejected(tmp_path):
    tools_path = tmp_path / "tools.json"
    tools_path.write_text(
        """
{
  "wildcard": true,
  "tools": [
    {"name": "support.lookup", "description": "Look up support metadata."}
  ]
}
""",
        encoding="utf-8",
    )

    try:
        load_mcp_tools(
            ToolSourceConfig(id="wildcard_tools", type="mcp", path="tools.json"),
            tmp_path,
        )
    except InputParseError as exc:
        assert "wildcard tool exposure and an explicit tools array" in str(exc)
    else:
        raise AssertionError("expected wildcard plus explicit tools to be rejected")


def test_mcp_loader_warns_on_duplicate_and_non_conventional_names(tmp_path):
    tools_path = tmp_path / "tools.json"
    tools_path.write_text(
        """
{
  "tools": [
    {"name": "bad/name|chars", "description": "Bad name."},
    {"name": "bad/name|chars", "description": "Duplicate bad name."}
  ]
}
""",
        encoding="utf-8",
    )

    loaded = load_mcp_tools(
        ToolSourceConfig(id="mcp_tools", type="mcp", path="tools.json"),
        tmp_path,
    )

    assert len(loaded.tools) == 2
    assert any("non-conventional" in warning for warning in loaded.warnings)
    assert any("Duplicate MCP tool name" in warning for warning in loaded.warnings)


def test_mcp_loader_rejects_path_traversal(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"tools": []}', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    try:
        load_mcp_tools(
            ToolSourceConfig(id="outside", type="mcp", path="../outside.json"),
            project,
        )
    except InputParseError as exc:
        assert "resolves outside manifest directory" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")


def test_json_content_with_yaml_extension_is_parsed_as_json(tmp_path):
    tools_path = tmp_path / "tools.yaml"
    tools_path.write_text(
        '[{"name": "support.lookup", "description": "Look up support metadata."}]',
        encoding="utf-8",
    )
    loaded = load_mcp_tools(
        ToolSourceConfig(id="json_in_yaml", type="mcp", path="tools.yaml"),
        tmp_path,
    )

    assert [tool.name for tool in loaded.tools] == ["support.lookup"]


def test_openapi_loader_handles_recursive_refs(tmp_path):
    spec_path = tmp_path / "recursive.openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: Recursive
  version: "1.0"
components:
  schemas:
    Node:
      type: object
      properties:
        child:
          $ref: "#/components/schemas/Node"
paths:
  /nodes:
    post:
      operationId: create_node
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/Node"
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )

    loaded = load_openapi_tools(
        ToolSourceConfig(id="recursive", type="openapi", path="recursive.openapi.yaml"),
        tmp_path,
    )

    assert [tool.name for tool in loaded.tools] == ["create_node"]
    child_schema = loaded.tools[0].input_schema["properties"]["child"]
    assert child_schema["x-agents-shipgate-recursive-ref"] is True


def test_openapi_loader_treats_empty_scope_list_as_no_specific_scopes(tmp_path):
    spec_path = tmp_path / "scoped.openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: Scoped
  version: "1.0"
components:
  securitySchemes:
    scopedOAuth:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: https://auth.example.test/token
          scopes:
            support:write: Write support records.
paths:
  /records:
    post:
      operationId: write_record
      security:
        - scopedOAuth: []
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )

    loaded = load_openapi_tools(
        ToolSourceConfig(id="scoped", type="openapi", path="scoped.openapi.yaml"),
        tmp_path,
    )

    assert loaded.tools[0].auth.type == "oauth2"
    assert loaded.tools[0].auth.scopes == []


def test_openapi_loader_uses_explicit_operation_scopes(tmp_path):
    spec_path = tmp_path / "explicit-scoped.openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: Scoped
  version: "1.0"
components:
  securitySchemes:
    scopedOAuth:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: https://auth.example.test/token
          scopes:
            support:write: Write support records.
paths:
  /records:
    post:
      operationId: write_record
      security:
        - scopedOAuth:
            - support:write
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )

    loaded = load_openapi_tools(
        ToolSourceConfig(id="scoped", type="openapi", path="explicit-scoped.openapi.yaml"),
        tmp_path,
    )

    assert loaded.tools[0].auth.scopes == ["support:write"]


def test_openapi_loader_warns_on_non_conventional_operation_id(tmp_path):
    spec_path = tmp_path / "weird.openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: Weird Names
  version: "1.0"
paths:
  /records:
    get:
      operationId: "[Click here](https://evil.example)"
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )

    loaded = load_openapi_tools(
        ToolSourceConfig(id="weird", type="openapi", path="weird.openapi.yaml"),
        tmp_path,
    )

    assert loaded.tools[0].name == "[Click here](https://evil.example)"
    assert any("non-conventional" in warning for warning in loaded.warnings)


def test_openapi_external_file_ref_is_not_resolved(tmp_path):
    spec_path = tmp_path / "external-ref.openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: External Ref
  version: "1.0"
paths:
  /external:
    post:
      operationId: external_ref
      requestBody:
        content:
          application/json:
            schema:
              $ref: "file:///etc/passwd"
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )

    loaded = load_openapi_tools(
        ToolSourceConfig(id="external", type="openapi", path="external-ref.openapi.yaml"),
        tmp_path,
    )

    assert loaded.tools[0].input_schema == {"$ref": "file:///etc/passwd"}
    assert any("Unresolved OpenAPI $ref" in warning for warning in loaded.warnings)


def test_openapi_operation_parse_error_is_fatal(tmp_path):
    spec_path = tmp_path / "bad-operation.openapi.yaml"
    spec_path.write_text(
        """
openapi: 3.1.0
info:
  title: Bad Operation
  version: "1.0"
paths:
  /bad:
    post: "not an object"
""",
        encoding="utf-8",
    )

    try:
        load_openapi_tools(
            ToolSourceConfig(id="bad", type="openapi", path="bad-operation.openapi.yaml"),
            tmp_path,
        )
    except InputParseError as exc:
        assert "must be an object" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")


def test_load_structured_file_rejects_large_inputs(tmp_path):
    input_path = tmp_path / "large.json"
    input_path.write_text(" " * (10 * 1024 * 1024 + 1), encoding="utf-8")

    try:
        load_structured_file(input_path)
    except InputParseError as exc:
        assert "Input file too large" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")


def test_yaml_alias_input_stays_within_loader_contract(tmp_path):
    input_path = tmp_path / "aliases.yaml"
    input_path.write_text(
        """
base: &base
  name: support.lookup
  description: Look up support metadata.
tools:
  - *base
  - *base
""",
        encoding="utf-8",
    )

    payload = load_structured_file(input_path)

    assert isinstance(payload, dict)
    assert len(payload["tools"]) == 2


def test_openai_sdk_static_handles_defaults_kwonly_and_name_override(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(
        """
from agents import function_tool

@function_tool(name_override="support.lookup")
def lookup(customer_id: str, limit: int = 10, *, include_notes: bool = False) -> str:
    \"\"\"Look up support metadata.\"\"\"
    return ""
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(
            id="sdk",
            type="openai_agents_sdk",
            path=str(agent_path),
        ),
        manifest,
        tmp_path,
    )

    tool = loaded.tools[0]
    assert tool.name == "support.lookup"
    assert [parameter.name for parameter in tool.parameters] == [
        "customer_id",
        "limit",
        "include_notes",
    ]
    assert tool.input_schema["required"] == ["customer_id"]


def test_openai_sdk_static_detects_aliased_function_tool_import(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(
        """
from agents import function_tool as ft
import openai_agents as oa

@ft(name_override="support.lookup")
def lookup(customer_id: str) -> str:
    return ""

@oa.function_tool
def summarize(case_id: str) -> str:
    return ""
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=str(agent_path)),
        manifest,
        tmp_path,
    )

    assert [tool.name for tool in loaded.tools] == ["support.lookup", "summarize"]


def test_openai_sdk_static_reads_description_override(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(
        """
from agents import function_tool

@function_tool(
    name_override="faq_lookup_tool",
    description_override="Lookup frequently asked questions.",
)
async def faq_lookup_tool(question: str) -> str:
    return ""
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=str(agent_path)),
        manifest,
        tmp_path,
    )

    assert len(loaded.tools) == 1
    assert loaded.tools[0].name == "faq_lookup_tool"
    assert loaded.tools[0].description == "Lookup frequently asked questions."


def test_openai_sdk_static_description_override_takes_precedence_over_docstring(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(
        """
from agents import function_tool

@function_tool(description_override="Override wins.")
def lookup(customer_id: str) -> str:
    \"\"\"Original docstring should not be used.\"\"\"
    return ""
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=str(agent_path)),
        manifest,
        tmp_path,
    )

    assert loaded.tools[0].description == "Override wins."


def test_openai_sdk_static_rejects_fake_function_tool_decorator(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(
        """
def fake_function_tool(fn):
    return fn

@fake_function_tool
def lookup(customer_id: str) -> str:
    return ""
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=str(agent_path)),
        manifest,
        tmp_path,
    )

    assert loaded.tools == []


def test_openai_sdk_static_syntax_error_is_input_parse_error(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def broken(:\n", encoding="utf-8")

    manifest = load_manifest(BASE / "shipgate.yaml")
    try:
        load_openai_sdk_static_tools(
            ToolSourceConfig(id="sdk", type="openai_agents_sdk", path=str(agent_path)),
            manifest,
            tmp_path,
        )
    except InputParseError as exc:
        assert "Unable to parse OpenAI Agents SDK entrypoint" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")
