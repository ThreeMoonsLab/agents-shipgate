from __future__ import annotations

from typing import Any

from agents_shipgate.schemas.agent_handoff import (
    AgentHandoffArtifact,
    AgentHandoffBlockedBy,
    AgentHandoffGateV3,
    AgentHandoffRemediationStep,
    AgentHandoffReproducibility,
    AgentHandoffSubject,
)
from agents_shipgate.schemas.contract import CONTRACT_VERSION
from agents_shipgate.schemas.verifier import VerifierArtifact
from agents_shipgate.schemas.verify_run import VerifyRunArtifact


def build_agent_handoff(
    *,
    verifier: VerifierArtifact | dict[str, Any],
    report: Any | None = None,
    verify_run: Any | None = None,
) -> AgentHandoffArtifact:
    """Project verifier/report/verify-run artifacts into one agent handoff.

    The handoff is deliberately a projection. It never computes an independent
    release verdict; inconsistencies with the verifier substrate fail closed via
    ``VerifierArtifact`` and ``AgentHandoffArtifact`` validators.
    """

    verifier_payload = _model_payload(verifier)
    verifier_model = VerifierArtifact.model_validate(verifier_payload)
    verifier_payload = verifier_model.model_dump(mode="json")
    report_payload = _model_payload(report) if report is not None else {}
    verify_run_payload = _model_payload(verify_run) if verify_run is not None else {}
    verify_run_model = (
        VerifyRunArtifact.model_validate(verify_run_payload)
        if verify_run_payload.get("schema_version") == "shipgate.verify_run/v3"
        else None
    )
    if verify_run_model is not None:
        verify_run_payload = verify_run_model.model_dump(mode="json")
    verify_run_control = _dict(_dict(verify_run_payload.get("outcome")).get("control"))
    verifier_control = verifier_model.control.model_dump(mode="json")
    if verify_run_control and verify_run_control != verifier_control:
        raise ValueError(
            "verify-run outcome control and verifier control disagree; refusing "
            "to emit a trusted handoff"
        )
    if verify_run_model is not None:
        outcome = verify_run_model.outcome
        projections = {
            "execution": (outcome.execution, verifier_model.execution),
            "applicability": (outcome.applicability, verifier_model.applicability),
            "decision": (outcome.decision, verifier_model.decision),
            "merge_verdict": (outcome.merge_verdict, verifier_model.merge_verdict),
            "can_merge_without_human": (
                outcome.can_merge_without_human,
                verifier_model.can_merge_without_human,
            ),
            "base_status": (outcome.base_status, verifier_model.base_status),
        }
        mismatches = [
            name
            for name, (run_value, verifier_value) in projections.items()
            if run_value != verifier_value
        ]
        if mismatches:
            raise ValueError(
                "verify-run outcome and verifier disagree on "
                f"{', '.join(mismatches)}; refusing to emit a trusted handoff"
            )
    release_decision = _release_decision(verifier_payload, report_payload)
    operation = _operation(verifier_payload)
    gate = AgentHandoffGateV3(
        static_analysis_only=verifier_model.static_analysis_only,
        runtime_behavior_verified=verifier_model.runtime_behavior_verified,
        static_verdict_disclaimer=verifier_model.static_verdict_disclaimer,
        decision=release_decision.get("decision"),
        merge_verdict=verifier_model.merge_verdict,
        applicability=verifier_model.applicability,
        can_merge_without_human=verifier_model.can_merge_without_human,
        ci_would_fail=_ci_would_fail(release_decision),
    )
    return AgentHandoffArtifact(
        contract_version=CONTRACT_VERSION,
        operation=operation,
        subject=AgentHandoffSubject(
            workspace=verifier_model.workspace,
            config=verifier_model.config,
            base_ref=verifier_model.base_ref,
            head_ref=verifier_model.head_ref,
            changed_files=list(verifier_model.changed_files),
        ),
        gate=gate,
        control=verifier_model.control,
        authorization=verifier_model.authorization,
        fix_task=_dict_or_none(verifier_payload.get("fix_task")),
        blocked_by=_blocked_by(release_decision),
        remediation_plan=_remediation_plan(verifier_payload.get("fix_task")),
        capability_review=_dict(verifier_payload.get("capability_review")),
        forbidden_file_edits=list(verifier_model.forbidden_file_edits),
        forbidden_actions=list(verifier_model.forbidden_actions),
        reproducibility=_reproducibility(verify_run_payload),
        artifacts=_artifacts(verifier_payload),
    )


def _model_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _release_decision(
    verifier: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    verifier_decision = _dict(verifier.get("release_decision"))
    report_decision = _dict(report.get("release_decision"))
    if verifier_decision and report_decision:
        left = verifier_decision.get("decision")
        right = report_decision.get("decision")
        if left != right:
            raise ValueError(
                "verifier.release_decision.decision and "
                "report.release_decision.decision disagree "
                f"({left!r} != {right!r})."
            )
    return verifier_decision or report_decision


def _operation(verifier: dict[str, Any]) -> str:
    if verifier.get("mode") == "preview":
        return "verify_preview"
    if verifier.get("base_ref"):
        return "verify_pr"
    return "verify_local"


def _ci_would_fail(release_decision: dict[str, Any]) -> bool | None:
    fail_policy = _dict(release_decision.get("fail_policy"))
    value = fail_policy.get("would_fail_ci")
    return value if isinstance(value, bool) else None


def _blocked_by(release_decision: dict[str, Any]) -> list[AgentHandoffBlockedBy]:
    out: list[AgentHandoffBlockedBy] = []
    for bucket_name, field_name in (
        ("blocker", "blockers"),
        ("review_item", "review_items"),
    ):
        for item in release_decision.get(field_name) or []:
            if not isinstance(item, dict):
                continue
            out.append(
                AgentHandoffBlockedBy(
                    bucket=bucket_name,  # type: ignore[arg-type]
                    id=_str_or_none(item.get("id")),
                    fingerprint=_str_or_none(item.get("fingerprint")),
                    check_id=str(item.get("check_id") or "agents-shipgate"),
                    severity=str(item.get("severity") or "info"),
                    title=str(item.get("title") or item.get("check_id") or "Shipgate finding"),
                    baseline_status=_str_or_none(item.get("baseline_status")),
                    blocks_release=_bool_or_none(item.get("blocks_release")),
                    capability_refs=_str_list(item.get("capability_refs")),
                    capability_trace_refs=_str_list(item.get("capability_trace_refs")),
                    support_hash=_str_or_none(_dict(item.get("support")).get("support_hash")),
                )
            )
    return out


def _remediation_plan(fix_task_value: Any) -> list[AgentHandoffRemediationStep]:
    fix_task = _dict(fix_task_value)
    if not fix_task:
        return []
    steps: list[AgentHandoffRemediationStep] = []
    for item in fix_task.get("allowed_repairs") or []:
        if isinstance(item, dict):
            steps.append(_repair_step(item, safety="allowed"))
    for item in fix_task.get("forbidden_repairs") or []:
        if isinstance(item, dict):
            steps.append(_repair_step(item, safety="forbidden"))
    for item in fix_task.get("patches") or []:
        if not isinstance(item, dict):
            continue
        patch = _dict(item.get("patch"))
        steps.append(
            AgentHandoffRemediationStep(
                safety="patch",
                id=None,
                actor="coding_agent",
                kind=str(patch.get("kind") or "patch"),
                target=_patch_target(patch),
                finding_id=_str_or_none(item.get("finding_id")),
                check_id=_str_or_none(item.get("check_id")),
                command=fix_task.get("verification_command"),
                reason=str(patch.get("rationale") or "Machine-applicable patch from fix_task."),
                patch=patch,
            )
        )
    return steps


def _repair_step(item: dict[str, Any], *, safety: str) -> AgentHandoffRemediationStep:
    return AgentHandoffRemediationStep(
        safety=safety,  # type: ignore[arg-type]
        id=_str_or_none(item.get("id")),
        actor=_str_or_none(item.get("actor")),
        kind=str(item.get("kind") or safety),
        target=_str_or_none(item.get("target")),
        finding_id=_str_or_none(item.get("finding_id")),
        check_id=_str_or_none(item.get("check_id")),
        command=_str_or_none(item.get("command")),
        reason=str(item.get("reason") or ""),
    )


def _patch_target(patch: dict[str, Any]) -> str | None:
    for key in ("target_file", "path", "target"):
        value = patch.get(key)
        if value:
            return str(value)
    return None


def _reproducibility(verify_run: dict[str, Any]) -> AgentHandoffReproducibility:
    plan = _dict(verify_run.get("plan"))
    inputs = _dict(plan.get("inputs")) or _dict(verify_run.get("inputs"))
    subject = _dict(plan.get("subject"))
    engine = _dict(plan.get("engine"))
    executor = _dict(verify_run.get("executor"))
    artifacts = _dict(verify_run.get("artifacts"))
    return AgentHandoffReproducibility(
        run_id=_str_or_none(verify_run.get("run_id")),
        request_id=_str_or_none(verify_run.get("request_id")),
        subject_id=_str_or_none(subject.get("subject_id")),
        input_set_id=_str_or_none(inputs.get("input_set_id")),
        engine_requirement_id=_str_or_none(engine.get("engine_requirement_id")),
        executor_id=_str_or_none(executor.get("executor_id")),
        decision_id=_str_or_none(verify_run.get("decision_id")),
        config_sha256=_str_or_none(
            _dict(inputs.get("config")).get("sha256") or inputs.get("config_sha256")
        ),
        baseline_sha256=_str_or_none(
            _dict(inputs.get("baseline")).get("sha256") or inputs.get("baseline_sha256")
        ),
        policy_packs=[
            dict(item) for item in inputs.get("policy_packs") or [] if isinstance(item, dict)
        ],
        artifact_sha256={
            str(key): str(ref.get("sha256"))
            for key, ref in artifacts.items()
            if isinstance(ref, dict) and ref.get("sha256")
        },
    )


def _artifacts(verifier: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in _dict(verifier.get("artifacts")).items()
        if value is not None
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


__all__ = ["build_agent_handoff"]
