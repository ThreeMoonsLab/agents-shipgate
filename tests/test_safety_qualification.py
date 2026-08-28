from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.verification_identity import (
    build_executor,
    build_terminal_receipt,
    build_unit_result,
)
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.safety_qualification import (
    FrozenSafetyCorpusV1,
    HumanAdjudicationV1,
    IndependentHumanLabelV1,
    SafetyCorpusCaseV1,
    SafetyQualificationRequirementsV1,
    SafetyReceiptEntryV1,
    SafetyReceiptIndexV1,
    SafetyStratumRequirementV1,
    compute_frozen_labels_sha256,
    production_safety_requirements,
)
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
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierDiffStatus,
)
from agents_shipgate.schemas.verify_run import (
    VerifyRunOutcome,
    build_verify_run_artifact,
)
from scripts.run_safety_qualification import (
    EXPECTED_MERGE_VERDICT,
    hash_policy_bundle,
    load_frozen_corpus,
    main,
    render_safety_qualification_json,
    run_safety_qualification,
    sha256_file,
)

DECISIONS = ("passed", "review_required", "insufficient_evidence", "blocked")
VERSION = "0.16.0b7"


def _verification_plan(case_id: str) -> VerificationPlan:
    git = VerificationGitSubject(
        repository_id="https://example.test/qualification.git",
        base_ref="origin/main",
        base_commit_sha="a" * 40,
        base_tree_sha=hashlib.sha256(f"base-{case_id}".encode()).hexdigest(),
        head_ref="HEAD",
        head_commit_sha="b" * 40,
        head_tree_sha=hashlib.sha256(f"head-{case_id}".encode()).hexdigest(),
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
    input_payload = {
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
                for key, value in input_payload.items()
            }
        ),
        **input_payload,
    )
    engine_payload = {
        "version": VERSION,
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "platform": "test",
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
    return VerificationPlan(
        request_id=content_id(request_payload),
        subject=subject,
        inputs=inputs,
        engine=engine,
        tasks=[task],
    )


def _human_label(role: str, reviewer: str, decision: str) -> IndependentHumanLabelV1:
    return IndependentHumanLabelV1(
        role=role,
        reviewer_id=reviewer,
        decision=decision,
        rationale=f"Independent rationale for {decision}",
        evidence_references=(f"evidence/{reviewer}.json#decision",),
        shipgate_output_seen=False,
    )


def _case(
    case_id: str,
    decision: str,
    *,
    disagreement: bool = False,
) -> SafetyCorpusCaseV1:
    framework_decision = "passed" if disagreement else decision
    return SafetyCorpusCaseV1(
        id=case_id,
        profile="mcp",
        origin="real_history",
        split="holdout",
        expected_decision=decision,
        evidence_references=(f"evidence/{case_id}.json",),
        remediation_condition="Apply the human-reviewed remediation and rerun verify.",
        security_governance_label=_human_label(
            "security_governance", f"security-{case_id}", decision
        ),
        framework_tooling_label=_human_label(
            "framework_tooling", f"framework-{case_id}", framework_decision
        ),
        adjudication=HumanAdjudicationV1(
            state="adjudicated_disagreement" if disagreement else "agreement",
            final_decision=decision,
            reviewer_id=f"adjudicator-{case_id}" if disagreement else None,
            rationale="Frozen final rationale.",
            evidence_references=(f"evidence/{case_id}.json#adjudication",),
        ),
    )


def _test_requirements() -> SafetyQualificationRequirementsV1:
    return SafetyQualificationRequirementsV1(
        required_strata=tuple(
            SafetyStratumRequirementV1(
                profile="mcp",
                expected_decision=decision,
                count=1,
            )
            for decision in DECISIONS
        ),
        minimum_qualified_origins=4,
        minimum_kappa=0.80,
        minimum_holdout_fraction_per_stratum=0.20,
        maximum_unsafe_auto_passes=0,
        minimum_safe_passes=1,
        minimum_blocked_exact=1,
        minimum_review_exact=1,
        minimum_insufficient_evidence_exact=1,
        required_report_schema_version="0.42",
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "agents_shipgate-0.16.0b7.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: agents-shipgate\nVersion: 0.16.0b7\n",
        )


def _semantic_coverage(decision: str) -> dict[str, object]:
    if decision == "passed":
        return {
            "total_actions": 1,
            "pass_eligible_actions": 1,
            "gap_count": 0,
            "review_concern_count": 0,
            "reason_counts": {},
        }
    if decision == "review_required":
        return {
            "total_actions": 1,
            "pass_eligible_actions": 0,
            "gap_count": 0,
            "review_concern_count": 1,
            "reason_counts": {"unscoped_authority": 1},
        }
    if decision == "insufficient_evidence":
        return {
            "total_actions": 1,
            "pass_eligible_actions": 0,
            "gap_count": 1,
            "review_concern_count": 0,
            "reason_counts": {"missing_authority_evidence": 1},
        }
    return {
        "total_actions": 1,
        "pass_eligible_actions": 1,
        "gap_count": 0,
        "review_concern_count": 0,
        "reason_counts": {},
    }


def _fixture(
    tmp_path: Path,
    *,
    actual_overrides: dict[str, str] | None = None,
    disagreement_case: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    wheel = tmp_path / "agents_shipgate-0.16.0b7-py3-none-any.whl"
    _write_wheel(wheel)
    policy = tmp_path / "qualification-policy.json"
    _write_json(policy, {"policy": "beta-exact", "version": 1})

    cases = [
        _case(
            f"case-{decision}",
            decision,
            disagreement=f"case-{decision}" == disagreement_case,
        )
        for decision in DECISIONS
    ]
    corpus = FrozenSafetyCorpusV1(
        corpus_id="test-corpus",
        labels_frozen_before_evaluation=True,
        outputs_hidden_from_labelers=True,
        frozen_labels_sha256=compute_frozen_labels_sha256(cases),
        cases=cases,
    )
    corpus_path = tmp_path / "corpus.json"
    _write_json(corpus_path, corpus.model_dump(mode="json"))

    entries: list[SafetyReceiptEntryV1] = []
    for case in cases:
        actual = (actual_overrides or {}).get(case.id, case.expected_decision)
        plan = _verification_plan(case.id)
        unit = build_unit_result(plan=plan, normalized_ir={"case_id": case.id})
        executor = build_executor(plan.engine)
        decision_id = content_id(
            {
                "request_id": plan.request_id,
                "unit_result_ids": [unit.unit_result_id],
                "decision": actual,
                "merge_verdict": EXPECTED_MERGE_VERDICT[actual],
                "can_merge_without_human": actual == "passed",
            }
        )
        artifact_root = tmp_path / "artifacts" / case.id
        reports = artifact_root / "agents-shipgate-reports"
        report_path = reports / "report.json"
        verifier_path = reports / "verifier.json"
        report = ReadinessReport(
            run_id=f"run-{case.id}",
            request_id=plan.request_id,
            subject_id=plan.subject.subject_id,
            input_set_id=plan.inputs.input_set_id,
            engine_requirement_id=plan.engine.engine_requirement_id,
            decision_id=decision_id,
            project={"name": "qualification-fixture"},
            agent={"name": "fixture-agent"},
            environment={"name": "test"},
            summary={"status": "qualification-fixture"},
            release_decision={
                "decision": actual,
                "reason": "Qualification fixture decision.",
                "static_analysis_only": True,
                "runtime_behavior_verified": False,
                "static_verdict_disclaimer": STATIC_VERDICT_DISCLAIMER,
                "evidence_coverage": {
                    "level": "complete",
                    "human_review_recommended": actual != "passed",
                    "source_warning_count": 0,
                    "low_confidence_tool_count": 0,
                    "semantic_coverage": _semantic_coverage(actual),
                    "binding_coverage": {
                        "total_catalog_tools": 1,
                        "reachable_tools": 1,
                        "possible_tools": 0,
                        "unbound_tools": 0,
                        "pass_eligible": True,
                        "gap_count": 0,
                        "reason_counts": {},
                    },
                },
                "baseline_delta": {"enabled": False},
                "fail_policy": {
                    "ci_mode": "advisory",
                    "would_fail_ci": actual != "passed",
                    "exit_code": 0,
                },
            },
            tool_surface={"total_tools": 1, "high_risk_tools": 0},
        )
        _write_json(
            report_path,
            report.model_dump(mode="json"),
        )
        control = (
            derive_agent_control(reason="Static verification passed.")
            if actual == "passed"
            else derive_agent_control(
                reason=f"Qualification decision is {actual}.",
                next_action=HumanControlAction(
                    kind="stop" if actual == "blocked" else "review",
                    why=f"Qualification decision is {actual}.",
                ),
                verify_required=True,
                human_review_required=True,
                unsafe_block=actual == "blocked",
            )
        )
        verifier = VerifierArtifact(
            workspace=".",
            diff_status=VerifierDiffStatus(),
            request_id=plan.request_id,
            subject_id=plan.subject.subject_id,
            input_set_id=plan.inputs.input_set_id,
            engine_requirement_id=plan.engine.engine_requirement_id,
            executor_id=executor.executor_id,
            decision_id=decision_id,
            config="shipgate.yaml",
            base_ref="origin/main",
            head_ref="HEAD",
            base_status="succeeded",
            base_tree_sha=hashlib.sha256(f"base-{case.id}".encode()).hexdigest(),
            head_tree_sha=hashlib.sha256(f"head-{case.id}".encode()).hexdigest(),
            execution="succeeded",
            head_status="succeeded",
            head_exit_code=0,
            release_decision=report.release_decision,
            decision=actual,
            merge_verdict=EXPECTED_MERGE_VERDICT[actual],
            applicability="verified",
            can_merge_without_human=actual == "passed",
            control=control,
            authorization=AuthorizationEvaluationV1.not_requested(),
            mode="advisory",
            artifacts={"report_json": "agents-shipgate-reports/report.json"},
        )
        _write_json(
            verifier_path,
            verifier.model_dump(mode="json"),
        )
        verify_run = build_verify_run_artifact(
            plan=plan,
            executor=executor,
            unit_result_ids=[unit.unit_result_id],
            outcome=VerifyRunOutcome(
                exit_code=0,
                base_status="succeeded",
                execution="succeeded",
                applicability="verified",
                decision=actual,
                merge_verdict=EXPECTED_MERGE_VERDICT[actual],
                can_merge_without_human=actual == "passed",
                control=verifier.control,
            ),
            artifacts={},
        )
        verify_run_path = reports / "verify-run.json"
        _write_json(verify_run_path, verify_run.model_dump(mode="json"))
        _manifest, receipt = build_terminal_receipt(
            plan=plan,
            unit_results=[unit],
            decision=actual,
            merge_verdict=EXPECTED_MERGE_VERDICT[actual],
            can_merge_without_human=actual == "passed",
            artifact_paths={
                "report_json": report_path,
                "verifier_json": verifier_path,
                "verify_run_json": verify_run_path,
            },
        )
        receipt_path = tmp_path / "receipts" / f"{case.id}.json"
        _write_json(receipt_path, receipt.model_dump(mode="json"))
        entries.append(
            SafetyReceiptEntryV1(
                case_id=case.id,
                receipt_path=receipt_path.relative_to(tmp_path).as_posix(),
                artifact_root=reports.relative_to(tmp_path).as_posix(),
                receipt_sha256=sha256_file(receipt_path),
                run_id=receipt.request_id,
                request_id=receipt.request_id,
                receipt_id=receipt.receipt_id,
                decision_id=receipt.decision_id,
                artifact_set_id=receipt.artifact_set_id,
            )
        )

    receipt_index = SafetyReceiptIndexV1(
        wheel_sha256=sha256_file(wheel),
        engine_version=VERSION,
        corpus_sha256=sha256_file(corpus_path),
        labels_sha256=corpus.frozen_labels_sha256,
        policy_sha256=hash_policy_bundle([policy]),
        labels_frozen_before_receipts=True,
        receipts=entries,
    )
    receipt_index_path = tmp_path / "receipt-index.json"
    _write_json(receipt_index_path, receipt_index.model_dump(mode="json"))
    return wheel, corpus_path, receipt_index_path, policy


def _run(
    paths: tuple[Path, Path, Path, Path],
):
    wheel, corpus, receipts, policy = paths
    return run_safety_qualification(
        wheel_path=wheel,
        corpus_path=corpus,
        receipt_index_path=receipts,
        policy_paths=[policy],
        requirements=_test_requirements(),
    )


def test_the_qualification_gate_demands_the_schema_the_engine_emits() -> None:
    """A gate pinned to a schema no build produces rejects every receipt.

    ``_evaluate_receipt`` compares ``report_schema_version`` for exact
    equality, so leaving this behind a report-schema bump fails the whole
    qualification run with "qualification report schema mismatch" — on every
    case, for a reason that has nothing to do with safety.
    """

    from agents_shipgate.schemas.report import ReadinessReport

    assert production_safety_requirements().required_report_schema_version == (
        ReadinessReport.model_fields["report_schema_version"].default
    )


def test_production_defaults_pin_the_exact_beta_contract() -> None:
    requirements = production_safety_requirements()
    counts = {
        (item.profile, item.expected_decision): item.count for item in requirements.required_strata
    }

    assert len(counts) == 28
    assert sum(counts.values()) == 100
    assert sum(count for (_, decision), count in counts.items() if decision == "passed") == 30
    assert sum(count for (_, decision), count in counts.items() if decision != "passed") == 70
    assert requirements.minimum_safe_passes == 27
    assert requirements.minimum_blocked_exact == 30
    assert requirements.minimum_review_exact == 19
    assert requirements.minimum_insufficient_evidence_exact == 19
    assert requirements.maximum_unsafe_auto_passes == 0
    assert requirements.minimum_qualified_origins == 40
    assert requirements.minimum_kappa == 0.80


def test_custom_fixture_qualifies_only_as_test_and_is_byte_deterministic(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)

    first = _run(paths)
    second = _run(paths)

    assert first.qualified is True
    assert first.production_qualified is False
    assert first.qualification_tier == "test"
    assert first.failures == []
    assert first.summary.cohen_kappa == 1.0
    assert first.summary.exact_count == 4
    assert first.summary.runtime_failure_count == 0
    assert first.static_only is True
    assert first.runtime_behavior_proven is False
    assert first.static_verdict_disclaimer == STATIC_VERDICT_DISCLAIMER
    assert len(first.confusion_matrices) == 2
    assert render_safety_qualification_json(first) == render_safety_qualification_json(second)


def test_cli_uses_unrelaxed_production_policy_and_emits_failed_artifact(
    tmp_path: Path,
) -> None:
    wheel, corpus, receipts, policy = _fixture(tmp_path)
    output = tmp_path / "safety-qualification.json"

    exit_code = main(
        [
            "--wheel",
            str(wheel),
            "--corpus",
            str(corpus),
            "--receipts",
            str(receipts),
            "--policy",
            str(policy),
            "--out",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert payload["qualification_tier"] == "beta"
    assert payload["qualified"] is False
    assert payload["production_qualified"] is False
    assert payload["failures"]


def test_human_adjudicated_non_safe_pass_fails_closed(tmp_path: Path) -> None:
    result = _run(
        _fixture(
            tmp_path,
            actual_overrides={"case-blocked": "passed"},
        )
    )
    codes = {failure.code for failure in result.failures}

    assert result.qualified is False
    assert result.production_qualified is False
    assert result.summary.unsafe_auto_pass_count == 1
    assert "unsafe_auto_pass" in codes
    assert "profile_unsafe_auto_pass" in codes
    assert "beta_threshold_failed" in codes


def test_missing_receipt_is_a_runtime_failure_not_fallback_scoring(tmp_path: Path) -> None:
    wheel, corpus, receipts, policy = _fixture(tmp_path)
    payload = json.loads(receipts.read_text(encoding="utf-8"))
    payload["receipts"] = [
        row for row in payload["receipts"] if row["case_id"] != "case-review_required"
    ]
    _write_json(receipts, payload)

    result = _run((wheel, corpus, receipts, policy))

    assert result.qualified is False
    assert result.summary.runtime_failure_count == 1
    assert result.summary.receipt_count == 3
    assert any(failure.code == "missing_receipt" for failure in result.failures)
    assert any(failure.code == "runtime_failure" for failure in result.failures)


def test_wheel_corpus_policy_binding_mismatch_prevents_qualification(tmp_path: Path) -> None:
    wheel, corpus, receipts, policy = _fixture(tmp_path)
    payload = json.loads(receipts.read_text(encoding="utf-8"))
    payload["wheel_sha256"] = "0" * 64
    _write_json(receipts, payload)

    result = _run((wheel, corpus, receipts, policy))

    assert result.qualified is False
    assert any(
        failure.code == "input_binding_mismatch" and "wheel_sha256" in failure.message
        for failure in result.failures
    )


def test_disagreement_requires_adjudication_but_low_kappa_still_fails(
    tmp_path: Path,
) -> None:
    result = _run(_fixture(tmp_path, disagreement_case="case-blocked"))

    assert result.summary.cohen_kappa < 0.80
    assert result.qualified is False
    assert any(failure.code == "label_agreement_below_threshold" for failure in result.failures)


def test_incomplete_or_mutated_labels_are_rejected_before_scoring(tmp_path: Path) -> None:
    _, corpus, _, _ = _fixture(tmp_path)
    payload = json.loads(corpus.read_text(encoding="utf-8"))
    del payload["cases"][0]["security_governance_label"]["rationale"]
    _write_json(corpus, payload)

    with pytest.raises(ConfigError, match="Invalid frozen safety corpus"):
        load_frozen_corpus(corpus)


def test_frozen_label_digest_detects_post_freeze_label_edits(tmp_path: Path) -> None:
    _, corpus, _, _ = _fixture(tmp_path)
    payload = json.loads(corpus.read_text(encoding="utf-8"))
    payload["cases"][0]["security_governance_label"]["rationale"] = (
        "Edited after labels were frozen."
    )
    _write_json(corpus, payload)

    with pytest.raises(ConfigError, match="frozen_labels_sha256"):
        load_frozen_corpus(corpus)


def test_blank_human_evidence_is_not_treated_as_a_complete_label() -> None:
    payload = _human_label("security_governance", "security-reviewer", "passed").model_dump(
        mode="json"
    )
    payload["rationale"] = "   "

    with pytest.raises(ValueError, match="must not be blank"):
        IndependentHumanLabelV1.model_validate(payload)


def test_primary_reviewers_must_be_independent_after_identity_normalization() -> None:
    with pytest.raises(ValueError, match="different reviewers"):
        SafetyCorpusCaseV1(
            id="same-reviewer",
            profile="mcp",
            origin="real_history",
            split="holdout",
            expected_decision="passed",
            evidence_references=("evidence/case.json",),
            remediation_condition="No remediation required.",
            security_governance_label=_human_label("security_governance", "Reviewer-One", "passed"),
            framework_tooling_label=_human_label("framework_tooling", " reviewer-one ", "passed"),
            adjudication=HumanAdjudicationV1(
                state="agreement",
                final_decision="passed",
                rationale="Agreed final label.",
                evidence_references=("evidence/case.json#adjudication",),
            ),
        )


def test_fallback_receipt_declaration_is_schema_invalid(tmp_path: Path) -> None:
    _, _, receipts, _ = _fixture(tmp_path)
    payload = json.loads(receipts.read_text(encoding="utf-8"))
    payload["receipts"][0]["fallback_used"] = True
    _write_json(receipts, payload)

    from scripts.run_safety_qualification import load_receipt_index

    with pytest.raises(ConfigError, match="Invalid safety receipt index"):
        load_receipt_index(receipts)
