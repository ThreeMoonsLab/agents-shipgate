from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.github_action_outputs import (
    append_step_summary,
    extract_outputs,
    merge_verdict_policy_exit_code,
    trigger_action,
)


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        # A fired stop with no run rule to contradict it: still a skip. The
        # `stop_conditions_terminal` bit is absent from payloads written
        # before it existed, and there a fired stop *was* terminal.
        ({"stop_conditions_fired": True}, "skip_shipgate"),
        ({"stop_conditions_fired": True, "stop_conditions_terminal": True}, "skip_shipgate"),
        # A stop the runtime overrode. Publishing `skip_shipgate` here
        # reinstated in the Action the exact skip the runtime had refused.
        (
            {
                "stop_conditions_fired": True,
                "stop_conditions_terminal": False,
                "should_run": True,
            },
            "run_shipgate",
        ),
        (
            {
                "stop_conditions_fired": True,
                "stop_conditions_terminal": False,
                "force_run": True,
            },
            "force_run",
        ),
        # Withheld verdicts are not skips and are not "nothing matched".
        ({"evaluation_status": "unclassified", "should_run": None}, "withheld"),
        ({"evaluation_status": "not_evaluated", "should_run": None}, "withheld"),
        ({"force_run": True}, "force_run"),
        ({"should_run": True}, "run_shipgate"),
        ({"run_shipgate": True}, "run_shipgate"),
        ({"dry_run_recommended": True}, "dry_run"),
        ({"skip_reason": "skip_rule"}, "skip_shipgate"),
        ({"skip_reason": "stop_conditions"}, "skip_shipgate"),
        ({"skip_reason": "docs_only"}, "none"),
        ({}, ""),
    ],
)
def test_trigger_action_projects_trigger_verdict(trigger, expected) -> None:
    assert trigger_action(trigger) == expected


def test_action_outputs_prefer_canonical_capability_and_verifier_blocks(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "report.json",
        {
            "summary": {
                "status": "release_blockers_detected",
                "critical_count": 1,
                "high_count": 0,
                "medium_count": 1,
            },
            "release_decision": {
                "decision": "blocked",
                "blockers": [{"id": "F1"}],
                "review_items": [{"id": "F2"}],
                "fail_policy": {"would_fail_ci": True, "exit_code": 20},
            },
            "action_surface_diff": {
                "enabled": True,
                "summary": {
                    "actions_added": 9,
                    "actions_modified": 8,
                    "actions_removed": 7,
                },
            },
            "tool_surface_diff": {
                "enabled": True,
                "summary": {
                    "tools_added": 6,
                    "tools_changed": 5,
                    "tools_removed": 4,
                    "new_scopes": 3,
                    "removed_scopes": 2,
                },
            },
            "capability_change": {
                "enabled": True,
                "added": [{"id": "a1"}, {"id": "a2"}],
                "broadened": [{"id": "b1"}],
                "narrowed": [{"id": "n1"}, {"id": "n2"}],
                "removed": [{"id": "r1"}],
            },
            "verifier_summary": {
                "verdict": "blocked",
                "protected_surface_touched": True,
                "policy_weakened": False,
            },
            "findings": [{"check_id": "SHIP-VERIFY-POLICY-WEAKENED", "suppressed": False}],
        },
    )
    _write_json(
        output_dir / "verifier.json",
        {
            "execution": "succeeded",
            "head_status": "succeeded",
            "trigger": {
                "should_run": True,
                "matched_rules": [{"id": "manifest-present"}, {"id": "tools"}],
            },
            "capability_review": {
                "added": 99,
                "modified": 99,
                "removed": 99,
                "trust_root_touched": False,
                "policy_weakened": True,
            },
        },
    )

    outputs = extract_outputs(output_dir)

    assert outputs["decision"] == "blocked"
    assert outputs["verifier_verdict"] == "blocked"
    assert outputs["blocker_count"] == 1
    assert outputs["review_item_count"] == 1
    assert outputs["trigger_action"] == "run_shipgate"
    assert outputs["trigger_rule_ids"] == "manifest-present,tools"
    assert outputs["capability_changes_added"] == 2
    assert outputs["capability_changes_modified"] == 3
    assert outputs["capability_changes_removed"] == 1
    assert outputs["trust_root_touched"] == "true"
    assert outputs["policy_weakened"] == "false"


def test_action_outputs_fall_back_to_verifier_artifact_for_older_reports(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(output_dir / "report.json", {"summary": {"status": "clean"}})
    _write_json(
        output_dir / "verifier.json",
        {
            "execution": "succeeded",
            "head_status": "succeeded",
            "capability_review": {
                "added": 1,
                "modified": 2,
                "removed": 3,
                "trust_root_touched": True,
                "policy_weakened": True,
            },
        },
    )

    outputs = extract_outputs(output_dir)

    assert outputs["capability_changes_added"] == 1
    assert outputs["capability_changes_modified"] == 2
    assert outputs["capability_changes_removed"] == 3
    assert outputs["trust_root_touched"] == "true"
    assert outputs["policy_weakened"] == "true"


def test_action_outputs_include_verify_run_and_agent_control_fields(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "report.json",
        {
            "summary": {"status": "clean"},
            "release_decision": {
                "decision": "passed",
                "blockers": [],
                "review_items": [],
                "fail_policy": {"would_fail_ci": False, "exit_code": 0},
            },
        },
    )
    _write_json(
        output_dir / "verifier.json",
        {
            "execution": "succeeded",
            "head_status": "succeeded",
            "merge_verdict": "human_review_required",
            "can_merge_without_human": False,
            "control": {
                "state": "human_review_required",
                "reason": "A human must review the capability change.",
                "must_stop": True,
                "stop_reason": "A human must review the capability change.",
                "completion_allowed": False,
                "verify_required": False,
                "next_action": {
                    "actor": "human",
                    "kind": "review",
                    "command": None,
                    "expects": None,
                    "why": "A human must review the capability change.",
                },
                "allowed_next_commands": [],
                "human_review": {
                    "required": True,
                    "why": "A human must review the capability change.",
                    "required_reviewers": [],
                },
            },
        },
    )
    _write_json(output_dir / "verify-run.json", {"run_id": "sha256:" + "a" * 64})

    outputs = extract_outputs(output_dir)

    assert outputs["verify_run_json"] == output_dir / "verify-run.json"
    assert outputs["agent_handoff_json"] == output_dir / "agent-handoff.json"
    assert outputs["run_id"] == "sha256:" + "a" * 64
    assert outputs["check_annotations_json"] == output_dir / "check-annotations.json"
    assert outputs["capability_lock_json"] == output_dir / "capabilities.lock.json"
    assert outputs["base_capability_lock_json"] == output_dir / "base.capabilities.lock.json"
    assert outputs["capability_lock_diff_json"] == output_dir / "capability-lock-diff.json"
    assert outputs["attestation_json"] == output_dir / "attestation.json"
    assert outputs["org_evidence_bundle_json"] == output_dir / "org-evidence-bundle.json"
    assert outputs["host_grants_json"] == output_dir / "host-grants.json"
    assert outputs["org_status_json"] == output_dir / "org-status.json"
    assert outputs["merge_verdict"] == "human_review_required"
    assert outputs["can_merge_without_human"] == "false"
    assert outputs["agent_control_state"] == "human_review_required"
    assert outputs["agent_control_reason"] == "A human must review the capability change."
    assert outputs["agent_controller_must_stop"] == "true"
    assert outputs["agent_controller_stop_reason"] == "A human must review the capability change."
    assert outputs["agent_controller_completion_allowed"] == "false"


def test_action_boolean_outputs_are_exact_control_mirrors(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "verifier.json",
        {
            "execution": "skipped",
            "head_status": "skipped",
            "merge_verdict": "mergeable",
            "can_merge_without_human": True,
            "control": {
                "state": "complete",
                "reason": "The deterministic trigger found no applicable changes.",
                "completion_allowed": True,
                "must_stop": False,
                "verify_required": False,
                "next_action": None,
                "allowed_next_commands": [],
                "human_review": {
                    "required": False,
                    "why": None,
                    "required_reviewers": [],
                },
                "stop_reason": None,
            },
        },
    )

    outputs = extract_outputs(output_dir)

    assert outputs["agent_control_state"] == "complete"
    assert outputs["agent_controller_completion_allowed"] == "true"
    assert outputs["agent_controller_must_stop"] == "false"
    assert outputs["can_merge_without_human"] == "true"


def test_action_outputs_reject_merge_authority_that_contradicts_control(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "verifier.json",
        {
            "execution": "skipped",
            "head_status": "skipped",
            "merge_verdict": "mergeable",
            "applicability": "not_applicable",
            "can_merge_without_human": False,
            "control": {
                "state": "complete",
                "reason": "No applicable change.",
                "completion_allowed": True,
                "must_stop": False,
                "verify_required": False,
                "next_action": None,
                "allowed_next_commands": [],
                "human_review": {
                    "required": False,
                    "why": None,
                    "required_reviewers": [],
                },
                "stop_reason": None,
            },
        },
    )

    with pytest.raises(ValueError, match="contradicts"):
        extract_outputs(output_dir)


def test_action_outputs_do_not_allow_failed_missing_config_verify(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "verifier.json",
        {
            "head_status": "failed",
            "head_exit_code": 2,
            "merge_verdict": "unknown",
            "can_merge_without_human": False,
            "headline": "Shipgate config not found at missing.yaml.",
            "trigger": {"run_shipgate": False},
        },
    )

    outputs = extract_outputs(output_dir)

    assert outputs["decision"] == ""
    assert outputs["verifier_verdict"] == "failed"
    assert outputs["merge_verdict"] == "unknown"
    assert outputs["can_merge_without_human"] == "false"


def test_step_summary_leads_with_verifier_merge_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    _write_json(
        output_dir / "report.json",
        {
            "summary": {"status": "release_blockers_detected"},
            "release_decision": {
                "decision": "blocked",
                "blockers": [{"id": "F1"}],
                "review_items": [{"id": "F2"}],
                "fail_policy": {"would_fail_ci": True, "exit_code": 20},
            },
        },
    )
    _write_json(
        output_dir / "verifier.json",
        {
            "execution": "succeeded",
            "head_status": "succeeded",
            "applicability": "verified",
            "decision": "blocked",
            "merge_verdict": "blocked",
            "can_merge_without_human": False,
            "control": {
                "state": "human_review_required",
                "reason": "A blocker requires human review.",
                "must_stop": True,
                "completion_allowed": False,
                "verify_required": False,
                "next_action": {
                    "actor": "human",
                    "kind": "review",
                    "command": None,
                    "expects": None,
                    "why": "A blocker requires human review.",
                },
                "allowed_next_commands": [],
                "human_review": {
                    "required": True,
                    "why": "A blocker requires human review.",
                    "required_reviewers": [],
                },
                "stop_reason": "A blocker requires human review.",
            },
        },
    )
    values = extract_outputs(output_dir)

    append_step_summary(output_dir, values)

    text = summary_path.read_text(encoding="utf-8")
    assert text.index("Merge verdict: `blocked`") < text.index("Release gate: `blocked`")
    assert "Can merge without human: `false`" in text
    assert "Agent control: state=`human_review_required`" in text
    assert "First next action: `human/review`" in text
    assert f"Verifier JSON: `{output_dir / 'verifier.json'}`" in text
    assert f"Attestation JSON: `{output_dir / 'attestation.json'}`" in text
    assert f"Org evidence bundle JSON: `{output_dir / 'org-evidence-bundle.json'}`" in text
    assert f"Host grants JSON: `{output_dir / 'host-grants.json'}`" in text
    assert f"Org status JSON: `{output_dir / 'org-status.json'}`" in text
    assert f"Verify-run JSON: `{output_dir / 'verify-run.json'}`" in text
    assert f"Agent handoff JSON: `{output_dir / 'agent-handoff.json'}`" in text
    assert f"PR comment Markdown: `{output_dir / 'pr-comment.md'}`" in text


def test_merge_verdict_policy_exit_code_is_opt_in() -> None:
    assert merge_verdict_policy_exit_code("blocked", "") == 0
    assert merge_verdict_policy_exit_code("blocked", "blocked") == 20
    assert merge_verdict_policy_exit_code("human_review_required", "blocked") == 0
    assert (
        merge_verdict_policy_exit_code(
            "human_review_required",
            "blocked, human_review_required",
        )
        == 20
    )
    assert merge_verdict_policy_exit_code("", "blocked") == 21


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_action_outputs_expose_the_withheld_state_a_workflow_must_recover(
    tmp_path: Path,
) -> None:
    """`should_run` is empty for both withheld states; they differ in remedy.

    `unclassified` means the diff was read in full and nothing classified it —
    run the scan. `not_evaluated` means the diff could not be read — repair the
    input. Collapsing both into `trigger_action: "none"` left a workflow with
    no way to tell either from "the trigger ran and matched nothing"
    (PR #404 review 2).
    """

    from agents_shipgate.triggers import evaluate

    unclassified = evaluate(paths=["src/lib.py"], input_status="complete")
    outputs = _trigger_outputs(tmp_path / "a", unclassified)
    assert outputs["trigger_action"] == "withheld"
    assert outputs["trigger_evaluation_status"] == "unclassified"
    assert outputs["should_run"] == ""

    unreadable = evaluate(paths=[], input_status="unavailable")
    outputs = _trigger_outputs(tmp_path / "b", unreadable)
    assert outputs["trigger_action"] == "withheld"
    assert outputs["trigger_evaluation_status"] == "not_evaluated"


def test_action_outputs_do_not_reinstate_a_stop_the_runtime_overrode(
    tmp_path: Path,
) -> None:
    """The `.snap` case, projected end to end through the Action."""

    from agents_shipgate.triggers import evaluate

    result = evaluate(
        paths=["pkg/github/__toolsnaps__/delete_repository.snap"],
        diff_text='+"inputSchema": {}\n+"destructiveHint": true\n',
        detect_result={
            "is_agent_project": False,
            "suggested_sources": [],
            "codex_plugin_candidates": [],
            "python_parse_truncated": False,
        },
    )
    assert result["should_run"] is True
    assert result["stop_conditions_fired"] is True
    assert result["stop_conditions_terminal"] is False

    outputs = _trigger_outputs(tmp_path, result)
    assert outputs["trigger_action"] == "run_shipgate"
    assert outputs["should_run"] == "true"


def _trigger_outputs(out_dir: Path, trigger: dict) -> dict[str, object]:
    """Run the real Action projection over a verifier.json carrying `trigger`."""

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verifier.json").write_text(
        json.dumps({"trigger": trigger}), encoding="utf-8"
    )
    return extract_outputs(out_dir)
