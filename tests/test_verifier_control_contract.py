from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.verification_identity import (
    build_executor,
    build_unit_result,
    build_verification_plan,
)
from agents_shipgate.schemas.agent_control import CodingAgentCommandAction, HumanControlAction
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierDiffStatus,
    VerifierFixTask,
    VerifierRepair,
    map_merge_verdict,
)
from agents_shipgate.schemas.verify_run import VerifyRunOutcome, build_verify_run_artifact

ROOT = Path(__file__).resolve().parent.parent
AUTHORIZED_COMMAND = "git push origin HEAD:refs/heads/codex/human-authorization-state"


def _accepted_authorization() -> AuthorizationEvaluationV1:
    return AuthorizationEvaluationV1(
        status="accepted",
        authorization_id=f"sha256:{'1' * 64}",
        authorization_request_id=f"sha256:{'2' * 64}",
        trust_policy_id=f"sha256:{'3' * 64}",
        key_id=f"sha256:{'5' * 64}",
        provider="github",
        principal="github:user:reviewer",
        operation_id=f"sha256:{'4' * 64}",
        command=AUTHORIZED_COMMAND,
        issued_at="2026-07-18T12:00:00Z",
        expires_at="2026-07-18T12:15:00Z",
        reason_codes=[],
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


def _passed_verifier() -> VerifierArtifact:
    return VerifierArtifact(
        workspace="/tmp/repo",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        execution="succeeded",
        head_status="succeeded",
        release_decision=_release_decision("passed"),
        decision="passed",
        merge_verdict="mergeable",
        applicability="verified",
        can_merge_without_human=True,
        control=derive_agent_control(reason="Static verification passed."),
        authorization=AuthorizationEvaluationV1.not_requested(),
    )


def _authorized_verifier() -> VerifierArtifact:
    control = derive_agent_control(
        reason="Run only the externally authorized operation.",
        next_action=CodingAgentCommandAction(
            kind="repair",
            command=AUTHORIZED_COMMAND,
            why="Run only the externally authorized operation.",
        ),
        verify_required=True,
        allowed_next_commands=[AUTHORIZED_COMMAND],
    )
    return VerifierArtifact(
        workspace="/tmp/repo",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        execution="succeeded",
        head_status="succeeded",
        release_decision=_release_decision("review_required"),
        decision="review_required",
        merge_verdict="human_review_required",
        applicability="verified",
        can_merge_without_human=False,
        control=control,
        authorization=_accepted_authorization(),
        fix_task=None,
    )


def _passed_run(verifier: VerifierArtifact):
    plan = build_verification_plan(
        git_root=ROOT,
        input_root=ROOT,
        config_path=ROOT / "shipgate.yaml",
        config_logical_path="shipgate.yaml",
        base_ref=None,
        head_ref="HEAD",
        archived_head=True,
        repository_id="local:test",
        base_commit_sha=None,
        base_tree_sha=None,
        head_commit_sha="a" * 40,
        head_tree_sha="b" * 40,
        merge_base_sha=None,
        changed_files=[],
        diff_text="",
        baseline_path=None,
        diff_from_path=None,
        policy_pack_paths=[],
        evaluation_date="2026-07-13",
        options={"plugins_enabled": False},
        plugins_enabled=False,
    )
    unit = build_unit_result(plan=plan, normalized_ir={"test": "control-projection"})
    return build_verify_run_artifact(
        plan=plan,
        executor=build_executor(plan.engine),
        unit_result_ids=[unit.unit_result_id],
        outcome=VerifyRunOutcome(
            exit_code=0,
            base_status="not_requested",
            execution="succeeded",
            applicability="verified",
            decision="passed",
            merge_verdict="mergeable",
            can_merge_without_human=True,
            control=verifier.control,
        ),
        artifacts={},
    )


def test_control_is_byte_identical_across_verifier_run_and_handoff() -> None:
    verifier = _passed_verifier()
    run = _passed_run(verifier)
    handoff = build_agent_handoff(verifier=verifier, verify_run=run)

    expected = verifier.control.model_dump_json()
    assert run.outcome.control.model_dump_json() == expected
    assert handoff.control.model_dump_json() == expected


def test_handoff_rejects_tampered_current_verify_run_outcome() -> None:
    verifier = _passed_verifier()
    run = _passed_run(verifier).model_dump(mode="json")
    run["outcome"]["decision"] = "blocked"

    with pytest.raises(ValidationError):
        build_agent_handoff(verifier=verifier, verify_run=run)


@pytest.mark.parametrize(
    ("schema_path", "control_path"),
    [
        ("docs/verifier-schema.v0.7.json", ("control",)),
        ("docs/agent-handoff-schema.v6.json", ("control",)),
        ("docs/verify-run-schema.v3.json", ("outcome", "control")),
    ],
)
def test_generated_public_schemas_reject_contradictory_control(
    schema_path: str,
    control_path: tuple[str, ...],
) -> None:
    verifier = _passed_verifier()
    run = _passed_run(verifier)
    handoff = build_agent_handoff(verifier=verifier, verify_run=run)
    payload_by_schema = {
        "docs/verifier-schema.v0.7.json": verifier.model_dump(mode="json"),
        "docs/agent-handoff-schema.v6.json": handoff.model_dump(mode="json"),
        "docs/verify-run-schema.v3.json": run.model_dump(mode="json"),
    }
    payload = deepcopy(payload_by_schema[schema_path])
    control = payload
    for key in control_path:
        control = control[key]
    control["must_stop"] = True

    schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(payload))


@pytest.mark.parametrize(
    "schema_path",
    ["docs/verifier-schema.v0.7.json", "docs/agent-handoff-schema.v6.json"],
)
def test_generated_schemas_reject_accepted_authorization_on_passed_gate(
    schema_path: str,
) -> None:
    verifier = _passed_verifier()
    run = _passed_run(verifier)
    handoff = build_agent_handoff(verifier=verifier, verify_run=run)
    payload_by_schema = {
        "docs/verifier-schema.v0.7.json": verifier.model_dump(mode="json"),
        "docs/agent-handoff-schema.v6.json": handoff.model_dump(mode="json"),
    }
    payload = deepcopy(payload_by_schema[schema_path])
    payload["authorization"] = _accepted_authorization().model_dump(mode="json")

    schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(payload))


@pytest.mark.parametrize(
    "field",
    [
        "execution",
        "head_status",
        "release_decision",
        "decision",
        "merge_verdict",
        "applicability",
        "can_merge_without_human",
        "control",
        "fix_task",
    ],
)
def test_verifier_schema_requires_complete_authorized_projection(field: str) -> None:
    payload = _authorized_verifier().model_dump(mode="json")
    payload.pop(field)

    schema = json.loads((ROOT / "docs/verifier-schema.v0.7.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(payload))


@pytest.mark.parametrize(
    "decision",
    ["blocked", "review_required", "insufficient_evidence"],
)
def test_nonpassing_release_decision_cannot_claim_merge_authority(decision: str) -> None:
    why = f"Release decision is {decision}."
    human = derive_agent_control(
        reason=why,
        next_action=HumanControlAction(kind="review", why=why),
        human_review_required=True,
    )
    with pytest.raises(ValidationError):
        VerifierArtifact(
            workspace="/tmp/repo",
            diff_status=VerifierDiffStatus(),
            config="shipgate.yaml",
            execution="succeeded",
            head_status="succeeded",
            release_decision=_release_decision(decision),
            decision=decision,
            merge_verdict=map_merge_verdict(decision),
            applicability="verified",
            can_merge_without_human=True,
            control=human,
            authorization=AuthorizationEvaluationV1.not_requested(),
        )


@pytest.mark.parametrize("mismatch", ["human_control", "different_command"])
def test_accepted_authorization_rejects_control_mismatch(mismatch: str) -> None:
    why = "A human must review this release decision."
    if mismatch == "human_control":
        control = derive_agent_control(
            reason=why,
            next_action=HumanControlAction(kind="review", why=why),
            human_review_required=True,
            human_review_why=why,
            stop_reason=why,
        )
    else:
        different_command = "git push origin HEAD:refs/heads/a-different-branch"
        control = derive_agent_control(
            reason=why,
            next_action=CodingAgentCommandAction(
                kind="repair",
                command=different_command,
                why="Run only the separately authorized operation.",
            ),
            verify_required=True,
            allowed_next_commands=[different_command],
        )

    with pytest.raises(ValidationError):
        VerifierArtifact(
            workspace="/tmp/repo",
            diff_status=VerifierDiffStatus(),
            config="shipgate.yaml",
            execution="succeeded",
            head_status="succeeded",
            release_decision=_release_decision("review_required"),
            decision="review_required",
            merge_verdict="human_review_required",
            applicability="verified",
            can_merge_without_human=False,
            control=control,
            authorization=_accepted_authorization(),
            fix_task=None,
        )


@pytest.mark.parametrize(
    "release_patch",
    [
        {"blockers": [{"check_id": "SHIP-TEST"}]},
        {"review_items": [{"check_id": "SHIP-TEST"}]},
        {"evidence_coverage": {"evidence_gaps": [{"kind": "missing_evidence"}]}},
        {"evidence_coverage": {"human_review_recommended": True}},
    ],
)
def test_passed_with_contradictory_release_substrate_fails_closed(
    release_patch: dict[str, object],
) -> None:
    payload = _passed_verifier().model_dump(mode="json")
    payload["release_decision"].update(release_patch)
    with pytest.raises(ValidationError):
        VerifierArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(can_merge_without_human=False),
        lambda payload: payload["capability_review"].update(trust_root_touched=True),
        lambda payload: payload["capability_review"].update(policy_weakened=True),
        lambda payload: payload["release_decision"].update(
            blockers=[
                {
                    "id": "F1",
                    "check_id": "SHIP-TEST",
                    "title": "Contradictory blocker",
                    "severity": "critical",
                    "blocks_release": True,
                }
            ]
        ),
    ],
)
def test_passed_wrapper_contradictions_fail_pydantic_and_generated_schema(
    mutate,
) -> None:
    payload = _passed_verifier().model_dump(mode="json")
    mutate(payload)
    with pytest.raises(ValidationError):
        VerifierArtifact.model_validate(payload)
    schema = json.loads((ROOT / "docs/verifier-schema.v0.7.json").read_text())
    assert list(Draft202012Validator(schema).iter_errors(payload))


# --- Contract v20: a review route publishes evidence, it does not merge ----

_RERUN = "agents-shipgate verify --config shipgate.yaml --head HEAD --json"


def _human_fix_task() -> VerifierFixTask:
    return VerifierFixTask(
        actor="human",
        safe_to_attempt=False,
        instructions=["A reviewer must approve the new tool authority."],
        allowed_repairs=[
            VerifierRepair(
                id="review_capability_change",
                actor="human",
                kind="investigate",
                target="agents-shipgate-reports",
                reason="A capability change needs a reviewer.",
            )
        ],
        forbidden_repairs=[],
        forbidden_shortcuts=["Do not suppress the finding to pass."],
        verification_command=_RERUN,
    )


def _review_publishable_verifier(commands: list[str]) -> VerifierArtifact:
    why = "A reviewer must approve the new tool authority."
    return VerifierArtifact(
        workspace="/tmp/repo",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        execution="succeeded",
        head_status="succeeded",
        release_decision=_release_decision("review_required"),
        decision="review_required",
        merge_verdict="human_review_required",
        applicability="verified",
        can_merge_without_human=False,
        control=derive_agent_control(
            reason=why,
            next_action=HumanControlAction(kind="review", why=why),
            verify_required=True,
            human_review_required=True,
            publication_allowed=True,
            allowed_next_commands=commands,
            human_review_why=why,
        ),
        authorization=AuthorizationEvaluationV1.not_requested(),
        fix_task=_human_fix_task(),
    )


def test_review_publishable_verifier_denies_merge_and_authorizes_the_rerun() -> None:
    verifier = _review_publishable_verifier([_RERUN])

    assert verifier.control.state == "review_publishable"
    assert verifier.can_merge_without_human is False
    assert verifier.control.completion_allowed is False
    assert verifier.control.permissions.merge is False
    assert verifier.control.permissions.report_complete is False
    assert verifier.control.permissions.commit is True
    assert verifier.control.permissions.push is True
    assert verifier.control.permissions.update_pr is True
    assert verifier.control.allowed_next_commands == [_RERUN]
    assert verifier.control.next_action.actor == "human"

    schema = json.loads(
        (ROOT / "docs" / "verifier-schema.v0.7.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(verifier.model_dump(mode="json"))


def test_review_publishable_verifier_cannot_authorize_an_unrelated_command() -> None:
    """Publishing evidence is authority over the PR, never over Shipgate."""

    with pytest.raises(ValidationError):
        _review_publishable_verifier([AUTHORIZED_COMMAND])


@pytest.mark.parametrize(
    ("execution", "decision", "applicability", "verdict"),
    [
        ("failed", None, "failed", "unknown"),
        ("skipped", None, "not_applicable", "mergeable"),
    ],
)
def test_publication_requires_a_completed_release_decision(
    execution: str, decision: str | None, applicability: str, verdict: str
) -> None:
    """A run with no decision has nothing reviewable to publish.

    The control variant alone cannot see the substrate, so without this
    container invariant a hand-built artifact could pair `review_publishable`
    with a failed run and keep every publication permission.
    """

    why = "A reviewer must approve this."
    control = derive_agent_control(
        reason=why,
        next_action=HumanControlAction(kind="review", why=why),
        human_review_required=True,
        publication_allowed=True,
        human_review_why=why,
    )
    with pytest.raises(ValidationError):
        VerifierArtifact(
            workspace="/tmp/repo",
            diff_status=VerifierDiffStatus(),
            config="shipgate.yaml",
            execution=execution,  # type: ignore[arg-type]
            head_status=execution,  # type: ignore[arg-type]
            release_decision=None,
            decision=decision,
            merge_verdict=verdict,  # type: ignore[arg-type]
            applicability=applicability,  # type: ignore[arg-type]
            can_merge_without_human=execution == "skipped",
            control=control,
            authorization=AuthorizationEvaluationV1.not_requested(),
        )


def test_a_blocked_release_decision_cannot_authorize_publication() -> None:
    why = "A reviewer must approve this."
    control = derive_agent_control(
        reason=why,
        next_action=HumanControlAction(kind="review", why=why),
        human_review_required=True,
        publication_allowed=True,
        human_review_why=why,
    )
    with pytest.raises(ValidationError):
        VerifierArtifact(
            workspace="/tmp/repo",
            diff_status=VerifierDiffStatus(),
            config="shipgate.yaml",
            execution="succeeded",
            head_status="succeeded",
            release_decision=_release_decision("blocked"),
            decision="blocked",
            merge_verdict="blocked",
            applicability="verified",
            can_merge_without_human=False,
            control=control,
            authorization=AuthorizationEvaluationV1.not_requested(),
            fix_task=_human_fix_task(),
        )
