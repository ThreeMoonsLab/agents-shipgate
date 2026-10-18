"""PR-A contract lock: one verdict engine, one drift-proof projection.

These tests are the structural guarantee behind the "one decision engine"
discipline. ``build_release_decision()`` computes the canonical
``ReleaseDecisionStatus``; everything else — the report summary blocks
(``AgentSummary`` / ``ReviewerSummary`` / ``VerifierSummary``), the
``ReleaseConsequence`` lens, and the agent-facing ``MergeVerdict`` in
verifier.json — must be a *projection* of it that cannot silently drift. If
any of these fail, two parts of the system can disagree about whether a PR
can merge, which is exactly the failure this product exists to prevent.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.capability_change import VerifierSummary, VerifierVerdict
from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.report import (
    AgentSummary,
    ReleaseConsequence,
    ReleaseDecision,
    ReviewerSummary,
)
from agents_shipgate.schemas.verification_identity import (
    VerificationArtifactManifest,
    VerificationArtifactRef,
    VerificationReceipt,
    content_id,
)
from agents_shipgate.schemas.verifier import (
    _DECISION_TO_VERDICT,
    MergeVerdict,
    VerifierArtifact,
    VerifierDiffStatus,
    applicability_for,
    map_merge_verdict,
    merge_verdict_for,
)
from agents_shipgate.schemas.verify_run import VerifyRunOutcome

CANONICAL = set(get_args(ReleaseDecisionStatus))
MERGE_VERDICTS = get_args(MergeVerdict)
REPO_ROOT = Path(__file__).resolve().parents[1]


# --- One vocabulary: every verdict surface shares the canonical enum --------


def test_canonical_vocabulary_is_the_expected_four() -> None:
    assert CANONICAL == {
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    }


@pytest.mark.parametrize(
    "model, field",
    [
        (ReleaseDecision, "decision"),
        (ReleaseConsequence, "decision"),
        (AgentSummary, "verdict"),
        (ReviewerSummary, "verdict"),
        (VerifierSummary, "verdict"),
    ],
)
def test_every_verdict_field_uses_the_canonical_enum(model, field) -> None:
    members = set(get_args(model.model_fields[field].annotation))
    assert members == CANONICAL, (
        f"{model.__name__}.{field} re-spells the verdict vocabulary instead "
        "of reusing ReleaseDecisionStatus; the enums can now drift apart."
    )


def test_verifier_verdict_alias_is_the_canonical_enum() -> None:
    assert set(get_args(VerifierVerdict)) == CANONICAL


# --- The projection is total and drift-proof --------------------------------


def test_projection_is_total_over_release_status() -> None:
    for status in get_args(ReleaseDecisionStatus):
        assert status in _DECISION_TO_VERDICT, (
            f"release status {status!r} has no explicit MergeVerdict mapping; "
            "add it to _DECISION_TO_VERDICT (do not rely on the fail-safe "
            "fallback for a known status)."
        )


def test_projection_table_is_pinned() -> None:
    # Pin the exact bridge so changing a mapping is a deliberate, reviewed edit.
    assert _DECISION_TO_VERDICT == {
        "passed": "mergeable",
        "review_required": "human_review_required",
        "insufficient_evidence": "insufficient_evidence",
        "blocked": "blocked",
    }


def test_map_merge_verdict_none_is_unknown() -> None:
    assert map_merge_verdict(None) == "unknown"


def test_map_merge_verdict_unknown_status_fails_safe_not_mergeable() -> None:
    # An out-of-contract decision string must never auto-pass.
    assert map_merge_verdict("definitely-not-a-status") == "human_review_required"


@pytest.mark.parametrize(
    "decision, head_status, expected",
    [
        ("passed", "succeeded", "mergeable"),
        ("review_required", "succeeded", "human_review_required"),
        ("insufficient_evidence", "succeeded", "insufficient_evidence"),
        ("blocked", "succeeded", "blocked"),
        (None, "skipped", "mergeable"),  # nothing to gate
        (None, "succeeded", "unknown"),  # ran but produced no decision
        (None, "failed", "unknown"),  # scan failed
    ],
)
def test_merge_verdict_for_matrix(decision, head_status, expected) -> None:
    assert merge_verdict_for(decision=decision, head_status=head_status) == expected


@pytest.mark.parametrize(
    "decision, head_status, expected",
    [
        ("passed", "succeeded", "verified"),
        ("review_required", "succeeded", "verified"),
        ("insufficient_evidence", "succeeded", "verified"),
        ("blocked", "succeeded", "verified"),
        (None, "skipped", "not_applicable"),  # nothing to gate
        (None, "succeeded", "not_evaluated"),  # ran but produced no decision
        (None, "failed", "failed"),  # scan failed
    ],
)
def test_applicability_for_matrix(decision, head_status, expected) -> None:
    assert applicability_for(decision=decision, head_status=head_status) == expected


def test_applicability_disambiguates_mergeable_skip() -> None:
    # The reason the field exists: a skipped head projects merge_verdict
    # "mergeable" but applicability "not_applicable" — never let an agent read
    # "Shipgate verified this is safe" off a run where Shipgate did not run.
    assert merge_verdict_for(decision=None, head_status="skipped") == "mergeable"
    assert applicability_for(decision=None, head_status="skipped") == "not_applicable"


# --- The artifact cannot disagree with its substrate (structural lock) ------


def _artifact(**overrides) -> VerifierArtifact:
    base: dict = {
        "workspace": "/tmp/w",
        "config": "shipgate.yaml",
        "head_status": "succeeded",
        "authorization": AuthorizationEvaluationV1.not_requested(),
        "diff_status": VerifierDiffStatus(),
    }
    base.update(overrides)
    return VerifierArtifact(**base)


def _release_decision(status: str) -> dict[str, object]:
    return {
        "decision": status,
        "reason": f"Release decision is {status}.",
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
            "would_fail_ci": False,
            "exit_code": 0,
        },
    }


@pytest.mark.parametrize("status", sorted(CANONICAL))
def test_consistent_artifact_is_accepted(status) -> None:
    control = (
        derive_agent_control(reason="Static verification passed.", subject_evaluated=True)
        if status == "passed"
        else derive_agent_control(
            reason=f"Release decision is {status}.",
            human_review_required=True,
        )
    )
    art = _artifact(
        execution="succeeded",
        release_decision=_release_decision(status),
        decision=status,
        merge_verdict=map_merge_verdict(status),
        applicability="verified",
        can_merge_without_human=status == "passed",
        control=control,
    )
    assert art.merge_verdict == map_merge_verdict(status)


def test_artifact_rejects_merge_verdict_inconsistent_with_decision() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            execution="succeeded",
            release_decision=_release_decision("blocked"),
            decision="blocked",
            merge_verdict="mergeable",  # lie: blocked must project to "blocked"
            applicability="verified",
            control=derive_agent_control(reason="Blocked.", human_review_required=True),
        )


def test_artifact_rejects_top_level_decision_mismatch() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            execution="succeeded",
            release_decision=_release_decision("passed"),
            decision="blocked",  # disagrees with the substrate
            merge_verdict="mergeable",
            applicability="verified",
            can_merge_without_human=True,
            control=derive_agent_control(reason="Static verification passed.", subject_evaluated=True),
            authorization=AuthorizationEvaluationV1.not_requested(),
        )


def test_artifact_without_release_decision_projects_from_execution() -> None:
    # preview / skipped / failed paths have no substrate to project.
    art = _artifact(
        execution="succeeded",
        release_decision=None,
        merge_verdict="unknown",
        control=derive_agent_control(reason="Verification failed.", human_review_required=True),
    )
    assert art.merge_verdict == "unknown"
    art2 = _artifact(
        release_decision=None,
        head_status="skipped",
        merge_verdict="mergeable",
        execution="skipped",
        applicability="not_applicable",
        can_merge_without_human=True,
        control=derive_agent_control(reason="No applicable changes.", subject_evaluated=True),
    )
    assert art2.merge_verdict == "mergeable"


def test_artifact_rejects_applicability_inconsistent_with_substrate() -> None:
    # A present release_decision means Shipgate was applicable; claiming
    # "not_applicable" is the exact lie this lock prevents.
    with pytest.raises(ValidationError):
        VerifierArtifact(
            workspace="/tmp/w",
            diff_status=VerifierDiffStatus(),
            config="shipgate.yaml",
            head_status="succeeded",
            execution="succeeded",
            release_decision=_release_decision("passed"),
            decision="passed",
            merge_verdict="mergeable",
            applicability="not_applicable",
            can_merge_without_human=True,
            control=derive_agent_control(reason="Static verification passed.", subject_evaluated=True),
        )


def test_artifact_model_validate_backfills_applicability_for_old_payloads() -> None:
    # An older verifier.json (schema 0.1) carries release_decision but no
    # applicability key. model_validate must round-trip it — backfilling
    # "verified" via the before-validator — instead of tripping the lock.
    art = VerifierArtifact.model_validate(
        {
            "verifier_schema_version": "0.2",
            "workspace": "/tmp/w",
            "config": "shipgate.yaml",
            "head_status": "succeeded",
            "release_decision": _release_decision("blocked"),
            "decision": "blocked",
            "merge_verdict": "blocked",
        }
    )
    assert art.applicability == "verified"
    # A skipped older artifact backfills "not_applicable" — never a bare
    # "mergeable" that an agent could read as "verified safe".
    skipped = VerifierArtifact.model_validate(
        {
            "verifier_schema_version": "0.2",
            "workspace": "/tmp/w",
            "config": "shipgate.yaml",
            "head_status": "skipped",
            "merge_verdict": "mergeable",
        }
    )
    assert skipped.applicability == "not_applicable"


# --- Every validator reads the one projection (#1029) -----------------------


def test_receipt_vocabulary_is_the_canonical_one() -> None:
    """The receipt spells its enums inline; they must stay the canonical ones.

    ``schemas.verifier`` imports ``verification_identity``, so the receipt
    cannot import ``MergeVerdict`` for its annotation. The published v1
    receipt schema is generated from these literals, so the order is pinned.
    """

    fields = VerificationReceipt.model_fields
    assert get_args(fields["merge_verdict"].annotation) == MERGE_VERDICTS
    (decision,) = (
        arg for arg in get_args(fields["decision"].annotation) if arg is not type(None)
    )
    assert set(get_args(decision)) == CANONICAL


def _control_for(status: str):
    if status == "passed":
        return derive_agent_control(reason="Static verification passed.", subject_evaluated=True)
    return derive_agent_control(
        reason=f"Release decision is {status}.", human_review_required=True
    )


def _verifier_artifact(status: str, merge_verdict: str) -> VerifierArtifact:
    return _artifact(
        execution="succeeded",
        release_decision=_release_decision(status),
        decision=status,
        merge_verdict=merge_verdict,
        applicability="verified",
        can_merge_without_human=status == "passed",
        control=_control_for(status),
    )


def _verify_run_outcome(status: str, merge_verdict: str) -> VerifyRunOutcome:
    return VerifyRunOutcome(
        exit_code=0,
        base_status="succeeded",
        execution="succeeded",
        applicability="verified",
        decision=status,
        merge_verdict=merge_verdict,
        can_merge_without_human=status == "passed",
        control=_control_for(status),
    )


def _receipt(status: str, merge_verdict: str) -> VerificationReceipt:
    """A receipt whose content ids all hold, so only the projection decides."""

    request_id = content_id("request")
    unit_result_ids = [content_id("unit")]
    can_merge = status == "passed"
    decision_id = content_id(
        {
            "request_id": request_id,
            "unit_result_ids": unit_result_ids,
            "decision": status,
            "merge_verdict": merge_verdict,
            "can_merge_without_human": can_merge,
        }
    )
    manifest_fields = {
        "request_id": request_id,
        "decision_id": decision_id,
        "artifacts": {
            "report": VerificationArtifactRef(
                path="report.json",
                sha256=content_id("report"),
                size_bytes=2,
                media_type="application/json",
            )
        },
    }
    manifest = VerificationArtifactManifest(
        artifact_set_id=content_id(
            VerificationArtifactManifest.model_construct(
                artifact_set_id="", **manifest_fields
            ).model_dump(mode="json", exclude={"artifact_set_id", "schema_version"})
        ),
        **manifest_fields,
    )
    fields = {
        "request_id": request_id,
        "subject_id": content_id("subject"),
        "input_set_id": content_id("inputs"),
        "engine_requirement_id": content_id("engine"),
        "executor_id": content_id("executor"),
        "decision_id": decision_id,
        "artifact_set_id": manifest.artifact_set_id,
        "unit_result_ids": unit_result_ids,
        "decision": status,
        "merge_verdict": merge_verdict,
        "can_merge_without_human": can_merge,
        "artifact_manifest": manifest,
    }
    receipt_id = content_id(
        VerificationReceipt.model_construct(receipt_id="", **fields).model_dump(
            mode="json",
            exclude={"receipt_id", "schema_version", "attempt_id"},
            exclude_none=False,
        )
    )
    return VerificationReceipt(receipt_id=receipt_id, **fields)


#: Every validator that checks a decision against its merge verdict, with the
#: error it raises when the two disagree.
VALIDATORS = {
    "verifier.json": (_verifier_artifact, "must be the projection"),
    "verify-run.json": (_verify_run_outcome, "merge verdict contradicts its decision"),
    "verification-receipt.json": (_receipt, "contradicts its release decision"),
}


@pytest.mark.parametrize("validator", sorted(VALIDATORS))
@pytest.mark.parametrize("status", sorted(CANONICAL))
def test_every_decision_status_runs_through_every_validator(validator, status) -> None:
    build, contradiction = VALIDATORS[validator]
    expected = map_merge_verdict(status)

    assert build(status, expected).merge_verdict == expected
    for other in MERGE_VERDICTS:
        if other == expected:
            continue
        with pytest.raises(ValidationError, match=contradiction):
            build(status, other)


@pytest.mark.parametrize("validator", sorted(VALIDATORS))
def test_a_changed_projection_moves_every_validator_with_it(monkeypatch, validator) -> None:
    """The failure #1029 describes, staged.

    Re-map ``insufficient_evidence`` in the one table. The verifier then emits
    the new verdict, so every validator must accept it and refuse the old one.
    A validator holding a copy of the table would refuse every receipt.
    """

    build, contradiction = VALIDATORS[validator]
    monkeypatch.setitem(_DECISION_TO_VERDICT, "insufficient_evidence", "human_review_required")

    accepted = build("insufficient_evidence", "human_review_required")
    assert accepted.merge_verdict == "human_review_required"
    with pytest.raises(ValidationError, match=contradiction):
        build("insufficient_evidence", "insufficient_evidence")


#: The one module allowed to state the decision -> merge verdict table.
_AUTHORITY = Path("src/agents_shipgate/schemas/verifier.py")
#: Trees that ship or run the engine. ``harness/adoption`` is left out on
#: purpose: its scorer is an independent oracle that never reads the
#: agents-shipgate schema (see the ``harness/adoption/scorer/rules.py`` header).
_SCANNED_TREES = (Path("src/agents_shipgate"), Path("scripts"))


def _names_a_merge_verdict(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in MERGE_VERDICTS
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_names_a_merge_verdict(element) for element in node.elts)
    return False


def _decision_to_verdict_literals(source: str) -> list[int]:
    """Lines of dict literals with a decision key whose value names a verdict."""

    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Dict)
        and any(
            isinstance(key, ast.Constant)
            and key.value in CANONICAL
            and _names_a_merge_verdict(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    ]


def test_the_copy_guard_recognises_both_shapes_of_copy() -> None:
    assert _decision_to_verdict_literals('{"passed": "mergeable"}') == [1]
    assert _decision_to_verdict_literals('{"blocked": ("blocked", False)}') == [1]
    assert _decision_to_verdict_literals('{"passed": "allow", "mergeable": 1}') == []


def test_no_dict_literal_outside_the_verifier_maps_a_decision_to_a_merge_verdict() -> None:
    """A copied table keeps validating the old mapping after the authority moves.

    Call ``map_merge_verdict`` or ``merge_verdict_for`` instead (#1029).
    """

    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line}"
        for tree in _SCANNED_TREES
        for path in sorted((REPO_ROOT / tree).rglob("*.py"))
        if path.relative_to(REPO_ROOT) != _AUTHORITY
        for line in _decision_to_verdict_literals(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    # The guard must still see the table it protects, or it has gone blind.
    assert _decision_to_verdict_literals((REPO_ROOT / _AUTHORITY).read_text(encoding="utf-8"))
