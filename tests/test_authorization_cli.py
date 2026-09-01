from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.verification_identity import (
    build_terminal_receipt,
    build_unit_result,
    load_validated_receipt_artifacts,
    sha256_bytes,
)
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.human_authorization import (
    HumanAuthorizationRequestV1,
    authorization_review_items,
)
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance
from agents_shipgate.schemas.verification_identity import (
    VerificationBlob,
    VerificationEngineRequirement,
    VerificationGitSubject,
    VerificationInputSet,
    VerificationPlan,
    VerificationSubject,
    VerificationTask,
    content_id,
)
from agents_shipgate.schemas.verify_run import VerifyRunOutcome, build_verify_run_artifact

runner = CliRunner()
REPOSITORY_ID = "example.test/ThreeMoonsLab/agents-shipgate"
PUSH_URL = "https://example.test/ThreeMoonsLab/agents-shipgate.git"


def _review_required_report(items: list[Any]) -> dict[str, Any]:
    return {
        "release_decision": {
            "decision": "review_required",
            "review_items": items,
        }
    }


def _plan(*, committed: bool, plugins_enabled: bool = False) -> VerificationPlan:
    git = VerificationGitSubject(
        repository_id=REPOSITORY_ID,
        base_ref="origin/main",
        base_commit_sha="a" * 40,
        base_tree_sha="b" * 40,
        head_ref="HEAD",
        source_head_commit_sha="c" * 40,
        head_commit_sha="c" * 40,
        head_tree_sha="d" * 40,
        merge_base_sha="a" * 40,
        snapshot_kind="committed_tree" if committed else "worktree_overlay",
        worktree_overlay_sha256=None if committed else content_id({"overlay": True}),
    )
    subject = VerificationSubject(subject_id=content_id(git), git=git)
    config = VerificationBlob(
        path="shipgate.yaml",
        sha256=sha256_bytes(b'version: "0.1"\n'),
        size_bytes=len(b'version: "0.1"\n'),
        source="git_blob" if committed else "worktree",
    )
    diff = VerificationBlob(
        path="verification-input.diff",
        sha256=sha256_bytes(b""),
        size_bytes=0,
        source="generated",
    )
    input_payload = {
        "evaluation_date": "2026-07-18",
        "manifest_provenance": ManifestProvenance.repository(),
        "config": config,
        "diff": diff,
        "baseline": None,
        "diff_from": None,
        "policy_packs": [],
        "tool_sources": [],
        "changed_paths": [],
        "changed_files": [],
        "options": {
            "ci_mode": "advisory",
            "plugins_enabled": plugins_enabled,
        },
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
        "engine_distribution_sha256": "sha256:" + "1" * 64,
        "dependency_set_sha256": "sha256:" + "2" * 64,
        "adapter_set_sha256": "sha256:" + "3" * 64,
        "plugin_set_sha256": "sha256:" + "4" * 64,
        "policy_catalog_sha256": "sha256:" + "5" * 64,
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


def _write_receipt_bundle(
    root: Path,
    *,
    committed: bool,
    plugins_enabled: bool = False,
    review_items: list[dict[str, Any]] | None = None,
) -> tuple[Path, VerificationPlan]:
    root.mkdir()
    plan = _plan(committed=committed, plugins_enabled=plugins_enabled)
    items = review_items or [
        {
            "id": "fp_binding_review",
            "fingerprint": "fp_binding_review",
            "check_id": "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            "source": {"path": "shipgate.yaml"},
            "support": {"support_hash": "sha256:" + "9" * 64},
        }
    ]
    paths = {
        "verification_plan_json": root / "verification-plan.json",
        "verification_input_diff": root / "verification-input.diff",
        "verification_unit_result_json": root / "verification-unit-result.json",
        "verify_run_json": root / "verify-run.json",
        "report_json": root / "report.json",
    }
    unit = build_unit_result(plan=plan, normalized_ir={"test": "authorization-request"})
    outcome = VerifyRunOutcome(
        exit_code=0,
        base_status="not_requested",
        execution="succeeded",
        applicability="verified",
        decision="review_required",
        merge_verdict="human_review_required",
        can_merge_without_human=False,
        control=derive_agent_control(
            reason="Human review is required.",
            next_action=HumanControlAction(
                kind="review",
                why="Review the closed release-decision set.",
            ),
            human_review_required=True,
        ),
        manifest_provenance=plan.inputs.manifest_provenance,
    )
    verify_run = build_verify_run_artifact(
        plan=plan,
        executor=unit.executor,
        unit_result_ids=[unit.unit_result_id],
        outcome=outcome,
        artifacts={},
    )
    paths["verification_plan_json"].write_text(
        json.dumps(plan.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    paths["verification_input_diff"].write_bytes(b"")
    paths["verification_unit_result_json"].write_text(
        json.dumps(unit.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    paths["verify_run_json"].write_text(
        json.dumps(verify_run.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    report = _review_required_report(items)
    report.update(
        {
            "request_id": plan.request_id,
            "subject_id": plan.subject.subject_id,
            "input_set_id": plan.inputs.input_set_id,
            "engine_requirement_id": plan.engine.engine_requirement_id,
            "decision_id": verify_run.decision_id,
        }
    )
    paths["report_json"].write_text(
        json.dumps(report, sort_keys=True),
        encoding="utf-8",
    )
    manifest, receipt = build_terminal_receipt(
        plan=plan,
        unit_results=[unit],
        decision="review_required",
        merge_verdict="human_review_required",
        can_merge_without_human=False,
        artifact_paths=paths,
        artifact_root=root,
    )
    (root / "verification-artifacts.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    receipt_path = root / "verification-receipt.json"
    receipt_path.write_text(
        json.dumps(receipt.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    return receipt_path, plan


def _request_args(root: Path, receipt_path: Path, out: Path) -> list[str]:
    return [
        "authorization",
        "request",
        "--receipt",
        str(receipt_path),
        "--artifacts-root",
        str(root),
        "--remote",
        "origin",
        "--destination-ref",
        "refs/heads/codex/human-authorization-state",
        "--expected-lease-oid",
        "e" * 40,
        "--out",
        str(out),
        "--json",
    ]


def _git_repository(path: Path, *, remote_url: str = PUSH_URL) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", remote_url],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return path


def test_review_items_projection_is_canonical_and_complete() -> None:
    first = {
        "id": "review-z",
        "fingerprint": "fp-z",
        "check_id": "SHIP-Z",
        "source": {"path": "z/source.json"},
        "policy_evidence_source": {"path": "a/policy.yaml"},
        "support": {"support_hash": "sha256:" + "f" * 64},
    }
    second = {
        "id": "review-a",
        "fingerprint": "review-a",
        "check_id": "SHIP-A",
        "source": {"path": "b/source.json"},
    }

    forward = authorization_review_items(
        _review_required_report([first, second])["release_decision"]
    )
    reverse = authorization_review_items(
        _review_required_report([second, first])["release_decision"]
    )

    assert forward == reverse
    assert [item.review_item_id for item in forward] == ["review-a", "review-z"]
    assert forward[0].fingerprint == "review-a"
    assert forward[1].support_hash == "sha256:" + "f" * 64
    assert forward[1].paths == ["a/policy.yaml", "z/source.json"]


@pytest.mark.parametrize(
    "release_decision",
    [
        {},
        {"decision": "passed", "review_items": [{}]},
        _review_required_report([])["release_decision"],
        _review_required_report(["not-an-object"])["release_decision"],
        _review_required_report([{"id": "review-1"}])["release_decision"],
        _review_required_report([{"check_id": "SHIP-TEST"}])["release_decision"],
        _review_required_report(
            [
                {
                    "id": "review-1",
                    "check_id": "SHIP-TEST",
                    "source": {"path": "../escape"},
                }
            ]
        )["release_decision"],
        _review_required_report(
            [
                {"id": "review-1", "check_id": "SHIP-TEST"},
                {"id": "review-1", "check_id": "SHIP-OTHER"},
            ]
        )["release_decision"],
    ],
    ids=[
        "missing-release-decision",
        "not-review-required",
        "empty-set",
        "non-object-item",
        "missing-check-id",
        "missing-item-identity",
        "non-portable-source-path",
        "duplicate-item-identity",
    ],
)
def test_review_items_reject_empty_or_malformed_closed_sets(
    release_decision: dict[str, Any],
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        authorization_review_items(release_decision)


def test_authorization_request_binds_exact_committed_git_push(
    tmp_path: Path,
) -> None:
    repo = _git_repository(tmp_path / "repo")
    root = repo / "artifacts"
    receipt_path, plan = _write_receipt_bundle(root, committed=True)
    out = tmp_path / "authorization-request.json"
    result = runner.invoke(app, _request_args(root, receipt_path, out))

    assert result.exit_code == 0, result.output
    request = HumanAuthorizationRequestV1.model_validate(
        json.loads(out.read_text(encoding="utf-8"))
    )
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    git = plan.subject.git
    assert request.source_receipt_id == receipt_payload["receipt_id"]
    assert request.source_artifact_set_id == receipt_payload["artifact_set_id"]
    assert request.source_engine_requirement_id == receipt_payload["engine_requirement_id"]
    assert request.source_executor_id == receipt_payload["executor_id"]
    assert request.verification_request_id == plan.request_id
    assert request.subject_id == plan.subject.subject_id
    assert request.base_commit_sha == git.base_commit_sha
    assert request.merge_base_sha == git.merge_base_sha
    assert request.base_tree_sha == git.base_tree_sha
    assert request.head_tree_sha == git.head_tree_sha
    assert request.source_head_commit_sha == "c" * 40
    assert request.operation.destination_repository_id == REPOSITORY_ID
    assert request.operation.push_url == PUSH_URL
    assert "remote" not in request.operation.model_dump(mode="json")
    assert request.operation.destination_ref == ("refs/heads/codex/human-authorization-state")
    assert request.operation.expected_lease_oid == "e" * 40
    assert request.operation.command == (
        "git push "
        "--force-with-lease=refs/heads/codex/human-authorization-state:"
        + "e" * 40
        + " "
        + PUSH_URL
        + " "
        + "c" * 40
        + ":refs/heads/codex/human-authorization-state"
    )
    assert json.loads(result.output)["output_path"] == str(out)

    for field, replacement in (
        ("destination_ref", "refs/heads/attacker"),
        ("expected_lease_oid", "f" * 40),
        ("push_url", "https://attacker.test/ThreeMoonsLab/agents-shipgate.git"),
        (
            "destination_repository_id",
            "attacker.test/ThreeMoonsLab/agents-shipgate",
        ),
    ):
        tampered = request.model_dump(mode="json")
        tampered["operation"][field] = replacement
        with pytest.raises(ValidationError):
            HumanAuthorizationRequestV1.model_validate(tampered)


def test_receipt_loader_rejects_symlinked_artifacts_and_returns_immutable_bytes(
    tmp_path: Path,
) -> None:
    repo = _git_repository(tmp_path / "repo")
    root = repo / "artifacts"
    receipt_path, _plan_value = _write_receipt_bundle(root, committed=True)

    _receipt, artifacts = load_validated_receipt_artifacts(
        receipt_path=receipt_path,
        root=root,
    )
    original_report = artifacts["report_json"]
    (root / "report.json").write_text("{}", encoding="utf-8")
    assert artifacts["report_json"] == original_report

    # Restore valid bytes behind a symlink. Hashes still match, but the loader
    # rejects path substitution instead of following it.
    target = root / "report-target.json"
    target.write_bytes(original_report)
    (root / "report.json").unlink()
    (root / "report.json").symlink_to(target.name)
    result = runner.invoke(
        app,
        _request_args(root, receipt_path, tmp_path / "request.json"),
    )
    assert result.exit_code == 3
    assert "safely read receipt artifact" in result.output
    error = json.loads(result.output)
    assert error["error"] == "input_parse_error"
    assert error["next_action"]
    assert len(error["next_actions"]) == 1


def test_receipt_loader_enforces_trusted_name_and_byte_budgets(
    tmp_path: Path,
) -> None:
    repo = _git_repository(tmp_path / "repo")
    root = repo / "artifacts"
    receipt_path, _plan_value = _write_receipt_bundle(root, committed=True)

    with pytest.raises(ValueError, match="outside the allowed execution closure"):
        load_validated_receipt_artifacts(
            receipt_path=receipt_path,
            root=root,
            allowed_artifact_names=frozenset(),
        )
    with pytest.raises(ValueError, match="trusted total size limit"):
        load_validated_receipt_artifacts(
            receipt_path=receipt_path,
            root=root,
            max_total_size=1,
        )


def test_authorization_request_rejects_worktree_overlay(
    tmp_path: Path,
) -> None:
    repo = _git_repository(tmp_path / "repo")
    root = repo / "artifacts"
    receipt_path, _plan_value = _write_receipt_bundle(root, committed=False)
    result = runner.invoke(
        app,
        _request_args(root, receipt_path, tmp_path / "request.json"),
    )

    assert result.exit_code == 3
    assert "human authorization rejects uncommitted worktree overlays" in result.output
    assert not (tmp_path / "request.json").exists()


def test_authorization_request_rejects_plugin_enabled_engine(
    tmp_path: Path,
) -> None:
    repo = _git_repository(tmp_path / "repo")
    root = repo / "artifacts"
    receipt_path, _plan_value = _write_receipt_bundle(
        root,
        committed=True,
        plugins_enabled=True,
    )

    result = runner.invoke(
        app,
        _request_args(root, receipt_path, tmp_path / "request.json"),
    )

    assert result.exit_code == 3
    assert "plugins-disabled engine mode" in result.output
    assert not (tmp_path / "request.json").exists()


def test_authorization_request_rejects_remote_for_another_repository(
    tmp_path: Path,
) -> None:
    repo = _git_repository(
        tmp_path / "repo",
        remote_url="https://attacker.test/ThreeMoonsLab/agents-shipgate.git",
    )
    root = repo / "artifacts"
    receipt_path, _plan_value = _write_receipt_bundle(root, committed=True)

    result = runner.invoke(
        app,
        _request_args(root, receipt_path, tmp_path / "request.json"),
    )

    assert result.exit_code == 3
    assert "does not match the reviewed repository" in result.output
    assert not (tmp_path / "request.json").exists()


@pytest.mark.parametrize(
    "remote_url",
    [
        "http://example.test/ThreeMoonsLab/agents-shipgate.git",
        "ssh://git@example.test/ThreeMoonsLab/agents-shipgate.git",
        "file:///tmp/agents-shipgate.git",
        "../agents-shipgate.git",
        "https://token@example.test/ThreeMoonsLab/agents-shipgate.git",
        "https://example.test/ThreeMoonsLab/agents-shipgate.git?token=secret",
        "https://example.test/ThreeMoonsLab/agents-shipgate.git#alternate",
    ],
)
def test_authorization_request_rejects_unsafe_remote_endpoint(
    tmp_path: Path,
    remote_url: str,
) -> None:
    repo = _git_repository(tmp_path / "repo", remote_url=remote_url)
    root = repo / "artifacts"
    receipt_path, _plan_value = _write_receipt_bundle(root, committed=True)

    result = runner.invoke(
        app,
        _request_args(root, receipt_path, tmp_path / "request.json"),
    )

    assert result.exit_code == 3
    assert "unsafe push URL" in result.output
    assert not (tmp_path / "request.json").exists()
