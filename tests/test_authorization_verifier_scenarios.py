from __future__ import annotations

from typing import Literal

import pytest

from agents_shipgate.cli.verify.orchestrator import _apply_authorization_overlay
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierDiffStatus,
    VerifierFixTask,
    map_merge_verdict,
)

AUTHORIZED_COMMAND = (
    "git push --force-with-lease=refs/heads/codex/human-authorization-state:"
    + "a" * 40
    + " origin HEAD:refs/heads/codex/human-authorization-state"
)


def _content_id(character: str) -> str:
    return f"sha256:{character * 64}"


def _accepted_authorization(*, command: str = AUTHORIZED_COMMAND) -> AuthorizationEvaluationV1:
    return AuthorizationEvaluationV1(
        status="accepted",
        authorization_id=_content_id("1"),
        authorization_request_id=_content_id("2"),
        trust_policy_id=_content_id("3"),
        key_id=_content_id("5"),
        provider="github",
        principal="github:user:reviewer",
        operation_id=_content_id("4"),
        command=command,
        issued_at="2026-07-18T12:00:00Z",
        expires_at="2026-07-18T12:15:00Z",
        reason_codes=[],
    )


def _rejected_authorization(*, reason: str) -> AuthorizationEvaluationV1:
    return AuthorizationEvaluationV1(
        status="rejected",
        authorization_id=_content_id("1"),
        authorization_request_id=_content_id("2"),
        trust_policy_id=_content_id("3"),
        key_id=_content_id("5"),
        provider="github",
        principal="github:user:reviewer",
        operation_id=_content_id("4"),
        command=None,
        issued_at="2026-07-18T12:00:00Z",
        expires_at="2026-07-18T12:15:00Z",
        reason_codes=[reason],
    )


def _release_decision(decision: str) -> dict[str, object]:
    return {
        "decision": decision,
        "reason": f"Release decision is {decision}.",
        "blockers": [],
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
            "ci_mode": "advisory",
            "fail_on": ["critical", "high"],
            "new_findings_only": False,
            "would_fail_ci": False,
            "exit_code": 0,
        },
        "static_analysis_only": True,
        "runtime_behavior_verified": False,
        "static_verdict_disclaimer": STATIC_VERDICT_DISCLAIMER,
    }


def _human_control(*, reason: str, blocked: bool = False):
    return derive_agent_control(
        reason=reason,
        next_action=HumanControlAction(
            kind="stop" if blocked else "review",
            why=reason,
        ),
        verify_required=True,
        human_review_required=True,
        unsafe_block=blocked,
        human_review_why=reason,
        stop_reason=reason,
    )


def _verifier(
    decision: Literal["blocked", "review_required", "insufficient_evidence"] | None,
    *,
    include_human_fix_task: bool = False,
) -> VerifierArtifact:
    if decision is None:
        reason = "Shipgate could not complete verification."
        return VerifierArtifact(
            workspace="/tmp/repo",
            diff_status=VerifierDiffStatus(),
            config="shipgate.yaml",
            execution="failed",
            head_status="failed",
            release_decision=None,
            decision=None,
            merge_verdict="unknown",
            applicability="failed",
            can_merge_without_human=False,
            control=_human_control(reason=reason),
            authorization=AuthorizationEvaluationV1.not_requested(),
        )

    reason = f"Release decision is {decision}."
    return VerifierArtifact(
        workspace="/tmp/repo",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        execution="succeeded",
        head_status="succeeded",
        release_decision=_release_decision(decision),
        decision=decision,
        merge_verdict=map_merge_verdict(decision),
        applicability="verified",
        can_merge_without_human=False,
        control=_human_control(reason=reason, blocked=decision == "blocked"),
        authorization=AuthorizationEvaluationV1.not_requested(),
        fix_task=(
            VerifierFixTask(
                actor="human",
                safe_to_attempt=False,
                instructions=["Review and authorize the exact requested operation."],
            )
            if include_human_fix_task
            else None
        ),
    )


def test_no_authorization_preserves_human_stop() -> None:
    verifier = _verifier("review_required", include_human_fix_task=True)
    before_control = verifier.control.model_dump_json()

    _apply_authorization_overlay(verifier, AuthorizationEvaluationV1.not_requested())

    assert verifier.authorization.status == "not_requested"
    assert verifier.control.model_dump_json() == before_control
    assert verifier.control.state == "human_review_required"
    assert verifier.control.must_stop is True
    assert verifier.control.completion_allowed is False
    assert verifier.control.allowed_next_commands == []
    assert verifier.fix_task is not None


def test_not_applicable_authorization_preserves_human_stop() -> None:
    verifier = _verifier("review_required", include_human_fix_task=True)
    before_control = verifier.control.model_dump_json()

    _apply_authorization_overlay(
        verifier,
        AuthorizationEvaluationV1.not_applicable("authorization_input_not_supplied"),
    )

    assert verifier.authorization.status == "not_applicable"
    assert verifier.control.model_dump_json() == before_control
    assert verifier.control.state == "human_review_required"
    assert verifier.control.allowed_next_commands == []
    assert verifier.fix_task is not None


def test_accepted_exact_git_push_authorizes_only_that_operation() -> None:
    verifier = _verifier("review_required", include_human_fix_task=True)
    authorization = _accepted_authorization()
    release_before = verifier.release_decision.model_dump_json()

    _apply_authorization_overlay(verifier, authorization)

    assert verifier.authorization.model_dump_json() == authorization.model_dump_json()
    assert verifier.release_decision.model_dump_json() == release_before
    assert verifier.decision == "review_required"
    assert verifier.merge_verdict == "human_review_required"
    assert verifier.can_merge_without_human is False
    assert verifier.control.state == "agent_action_required"
    assert verifier.control.completion_allowed is False
    assert verifier.control.must_stop is False
    assert verifier.control.next_action.actor == "coding_agent"
    assert verifier.control.next_action.kind == "repair"
    assert verifier.control.next_action.command == AUTHORIZED_COMMAND
    assert verifier.control.allowed_next_commands == [AUTHORIZED_COMMAND]
    assert verifier.fix_task is None
    VerifierArtifact.model_validate(verifier.model_dump(mode="json"))


@pytest.mark.parametrize(
    "decision",
    ["blocked", "insufficient_evidence", None],
    ids=["blocked", "insufficient-evidence", "unknown"],
)
def test_incompatible_accepted_authorization_fails_atomically(
    decision: Literal["blocked", "insufficient_evidence"] | None,
) -> None:
    verifier = _verifier(decision)
    before = verifier.model_dump(mode="json")

    with pytest.raises(ValueError):
        _apply_authorization_overlay(verifier, _accepted_authorization())

    assert verifier.model_dump(mode="json") == before
    assert verifier.control.state == "human_review_required"
    assert verifier.control.allowed_next_commands == []


def test_rejected_review_scope_mismatch_never_authorizes() -> None:
    verifier = _verifier("review_required", include_human_fix_task=True)
    before_control = verifier.control.model_dump_json()
    before_fix_task = verifier.fix_task.model_dump_json()
    evaluation = _rejected_authorization(reason="authorization_request_id_mismatch")

    _apply_authorization_overlay(verifier, evaluation)

    assert verifier.authorization.model_dump_json() == evaluation.model_dump_json()
    assert verifier.control.model_dump_json() == before_control
    assert verifier.control.state == "human_review_required"
    assert verifier.control.allowed_next_commands == []
    assert verifier.fix_task is not None
    assert verifier.fix_task.model_dump_json() == before_fix_task
