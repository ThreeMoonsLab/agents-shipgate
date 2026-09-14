"""`.vscode/mcp.json` is read as JSON with comments, as VS Code reads it (#659).

`amelioro/ameliorate#936` in the host-config benchmark committed a
`.vscode/mcp.json` with `//` comments. VS Code runs it; the reader refused the
whole comparison as unparsable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.core.host_boundary import evaluate_host_boundary
from agents_shipgate.core.jsonc import is_vscode_mcp_path, loads_jsonc

COMMENTED = """{
  // the server VS Code starts
  "servers": {
    "docs": {
      "type": "http",
      "url": "https://example.test/mcp", /* a // inside a string stays */
      "headers": {"x": "a/*b*/c",},
    },
  },
}
"""


def test_comments_and_trailing_commas_parse_and_strings_are_untouched() -> None:
    data = loads_jsonc(COMMENTED)

    assert data == {
        "servers": {
            "docs": {
                "type": "http",
                "url": "https://example.test/mcp",
                "headers": {"x": "a/*b*/c"},
            }
        }
    }


@pytest.mark.parametrize(
    "text",
    ["{'servers': {}}", '{"servers": {}} /* unterminated', '{"servers": {},,}', '{"a": 1 "b": 2}'],
    ids=["single-quotes", "unterminated-comment", "double-comma", "missing-comma"],
)
def test_anything_else_json_rejects_is_still_rejected(text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        loads_jsonc(text)


def test_only_the_vscode_file_is_read_with_comments() -> None:
    assert is_vscode_mcp_path(".vscode/mcp.json")
    assert is_vscode_mcp_path("./.VSCode/MCP.json")
    assert not is_vscode_mcp_path(".mcp.json")
    assert not is_vscode_mcp_path(".cursor/mcp.json")
    assert not is_vscode_mcp_path(".claude/settings.json")


def test_audit_reads_a_commented_vscode_file(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(COMMENTED, encoding="utf-8")
    (tmp_path / ".mcp.json").write_text('{\n  // Claude Code reads strict JSON\n  "mcpServers": {}\n}\n', encoding="utf-8")

    result = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(tmp_path), "--json"])
    inventory = json.loads(result.stdout)

    issues = {(item["source"], item["kind"]) for item in inventory["issues"]}
    assert (".vscode/mcp.json", "parse_failed") not in issues
    assert (".mcp.json", "parse_failed") in issues
    assert any(
        grant.get("source") == ".vscode/mcp.json" and "docs" in json.dumps(grant)
        for grant in inventory["grants"]
    )


def _new_file_diff(path: str, text: str) -> str:
    lines = text.splitlines()
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..1111111\n"
        f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        + "\n".join(f"+{line}" for line in lines)
        + "\n"
    )


def test_the_boundary_check_evaluates_a_commented_vscode_file(tmp_path: Path) -> None:
    violations, _ = evaluate_host_boundary(
        workspace=tmp_path, diff_text=_new_file_diff(".vscode/mcp.json", COMMENTED)
    )
    ids = {item.id for item in violations}

    assert "HOST-CONFIG-PARSE-FAILED" not in ids
    assert "HOST-MCP-SERVER-ADDED" in ids
