"""Contract tests for the verifier.json ``agent_controller`` projection.

The agent_controller block is the imperative restatement of the verdict an
autonomous coding agent must act on. These tests pin that it stays a pure
projection (no second verdict), that its negative affordances are standing and
correctly scoped (deny-list, not allow-list; key-level surfaces excluded), and
that its stop-reason routing matches the verdict.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_shipgate.cli.verify.agent_controller import (
    PROTECTED_FILE_EDITS,
    build_agent_controller,
)
from agents_shipgate.cli.verify.fix_task import FORBIDDEN_SHORTCUTS
from agents_shipgate.schemas.verifier import (
    AgentController,
    VerifierArtifact,
    VerifierCapabilityReview,
    VerifierFixTask,
    VerifierHumanReview,
)

VERIFY_CMD = "agents-shipgate verify --base origin/main --head HEAD --json"


def _human_fix(**overrides) -> VerifierFixTask:
    base = dict(actor="human", safe_to_attempt=False, verification_command=VERIFY_CMD)
    base.update(overrides)
    return VerifierFixTask(**base)


def _agent_fix(**overrides) -> VerifierFixTask:
    base = dict(actor="coding_agent", safe_to_attempt=True, verification_command=VERIFY_CMD)
    base.update(overrides)
    return VerifierFixTask(**base)


# --- completion / stop routing ---------------------------------------------


def test_mergeable_allows_completion_and_does_not_stop() -> None:
    ac = build_agent_controller(
        merge_verdict="mergeable",
        can_merge_without_human=True,
        fix_task=None,
        capability_review=None,
        human_review=None,
        headline="No agent-capability changes gate this PR; safe to merge.",
    )
    assert ac.completion_allowed is True
    assert ac.must_stop is False
    assert ac.stop_reason is None
    assert ac.allowed_next_commands == []


def test_blocked_authority_gap_stops_for_human() -> None:
    ac = build_agent_controller(
        merge_verdict="blocked",
        can_merge_without_human=False,
        fix_task=_human_fix(),
        capability_review=VerifierCapabilityReview(),
        human_review=VerifierHumanReview(required=True, why="4 active findings block release."),
        headline="4 active findings block release.",
    )
    assert ac.completion_allowed is False
    assert ac.must_stop is True
    assert ac.stop_reason == "blocked_findings"
    assert ac.allowed_next_commands == []  # no agent-safe move; a human must act


def test_blocked_mechanical_repairs_without_stopping() -> None:
    # Every gating gap is mechanical -> the agent may fix and re-verify; not a stop.
    ac = build_agent_controller(
        merge_verdict="blocked",
        can_merge_without_human=False,
        fix_task=_agent_fix(),
        capability_review=VerifierCapabilityReview(),
        human_review=None,
        headline="2 mechanical gaps.",
    )
    assert ac.completion_allowed is False
    assert ac.must_stop is False
    assert ac.stop_reason is None
    assert ac.allowed_next_commands == [VERIFY_CMD]  # apply fix, then re-verify


@pytest.mark.parametrize(
    "merge_verdict, expected_reason",
    [
        ("blocked", "blocked_findings"),
        ("insufficient_evidence", "insufficient_evidence"),
        ("human_review_required", "human_review_required"),
        ("unknown", "scan_incomplete"),
    ],
)
def test_stop_reason_tracks_verdict(merge_verdict, expected_reason) -> None:
    ac = build_agent_controller(
        merge_verdict=merge_verdict,
        can_merge_without_human=False,
        fix_task=_human_fix(),
        capability_review=VerifierCapabilityReview(),
        human_review=None,
        headline=None,
    )
    assert ac.must_stop is True
    assert ac.stop_reason == expected_reason


@pytest.mark.parametrize("weak_field", ["trust_root_touched", "policy_weakened"])
def test_self_approval_takes_precedence_over_verdict(weak_field) -> None:
    # Editing the rules that judge the change wins the stop_reason, even when
    # the verdict would otherwise read as a plain human review.
    ac = build_agent_controller(
        merge_verdict="human_review_required",
        can_merge_without_human=False,
        fix_task=_human_fix(),
        capability_review=VerifierCapabilityReview(**{weak_field: True}),
        human_review=None,
        headline=None,
    )
    assert ac.stop_reason == "self_approval_prohibited"


# --- standing negative affordance (deny-list, not allow-list) ---------------


def test_forbidden_actions_are_standing_even_when_mergeable() -> None:
    # A passing run must still carry the "do not" list — green is never
    # "anything goes".
    ac = build_agent_controller(
        merge_verdict="mergeable",
        can_merge_without_human=True,
        fix_task=None,
        capability_review=None,
        human_review=None,
        headline=None,
    )
    assert ac.forbidden_actions == list(FORBIDDEN_SHORTCUTS)
    assert ac.forbidden_actions  # non-empty
    assert ac.forbidden_file_edits == list(PROTECTED_FILE_EDITS)


def test_forbidden_file_edits_is_a_scoped_deny_list() -> None:
    joined = "\n".join(PROTECTED_FILE_EDITS)
    # Whole-file trust roots that judge the change ARE denied.
    assert any("AGENTS.md" in p for p in PROTECTED_FILE_EDITS)
    assert any("CLAUDE.md" in p for p in PROTECTED_FILE_EDITS)
    assert any("agents-shipgate.yml" in p for p in PROTECTED_FILE_EDITS)
    assert any("policies/" in p for p in PROTECTED_FILE_EDITS)
    # Key-level surfaces are NOT path-denied (covered by forbidden_actions):
    # editing a scope field in shipgate.yaml is a legitimate mechanical fix.
    # (Match the manifest precisely — the CI pattern legitimately ends in
    # "agents-shipgate.yaml", which contains the substring "shipgate.yaml".)
    assert "**/shipgate.yaml" not in PROTECTED_FILE_EDITS
    assert not any(p.endswith("/shipgate.yaml") for p in PROTECTED_FILE_EDITS)
    assert ".agents-shipgate" not in joined
    # The capability surface UNDER review is not denied — a PR may edit it.
    assert ".mcp.json" not in joined
    assert "SKILL.md" not in joined
    assert ".codex-plugin" not in joined


# --- one decision engine: the controller cannot disagree with the gate ------


def test_artifact_locks_completion_allowed_to_can_merge() -> None:
    with pytest.raises(ValidationError):
        VerifierArtifact(
            workspace="/tmp/w",
            config="shipgate.yaml",
            head_status="succeeded",
            can_merge_without_human=False,
            agent_controller=AgentController(completion_allowed=True),  # lie
        )


def test_artifact_accepts_consistent_controller() -> None:
    art = VerifierArtifact(
        workspace="/tmp/w",
        config="shipgate.yaml",
        head_status="succeeded",
        can_merge_without_human=True,
        agent_controller=AgentController(completion_allowed=True, must_stop=False),
    )
    assert art.agent_controller is not None
    assert art.agent_controller.completion_allowed is True
