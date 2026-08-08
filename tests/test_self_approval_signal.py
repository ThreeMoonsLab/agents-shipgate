"""PR-D: the self-approval prohibition is surfaced at the top of verifier.json.

When a PR edits the rules that evaluate it — a weakened release policy or a
touched trust root — a coding agent must not silently self-approve (reward
hacking). That prohibition is the verifier headline and the human-review
reason, not buried in a fix_task instruction.
"""

from __future__ import annotations

import pytest

from agents_shipgate.cli.verify.orchestrator import (
    _can_merge_without_human,
    _derive_verifier_control,
    _self_approval_note,
    _verifier_headline,
)
from agents_shipgate.schemas.verifier import VerifierCapabilityReview, VerifierDiffStatus


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


# --- canonical control surfacing -------------------------------------------


def test_human_review_why_leads_with_self_approval_note() -> None:
    control = _derive_verifier_control(
        execution="failed",
        merge_verdict="unknown",
        release_decision=None,
        fix_task=None,
        capability_review=_cr(policy_weakened=True),
        headline=None,
        first_next_action_override=None,
        base_status="not_requested",
        base_ref=None,
        diff_status=VerifierDiffStatus(completeness="complete"),
    )
    assert control.state == "human_review_required"
    assert control.human_review.required is True
    assert "self-approve" in control.human_review.why


def test_self_approval_contradicting_mergeable_fails_closed() -> None:
    with pytest.raises(ValueError, match="trust root"):
        _can_merge_without_human(
            merge_verdict="mergeable",
            release_decision=None,
            capability_review=_cr(trust_root_touched=True),
        )


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


def test_self_approval_never_silently_reclassifies_merge_authority() -> None:
    with pytest.raises(ValueError, match="trust root"):
        _can_merge_without_human(
            merge_verdict="mergeable",
            release_decision=None,
            capability_review=_cr(policy_weakened=True),
        )


def test_clean_mergeable_still_merges_and_keeps_safe_action() -> None:
    # Regression: with no self-approval note, mergeable behaves as before.
    assert (
        _can_merge_without_human(
            merge_verdict="mergeable", release_decision=None, capability_review=_cr()
        )
        is True
    )
    control = _derive_verifier_control(
        execution="skipped",
        merge_verdict="mergeable",
        release_decision=None,
        fix_task=None,
        capability_review=_cr(),
        headline="No applicable capability change.",
        first_next_action_override=None,
        base_status="skipped",
        base_ref=None,
        diff_status=VerifierDiffStatus(completeness="complete"),
    )
    assert control.state == "complete"
    assert control.next_action is None
