from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.github_action_outputs import extract_outputs, trigger_action


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        ({"stop_conditions_fired": True, "force_run": True}, "skip_shipgate"),
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
            "findings": [
                {"check_id": "SHIP-VERIFY-POLICY-WEAKENED", "suppressed": False}
            ],
        },
    )
    _write_json(
        output_dir / "verifier.json",
        {
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


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
