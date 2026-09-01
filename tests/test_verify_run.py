from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance
from agents_shipgate.schemas.verification_identity import (
    VerificationBlob,
    VerificationEngineRequirement,
    VerificationExecutor,
    VerificationGitSubject,
    VerificationInputSet,
    VerificationPlan,
    VerificationSubject,
    VerificationTask,
    content_id,
)
from agents_shipgate.schemas.verify_run import (
    VerifyRunArtifact,
    VerifyRunOutcome,
    build_verify_run_artifact,
)


def _plan(*, no_heuristics: bool = False) -> tuple[VerificationPlan, VerificationExecutor]:
    git = VerificationGitSubject(
        repository_id="https://example.test/org/repo.git",
        base_ref="origin/main",
        base_commit_sha="a" * 40,
        base_tree_sha="b" * 40,
        head_ref="HEAD",
        head_commit_sha="c" * 40,
        head_tree_sha="d" * 40,
        merge_base_sha="a" * 40,
        snapshot_kind="committed_tree",
    )
    subject = VerificationSubject(subject_id=content_id(git), git=git)
    blob = VerificationBlob(
        path="shipgate.yaml",
        sha256="sha256:" + "1" * 64,
        size_bytes=10,
        source="git_blob",
    )
    diff = VerificationBlob(
        path="verification-input.diff",
        sha256="sha256:" + "2" * 64,
        size_bytes=0,
        source="generated",
    )
    input_payload = {
        "evaluation_date": "2026-07-13",
        "manifest_provenance": ManifestProvenance.repository(),
        "config": blob,
        "diff": diff,
        "baseline": None,
        "diff_from": None,
        "policy_packs": [],
        "tool_sources": [],
        "changed_paths": [],
        "changed_files": [],
        "options": {"no_heuristics": no_heuristics},
    }
    inputs = VerificationInputSet(
        input_set_id=content_id(
            {
                key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for key, value in input_payload.items()
            }
        ),
        **input_payload,
    )
    engine_payload = {
        "version": "test-version",
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "platform": "linux",
        "engine_distribution_sha256": "sha256:" + "8" * 64,
        "dependency_set_sha256": "sha256:" + "3" * 64,
        "adapter_set_sha256": "sha256:" + "4" * 64,
        "plugin_set_sha256": "sha256:" + "5" * 64,
        "policy_catalog_sha256": "sha256:" + "6" * 64,
    }
    engine = VerificationEngineRequirement(
        engine_requirement_id=content_id({"package": "agents-shipgate", **engine_payload}),
        **engine_payload,
    )
    task_payload = {"kind": "evaluate", "shard": 0, "shard_count": 1, "input_paths": []}
    task = VerificationTask(task_id=content_id(task_payload), **task_payload)
    request_payload = {
        "subject_id": subject.subject_id,
        "input_set_id": inputs.input_set_id,
        "engine_requirement_id": engine.engine_requirement_id,
        "task_ids": [task.task_id],
    }
    plan = VerificationPlan(
        request_id=content_id(request_payload),
        subject=subject,
        inputs=inputs,
        engine=engine,
        tasks=[task],
    )
    executor_payload = {
        "engine_requirement_id": engine.engine_requirement_id,
        "runtime_sha256": "sha256:" + "7" * 64,
    }
    executor = VerificationExecutor(
        executor_id=content_id(executor_payload),
        **executor_payload,
    )
    return plan, executor


def _passed_outcome() -> VerifyRunOutcome:
    return VerifyRunOutcome(
        exit_code=0,
        base_status="succeeded",
        execution="succeeded",
        applicability="verified",
        decision="passed",
        merge_verdict="mergeable",
        can_merge_without_human=True,
        control=derive_agent_control(reason="Static verification passed."),
        manifest_provenance=ManifestProvenance.repository(),
    )


def test_run_id_is_exact_request_alias_and_decision_has_separate_identity() -> None:
    plan, executor = _plan()
    passed = build_verify_run_artifact(
        plan=plan,
        executor=executor,
        unit_result_ids=["sha256:" + "8" * 64],
        outcome=_passed_outcome(),
        artifacts={},
    )
    blocked_outcome = VerifyRunOutcome(
        exit_code=20,
        base_status="succeeded",
        execution="succeeded",
        applicability="verified",
        decision="blocked",
        merge_verdict="blocked",
        can_merge_without_human=False,
        control=derive_agent_control(
            reason="A blocking policy condition requires a human.",
            next_action=HumanControlAction(
                kind="stop", why="A blocking policy condition requires a human."
            ),
            human_review_required=True,
        ),
        manifest_provenance=ManifestProvenance.repository(),
    )
    blocked = build_verify_run_artifact(
        plan=plan,
        executor=executor,
        unit_result_ids=["sha256:" + "8" * 64],
        outcome=blocked_outcome,
        artifacts={},
    )
    assert passed.run_id == passed.request_id == plan.request_id
    assert blocked.request_id == passed.request_id
    assert blocked.decision_id != passed.decision_id

    changed_plan, changed_executor = _plan(no_heuristics=True)
    changed = build_verify_run_artifact(
        plan=changed_plan,
        executor=changed_executor,
        unit_result_ids=["sha256:" + "8" * 64],
        outcome=_passed_outcome(),
        artifacts={},
    )
    assert changed.request_id != passed.request_id


def test_verify_run_rejects_run_id_that_is_not_request_alias() -> None:
    plan, executor = _plan()
    artifact = build_verify_run_artifact(
        plan=plan,
        executor=executor,
        unit_result_ids=["sha256:" + "8" * 64],
        outcome=_passed_outcome(),
        artifacts={},
    )
    payload = artifact.model_dump(mode="json")
    payload["run_id"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        VerifyRunArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "unit_result_ids",
    [[], ["not-a-content-id"], ["sha256:" + "8" * 64] * 2],
)
def test_verify_run_rejects_missing_malformed_or_duplicate_unit_ids(
    unit_result_ids: list[str],
) -> None:
    plan, executor = _plan()
    with pytest.raises(ValidationError):
        build_verify_run_artifact(
            plan=plan,
            executor=executor,
            unit_result_ids=unit_result_ids,
            outcome=_passed_outcome(),
            artifacts={},
        )


def test_verify_run_rejects_noncomplete_control_for_merge_authority() -> None:
    human = derive_agent_control(
        reason="Human review is required.",
        next_action=HumanControlAction(kind="review", why="Human review is required."),
        human_review_required=True,
    )
    with pytest.raises(ValidationError):
        VerifyRunOutcome(
            exit_code=0,
            base_status="succeeded",
            execution="succeeded",
            applicability="verified",
            decision="passed",
            merge_verdict="mergeable",
            can_merge_without_human=True,
            control=human,
            manifest_provenance=ManifestProvenance.repository(),
        )
