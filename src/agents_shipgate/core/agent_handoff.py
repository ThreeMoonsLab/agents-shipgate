from __future__ import annotations

from typing import Any

from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
from agents_shipgate.core.agent_controls import FORBIDDEN_SHORTCUTS
from agents_shipgate.schemas.agent_handoff import (
    AgentHandoffArtifact,
    AgentHandoffBlockedBy,
    AgentHandoffController,
    AgentHandoffGate,
    AgentHandoffRemediationStep,
    AgentHandoffReproducibility,
    AgentHandoffSubject,
)
from agents_shipgate.schemas.contract import CONTRACT_VERSION
from agents_shipgate.schemas.verifier import VerifierArtifact


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
    release_decision = _release_decision(verifier_payload, report_payload)
    operation = _operation(verifier_payload)
    gate = AgentHandoffGate(
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
        controller=_controller(verifier_payload, gate=gate, operation=operation),
        next_action=_dict_or_none(verifier_payload.get("first_next_action")),
        human_review=_dict_or_none(verifier_payload.get("human_review")),
        fix_task=_dict_or_none(verifier_payload.get("fix_task")),
        blocked_by=_blocked_by(release_decision),
        remediation_plan=_remediation_plan(verifier_payload.get("fix_task")),
        capability_review=_dict(verifier_payload.get("capability_review")),
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


def _controller(
    verifier: dict[str, Any],
    *,
    gate: AgentHandoffGate,
    operation: str,
) -> AgentHandoffController:
    controller = _dict(verifier.get("agent_controller"))
    if controller:
        return AgentHandoffController(
            completion_allowed=bool(controller.get("completion_allowed")),
            must_stop=bool(controller.get("must_stop")),
            stop_reason=_str_or_none(controller.get("stop_reason")),
            allowed_next_commands=_str_list(controller.get("allowed_next_commands")),
            forbidden_file_edits=_str_list(controller.get("forbidden_file_edits")),
            forbidden_actions=_str_list(controller.get("forbidden_actions")),
        )
    next_action = _dict(verifier.get("first_next_action"))
    allowed_next_commands = []
    command = next_action.get("command")
    if isinstance(command, str) and command:
        allowed_next_commands.append(command)
    return AgentHandoffController(
        completion_allowed=gate.can_merge_without_human,
        must_stop=False if operation == "verify_preview" else not gate.can_merge_without_human,
        stop_reason=None,
        allowed_next_commands=allowed_next_commands,
        # Standing negative affordance: the forbidden lists are present on EVERY
        # verdict (per the verifier/handoff contract), including the preview
        # operation where no agent_controller is computed. Emit the same
        # canonical deny-lists the verify controller uses so a preview handoff
        # never reads as "nothing is forbidden".
        forbidden_file_edits=list(PROTECTED_FILE_EDITS),
        forbidden_actions=list(FORBIDDEN_SHORTCUTS),
    )


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
    inputs = _dict(verify_run.get("inputs"))
    artifacts = _dict(verify_run.get("artifacts"))
    return AgentHandoffReproducibility(
        run_id=_str_or_none(verify_run.get("run_id")),
        config_sha256=_str_or_none(inputs.get("config_sha256")),
        baseline_sha256=_str_or_none(inputs.get("baseline_sha256")),
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
