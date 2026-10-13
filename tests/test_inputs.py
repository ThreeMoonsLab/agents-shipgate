from pathlib import Path

import pytest
import yaml

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_structured_file
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig

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


def test_mcp_loader_strips_reserved_binding_annotations(tmp_path):
    (tmp_path / "tools.json").write_text(
        '{"tools":[{"name":"danger","annotations":{'
        '"readOnlyHint":true,"agent_bindings":[{"agent":"root"}],'
        '"agent_handoffs":[],"adk_agent_name":"root",'
        '"adk_agent_source_id":"catalog","binding_surface_partial":[],'
        '"n8n_workflow_id":"forged"}}]}',
        encoding="utf-8",
    )

    loaded = load_mcp_tools(ToolSourceConfig(id="catalog", type="mcp", path="tools.json"), tmp_path)

    assert loaded.tools[0].annotations == {"readOnlyHint": True}
    assert any("reserved binding annotations" in warning for warning in loaded.warnings)


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


def _load_openapi_text(tmp_path, text, name="api.openapi.yaml"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return load_openapi_tools(
        ToolSourceConfig(id="api", type="openapi", path=name),
        tmp_path,
    )


def _pointer_target(document, pointer):
    for token in pointer.removeprefix("/").split("/"):
        document = document[token.replace("~1", "/").replace("~0", "~")]
    return document


_PATH_ITEM_REF_SPEC = """
openapi: 3.1.0
info:
  title: Accounts
  version: "1.0"
paths:
  /status:
    get:
      operationId: getStatus
      responses:
        "200":
          description: ok
  /accounts/{id}:
    $ref: '#/components/pathItems/Account'
components:
  pathItems:
    Account:
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      delete:
        operationId: deleteAccount
        responses:
          "204":
            description: removed
"""


def test_openapi_path_item_ref_reads_the_referenced_operations(tmp_path):
    """#1021: a path item reached through `$ref` was skipped without a word."""

    loaded = _load_openapi_text(tmp_path, _PATH_ITEM_REF_SPEC)

    assert [tool.name for tool in loaded.tools] == ["getStatus", "deleteAccount"]
    tool = loaded.tools[1]
    assert tool.annotations["httpMethod"] == "DELETE"
    assert tool.annotations["path"] == "/accounts/{id}"
    # The path item's own parameters come with it.
    assert tool.input_schema["required"] == ["id"]
    # The pointer and line name where the operation is written.
    assert tool.source_pointer == "/components/pathItems/Account/delete"
    assert tool.source_ref == "api.openapi.yaml#/components/pathItems/Account/delete"
    lines = _PATH_ITEM_REF_SPEC.splitlines()
    assert lines[tool.source_start_line - 1].strip() == "delete:"
    document = yaml.safe_load(_PATH_ITEM_REF_SPEC)
    assert _pointer_target(document, tool.source_pointer)["operationId"] == "deleteAccount"
    assert loaded.warnings == []
    assert loaded.omissions == []
    # The proof profile still refuses a DELETE whose path item is a reference.
    (row,) = loaded.operation_evidence
    assert row.status == "unresolved"
    assert row.source_pointer == tool.source_pointer


def test_openapi_path_item_ref_merges_fields_written_beside_it(tmp_path):
    loaded = _load_openapi_text(
        tmp_path,
        """
openapi: 3.1.0
info: {title: Accounts, version: "1.0"}
paths:
  /accounts:
    $ref: '#/components/pathItems/Accounts'
    get:
      operationId: listAccountsHere
      responses: {"200": {description: ok}}
    delete:
      operationId: purgeAccountsHere
      responses: {"204": {description: removed}}
components:
  pathItems:
    Accounts:
      $ref: '#/components/pathItems/Base'
      post:
        operationId: createAccount
        responses: {"201": {description: created}}
      delete:
        operationId: purgeAccountsThere
        responses: {"204": {description: removed}}
    Base:
      put:
        operationId: replaceAccounts
        responses: {"200": {description: ok}}
""",
    )

    pointers = {tool.name: tool.source_pointer for tool in loaded.tools}
    assert pointers == {
        "listAccountsHere": "/paths/~1accounts/get",
        "purgeAccountsHere": "/paths/~1accounts/delete",
        "createAccount": "/components/pathItems/Accounts/post",
        "replaceAccounts": "/components/pathItems/Base/put",
    }
    # OpenAPI leaves a field on both sides undefined. The one beside the $ref
    # is read, and the one it replaces is a recorded omission, not a silence.
    (omission,) = loaded.omissions
    assert omission.reason == "shadowed_path_item_operation"
    assert omission.subject == "/components/pathItems/Accounts/delete"
    assert omission.warning in loaded.warnings
    assert "'/accounts'" in omission.warning


@pytest.mark.parametrize(
    ("ref", "extra", "named", "wording"),
    [
        ("./paths/users.yaml", "", "./paths/users.yaml", "outside this document"),
        (
            "https://example.test/users.yaml#/Users",
            "",
            "https://example.test/users.yaml#/Users",
            "outside this document",
        ),
        (
            "#/components/pathItems/Missing",
            "",
            "#/components/pathItems/Missing",
            "does not resolve",
        ),
        (
            "#/components/pathItems/NotAnObject",
            "  pathItems:\n    NotAnObject: text\n",
            "#/components/pathItems/NotAnObject",
            "does not resolve",
        ),
        # A chain names the reference it could not follow: here, the one that
        # closes the loop back to the path item.
        (
            "#/components/pathItems/Loop",
            "  pathItems:\n    Loop:\n      $ref: '#/paths/~1users'\n",
            "#/paths/~1users",
            "does not resolve",
        ),
        (
            "#/components/pathItems/Hop",
            "  pathItems:\n    Hop:\n      $ref: './paths/users.yaml'\n",
            "./paths/users.yaml",
            "outside this document",
        ),
    ],
)
def test_openapi_unreadable_path_item_ref_is_a_named_omission(
    tmp_path, ref, extra, named, wording
):
    # A file at the external path proves it is reported, never opened.
    (tmp_path / "paths").mkdir()
    (tmp_path / "paths" / "users.yaml").write_text(
        "delete:\n  operationId: deleteUser\n  responses: {'204': {description: gone}}\n",
        encoding="utf-8",
    )
    loaded = _load_openapi_text(
        tmp_path,
        f"""
openapi: 3.1.0
info: {{title: Users, version: "1.0"}}
paths:
  /users:
    $ref: '{ref}'
components:
  schemas: {{}}
{extra}""",
    )

    assert loaded.tools == []
    (omission,) = loaded.omissions
    assert omission.reason == "unresolved_path_item_ref"
    assert omission.subject == "/paths/~1users"
    assert loaded.warnings == [omission.warning]
    assert "'/users'" in omission.warning
    assert repr(named) in omission.warning
    assert "'api.openapi.yaml'" in omission.warning
    assert wording in omission.warning


def test_openapi_resolvable_schema_refs_do_not_warn(tmp_path):
    """#1021: refs the document resolves were reported as unresolved."""

    loaded = _load_openapi_text(
        tmp_path,
        """
openapi: 3.1.0
info: {title: Labels, version: "1.0"}
components:
  schemas:
    Label:
      type: object
      properties:
        name: {type: string}
    LabelAlias:
      $ref: '#/components/schemas/Label'
    Node:
      type: object
      properties:
        children:
          type: array
          items: {$ref: '#/components/schemas/Node'}
paths:
  /labels:
    get:
      operationId: listLabels
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                additionalProperties: {$ref: '#/components/schemas/Label'}
                patternProperties:
                  "^x-": {$ref: '#/components/schemas/LabelAlias'}
                not: {$ref: '#/components/schemas/Label'}
                prefixItems: [{$ref: '#/components/schemas/Label'}]
                items: [{$ref: '#/components/schemas/Label'}]
  /nodes:
    post:
      operationId: createNode
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/Node'}
      responses:
        "201":
          description: created
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Node'}
""",
    )

    assert loaded.warnings == []
    labels, nodes = loaded.tools
    label = {"type": "object", "properties": {"name": {"type": "string"}}}
    output = labels.output_schema
    assert output["additionalProperties"] == label
    assert output["patternProperties"] == {"^x-": label}
    assert output["not"] == label
    assert output["prefixItems"] == [label]
    assert output["items"] == [label]
    child = nodes.output_schema["properties"]["children"]["items"]
    assert child["x-agents-shipgate-recursive-ref"] is True


def test_openapi_missing_schema_ref_warns_once_per_target(tmp_path):
    loaded = _load_openapi_text(
        tmp_path,
        """
openapi: 3.1.0
info: {title: Labels, version: "1.0"}
paths:
  /labels:
    get:
      operationId: listLabels
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                additionalProperties: {$ref: '#/components/schemas/Missing'}
    post:
      operationId: createLabel
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/Missing'}
      responses:
        "201":
          description: created
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Missing'}
""",
    )

    assert loaded.warnings == [
        "Unresolved OpenAPI $ref '#/components/schemas/Missing' in tool 'listLabels' "
        "and 1 other tool; external or missing refs are left as metadata."
    ]


def test_openapi_document_cannot_silence_an_external_ref_with_the_recursion_marker(tmp_path):
    loaded = _load_openapi_text(
        tmp_path,
        """
openapi: 3.1.0
info: {title: Forged, version: "1.0"}
paths:
  /labels:
    post:
      operationId: createLabel
      requestBody:
        content:
          application/json:
            schema:
              $ref: './schemas/label.yaml'
              x-agents-shipgate-recursive-ref: true
      responses:
        "201":
          description: created
""",
    )

    assert loaded.warnings == [
        "Unresolved OpenAPI $ref './schemas/label.yaml' in tool 'createLabel'; "
        "external or missing refs are left as metadata."
    ]


def test_openapi_unquoted_status_codes_select_the_lowest_success_response(tmp_path):
    loaded = _load_openapi_text(
        tmp_path,
        """
openapi: 3.1.0
info: {title: Status, version: "1.0"}
paths:
  /records:
    post:
      operationId: createRecord
      responses:
        400:
          description: bad
          content:
            application/json:
              schema: {type: object, properties: {error: {type: string}}}
        201:
          description: created
          content:
            application/json:
              schema: {type: object, properties: {created: {type: string}}}
        200:
          description: ok
          content:
            application/json:
              schema: {type: object, properties: {record: {type: string}}}
  /ranges:
    get:
      operationId: listRanges
      responses:
        4XX:
          description: bad
          content:
            application/json:
              schema: {type: string}
        2XX:
          description: ok
          content:
            application/json:
              schema: {type: integer}
""",
    )

    record, ranges = loaded.tools
    assert record.output_schema["properties"] == {"record": {"type": "string"}}
    assert ranges.output_schema == {"type": "integer"}


def test_openapi_document_without_delete_is_never_composed(tmp_path, monkeypatch):
    """The DELETE proof's pure-Python parse runs only when a DELETE needs it."""

    from agents_shipgate.inputs import openapi as openapi_module

    calls = {"compose": 0, "parse": 0, "proof": 0}
    real_compose, real_parse = yaml.compose, yaml.parse
    real_proof = openapi_module.unambiguous_document

    def compose(*args, **kwargs):
        calls["compose"] += 1
        return real_compose(*args, **kwargs)

    def parse(*args, **kwargs):
        calls["parse"] += 1
        return real_parse(*args, **kwargs)

    def proof(text):
        calls["proof"] += 1
        return real_proof(text)

    monkeypatch.setattr(yaml, "compose", compose)
    monkeypatch.setattr(yaml, "parse", parse)
    monkeypatch.setattr(openapi_module, "unambiguous_document", proof)
    loaded = _load_openapi_text(
        tmp_path,
        '{"openapi": "3.0.3", "info": {"title": "Reads", "version": "1"}, "paths": '
        '{"/a": {"get": {"operationId": "readA", "responses": {"200": {"description": "ok"}}}}}}',
        name="reads.json",
    )
    assert [tool.name for tool in loaded.tools] == ["readA"]
    assert calls == {"compose": 0, "parse": 0, "proof": 0}

    loaded = _load_openapi_text(
        tmp_path,
        '{"openapi": "3.0.3", "info": {"title": "Deletes", "version": "1"}, "paths": {'
        '"/a": {"delete": {"operationId": "deleteA", "responses": {"204": {"description": "ok"}}}},'
        '"/b": {"delete": {"operationId": "deleteB", "responses": {"204": {"description": "ok"}}}}}}',
        name="deletes.json",
    )
    assert len(loaded.operation_evidence) == 2
    assert calls == {"compose": 1, "parse": 1, "proof": 1}


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


def test_openai_sdk_static_records_toolless_router_handoffs(tmp_path):
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(
        """
from agents import Agent, function_tool

@function_tool
def lookup(query: str) -> str:
    return ""

worker = Agent(tools=[lookup])
router = Agent(tools=[], handoffs=[worker])
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agent.py"),
        manifest,
        tmp_path,
    )

    by_agent = {observation.agent: observation for observation in loaded.binding_observations}
    assert by_agent["router"].tool_names == []
    assert by_agent["router"].handoff_names == ["worker"]
    assert by_agent["router"].handoffs_complete is True


def test_openai_sdk_static_directory_scans_sorted_python_files(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "b_tools.py").write_text(
        """
from agents import function_tool as ft

@ft(description_override="Render a reply draft.")
async def render_reply(case_id: str, *, include_private_notes: bool = False) -> str:
    return ""
""",
        encoding="utf-8",
    )
    (agents_dir / "a_tools.py").write_text(
        """
import agents

@agents.function_tool(name_override="support.lookup_case")
def lookup_case(config, case_id: str, tags: list[str] | None = None) -> dict:
    \"\"\"Look up support metadata.\"\"\"
    return {}
""",
        encoding="utf-8",
    )
    (agents_dir / "dynamic.py").write_text(
        """
def tool_factory():
    return []
""",
        encoding="utf-8",
    )

    manifest = load_manifest(BASE / "shipgate.yaml")
    loaded = load_openai_sdk_static_tools(
        ToolSourceConfig(id="sdk", type="openai_agents_sdk", path="agents"),
        manifest,
        tmp_path,
    )

    assert [tool.name for tool in loaded.tools] == [
        "support.lookup_case",
        "render_reply",
    ]
    lookup, render = loaded.tools
    assert lookup.source_ref == "agents/a_tools.py"
    assert lookup.source_location == "agents/a_tools.py:5"
    assert [parameter.name for parameter in lookup.parameters] == ["case_id", "tags"]
    assert lookup.input_schema["properties"]["tags"]["type"] == "array"
    assert lookup.output_schema["type"] == "object"
    assert render.source_ref == "agents/b_tools.py"
    assert render.description == "Render a reply draft."


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
    assert loaded.tools[0].source_ref == "agent.py"


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


def test_mcp_loader_rejects_symlink_escaping_manifest_dir(tmp_path):
    """A symlink inside the manifest dir pointing outside must be rejected.

    resolve() follows symlinks before the containment check, so the final
    real path lands outside the manifest dir and relative_to() raises.
    This test pins that behavior against regressions (e.g. a future
    refactor that checks the lexical path instead of the resolved one).
    """
    outside = tmp_path / "outside.json"
    outside.write_text('{"tools": []}', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "link.json").symlink_to(outside)

    try:
        load_mcp_tools(
            ToolSourceConfig(id="link", type="mcp", path="link.json"),
            project,
        )
    except InputParseError as exc:
        assert "resolves outside manifest directory" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")


def test_mcp_loader_rejects_symlinked_directory_escape(tmp_path):
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "tools.json").write_text('{"tools": []}', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "sub").symlink_to(outside_dir, target_is_directory=True)

    try:
        load_mcp_tools(
            ToolSourceConfig(id="sub", type="mcp", path="sub/tools.json"),
            project,
        )
    except InputParseError as exc:
        assert "resolves outside manifest directory" in str(exc)
    else:
        raise AssertionError("Expected InputParseError")


def test_mcp_loader_allows_symlink_resolving_inside_manifest_dir(tmp_path):
    """Symlinks that stay inside the manifest dir are legitimate (monorepo
    layouts) and must keep working."""
    project = tmp_path / "project"
    project.mkdir()
    real = project / "real-tools.json"
    real.write_text('{"tools": []}', encoding="utf-8")
    (project / "alias.json").symlink_to(real)

    loaded = load_mcp_tools(
        ToolSourceConfig(id="alias", type="mcp", path="alias.json"),
        project,
    )
    assert loaded.tools == []
