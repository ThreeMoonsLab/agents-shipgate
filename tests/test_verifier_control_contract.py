from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.verifier import VerifierArtifact, map_merge_verdict
from agents_shipgate.schemas.verify_run import (
    VerifyRunInputs,
    VerifyRunOutcome,
    VerifyRunSubject,
    build_verify_run_artifact,
)

ROOT = Path(__file__).resolve().parent.parent


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
        config="shipgate.yaml",
        execution="succeeded",
        head_status="succeeded",
        release_decision=_release_decision("passed"),
        decision="passed",
        merge_verdict="mergeable",
        applicability="verified",
        can_merge_without_human=True,
        control=derive_agent_control(reason="Static verification passed."),
    )


def test_control_is_byte_identical_across_verifier_run_and_handoff() -> None:
    verifier = _passed_verifier()
    run = build_verify_run_artifact(
        subject=VerifyRunSubject(config="shipgate.yaml"),
        inputs=VerifyRunInputs(config_sha256="sha256:config"),
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
    handoff = build_agent_handoff(verifier=verifier, verify_run=run)

    expected = verifier.control.model_dump_json()
    assert run.outcome.control.model_dump_json() == expected
    assert handoff.control.model_dump_json() == expected


def test_handoff_rejects_tampered_current_verify_run_outcome() -> None:
    verifier = _passed_verifier()
    run = build_verify_run_artifact(
        subject=VerifyRunSubject(config="shipgate.yaml"),
        inputs=VerifyRunInputs(config_sha256="sha256:config"),
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
    ).model_dump(mode="json")
    run["outcome"]["decision"] = "blocked"

    with pytest.raises(ValidationError):
        build_agent_handoff(verifier=verifier, verify_run=run)


@pytest.mark.parametrize(
    ("schema_path", "control_path"),
    [
        ("docs/verifier-schema.v0.3.json", ("control",)),
        ("docs/agent-handoff-schema.v3.json", ("control",)),
        ("docs/verify-run-schema.v2.json", ("outcome", "control")),
    ],
)
def test_generated_public_schemas_reject_contradictory_control(
    schema_path: str,
    control_path: tuple[str, ...],
) -> None:
    verifier = _passed_verifier()
    run = build_verify_run_artifact(
        subject=VerifyRunSubject(config="shipgate.yaml"),
        inputs=VerifyRunInputs(config_sha256="sha256:config"),
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
    handoff = build_agent_handoff(verifier=verifier, verify_run=run)
    payload_by_schema = {
        "docs/verifier-schema.v0.3.json": verifier.model_dump(mode="json"),
        "docs/agent-handoff-schema.v3.json": handoff.model_dump(mode="json"),
        "docs/verify-run-schema.v2.json": run.model_dump(mode="json"),
    }
    payload = deepcopy(payload_by_schema[schema_path])
    control = payload
    for key in control_path:
        control = control[key]
    control["must_stop"] = True

    schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
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
            config="shipgate.yaml",
            execution="succeeded",
            head_status="succeeded",
            release_decision=_release_decision(decision),
            decision=decision,
            merge_verdict=map_merge_verdict(decision),
            applicability="verified",
            can_merge_without_human=True,
            control=human,
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
    schema = json.loads((ROOT / "docs/verifier-schema.v0.3.json").read_text())
    assert list(Draft202012Validator(schema).iter_errors(payload))
