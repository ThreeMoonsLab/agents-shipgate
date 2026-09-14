"""A description the MCP source reader cannot resolve is not a missing one (#658).

The ten-server false-finding table found `SHIP-DOC-MISSING-DESCRIPTION` on
tools whose descriptions exist but are written as a variable, an f-string or a
template literal with a substitution: grafana's `MustTool` passes a literal the
reader did not look at, cloudflare builds one from `${names}`, and awslabs from
an f-string. Those are recorded as `extraction.description: unresolved`, and
the check reports only descriptions that are absent or too short.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.inputs.mcp_server_source import load_mcp_server_source
from agents_shipgate.schemas.manifest import ToolSourceConfig
from tests.mcp_idiom_corpus import DESCRIPTION_UNRESOLVED_CASES

GO_MOD = "module github.com/example/srv\nrequire github.com/mark3labs/mcp-go v0.1.0\n"
PACKAGE_JSON = '{"name":"srv","dependencies":{"@modelcontextprotocol/sdk":"^1.0.0"}}'
PYPROJECT = '[project]\nname = "srv"\ndependencies = ["mcp[cli]>=1.26.0,<2"]\n'


def _load(tmp_path: Path, language: str, text: str):
    if language == "go":
        (tmp_path / "go.mod").write_text(GO_MOD, encoding="utf-8")
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "tools.go").write_text(
            'package pkg\n\nimport "github.com/mark3labs/mcp-go/mcp"\n\n' + text + "\n", encoding="utf-8"
        )
    elif language == "typescript":
        (tmp_path / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "tools.ts").write_text(
            'import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";\n'
            "export function reg(server: McpServer) {\n" + text + "\n}\n",
            encoding="utf-8",
        )
    else:
        (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "server.py").write_text(text, encoding="utf-8")
    loaded = load_mcp_server_source(
        ToolSourceConfig(id="srv", type="mcp_server_source", path="."), tmp_path
    )
    return {tool.name: tool for tool in loaded.tools}


@pytest.mark.parametrize(
    ("case", "language", "text", "description", "unresolved"),
    DESCRIPTION_UNRESOLVED_CASES,
    ids=[case[0] for case in DESCRIPTION_UNRESOLVED_CASES],
)
def test_a_description_is_read_unresolved_or_absent(
    tmp_path: Path, case: str, language: str, text: str, description: str | None, unresolved: bool
) -> None:
    [tool] = _load(tmp_path, language, text).values()

    assert tool.description == description
    assert (tool.extraction.get("description") == "unresolved") is unresolved


_SERVER = """from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reports")
KIND = "sales"


@mcp.tool(description=f"Build the {KIND} report for a period and return it as text.")
def build_report(period: str) -> str:
    return ""


@mcp.tool()
def export_report(period: str) -> str:
    return ""
"""

_MANIFEST = """version: "0.1"
project:
  name: reports-mcp
agent:
  name: reports-agent
  declared_purpose:
    - build and export reports through an MCP server
environment:
  target: local
tool_sources:
  - id: server
    type: mcp_server_source
    path: src
    binding:
      complete: true
      reason: This repository is the server; every registered tool is callable by any connected client.
ci:
  mode: advisory
"""


def test_only_the_absent_description_is_reported_missing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.py").write_text(_SERVER, encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")

    report, _exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
    )

    missing = {
        finding.tool_name
        for finding in report.findings
        if finding.check_id == "SHIP-DOC-MISSING-DESCRIPTION"
    }
    assert missing == {"export_report"}
