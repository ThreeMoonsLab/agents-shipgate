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

#: These fixtures exercise how the step projects verifier and report fields,
#: with no receipt beside them, so the ones that state an outcome declare a
#: verifier written before receipts, and the ones that read a report name it in
#: ``head_report_json`` as every verify run with a head scan does. Receipt
#: validation on real verify output is in
#: tests/test_verification_receipt_consumers.py (#1022).
PRE_RECEIPT_VERIFIER = "0.4"


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
            "verifier_schema_version": PRE_RECEIPT_VERIFIER,
            "head_report_json": "agents-shipgate-reports/report.json",
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
            "verifier_schema_version": PRE_RECEIPT_VERIFIER,
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
            "verifier_schema_version": PRE_RECEIPT_VERIFIER,
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
            "verifier_schema_version": PRE_RECEIPT_VERIFIER,
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
            "verifier_schema_version": PRE_RECEIPT_VERIFIER,
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
            "verifier_schema_version": PRE_RECEIPT_VERIFIER,
            "head_report_json": "agents-shipgate-reports/report.json",
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
    # The manifest route links artifacts it actually produced.
    for name in (
        "attestation.json", "org-evidence-bundle.json", "host-grants.json",
        "org-status.json", "verify-run.json", "agent-handoff.json", "pr-comment.md",
    ):
        (output_dir / name).write_text("{}", encoding="utf-8")
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
            "host_boundary_candidates": [],
            "host_discovery_incomplete_paths": [],
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


@pytest.mark.parametrize("fork_fallback", [False, True])
def test_host_only_summary_lists_existing_artifacts_without_scan_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fork_fallback: bool,
) -> None:
    from scripts.github_action_annotations import build_annotations

    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    _write_json(output_dir / "verifier.json", {
        "merge_verdict": "unknown", "can_merge_without_human": False,
        "host_comparison": {"comparison_status": "comparable", "rows": [
            {"subject": "claude-code .mcp.json", "direction": "added", "severity": "high", "expands": True},
        ]},
    })
    for name in ("agent-handoff.json", "current-control.json", "check-annotations.json"):
        _write_json(output_dir / name, {})
    review = "## Agents Shipgate\n\n### Human summary\n\n⚠ high / added — claude-code .mcp.json\n"
    (output_dir / "pr-comment.md").write_text(review, encoding="utf-8")
    append_step_summary(output_dir, extract_outputs(output_dir))
    if fork_fallback:
        # Publication appends the review to this same job summary after a 403.
        with summary_path.open("a", encoding="utf-8") as stream:
            stream.write("\nPR comment publication is unavailable.\n\n" + review)
    text = summary_path.read_text()
    assert "Status:" not in text and "Critical:" not in text
    assert "High: 0" not in text and "Medium: 0" not in text
    for missing in ("Report JSON:", "Verify-run JSON:", "Attestation JSON:",
                    "Org evidence bundle JSON:", "Host grants JSON:", "Org status JSON:"):
        assert missing not in text
    for name in ("verifier.json", "agent-handoff.json", "pr-comment.md"):
        assert str(output_dir / name) in text
    if fork_fallback:
        assert "⚠ high / added" in text
    assert "source_report" not in build_annotations(output_dir)


def test_legacy_scan_summary_keeps_computed_severity_counts(tmp_path, monkeypatch):
    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    _write_json(output_dir / "report.json", {"summary": {
        "status": "findings_detected", "critical_count": 1, "high_count": 2, "medium_count": 3,
    }})
    append_step_summary(output_dir, extract_outputs(output_dir))
    text = summary_path.read_text()
    assert "- Status: `findings_detected`" in text
    assert "- Critical: 1 - High: 2 - Medium: 3" in text
    assert f"- Report JSON: `{output_dir / 'report.json'}`" in text
    assert "Scan report: unavailable" not in text


@pytest.mark.parametrize(
    ("code", "completed"),
    [("0", True), ("20", True), (" 20\n", True), ("2", False), ("3", False), ("4", False),
     ("1", False), ("137", False), ("", False), ("021", False)],
)
def test_only_a_run_that_completed_owns_a_receipt(code: str, completed: bool) -> None:
    """`0`, and the gate failure on a decision the run reached, are completions (#1165)."""

    from agents_shipgate.ci.exit_policy import GATE_FAILURE_EXIT_CODE
    from scripts.github_action_outputs import COMPLETED_RUN_EXIT_CODES, ActionInvocation

    assert COMPLETED_RUN_EXIT_CODES == {"0", str(GATE_FAILURE_EXIT_CODE)}
    invocation = ActionInvocation.from_env({"SHIPGATE_EXIT_CODE": code})
    assert invocation.completed is completed


def test_a_run_that_did_not_complete_still_publishes_a_run_with_no_receipt(tmp_path) -> None:
    """The binding refuses receipts only: a no-outcome run publishes as before."""

    from scripts.github_action_outputs import ActionInvocation

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    _write_json(output_dir / "verifier.json", {
        "verifier_schema_version": "0.21", "execution": "failed", "head_status": "failed",
        "merge_verdict": "unknown", "can_merge_without_human": False,
    })
    outputs = extract_outputs(output_dir, invocation=ActionInvocation(exit_code="2", head=""))
    assert outputs["merge_verdict"] == "unknown"
    assert outputs["receipt_id"] == ""


@pytest.mark.parametrize(
    "stated",
    [
        {"merge_verdict": "mergeable"},
        {"can_merge_without_human": True},
        {"decision": "passed"},
        {"release_decision": {"decision": "passed"}},
    ],
)
@pytest.mark.parametrize("label", ["0.4", "0.21"])
def test_after_a_cli_that_did_not_complete_no_verifier_outcome_is_this_runs(
    tmp_path, stated, label,
) -> None:
    """Whatever version it names, an outcome without a run that completed is refused."""

    from scripts.github_action_outputs import ActionInvocation, check_evidence

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    _write_json(output_dir / "verifier.json", {"verifier_schema_version": label, **stated})
    invocation = ActionInvocation(exit_code="2", head="")
    # A current label owes the receipt first; the legacy one is refused as not this run's.
    refusal = "is not this run's" if label == "0.4" else "verification-receipt.json is missing"
    with pytest.raises(ValueError, match=refusal):
        extract_outputs(output_dir, invocation=invocation)
    assert refusal in (check_evidence(output_dir, invocation) or "")


def test_after_a_cli_that_did_not_complete_no_report_is_this_runs(tmp_path) -> None:
    from scripts.github_action_outputs import ActionInvocation

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    _write_json(output_dir / "report.json", {"release_decision": {"decision": "passed"}})
    with pytest.raises(ValueError, match="is not this run's"):
        extract_outputs(output_dir, invocation=ActionInvocation(exit_code="3", head=""))
    # The same scan-route report after a run that completed is published as before.
    outputs = extract_outputs(output_dir, invocation=ActionInvocation(exit_code="0", head=""))
    assert outputs["decision"] == "passed"


def test_the_no_result_outputs_name_every_output(tmp_path) -> None:
    """The fixed set covers exactly the outputs a validated run publishes."""

    from scripts.github_action_outputs import no_result_outputs

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    assert set(no_result_outputs()) == set(extract_outputs(output_dir))


@pytest.mark.parametrize(
    "verifier",
    [
        # A control that authorizes completion, under a pre-receipt label.
        {"verifier_schema_version": "0.4", "merge_verdict": "unknown", "execution": "skipped",
         "control": {
             "state": "complete", "reason": "Release ready.", "completion_allowed": True,
             "must_stop": False, "verify_required": False, "next_action": None,
             "allowed_next_commands": [], "human_review": {"required": False},
             "stop_reason": None,
         }},
        # A legacy controller block saying the same.
        {"verifier_schema_version": "0.4", "merge_verdict": "unknown",
         "agent_controller": {"state": "complete", "must_stop": False,
                              "completion_allowed": True, "reason": "Release ready."}},
    ],
    ids=["control", "agent-controller"],
)
def test_a_run_that_did_not_complete_publishes_the_fixed_no_result_values(
    tmp_path, verifier,
) -> None:
    from scripts.github_action_outputs import ActionInvocation

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    _write_json(output_dir / "verifier.json", verifier)
    for name in ("pr-comment.md", "attestation.json", "org-evidence-bundle.json"):
        (output_dir / name).write_text("{}", encoding="utf-8")

    outputs = extract_outputs(output_dir, invocation=ActionInvocation(exit_code="2", head=""))

    # Field by field first, so the defect itself is what fails.
    assert outputs["agent_control_state"] == "human_review_required"
    assert outputs["agent_controller_must_stop"] == "true"
    assert outputs["agent_controller_completion_allowed"] == "false"
    assert outputs["can_merge_without_human"] == "false"
    assert outputs["merge_verdict"] == "unknown"
    assert all(outputs[key] == "" for key in (
        "attestation_json", "org_evidence_bundle_json", "pr_comment_markdown", "verifier_json",
    ))
    from scripts.github_action_outputs import no_result_outputs

    assert outputs == no_result_outputs()


def test_the_no_result_summary_reads_nothing_from_the_directory(tmp_path, monkeypatch) -> None:
    from scripts import github_action_outputs as outputs_script

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    _write_json(output_dir / "verifier.json", {
        "verifier_schema_version": "0.21", "execution": "failed", "merge_verdict": "unknown",
        "agent_controller": {"state": "agent_action_required",
                             "reason": "Point --config at the manifest.",
                             "next_action": {"actor": "coding_agent", "kind": "edit"}},
    })
    summary = tmp_path / "summary.md"
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("SHIPGATE_EXIT_CODE", "2")

    assert outputs_script.main([]) == 0

    text = summary.read_text(encoding="utf-8")
    assert "No validated verification result: agents-shipgate exited 2." in text
    assert "Point --config" not in text
    assert "coding_agent/edit" not in text and "Merge verdict" not in text
    assert "evidence=no_result\n" in github_output.read_text(encoding="utf-8")


def test_only_a_completed_run_has_a_result_for_the_other_steps(tmp_path) -> None:
    from scripts.github_action_outputs import ActionInvocation, check_evidence

    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    _write_json(output_dir / "verifier.json", {
        "verifier_schema_version": "0.21", "execution": "failed", "merge_verdict": "unknown",
    })
    reason = check_evidence(output_dir, ActionInvocation(exit_code="2", head=""))
    assert reason == "agents-shipgate exited 2, so this run has no verification result"
    assert check_evidence(output_dir, ActionInvocation(exit_code="", head="")) == (
        "agents-shipgate recorded no exit code, so this run has no verification result"
    )
