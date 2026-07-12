from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.verify_run import (
    VerifyRunArtifact,
    VerifyRunArtifactRef,
    VerifyRunInputs,
    VerifyRunOutcome,
    VerifyRunSubject,
    VerifyRunTool,
    build_verify_run_artifact,
)


def test_verify_run_id_excludes_outcome_and_artifact_hashes() -> None:
    tool = VerifyRunTool(version="test-version")
    subject = VerifyRunSubject(
        config="shipgate.yaml",
        base_ref="origin/main",
        head_ref="HEAD",
        base_tree_sha="base",
        head_tree_sha="head",
    )
    inputs = VerifyRunInputs(
        config_sha256="sha256:config",
        baseline_sha256=None,
        plugins_enabled=False,
        no_heuristics=False,
        ci_mode="advisory",
    )
    passed = build_verify_run_artifact(
        tool=tool,
        subject=subject,
        inputs=inputs,
        outcome=VerifyRunOutcome(
            exit_code=0,
            base_status="succeeded",
            execution="succeeded",
            applicability="verified",
            decision="passed",
            merge_verdict="mergeable",
            can_merge_without_human=True,
            control=derive_agent_control(reason="Static verification passed."),
        ),
        artifacts={
            "verifier_json": VerifyRunArtifactRef(
                path="agents-shipgate-reports/verifier.json",
                sha256="sha256:verifier-a",
            )
        },
    )
    blocked = build_verify_run_artifact(
        tool=tool,
        subject=subject,
        inputs=inputs,
        outcome=VerifyRunOutcome(
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
                    kind="stop",
                    why="A blocking policy condition requires a human.",
                ),
                human_review_required=True,
            ),
        ),
        artifacts={
            "verifier_json": VerifyRunArtifactRef(
                path="agents-shipgate-reports/verifier.json",
                sha256="sha256:verifier-b",
            ),
            "report_json": VerifyRunArtifactRef(
                path="agents-shipgate-reports/report.json",
                sha256="sha256:report-b",
            ),
        },
    )

    assert passed.run_id == blocked.run_id

    changed_inputs = build_verify_run_artifact(
        tool=tool,
        subject=subject,
        inputs=inputs.model_copy(update={"no_heuristics": True}),
        outcome=passed.outcome,
        artifacts=passed.artifacts,
    )
    assert changed_inputs.run_id != passed.run_id


def test_verify_run_rejects_run_id_that_does_not_match_identity() -> None:
    artifact = build_verify_run_artifact(
        tool=VerifyRunTool(version="test-version"),
        subject=VerifyRunSubject(config="shipgate.yaml"),
        inputs=VerifyRunInputs(config_sha256="sha256:config"),
        outcome=VerifyRunOutcome(
            exit_code=0,
            base_status="skipped",
            execution="skipped",
            applicability="not_applicable",
            decision=None,
            merge_verdict="mergeable",
            can_merge_without_human=True,
            control=derive_agent_control(
                reason="The deterministic trigger found no applicable changes."
            ),
        ),
        artifacts={},
    )
    payload = artifact.model_dump(mode="json")
    payload["run_id"] = "sha256:" + "0" * 64

    with pytest.raises(ValidationError):
        VerifyRunArtifact.model_validate(payload)


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
        )
