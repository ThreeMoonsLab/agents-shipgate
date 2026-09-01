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

from typing import get_args

import pytest
from pydantic import ValidationError

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.capability_change import VerifierSummary, VerifierVerdict
from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance
from agents_shipgate.schemas.report import (
    AgentSummary,
    ReleaseConsequence,
    ReleaseDecision,
    ReviewerSummary,
)
from agents_shipgate.schemas.verifier import (
    _DECISION_TO_VERDICT,
    VerifierArtifact,
    VerifierDiffStatus,
    applicability_for,
    map_merge_verdict,
    merge_verdict_for,
)

CANONICAL = set(get_args(ReleaseDecisionStatus))


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
        "manifest_provenance": ManifestProvenance.repository(),
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
        derive_agent_control(reason="Static verification passed.")
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
            control=derive_agent_control(reason="Static verification passed."),
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
        control=derive_agent_control(reason="No applicable changes."),
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
            manifest_provenance=ManifestProvenance.repository(),
            head_status="succeeded",
            execution="succeeded",
            release_decision=_release_decision("passed"),
            decision="passed",
            merge_verdict="mergeable",
            applicability="not_applicable",
            can_merge_without_human=True,
            control=derive_agent_control(reason="Static verification passed."),
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
