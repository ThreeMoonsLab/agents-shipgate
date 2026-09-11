"""#680: a TypeScript tool's description, in both SDK shapes.

`ts_sdk_register_tool` resolved the tool's *name* and nothing else, so
every tool declared on the reference MCP SDK was reported undocumented and
earned a `SHIP-DOC-MISSING-DESCRIPTION` finding. `_TS_DESCRIPTION_RE` looks
like it covered this and does not — it serves the class-property idiom,
`this.description = "…"`, not a registration call.

The SDK writes the description in the second argument, two ways:

    server.registerTool("get_job", { description: "…", inputSchema: {} }, fn)
    server.tool("get_job", "…", fn)

The refusals below matter as much as the reads, and one of them is the
reason this is not a two-line change: a registration's options object
carries `inputSchema`, and a JSON Schema describes every *parameter*. A
reader that takes the first `description:` it finds publishes an argument's
documentation as the tool's — an invented answer in place of an absent one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.inputs.mcp_server_source import load_mcp_server_source
from agents_shipgate.schemas.manifest import ToolSourceConfig
from tests.mcp_idiom_corpus import TS_DESCRIPTION_CASES as CASES

PACKAGE_JSON = '{"name":"srv","dependencies":{"@modelcontextprotocol/sdk":"^1.0.0"}}'
IMPORT = 'import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";\n'


def _tools(tmp_path: Path, body: str) -> dict[str, str | None]:
    (tmp_path / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / "tools.ts").write_text(
        IMPORT + "export function reg(server: McpServer) {\n" + body + "}\n",
        encoding="utf-8",
    )
    loaded = load_mcp_server_source(
        ToolSourceConfig(id="srv", type="mcp_server_source", path="."), tmp_path
    )
    return {tool.name: getattr(tool, "description", None) for tool in loaded.tools}


#: (case, the registration line, what must be read).



@pytest.mark.parametrize(
    ("case", "line", "expected"), CASES, ids=[case[0] for case in CASES]
)
def test_description_is_read_or_refused(
    tmp_path: Path, case: str, line: str, expected: str | None
) -> None:
    assert _tools(tmp_path, f"  {line}\n") == {case: expected}


def test_a_parameter_description_is_never_published_as_the_tools(
    tmp_path: Path,
) -> None:
    """Named separately because it is the one wrong-answer risk here.

    Every other refusal reports nothing where nothing is known. This one
    would report *something*, and a reader has no way to tell that the
    sentence they are reading describes an argument.
    """

    body = """
  server.registerTool("search", {
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "The text to search for." },
        limit: { type: "number", description: "How many results." }
      }
    }
  }, fn);
"""

    assert _tools(tmp_path, body) == {"search": None}


def test_every_tool_in_an_ordinary_server_is_documented(tmp_path: Path) -> None:
    """The headline, stated as a property: `SHIP-DOC-MISSING-DESCRIPTION`
    fires per undocumented tool, so this is the false-finding count."""

    body = "".join(
        f'  server.registerTool("tool_{index}", {{ description: "Tool {index} does a thing.", '
        f'inputSchema: {{ properties: {{ arg: {{ description: "An argument." }} }} }} }}, fn);\n'
        for index in range(12)
    )

    described = _tools(tmp_path, body)

    assert len(described) == 12
    assert [name for name, text in described.items() if not text] == []
    assert set(described.values()) == {
        f"Tool {index} does a thing." for index in range(12)
    }


def test_both_shapes_in_one_file(tmp_path: Path) -> None:
    body = (
        '  server.registerTool("a", { description: "Alpha.", inputSchema: {} }, fn);\n'
        '  server.tool("b", "Bravo.", fn);\n'
    )

    assert _tools(tmp_path, body) == {"a": "Alpha.", "b": "Bravo."}


@pytest.mark.parametrize("reestablish", [False, True])
def test_undecodable_commonjs_key_cannot_leave_a_stale_description(
    tmp_path: Path, reestablish: bool,
) -> None:
    # Legacy octal escapes are legal in non-strict CommonJS, which the same
    # reader supports. Declining to decode them must not mean ignoring them.
    (tmp_path / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
    later = ', description: "actual"' if reestablish else ""
    (tmp_path / "server.cjs").write_text(
        'const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");\n'
        'const server = new McpServer({ name: "srv", version: "1" });\n'
        'server.registerTool("probe", { description: "old", '
        + r'"\144escription": ""'
        + later + ' }, () => ({ content: [] }));\n',
        encoding="utf-8",
    )
    loaded = load_mcp_server_source(
        ToolSourceConfig(id="srv", type="mcp_server_source", path="."), tmp_path
    )
    assert {tool.name: tool.description for tool in loaded.tools} == {
        "probe": "actual" if reestablish else None,
    }
