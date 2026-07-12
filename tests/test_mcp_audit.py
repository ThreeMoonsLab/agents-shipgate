from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.schemas.agent_result import AgentResultV2

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus" / "mcp_permission_expansion"
AGENT_RESULT_SCHEMA = ROOT / "docs" / "agent-result-schema.v2.json"
runner = CliRunner()


def test_mcp_audit_acceptance_corpus(tmp_path: Path) -> None:
    cases = {
        "env_secret": (
            "require_review",
            ["MCP-ENV-SECRET-PASSTHROUGH", "MCP-UNKNOWN-TOOL-SCHEMA"],
        ),
        "auto_approve_write": ("require_review", ["MCP-UNKNOWN-TOOL-SCHEMA"]),
        "restrict_write_to_read": ("allow", []),
        "unknown_schema": ("require_review", ["MCP-UNKNOWN-TOOL-SCHEMA"]),
        "read_only_docs": ("require_review", ["MCP-UNKNOWN-TOOL-SCHEMA"]),
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
    Draft202012Validator(json.loads(AGENT_RESULT_SCHEMA.read_text(encoding="utf-8"))).validate(
        payload
    )
    AgentResultV2.model_validate(payload)
    assert payload["schema_version"] == "agent_result_v2"
    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["must_stop"] is True
    assert payload["control"]["next_action"]["kind"] == "review"
    assert payload["control"]["human_review"]["required"] is True
    for retired in (
        "completion_allowed",
        "must_stop",
        "verify_required",
        "first_next_action",
        "human_review",
        "exit_code_hint",
    ):
        assert retired not in payload


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
    assert payload["decision"] == "require_review"
    assert [item["id"] for item in payload["violated_rules"]] == ["MCP-UNKNOWN-TOOL-SCHEMA"]


def test_mcp_audit_policy_override_changes_decision(tmp_path: Path) -> None:
    policy = tmp_path / "mcp-policy.yaml"
    policy.write_text(
        """
version: "test-policy"
rules:
  - id: MCP-UNKNOWN-TOOL-SCHEMA
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
  - id: MCP-UNKNOWN-TOOL-SCHEMA
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
    assert payload["decision"] == "require_review"
    assert payload["policy_version"] != "cwd-policy"
    assert [item for item in payload["diagnostics"] if item["code"] == "mcp_policy_missing"] == []


def test_mcp_audit_relative_policy_resolves_under_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "mcp-policy.yaml").write_text(
        """
version: "workspace-policy"
rules:
  - id: MCP-UNKNOWN-TOOL-SCHEMA
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
