"""PR-D: the self-approval prohibition is surfaced at the top of verifier.json.

When a PR edits the rules that evaluate it — a weakened release policy or a
touched trust root — a coding agent must not silently self-approve (reward
hacking). That prohibition is the verifier headline and the human-review
reason, not buried in a fix_task instruction.
"""

from __future__ import annotations

from agents_shipgate.cli.verify.orchestrator import (
    _can_merge_without_human,
    _first_next_action,
    _human_review,
    _self_approval_note,
    _verifier_headline,
)
from agents_shipgate.schemas.verifier import VerifierCapabilityReview


def _cr(**kwargs) -> VerifierCapabilityReview:
    return VerifierCapabilityReview(**kwargs)


# --- the note itself --------------------------------------------------------


def test_policy_weakened_note_calls_out_self_approval() -> None:
    note = _self_approval_note(_cr(policy_weakened=True))
    assert note is not None
    assert "self-approve" in note
    assert "policy" in note


def test_trust_root_note_calls_out_self_approval() -> None:
    note = _self_approval_note(_cr(trust_root_touched=True))
    assert note is not None
    assert "self-approve" in note
    assert "trust root" in note


def test_policy_weakening_takes_precedence_over_trust_root() -> None:
    note = _self_approval_note(_cr(policy_weakened=True, trust_root_touched=True))
    assert note is not None
    assert "policy" in note


def test_clean_review_has_no_note() -> None:
    assert _self_approval_note(_cr()) is None
    assert _self_approval_note(None) is None


# --- human_review surfacing -------------------------------------------------


def test_human_review_why_leads_with_self_approval_note() -> None:
    review = _human_review(
        merge_verdict="human_review_required",
        release_decision=None,
        capability_review=_cr(policy_weakened=True),
    )
    assert review.required is True
    assert review.why is not None
    assert "self-approve" in review.why


def test_self_approval_forces_human_review_even_if_verdict_not_human() -> None:
    # Defensive: a weakened policy must require a human even if some other path
    # produced a non-human verdict — the agent can never clear its own gate.
    review = _human_review(
        merge_verdict="mergeable",
        release_decision=None,
        capability_review=_cr(trust_root_touched=True),
    )
    assert review.required is True
    assert review.why is not None
    assert "self-approve" in review.why


# --- headline surfacing -----------------------------------------------------


def test_headline_leads_with_self_approval_note() -> None:
    headline = _verifier_headline(
        report=None,
        merge_verdict="human_review_required",
        head_status="succeeded",
        capability_review=_cr(trust_root_touched=True),
    )
    assert headline is not None
    assert "self-approve" in headline


def test_headline_without_note_falls_back_to_default() -> None:
    headline = _verifier_headline(
        report=None,
        merge_verdict="unknown",
        head_status="failed",
        capability_review=_cr(),
    )
    assert headline == "Shipgate could not complete the scan; human review required."


# --- convenience fields stay consistent in the defensive case ---------------


def test_self_approval_blocks_can_merge_without_human() -> None:
    assert (
        _can_merge_without_human(
            merge_verdict="mergeable",
            release_decision=None,
            capability_review=_cr(policy_weakened=True),
        )
        is False
    )


def test_self_approval_first_next_action_routes_to_human_when_mergeable() -> None:
    # The defensive path: a 'mergeable' verdict carrying a self-approval note
    # must not emit "safe to merge"; the next step is a human review.
    action = _first_next_action(
        merge_verdict="mergeable",
        fix_task=None,
        agent_summary=None,
        reason=None,
        capability_review=_cr(trust_root_touched=True),
    )
    assert action.actor == "human"
    assert action.kind == "review"
    assert "self-approve" in action.why


def test_clean_mergeable_still_merges_and_keeps_safe_action() -> None:
    # Regression: with no self-approval note, mergeable behaves as before.
    assert (
        _can_merge_without_human(
            merge_verdict="mergeable", release_decision=None, capability_review=_cr()
        )
        is True
    )
    action = _first_next_action(
        merge_verdict="mergeable",
        fix_task=None,
        agent_summary=None,
        reason=None,
        capability_review=_cr(),
    )
    assert action.actor == "coding_agent"
    assert action.kind == "none"
