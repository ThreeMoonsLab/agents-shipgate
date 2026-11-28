"""The inventory checks fire through the scan, and stay quiet beside it (#1048)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan

_CODEX_MANIFEST = """version: "0.1"
project:
  name: inventory-checks
agent:
  name: codex-agent
  declared_purpose:
    - read repository MCP declarations
environment:
  target: {target}
tool_sources:
  - id: codex
    type: codex_config
    path: .
    binding:
      complete: true
      reason: Reviewed test fixture binding for the declared MCP servers.
    authority:
      mode: none
output:
  packet:
    enabled: false
"""

_MCP_MANIFEST = """version: "0.1"
project:
  name: inventory-checks
agent:
  name: mcp-agent
  declared_purpose:
    - review an MCP tool surface
environment:
  target: local
tool_sources:
  - id: mcp-tools
    type: mcp
    path: tools.json
    binding:
      complete: true
      reason: Reviewed test fixture binding for the complete published MCP surface.
output:
  packet:
    enabled: false
"""

#: A server that names no tools: one medium-confidence ``docs.*`` wildcard
#: stands in for whatever the running server publishes.
_UNENUMERATED_SERVER = '[mcp_servers.docs]\ncommand = "docs-mcp"\n'
_ENUMERATED_SERVER = (
    '[mcp_servers.docs]\ncommand = "docs-mcp"\n'
    "[mcp_servers.docs.tools.search_docs]\n"
    'inputSchema = { type = "object", properties = {}, additionalProperties = false }\n'
)


def _check_ids(report) -> set[str]:
    return {finding.check_id for finding in report.findings}


def _scan_codex(tmp_path: Path, config_toml: str, *, target: str = "local"):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(config_toml, encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text(
        _CODEX_MANIFEST.format(target=target), encoding="utf-8"
    )
    report, _exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "agents-shipgate-reports",
    )
    return report


def _scan_mcp_tools(tmp_path: Path, count: int):
    tools = [
        {
            "name": f"lookup_record_{index}",
            "description": f"Look up record set {index}.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        }
        for index in range(count)
    ]
    (tmp_path / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text(_MCP_MANIFEST, encoding="utf-8")
    report, _exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "agents-shipgate-reports",
    )
    return report


def test_a_wildcard_tool_surface_is_reported(tmp_path: Path) -> None:
    report = _scan_codex(tmp_path, _UNENUMERATED_SERVER)

    [finding] = [
        item for item in report.findings if item.check_id == "SHIP-INVENTORY-WILDCARD-TOOLS"
    ]
    assert finding.severity == "high"
    assert finding.tool_name == "docs.*"


def test_an_enumerated_tool_surface_has_no_wildcard_finding(tmp_path: Path) -> None:
    report = _scan_codex(tmp_path, _ENUMERATED_SERVER)

    assert "SHIP-INVENTORY-WILDCARD-TOOLS" not in _check_ids(report)


def test_a_production_target_with_a_low_confidence_tool_is_reported(tmp_path: Path) -> None:
    report = _scan_codex(tmp_path, _UNENUMERATED_SERVER, target="production")

    [finding] = [
        item
        for item in report.findings
        if item.check_id == "SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE"
    ]
    assert finding.severity == "high"
    assert finding.evidence == {"tools": ["docs.*"]}


@pytest.mark.parametrize(
    ("config_toml", "target"),
    [
        pytest.param(_UNENUMERATED_SERVER, "local", id="low-confidence-but-local"),
        pytest.param(_ENUMERATED_SERVER, "production", id="production-but-high-confidence"),
    ],
)
def test_low_confidence_extraction_is_a_production_concern_only(
    tmp_path: Path, config_toml: str, target: str
) -> None:
    report = _scan_codex(tmp_path, config_toml, target=target)

    assert "SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE" not in _check_ids(report)


def test_more_than_fifty_tools_is_a_surface_to_review(tmp_path: Path) -> None:
    report = _scan_mcp_tools(tmp_path, 51)

    [finding] = [
        item for item in report.findings if item.check_id == "SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE"
    ]
    assert finding.severity == "medium"
    assert finding.evidence == {"tool_count": 51, "threshold": 50}


def test_fifty_tools_is_within_the_threshold(tmp_path: Path) -> None:
    report = _scan_mcp_tools(tmp_path, 50)

    assert "SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE" not in _check_ids(report)
