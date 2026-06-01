from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.feedback import build_feedback_payload
from agents_shipgate.cli.main import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent


def _verifier_payload() -> dict:
    return {
        "merge_verdict": "blocked",
        "can_merge_without_human": False,
        "decision": "blocked",
        "mode": "advisory",
        "trigger": {
            "should_run": True,
            "action": "run_shipgate",
            "matched_rule_ids": ["TRIGGER-MCP"],
        },
        "first_next_action": {
            "actor": "human",
            "kind": "review",
            "command": None,
            "why": "Approval evidence is missing.",
        },
        "fix_task": {
            "actor": "human",
            "safe_to_attempt": False,
            "instructions": ["Add approval policy evidence."],
            "forbidden_shortcuts": ["do_not_suppress_to_pass"],
            "verification_command": "agents-shipgate verify --base origin/main --head HEAD --json",
        },
        "release_decision": {
            "decision": "blocked",
            "blockers": [{"id": "F1", "check_id": "SHIP-POLICY-APPROVAL-MISSING"}],
            "review_items": [{"id": "F2", "check_id": "SHIP-VERIFY-TRUST-ROOT-TOUCHED"}],
        },
        "capability_review": {
            "trust_root_touched": True,
            "policy_weakened": False,
            "added": 1,
            "modified": 0,
            "removed": 0,
            "top_changes": [
                {
                    "id": "action:stripe.create_refund",
                    "title": "stripe.create_refund added",
                    "impact": "blocks_release",
                    "rationale": "Contains potentially sensitive local detail.",
                    "source_path": "tools.json",
                    "source_start_line": 12,
                    "related_finding_ids": ["F1"],
                }
            ],
        },
        "artifacts": {
            "verifier_json": "agents-shipgate-reports/verifier.json",
            "report_json": "agents-shipgate-reports/report.json",
            "pr_comment": "agents-shipgate-reports/pr-comment.md",
        },
    }


def test_build_feedback_payload_redacts_detail() -> None:
    payload = build_feedback_payload(
        _verifier_payload(),
        source=Path("agents-shipgate-reports/verifier.json"),
        redacted=True,
    )

    assert payload["feedback_schema_version"] == "0.1"
    assert payload["merge_verdict"] == "blocked"
    assert payload["finding_ids"] == ["F1", "F2"]
    change = payload["capability_review"]["top_changes"][0]
    assert change["title"] == "stripe.create_refund added"
    assert "rationale" not in change
    assert "source_path" not in change
    assert "source_start_line" not in change
    assert "was_capability_correctly_classified" in payload["reviewer_feedback_requested"]


def test_feedback_payload_validates_against_schema() -> None:
    schema = json.loads(
        (REPO_ROOT / "docs/feedback-schema.v0.1.json").read_text(encoding="utf-8")
    )
    payload = build_feedback_payload(
        _verifier_payload(),
        source=Path("agents-shipgate-reports/verifier.json"),
        redacted=True,
    )

    jsonschema.validate(payload, schema)


def test_build_feedback_payload_redacts_absolute_paths() -> None:
    verifier = _verifier_payload()
    verifier["artifacts"] = {
        "verifier_json": "/Users/alice/Secret Client/agents-shipgate-reports/verifier.json",
        "report_json": "/Users/alice/Secret Client/agents-shipgate-reports/report.json",
        "pr_comment": "/Users/alice/Secret Client/agents-shipgate-reports/pr-comment.md",
    }

    payload = build_feedback_payload(
        verifier,
        source=Path("/Users/alice/Secret Client/agents-shipgate-reports/verifier.json"),
        redacted=True,
    )

    rendered = json.dumps(payload)
    assert "/Users/alice" not in rendered
    assert "Secret Client" not in rendered
    assert payload["source_verifier"] == "verifier.json"
    assert payload["artifacts"] == {
        "verifier_json": "verifier.json",
        "report_json": "report.json",
        "pr_comment": "pr-comment.md",
    }


def test_build_feedback_payload_no_redact_keeps_source_detail() -> None:
    payload = build_feedback_payload(
        _verifier_payload(),
        source=Path("/tmp/shipgate/verifier.json"),
        redacted=False,
    )

    change = payload["capability_review"]["top_changes"][0]
    assert payload["source_verifier"] == "/tmp/shipgate/verifier.json"
    assert change["rationale"] == "Contains potentially sensitive local detail."
    assert change["source_path"] == "tools.json"
    assert change["source_start_line"] == 12


def test_build_feedback_payload_finding_ids_include_changes_beyond_top_ten() -> None:
    verifier = _verifier_payload()
    verifier["capability_review"]["top_changes"] = [
        {
            "id": f"change-{idx}",
            "title": f"Change {idx}",
            "related_finding_ids": [f"F{idx}"],
        }
        for idx in range(1, 12)
    ]

    payload = build_feedback_payload(
        verifier,
        source=Path("agents-shipgate-reports/verifier.json"),
        redacted=True,
    )

    assert len(payload["capability_review"]["top_changes"]) == 10
    assert "F11" in payload["finding_ids"]


def test_feedback_export_cli_writes_json(tmp_path: Path) -> None:
    source = tmp_path / "verifier.json"
    out = tmp_path / "feedback.json"
    source.write_text(json.dumps(_verifier_payload()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "feedback",
            "export",
            "--from",
            str(source),
            "--out",
            str(out),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    disk_payload = json.loads(out.read_text(encoding="utf-8"))
    stdout_payload = json.loads(result.output)
    assert disk_payload == stdout_payload
    assert disk_payload["capability_review"]["policy_weakened"] is False


def test_feedback_export_cli_out_without_json_prints_written_message(tmp_path: Path) -> None:
    source = tmp_path / "verifier.json"
    out = tmp_path / "feedback.json"
    source.write_text(json.dumps(_verifier_payload()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "feedback",
            "export",
            "--from",
            str(source),
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"Wrote feedback artifact to {out}"
    assert json.loads(out.read_text(encoding="utf-8"))["merge_verdict"] == "blocked"


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("", "not valid JSON"),
        ("[]", "must contain an object"),
    ],
)
def test_feedback_export_cli_invalid_verifier_returns_3(
    tmp_path: Path,
    contents: str,
    expected: str,
) -> None:
    source = tmp_path / "verifier.json"
    source.write_text(contents, encoding="utf-8")

    result = runner.invoke(app, ["feedback", "export", "--from", str(source)])

    assert result.exit_code == 3
    assert expected in result.output


def test_feedback_export_cli_missing_verifier_returns_3(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["feedback", "export", "--from", str(tmp_path / "missing.json")],
    )

    assert result.exit_code == 3
    assert "not found" in result.output
