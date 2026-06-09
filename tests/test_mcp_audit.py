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


def test_mcp_audit_reads_mcp_json_diff(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "mcp",
            "audit",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(CORPUS / "mcp_json_auto_approve_write.diff"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "block"
    assert [item["id"] for item in payload["violated_rules"]] == [
        "MCP-AUTO-APPROVE-SIDE-EFFECT"
    ]


def test_mcp_audit_policy_override_changes_decision(tmp_path: Path) -> None:
    policy = tmp_path / "mcp-policy.yaml"
    policy.write_text(
        """
version: "test-policy"
rules:
  - id: MCP-READONLY-SERVER-ADDED
    action: block
    risk_level: critical
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "mcp",
            "audit",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(CORPUS / "read_only_docs.diff"),
            "--policy",
            str(policy),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["policy_version"] == "test-policy"
    assert payload["decision"] == "block"
    assert payload["risk_level"] == "critical"


def test_mcp_audit_default_policy_resolution_ignores_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cwd = tmp_path / "cwd"
    (cwd / "policies").mkdir(parents=True)
    (cwd / "policies" / "mcp-permissions.shipgate.yaml").write_text(
        """
version: "cwd-policy"
rules:
  - id: MCP-READONLY-SERVER-ADDED
    action: block
    risk_level: critical
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(cwd)

    result = runner.invoke(
        app,
        [
            "mcp",
            "audit",
            "--workspace",
            str(workspace),
            "--diff",
            str(CORPUS / "read_only_docs.diff"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"] == "warn"
    assert payload["policy_version"] != "cwd-policy"
    assert [
        item for item in payload["diagnostics"] if item["code"] == "mcp_policy_missing"
    ] == []


def test_mcp_audit_relative_policy_resolves_under_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "mcp-policy.yaml").write_text(
        """
version: "workspace-policy"
rules:
  - id: MCP-READONLY-SERVER-ADDED
    action: block
    risk_level: critical
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "mcp",
            "audit",
            "--workspace",
            str(workspace),
            "--diff",
            str(CORPUS / "read_only_docs.diff"),
            "--policy",
            "mcp-policy.yaml",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["policy_version"] == "workspace-policy"
    assert payload["decision"] == "block"
