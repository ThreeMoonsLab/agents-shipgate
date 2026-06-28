from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.schemas.agent_handoff import (
    AgentHandoffArtifact,
    AgentHandoffController,
    AgentHandoffGate,
    AgentHandoffSubject,
)

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _verifier_payload() -> dict:
    return {
        "workspace": "/tmp/repo",
        "config": "shipgate.yaml",
        "head_status": "succeeded",
        "release_decision": {
            "decision": "blocked",
            "blockers": [
                {
                    "id": "F1",
                    "fingerprint": "fp1",
                    "check_id": "SHIP-TEST",
                    "severity": "critical",
                    "title": "Blocked finding",
                    "baseline_status": None,
                    "blocks_release": True,
                    "capability_refs": ["cap:refund"],
                }
            ],
            "review_items": [],
            "fail_policy": {"would_fail_ci": True, "exit_code": 20},
        },
        "decision": "blocked",
        "merge_verdict": "blocked",
        "applicability": "verified",
        "can_merge_without_human": False,
        "first_next_action": {
            "actor": "human",
            "kind": "review",
            "command": None,
            "why": "Human review required.",
        },
        "human_review": {"required": True, "why": "Human review required."},
        "fix_task": {
            "actor": "human",
            "safe_to_attempt": False,
            "instructions": ["Review the blocker."],
            "allowed_repairs": [
                {
                    "id": "review",
                    "actor": "human",
                    "kind": "review",
                    "target": "shipgate.yaml",
                    "reason": "Human approval is required.",
                }
            ],
            "forbidden_repairs": [
                {
                    "id": "suppress",
                    "actor": "coding_agent",
                    "kind": "manifest_suppression",
                    "target": "checks.ignore",
                    "reason": "Do not suppress findings to pass.",
                }
            ],
            "forbidden_shortcuts": ["Do not suppress the finding."],
            "verification_command": "agents-shipgate verify --json",
        },
        "agent_controller": {
            "completion_allowed": False,
            "must_stop": True,
            "stop_reason": "blocked_findings",
            "allowed_next_commands": [],
            "forbidden_file_edits": ["**/AGENTS.md"],
            "forbidden_actions": ["Do not suppress the finding."],
        },
        "capability_review": {
            "added": 1,
            "modified": 0,
            "removed": 0,
            "trust_root_touched": False,
            "policy_weakened": False,
            "top_changes": [],
            "notes": [],
        },
        "artifacts": {
            "verifier_json": "agents-shipgate-reports/verifier.json",
            "report_json": "agents-shipgate-reports/report.json",
            "agent_handoff_json": "agents-shipgate-reports/agent-handoff.json",
        },
    }


def test_agent_handoff_projects_verifier_report_and_verify_run() -> None:
    handoff = build_agent_handoff(
        verifier=_verifier_payload(),
        verify_run={
            "run_id": "sha256:" + "a" * 64,
            "inputs": {
                "config_sha256": "config-hash",
                "baseline_sha256": None,
                "policy_packs": [{"path": "policies/org.yaml", "sha256": "pack-hash"}],
            },
            "artifacts": {
                "verifier_json": {
                    "path": "agents-shipgate-reports/verifier.json",
                    "sha256": "verifier-hash",
                }
            },
        },
    )

    assert handoff.schema_version == "shipgate.agent_handoff/v1"
    assert handoff.gate.decision == "blocked"
    assert handoff.gate.merge_verdict == "blocked"
    assert handoff.gate.ci_would_fail is True
    assert handoff.controller.must_stop is True
    assert handoff.blocked_by[0].id == "F1"
    assert {step.safety for step in handoff.remediation_plan} == {
        "allowed",
        "forbidden",
    }
    assert handoff.reproducibility.run_id == "sha256:" + "a" * 64
    assert handoff.reproducibility.artifact_sha256 == {
        "verifier_json": "verifier-hash"
    }


def test_agent_handoff_schema_validates_sample_projection() -> None:
    schema = json.loads((ROOT / "docs" / "agent-handoff-schema.v1.json").read_text())
    payload = build_agent_handoff(verifier=_verifier_payload()).model_dump(mode="json")

    Draft202012Validator(schema).validate(payload)


def test_agent_handoff_has_exact_top_level_sections() -> None:
    payload = build_agent_handoff(verifier=_verifier_payload()).model_dump(mode="json")

    assert list(payload) == [
        "schema_version",
        "contract_version",
        "tool",
        "operation",
        "subject",
        "gate",
        "controller",
        "next_action",
        "human_review",
        "fix_task",
        "blocked_by",
        "remediation_plan",
        "capability_review",
        "reproducibility",
        "artifacts",
    ]


def test_agent_handoff_cli_rerenders_existing_artifacts(tmp_path: Path) -> None:
    report_dir = tmp_path / "agents-shipgate-reports"
    report_dir.mkdir()
    verifier_path = report_dir / "verifier.json"
    report_path = report_dir / "report.json"
    verify_run_path = report_dir / "verify-run.json"
    out_path = report_dir / "agent-handoff.json"
    verifier_path.write_text(json.dumps(_verifier_payload()), encoding="utf-8")
    report_path.write_text(
        json.dumps({"release_decision": {"decision": "blocked"}}),
        encoding="utf-8",
    )
    verify_run_path.write_text(
        json.dumps({"run_id": "sha256:" + "b" * 64, "inputs": {}, "artifacts": {}}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "handoff",
            "--from",
            str(verifier_path),
            "--report",
            str(report_path),
            "--verify-run",
            str(verify_run_path),
            "--out",
            str(out_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    emitted = json.loads(result.output)
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert emitted == written
    assert emitted["schema_version"] == "shipgate.agent_handoff/v1"
    assert emitted["gate"]["decision"] == "blocked"


def test_agent_handoff_cli_missing_input_exits_three(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "agent",
            "handoff",
            "--from",
            str(tmp_path / "missing-verifier.json"),
            "--json",
        ],
    )

    assert result.exit_code == 3


def test_agent_handoff_rejects_mismatched_decision_and_merge_verdict() -> None:
    with pytest.raises(ValidationError):
        AgentHandoffArtifact(
            contract_version="8",
            operation="verify_pr",
            subject=AgentHandoffSubject(workspace="/tmp/repo", config="shipgate.yaml"),
            gate=AgentHandoffGate(
                decision="blocked",
                merge_verdict="mergeable",
                can_merge_without_human=False,
            ),
            controller=AgentHandoffController(completion_allowed=False),
        )


def test_agent_handoff_rejects_controller_completion_mismatch() -> None:
    with pytest.raises(ValidationError):
        AgentHandoffArtifact(
            contract_version="8",
            operation="verify_pr",
            subject=AgentHandoffSubject(workspace="/tmp/repo", config="shipgate.yaml"),
            gate=AgentHandoffGate(
                decision="passed",
                merge_verdict="mergeable",
                can_merge_without_human=True,
            ),
            controller=AgentHandoffController(completion_allowed=False),
        )


def test_agent_handoff_rejects_invalid_verifier_substrate() -> None:
    payload = _verifier_payload()
    payload["merge_verdict"] = "mergeable"

    with pytest.raises(ValidationError):
        build_agent_handoff(verifier=payload)


def test_preview_handoff_carries_standing_forbidden_lists() -> None:
    # verify --preview emits no agent_controller, so the handoff falls back to
    # deriving the controller. The forbidden lists are a STANDING negative
    # affordance present on every verdict — they must not be empty in preview,
    # and must equal the canonical deny-lists the verify controller uses.
    from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
    from agents_shipgate.core.agent_controls import FORBIDDEN_SHORTCUTS

    verifier = {
        "workspace": "/tmp/repo",
        "config": "shipgate.yaml",
        "mode": "preview",
        "head_status": "skipped",
        "merge_verdict": "mergeable",
        "applicability": "not_applicable",
        "can_merge_without_human": True,
        "agent_controller": None,
        "first_next_action": {
            "actor": "coding_agent",
            "kind": "continue",
            "command": None,
            "why": "Preview: nothing to gate yet.",
        },
    }

    handoff = build_agent_handoff(verifier=verifier).model_dump(mode="json")

    assert handoff["operation"] == "verify_preview"
    controller = handoff["controller"]
    assert controller["forbidden_actions"] == list(FORBIDDEN_SHORTCUTS)
    assert controller["forbidden_file_edits"] == list(PROTECTED_FILE_EDITS)
    # Non-empty: a preview handoff never reads as "anything goes".
    assert controller["forbidden_actions"]
    assert controller["forbidden_file_edits"]
