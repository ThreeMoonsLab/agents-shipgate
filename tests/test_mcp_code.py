from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.inputs import mcp_code
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.mcp_code import (
    GO_ADDTOOL_V1,
    GO_MUSTTOOL_V1,
    TYPESCRIPT_MCP_SDK_V1,
    TYPESCRIPT_STATIC_TOOL_V1,
)
from agents_shipgate.schemas.manifest import ToolSourceConfig
from agents_shipgate.schemas.manifest.tool_sources import (
    MCP_IDIOM_IDS,
    MCP_TOOL_SNAPSHOT_V1,
)


def _source(path: str, idiom: str, *, source_id: str = "server") -> ToolSourceConfig:
    return ToolSourceConfig(id=source_id, type="mcp", path=path, idiom=idiom)


def test_typescript_static_tool_names_and_operation_types_are_measured(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "dropDatabase.ts").write_text(
        """
export class DropDatabaseTool extends MongoDBToolBase {
    static toolName = "drop-database";
    public description = "Drop a MongoDB database.";
    static operationType: OperationType = "delete";
}
""",
        encoding="utf-8",
    )
    (tools / "listDatabases.ts").write_text(
        """
export class ListDatabasesTool extends MongoDBToolBase {
    static toolName = "list-databases";
    static operationType: OperationType = "metadata";
}
export class ConnectTool extends MongoDBToolBase {
    static toolName = "connect";
    static operationType: OperationType = "connect";
}
""",
        encoding="utf-8",
    )

    loaded = load_mcp_tools(_source("tools", TYPESCRIPT_STATIC_TOOL_V1), tmp_path)

    assert [tool.name for tool in loaded.tools] == [
        "drop-database",
        "list-databases",
        "connect",
    ]
    drop = loaded.tools[0]
    assert drop.extraction_confidence == "medium"
    assert drop.extraction == {
        "method": "mcp_code_idiom",
        "confidence": "medium",
        "registry_version": "1",
        "idiom": TYPESCRIPT_STATIC_TOOL_V1,
        "surface": "partial",
        "tool_set_proven": False,
        "surface_gaps": ["definition_only_runtime_binding"],
    }
    assert drop.annotations["operationType"] == "delete"
    assert {(hint.tag, hint.basis, hint.confidence) for hint in drop.risk_hints} == {
        ("destructive", "typed_provider_fact", "medium"),
        ("write", "typed_provider_fact", "medium"),
    }
    assert [hint.tag for hint in loaded.tools[1].risk_hints] == ["read_only"]
    # Connection selection is provider state, not proof of a data mutation.
    assert loaded.tools[2].risk_hints == []
    assert any("does not prove which definitions are registered" in row for row in loaded.warnings)
    assert [row.reason for row in loaded.omissions] == [
        "definition_only_runtime_binding"
    ]


def test_typescript_sdk_literal_calls_and_dynamic_name_omission(tmp_path: Path) -> None:
    (tmp_path / "server.ts").write_text(
        """
// server.registerTool("comment-only", {}, cb);
server.registerTool("search", {
  description: "Search the catalog",
  annotations: {readOnlyHint: true, destructiveHint: false}
}, search);
server.tool(runtimeName(), handler);
const example = `server.registerTool("template-only", {}, cb)`;
unrelated.tool("not-an-mcp-tool", handler);
""",
        encoding="utf-8",
    )

    loaded = load_mcp_tools(
        _source("server.ts", TYPESCRIPT_MCP_SDK_V1), tmp_path
    )

    assert [tool.name for tool in loaded.tools] == ["search"]
    tool = loaded.tools[0]
    assert tool.description == "Search the catalog"
    assert tool.annotations == {"readOnlyHint": True, "destructiveHint": False}
    assert tool.extraction["surface"] == "partial"
    assert tool.extraction["tool_set_proven"] is False
    assert len(loaded.omissions) == 1
    assert loaded.omissions[0].reason == "dynamic_tool_name"
    assert "server.tool" in loaded.omissions[0].detail
    assert loaded.omissions[0].warning in loaded.warnings


def test_name_expressions_are_not_truncated_to_a_literal_prefix(tmp_path: Path) -> None:
    (tmp_path / "server.ts").write_text(
        """
server.registerTool("prefix-" + suffix, {}, dynamicHandler);
server.registerTool("literal", {}, literalHandler);
""",
        encoding="utf-8",
    )
    (tmp_path / "definitions.ts").write_text(
        """
class DynamicTool {
  static toolName = "prefix-" + suffix;
}
class LiteralTool {
  static toolName = "literal-static";
  static operationType = "delete" + operationSuffix;
}
class NotStaticTool {
  static unused;
  toolName = "must-not-inherit-static";
}
""",
        encoding="utf-8",
    )
    (tmp_path / "server.go").write_text(
        """
package main
func mount(server *mcp.Server) {
    mcp.AddTool(server, &mcp.Tool{Name: "prefix-" + suffix}, dynamicHandler)
    mcp.AddTool(server, &mcp.Tool{Name: "literal-go"}, literalHandler)
}
""",
        encoding="utf-8",
    )

    sdk = load_mcp_tools(_source("server.ts", TYPESCRIPT_MCP_SDK_V1), tmp_path)
    definitions = load_mcp_tools(
        _source("definitions.ts", TYPESCRIPT_STATIC_TOOL_V1), tmp_path
    )
    go = load_mcp_tools(_source("server.go", GO_ADDTOOL_V1), tmp_path)

    assert [tool.name for tool in sdk.tools] == ["literal"]
    assert [row.reason for row in sdk.omissions] == ["dynamic_tool_name"]
    assert [tool.name for tool in definitions.tools] == ["literal-static"]
    assert definitions.tools[0].risk_hints == []
    assert "operationType" not in definitions.tools[0].annotations
    assert [row.reason for row in definitions.omissions] == [
        "dynamic_tool_name",
        "definition_only_runtime_binding",
    ]
    assert [tool.name for tool in go.tools] == ["literal-go"]
    assert [row.reason for row in go.omissions] == ["dynamic_tool_name"]


def test_dynamic_name_reaches_the_report_exclusion_ledger(tmp_path: Path) -> None:
    (tmp_path / "server.ts").write_text(
        "server.registerTool(toolNameFromConfig(), {}, handler);\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        """
version: "0.1"
project: {name: dynamic-mcp}
agent:
  name: server
  declared_purpose: [publish a tool surface]
environment: {target: production_like}
tool_sources:
  - id: server_mcp
    type: mcp
    path: server.ts
    idiom: typescript_mcp_sdk_v1
""".lstrip(),
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=manifest,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    (entry,) = [
        row for row in report.surface_exclusions.entries if row.stage == "adapter_parse"
    ]
    assert entry.reason == "dynamic_tool_name"
    assert entry.subject == "server.ts:1"
    assert entry.accounting == "evidence_gap"
    assert entry.accounted_by is not None
    assert "does not use a static string literal" in entry.detail


def test_go_musttool_is_mcp_anchored_and_registration_linked(tmp_path: Path) -> None:
    (tmp_path / "incident.go").write_text(
        """
package tools

var UpdateIncident = mcpgrafana.MustTool(
    "update_incident",
    "Update an incident",
    updateIncident,
    mcp.WithReadOnlyHintAnnotation(false),
    mcp.WithIdempotentHintAnnotation(true),
    mcp.WithDestructiveHintAnnotation(false),
)
var NotMCP = widgets.MustTool("generic_widget", "not MCP", buildWidget)
var MisleadingSuffix = notmcp.MustTool("generic_suffix", "not MCP", buildWidget)
var AlsoNotMCP = MustTool("unqualified", "not MCP", buildWidget)

func RegisterTools(mcp *server.MCPServer) {
    UpdateIncident.Register(mcp)
}
""",
        encoding="utf-8",
    )

    loaded = load_mcp_tools(_source("incident.go", GO_MUSTTOOL_V1), tmp_path)

    assert [tool.name for tool in loaded.tools] == ["update_incident"]
    assert loaded.tools[0].annotations == {
        "readOnlyHint": False,
        "idempotentHint": True,
        "destructiveHint": False,
    }
    assert loaded.tools[0].extraction["surface"] == "enumerated"
    assert loaded.tools[0].extraction["tool_set_proven"] is True
    assert loaded.omissions == []


def test_go_addtool_accepts_only_mcp_anchored_literal_shapes(tmp_path: Path) -> None:
    (tmp_path / "server.go").write_text(
        """
package main

func mount(server *mcp.Server) {
    mcp.AddTool(server, &mcp.Tool{
        Name: "greet",
        Description: "Say hi",
        Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
    }, sayHi)
    server.AddTool(mcp.NewTool("delete_cache"), deleteCache)
    widgetRegistry.AddTool(&Widget{Name: "not_mcp"}, widgetHandler)
    mcp.AddTool(server, runtimeTool(), runtimeHandler)
}
""",
        encoding="utf-8",
    )

    loaded = load_mcp_tools(_source("server.go", GO_ADDTOOL_V1), tmp_path)

    assert [tool.name for tool in loaded.tools] == ["greet", "delete_cache"]
    assert loaded.tools[0].annotations == {"readOnlyHint": True}
    assert loaded.tools[0].extraction["surface"] == "partial"
    assert [row.reason for row in loaded.omissions] == ["dynamic_tool_name"]


def test_code_scan_file_cap_is_an_explicit_omission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for index in range(2):
        (tools / f"tool{index}.ts").write_text(
            f'class T{index} {{ static toolName = "tool-{index}"; }}',
            encoding="utf-8",
        )
    monkeypatch.setattr(mcp_code, "MAX_MCP_CODE_FILES", 1)

    loaded = load_mcp_tools(_source("tools", TYPESCRIPT_STATIC_TOOL_V1), tmp_path)

    assert [tool.name for tool in loaded.tools] == ["tool-0"]
    assert {row.reason for row in loaded.omissions} == {
        "definition_only_runtime_binding",
        "source_file_cap",
    }
    assert loaded.tools[0].extraction["tool_set_proven"] is False
    assert any("source_file_cap" in gap for gap in loaded.tools[0].extraction["surface_gaps"])


def test_code_scan_aggregate_byte_cap_is_an_explicit_omission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "first.ts").write_text(
        'server.registerTool("first", {}, first);', encoding="utf-8"
    )
    (tmp_path / "second.ts").write_text(
        'server.registerTool("second", {}, second);', encoding="utf-8"
    )
    monkeypatch.setattr(mcp_code, "MAX_MCP_CODE_TOTAL_BYTES", 50)

    loaded = load_mcp_tools(_source(".", TYPESCRIPT_MCP_SDK_V1), tmp_path)

    assert [tool.name for tool in loaded.tools] == ["first"]
    assert [row.reason for row in loaded.omissions] == ["source_byte_cap"]
    assert loaded.tools[0].extraction["surface"] == "partial"


def test_code_scan_per_file_byte_cap_is_an_explicit_omission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "server.ts"
    source.write_text(
        'server.registerTool("tool", {}, handler);', encoding="utf-8"
    )
    monkeypatch.setattr(mcp_code, "MAX_MCP_CODE_FILE_BYTES", 8)

    loaded = load_mcp_tools(_source("server.ts", TYPESCRIPT_MCP_SDK_V1), tmp_path)

    assert loaded.tools == []
    assert [row.reason for row in loaded.omissions] == ["source_file_byte_cap"]


def test_typescript_nested_templates_do_not_hide_a_static_tool(tmp_path: Path) -> None:
    """MongoDB PR #1417's createDBUser class contains this nested shape."""

    (tmp_path / "createDBUser.ts").write_text(
        r'''
export class CreateDBUserTool extends AtlasToolBase {
  static toolName = "atlas-create-db-user";
  async execute(username: string, shouldGeneratePassword: boolean) {
    return {
      text: `User "${username}" created successfully${
        shouldGeneratePassword ? ` with password: \`${password}\`` : ""
      }.`
    };
  }
}
''',
        encoding="utf-8",
    )

    loaded = load_mcp_tools(
        _source("createDBUser.ts", TYPESCRIPT_STATIC_TOOL_V1), tmp_path
    )

    assert [tool.name for tool in loaded.tools] == ["atlas-create-db-user"]
    assert {row.reason for row in loaded.omissions} == {
        "definition_only_runtime_binding"
    }


def test_typescript_regex_literal_is_not_a_registration(tmp_path: Path) -> None:
    (tmp_path / "server.ts").write_text(
        r'''
const documentationPattern = /server\.registerTool\("phantom", \{\}, handler\)/;
server.registerTool("real", {}, handler);
''',
        encoding="utf-8",
    )

    loaded = load_mcp_tools(_source("server.ts", TYPESCRIPT_MCP_SDK_V1), tmp_path)

    assert [tool.name for tool in loaded.tools] == ["real"]
    assert loaded.omissions == []


def test_unbalanced_registration_and_class_are_structured_omissions(
    tmp_path: Path,
) -> None:
    (tmp_path / "server.ts").write_text(
        'server.registerTool("lost", {}, handler;\n', encoding="utf-8"
    )
    (tmp_path / "definition.ts").write_text(
        'class Lost { static toolName = "lost-static";\n', encoding="utf-8"
    )

    call = load_mcp_tools(_source("server.ts", TYPESCRIPT_MCP_SDK_V1), tmp_path)
    definition = load_mcp_tools(
        _source("definition.ts", TYPESCRIPT_STATIC_TOOL_V1), tmp_path
    )

    assert call.tools == []
    assert [row.reason for row in call.omissions] == ["structural_parse_gap"]
    assert definition.tools == []
    assert {row.reason for row in definition.omissions} == {
        "definition_only_runtime_binding",
        "structural_parse_gap",
    }


def test_go_musttool_emits_only_linked_runtime_registrations(tmp_path: Path) -> None:
    definitions = tmp_path / "tools.go"
    registrations = tmp_path / "server.go"
    definitions.write_text(
        "\n".join(
            [
                "package tools",
                *[
                    f'var Tool{index} = mcpgrafana.MustTool("tool_{index}", "Tool", handler)'
                    for index in range(115)
                ],
                'var Dynamic = mcpgrafana.MustTool(prefix + suffix, "Dynamic", handler)',
            ]
        ),
        encoding="utf-8",
    )
    def write_registrations(count: int) -> None:
        registrations.write_text(
            "\n".join(
                [
                    "package tools",
                    "func Register(mcp *server.MCPServer) {",
                    *[f"Tool{index}.Register(mcp)" for index in range(count)],
                    "Dynamic.Register(mcp)",
                    "Missing.Register(mcp)",
                    "}",
                ]
            ),
            encoding="utf-8",
        )

    write_registrations(99)
    before = load_mcp_tools(_source(".", GO_MUSTTOOL_V1), tmp_path)
    write_registrations(100)
    after = load_mcp_tools(_source(".", GO_MUSTTOOL_V1), tmp_path)

    assert len(before.tools) == 99
    assert len(after.tools) == 100
    assert after.tools[-1].name == "tool_99"
    assert {row.reason for row in after.omissions} == {
        "dynamic_tool_name",
        "unresolved_tool_registration",
    }


def test_grafana_register_method_does_not_activate_addtool(tmp_path: Path) -> None:
    (tmp_path / "tools.go").write_text(
        """
package main
func (t *Tool) Register(mcp *server.MCPServer) {
    mcp.AddTool(t.Tool, t.Handler)
}
""",
        encoding="utf-8",
    )

    loaded = load_mcp_tools(_source("tools.go", GO_ADDTOOL_V1), tmp_path)

    assert loaded.tools == []
    assert loaded.omissions == []


def test_mcp_tool_snapshot_directory_aggregates_single_tool_objects(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "pkg" / "github" / "__toolsnaps__"
    snapshots.mkdir(parents=True)
    (snapshots / "delete_repository.snap").write_text(
        json.dumps(
            {
                "name": "delete_repository",
                "description": "Delete a repository",
                "inputSchema": {"type": "object"},
                "annotations": {"destructiveHint": True},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_mcp_tools(
        _source("pkg/github/__toolsnaps__", MCP_TOOL_SNAPSHOT_V1), tmp_path
    )

    assert [tool.name for tool in loaded.tools] == ["delete_repository"]
    assert loaded.tools[0].extraction == {
        "method": "mcp_tool_snapshot",
        "confidence": "high",
        "idiom": MCP_TOOL_SNAPSHOT_V1,
        "surface": "enumerated",
        "tool_set_proven": True,
    }
    assert loaded.omissions == []


def test_checked_in_json_and_explicit_code_sources_stay_independent(tmp_path: Path) -> None:
    """Discovery may prefer JSON, but explicit rows are both real evidence."""

    (tmp_path / "tools.json").write_text(
        json.dumps({"tools": [{"name": "search"}]}), encoding="utf-8"
    )
    (tmp_path / "server.ts").write_text(
        'server.registerTool("search", {}, search);', encoding="utf-8"
    )

    exported = load_mcp_tools(
        ToolSourceConfig(id="export", type="mcp", path="tools.json"), tmp_path
    )
    source = load_mcp_tools(
        _source("server.ts", TYPESCRIPT_MCP_SDK_V1, source_id="source"), tmp_path
    )

    assert exported.source_id == "export"
    assert source.source_id == "source"
    assert exported.tools[0].extraction_confidence == "high"
    assert source.tools[0].extraction_confidence == "medium"
    assert exported.tools[0].extraction["method"] == "mcp_json"
    assert source.tools[0].extraction["method"] == "mcp_code_idiom"

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text(
        """
version: "0.1"
project: {name: explicit-mcp-sources}
agent:
  name: server
  declared_purpose: [publish a tool surface]
environment: {target: production_like}
tool_sources:
  - id: export
    type: mcp
    path: tools.json
  - id: source
    type: mcp
    path: server.ts
    idiom: typescript_mcp_sdk_v1
""".lstrip(),
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=manifest,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    same_name = [row for row in report.tool_catalog if row["name"] == "search"]
    assert len(same_name) == 2


@pytest.mark.parametrize("idiom", [*mcp_code.MCP_CODE_IDIOM_IDS])
def test_registry_ids_are_unique_and_versioned(idiom: str) -> None:
    assert idiom.endswith("_v1")
    assert len(mcp_code.MCP_CODE_IDIOM_IDS) == len(set(mcp_code.MCP_CODE_IDIOM_IDS))


def test_manifest_schema_publishes_the_closed_idiom_vocabulary() -> None:
    idiom_schema = ToolSourceConfig.model_json_schema()["properties"]["idiom"]

    assert idiom_schema["anyOf"][0]["enum"] == list(MCP_IDIOM_IDS)
    with pytest.raises(ValidationError):
        ToolSourceConfig(
            id="server",
            type="mcp",
            path="server.ts",
            idiom="user_supplied_regex_v1",
        )
    with pytest.raises(ValidationError, match="only type 'mcp'"):
        ToolSourceConfig(
            id="server",
            type="openapi",
            path="server.ts",
            idiom=TYPESCRIPT_MCP_SDK_V1,
        )
