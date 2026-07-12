from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.schemas.agent_control import (
    CodingAgentCommandAction,
    HumanControlAction,
)
from agents_shipgate.schemas.agent_handoff import (
    AgentHandoffArtifact,
    AgentHandoffGate,
    AgentHandoffSubject,
)

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _verifier_payload() -> dict:
    control = derive_agent_control(
        reason="Human review required.",
        next_action=HumanControlAction(kind="stop", why="Human review required."),
        human_review_required=True,
    )
    return {
        "verifier_schema_version": "0.3",
        "workspace": "/tmp/repo",
        "config": "shipgate.yaml",
        "execution": "succeeded",
        "head_status": "succeeded",
        "release_decision": {
            "decision": "blocked",
            "reason": "A blocker prevents release.",
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
            "evidence_coverage": {
                "level": "complete",
                "human_review_recommended": False,
                "source_warning_count": 0,
                "low_confidence_tool_count": 0,
                "evidence_gaps": [],
            },
            "baseline_delta": {"enabled": False},
            "fail_policy": {
                "ci_mode": "strict",
                "fail_on": ["critical", "high"],
                "new_findings_only": False,
                "would_fail_ci": True,
                "exit_code": 20,
            },
        },
        "decision": "blocked",
        "merge_verdict": "blocked",
        "applicability": "verified",
        "can_merge_without_human": False,
        "control": control.model_dump(mode="json"),
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
        "forbidden_file_edits": ["**/AGENTS.md"],
        "forbidden_actions": ["Do not suppress the finding."],
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
            "outcome": {"control": _verifier_payload()["control"]},
        },
    )

    assert handoff.schema_version == "shipgate.agent_handoff/v3"
    assert handoff.gate.decision == "blocked"
    assert handoff.gate.merge_verdict == "blocked"
    assert handoff.gate.ci_would_fail is True
    assert handoff.control.state == "human_review_required"
    assert handoff.control.must_stop is True
    assert handoff.blocked_by[0].id == "F1"
    assert {step.safety for step in handoff.remediation_plan} == {
        "allowed",
        "forbidden",
    }
    assert handoff.reproducibility.run_id == "sha256:" + "a" * 64
    assert handoff.reproducibility.artifact_sha256 == {"verifier_json": "verifier-hash"}


def test_agent_handoff_schema_validates_sample_projection() -> None:
    schema = json.loads((ROOT / "docs" / "agent-handoff-schema.v3.json").read_text())
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
        "control",
        "fix_task",
        "blocked_by",
        "remediation_plan",
        "capability_review",
        "forbidden_file_edits",
        "forbidden_actions",
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
    assert emitted["schema_version"] == "shipgate.agent_handoff/v3"
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
            contract_version="14",
            operation="verify_pr",
            subject=AgentHandoffSubject(workspace="/tmp/repo", config="shipgate.yaml"),
            gate=AgentHandoffGate(
                decision="blocked",
                merge_verdict="mergeable",
                can_merge_without_human=False,
            ),
            control=derive_agent_control(
                reason="Human review is required.",
                next_action=HumanControlAction(kind="review", why="Human review is required."),
                human_review_required=True,
            ),
        )


def test_agent_handoff_rejects_controller_completion_mismatch() -> None:
    with pytest.raises(ValidationError):
        AgentHandoffArtifact(
            contract_version="14",
            operation="verify_pr",
            subject=AgentHandoffSubject(workspace="/tmp/repo", config="shipgate.yaml"),
            gate=AgentHandoffGate(
                decision="passed",
                merge_verdict="mergeable",
                can_merge_without_human=True,
            ),
            control=derive_agent_control(
                reason="Human review is required.",
                next_action=HumanControlAction(kind="review", why="Human review is required."),
                human_review_required=True,
            ),
        )


def test_agent_handoff_rejects_invalid_verifier_substrate() -> None:
    payload = _verifier_payload()
    payload["merge_verdict"] = "mergeable"

    with pytest.raises(ValidationError):
        build_agent_handoff(verifier=payload)


def test_preview_handoff_carries_standing_forbidden_lists() -> None:
    # Preview is not a deterministic skip: execution has not run and the agent
    # has one exact initialization route. Standing deny-lists remain adjacent
    # verifier metadata and are projected unchanged into the handoff.
    from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
    from agents_shipgate.core.agent_controls import FORBIDDEN_SHORTCUTS

    verifier = {
        "workspace": "/tmp/repo",
        "config": "shipgate.yaml",
        "mode": "preview",
        "execution": "not_run",
        "head_status": "not_run",
        "merge_verdict": "unknown",
        "applicability": "not_evaluated",
        "can_merge_without_human": False,
        "control": derive_agent_control(
            reason="Configure Agents Shipgate before verification.",
            next_action=CodingAgentCommandAction(
                kind="initialize",
                command="agents-shipgate init --write --json",
                why="Create the minimal manifest.",
            ),
            verify_required=True,
        ).model_dump(mode="json"),
        "forbidden_actions": list(FORBIDDEN_SHORTCUTS),
        "forbidden_file_edits": list(PROTECTED_FILE_EDITS),
    }

    handoff = build_agent_handoff(verifier=verifier).model_dump(mode="json")

    assert handoff["operation"] == "verify_preview"
    assert handoff["control"]["state"] == "agent_action_required"
    assert handoff["control"]["must_stop"] is False
    assert handoff["forbidden_actions"] == list(FORBIDDEN_SHORTCUTS)
    assert handoff["forbidden_file_edits"] == list(PROTECTED_FILE_EDITS)
    # Non-empty: a preview handoff never reads as "anything goes".
    assert handoff["forbidden_actions"]
    assert handoff["forbidden_file_edits"]
