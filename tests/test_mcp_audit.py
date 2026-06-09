from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus" / "mcp_permission_expansion"
runner = CliRunner()


def test_mcp_audit_acceptance_corpus(tmp_path: Path) -> None:
    cases = {
        "env_secret": ("require_review", ["MCP-ENV-SECRET-PASSTHROUGH"]),
        "auto_approve_write": ("block", ["MCP-AUTO-APPROVE-SIDE-EFFECT"]),
        "restrict_write_to_read": ("allow", []),
        "unknown_schema": ("require_review", ["MCP-UNKNOWN-TOOL-SCHEMA"]),
        "read_only_docs": ("warn", ["MCP-READONLY-SERVER-ADDED"]),
    }
    for case, (decision, rule_ids) in cases.items():
        result = runner.invoke(
            app,
            [
                "mcp",
                "audit",
                "--workspace",
                str(tmp_path),
                "--diff",
                str(CORPUS / f"{case}.diff"),
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["schema_version"] == "mcp_audit_v1"
        assert payload["decision"] == decision
        assert [item["id"] for item in payload["violated_rules"]] == rule_ids


def test_mcp_audit_agent_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "mcp",
            "audit",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(CORPUS / "auto_approve_write.diff"),
            "--format",
            "agent-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "agent_result_v1"
    assert payload["decision"] == "block"
    assert payload["first_next_action"]["kind"] == "stop"
