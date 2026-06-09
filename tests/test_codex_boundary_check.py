from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.codex_boundary import evaluate_codex_boundary_result

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus" / "codex_boundary"
GOLDEN = ROOT / "tests" / "golden" / "agent_result"
SCHEMA = ROOT / "docs" / "agent-result-schema.v1.json"

runner = CliRunner()


CASES = {
    "network_wildcard": ("require_review", ["CODEX-NETWORK-WILDCARD"]),
    "mcp_auto_approve_write": ("block", ["CODEX-MCP-AUTO-APPROVE-WRITE"]),
    "agents_requirement_removed": (
        "require_review",
        ["CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED"],
    ),
    "github_action_removed": ("block", ["CODEX-CI-GATE-REMOVED"]),
    "docs_only": ("allow", []),
    "python_refactor": ("allow", []),
    "unknown_permission_key": ("require_review", ["CODEX-UNKNOWN-PERMISSION-KEY"]),
    "malformed_toml": ("require_review", ["CODEX-CONFIG-PARSE-FAILED"]),
}


def test_codex_check_agent_json_golden_outputs(tmp_path: Path) -> None:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    for case, (decision, rule_ids) in CASES.items():
        result = runner.invoke(
            app,
            [
                "check",
                "--workspace",
                str(tmp_path),
                "--diff",
                str(CORPUS / f"{case}.diff"),
                "--format",
                "agent-json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.output)
        validator.validate(payload)
        assert payload == json.loads((GOLDEN / f"{case}.json").read_text(encoding="utf-8"))
        assert payload["decision"] == decision
        assert [item["id"] for item in payload["violated_rules"]] == rule_ids


def test_codex_check_audit_id_is_stable(tmp_path: Path) -> None:
    args = [
        "check",
        "--workspace",
        str(tmp_path),
        "--diff",
        str(CORPUS / "network_wildcard.diff"),
        "--format",
        "agent-json",
    ]
    first = json.loads(runner.invoke(app, args).output)
    second = json.loads(runner.invoke(app, args).output)

    assert first["audit_id"] == second["audit_id"]


def test_codex_check_reads_diff_from_stdin(tmp_path: Path) -> None:
    diff_text = (CORPUS / "docs_only.diff").read_text(encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            "-",
            "--format",
            "agent-json",
        ],
        input=diff_text,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.output)["decision"] == "allow"


def test_codex_check_malformed_toml_returns_schema_valid_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(tmp_path),
            "--diff",
            str(CORPUS / "malformed_toml.diff"),
            "--format",
            "agent-json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(payload)
    assert payload["decision"] == "require_review"
    assert payload["violated_rules"][0]["id"] == "CODEX-CONFIG-PARSE-FAILED"


def test_agent_result_never_contradicts_release_decision(tmp_path: Path) -> None:
    result = evaluate_codex_boundary_result(
        workspace=tmp_path,
        diff_text=(CORPUS / "docs_only.diff").read_text(encoding="utf-8"),
        release_decision={"decision": "blocked", "reason": "release blocked"},
    )

    assert result.decision == "block"
    assert result.first_next_action.kind == "stop"
