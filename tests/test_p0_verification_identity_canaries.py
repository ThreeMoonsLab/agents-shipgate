from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents_shipgate.core.evaluation_clock import (
    trust_expiry_date,
    use_evaluation_date,
)
from agents_shipgate.core.verification_identity import (
    _add_distribution_tree,
    build_terminal_receipt,
    build_unit_result,
    validate_receipt_artifacts,
)
from agents_shipgate.schemas.verification_identity import (
    VerificationArtifactRef,
    VerificationBlob,
    VerificationEngineRequirement,
    VerificationGitSubject,
    VerificationInputSet,
    VerificationPlan,
    VerificationReceipt,
    VerificationSubject,
    VerificationTask,
    content_id,
)


def _plan() -> VerificationPlan:
    git = VerificationGitSubject(
        repository_id="https://example.test/org/repo.git",
        base_ref="origin/main",
        base_commit_sha="a" * 40,
        base_tree_sha="b" * 40,
        head_ref="HEAD",
        source_head_commit_sha="c" * 40,
        head_commit_sha="d" * 40,
        head_tree_sha="e" * 40,
        merge_base_sha="a" * 40,
        snapshot_kind="committed_tree",
    )
    subject = VerificationSubject(subject_id=content_id(git), git=git)
    config = VerificationBlob(
        path="shipgate.yaml",
        sha256="sha256:" + "1" * 64,
        size_bytes=12,
        source="git_blob",
    )
    diff = VerificationBlob(
        path="verification-input.diff",
        sha256="sha256:" + "2" * 64,
        size_bytes=0,
        source="generated",
    )
    inputs_payload = {
        "evaluation_date": "2026-07-13",
        "config": config,
        "diff": diff,
        "baseline": None,
        "diff_from": None,
        "policy_packs": [],
        "tool_sources": [],
        "changed_paths": [],
        "changed_files": [],
        "options": {"ci_mode": "advisory"},
    }
    inputs = VerificationInputSet(
        input_set_id=content_id(
            {
                key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for key, value in inputs_payload.items()
            }
        ),
        **inputs_payload,
    )
    engine_payload = {
        "version": "0.16.0b7",
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
    task_payload = {
        "kind": "evaluate",
        "shard": 0,
        "shard_count": 1,
        "input_paths": ["shipgate.yaml"],
    }
    task = VerificationTask(task_id=content_id(task_payload), **task_payload)
    request_payload = {
        "subject_id": subject.subject_id,
        "input_set_id": inputs.input_set_id,
        "engine_requirement_id": engine.engine_requirement_id,
        "task_ids": [task.task_id],
    }
    return VerificationPlan(
        request_id=content_id(request_payload),
        subject=subject,
        inputs=inputs,
        engine=engine,
        tasks=[task],
    )


@pytest.mark.parametrize("case", range(64), ids=lambda case: f"identity-canary-{case:02d}")
def test_64_identity_canaries_fail_closed(case: int, tmp_path: Path) -> None:
    plan = _plan()
    bucket = case // 16
    variant = case % 16
    if bucket == 0:
        payload = plan.subject.model_dump(mode="json")
        payload["git"]["repository_id"] = f"https://attacker.test/{variant}"
        with pytest.raises(ValidationError):
            VerificationSubject.model_validate(payload)
    elif bucket == 1:
        payload = plan.inputs.model_dump(mode="json")
        payload["options"][f"tamper_{variant}"] = True
        with pytest.raises(ValidationError):
            VerificationInputSet.model_validate(payload)
    elif bucket == 2:
        payload = plan.engine.model_dump(mode="json")
        payload["version"] = f"tampered-{variant}"
        with pytest.raises(ValidationError):
            VerificationEngineRequirement.model_validate(payload)
    else:
        artifact = tmp_path / f"artifact-{variant}.json"
        artifact.write_text(json.dumps({"case": variant}), encoding="utf-8")
        unit = build_unit_result(plan=plan, normalized_ir={"case": variant})
        _manifest, receipt = build_terminal_receipt(
            plan=plan,
            unit_results=[unit],
            decision="passed",
            merge_verdict="mergeable",
            can_merge_without_human=True,
            artifact_paths={"report": artifact},
        )
        payload = receipt.model_dump(mode="json")
        payload["merge_verdict"] = f"tampered-{variant}"
        with pytest.raises(ValidationError):
            VerificationReceipt.model_validate(payload)


def test_worker_ir_cannot_smuggle_a_release_decision() -> None:
    with pytest.raises(ValidationError, match="never release decisions"):
        build_unit_result(plan=_plan(), normalized_ir={"decision": "passed"})


def test_backdated_evaluation_date_cannot_weaken_trust_expiry_clock() -> None:
    """Commit-controlled time can never move hard expiry behind wall time."""

    wall_clock_today = date.today()
    with use_evaluation_date(wall_clock_today - timedelta(days=3650)):
        assert trust_expiry_date() >= wall_clock_today


def test_future_evaluation_date_only_makes_trust_expiry_more_conservative() -> None:
    future = date.today() + timedelta(days=3650)
    with use_evaluation_date(future):
        assert trust_expiry_date() == future


def test_engine_distribution_digest_is_part_of_engine_identity() -> None:
    payload = _plan().engine.model_dump(mode="json")
    payload["engine_distribution_sha256"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        VerificationEngineRequirement.model_validate(payload)


def test_engine_distribution_tree_excludes_generated_sample_reports(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    fixture = samples / "support_refund_agent"
    fixture.mkdir(parents=True)
    (fixture / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    reports = fixture / "agents-shipgate-reports"
    reports.mkdir()
    (reports / "report.json").write_text('{"stale": true}\n', encoding="utf-8")

    files: dict[str, str] = {}
    _add_distribution_tree(files, samples, logical_root="_fixtures")

    assert "_fixtures/support_refund_agent/shipgate.yaml" in files
    assert all("agents-shipgate-reports" not in path for path in files)


def test_worker_ir_cannot_nest_a_release_decision() -> None:
    with pytest.raises(ValidationError, match="never release decisions"):
        build_unit_result(
            plan=_plan(),
            normalized_ir={"payload": [{"control": {"state": "complete"}}]},
        )


def test_attempt_id_is_not_authoritative(tmp_path: Path) -> None:
    plan = _plan()
    unit = build_unit_result(plan=plan, normalized_ir={"catalog": []})
    artifact = tmp_path / "report.json"
    artifact.write_text("{}", encoding="utf-8")
    _, first = build_terminal_receipt(
        plan=plan,
        unit_results=[unit],
        decision="passed",
        merge_verdict="mergeable",
        can_merge_without_human=True,
        artifact_paths={"report": artifact},
        attempt_id="attempt:one",
    )
    _, second = build_terminal_receipt(
        plan=plan,
        unit_results=[unit],
        decision="passed",
        merge_verdict="mergeable",
        can_merge_without_human=True,
        artifact_paths={"report": artifact},
        attempt_id="attempt:two",
    )
    assert first.receipt_id == second.receipt_id


def test_receipt_artifact_paths_cannot_escape_the_bundle() -> None:
    with pytest.raises(ValidationError, match="portable relative paths"):
        VerificationArtifactRef(
            path="../outside.json",
            sha256="sha256:" + "1" * 64,
            size_bytes=1,
            media_type="application/json",
        )


def test_receipt_artifact_tamper_is_rejected(tmp_path: Path) -> None:
    plan = _plan()
    unit = build_unit_result(plan=plan, normalized_ir={"catalog": []})
    artifact = tmp_path / "report.json"
    artifact.write_text("{}", encoding="utf-8")
    _, receipt = build_terminal_receipt(
        plan=plan,
        unit_results=[unit],
        decision="passed",
        merge_verdict="mergeable",
        can_merge_without_human=True,
        artifact_paths={"report": artifact},
    )
    artifact.write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="hash does not match"):
        validate_receipt_artifacts(receipt, root=tmp_path)
