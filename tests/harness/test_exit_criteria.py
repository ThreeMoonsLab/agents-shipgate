"""Exit-criteria aggregation tests.

Pins finding P1.2 from the fifth review: published exit criteria must
measure behavioural-agent uplift, not be inflated by cursor-static
configuration-only scores.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from harness.adoption.scorer.aggregate import check_exit_criteria
from harness.adoption.scorer.schema import ScorecardV1


def _sc(
    *,
    agent: str,
    variant: str,
    score: int = 0,
    headline_pass: bool = True,
    negative_overlay: str | None = None,
    prompt: str = "01-prepare-for-release",
) -> ScorecardV1:
    now = datetime.now(UTC)
    return ScorecardV1(
        run_id="r",
        cell_id=f"x__{variant}__{prompt}__{agent}",
        archetype="x",
        variant=variant,
        negative_overlay=negative_overlay,
        prompt_id=prompt,
        agent=agent,
        model=None,
        started_at=now,
        ended_at=now,
        duration_s=0.1,
        criteria={},
        blockers=[],
        rubric_score=score,
        headline_pass=headline_pass,
        artifacts_dir=str(Path("/tmp/x")),
    )


def test_cursor_static_does_not_inflate_no_hints_baseline() -> None:
    """A pure cursor-static run yields 100 on 00-no-hints because the rule is
    correctly absent — that score must NOT enter the Claude uplift metric."""
    scorecards = [
        # Claude shows the typical no-hints baseline.
        _sc(agent="claude-code", variant="00-no-hints", score=40, headline_pass=False),
        _sc(agent="claude-code", variant="10-agents-md", score=70),
        # Cursor static lints — would inflate things if filtered by variant only.
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
    ]
    report = check_exit_criteria(scorecards)
    # Claude uplift is 70 - 40 = 30, ≥ 25 → True.
    assert report.materially_outperforms_no_hints
    assert report.details["mean_score_00_no_hints"] == 40
    assert report.details["mean_score_10_agents_md"] == 70


def test_cursor_static_reported_in_details_only() -> None:
    scorecards = [
        _sc(agent="claude-code", variant="10-agents-md", score=80),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
    ]
    report = check_exit_criteria(scorecards)
    assert report.details["cursor_static_cells"] == 2
    assert report.details["behavioural_cells"] == 1
    assert report.details["cursor_static_pass_rate"] == 1.0


def test_docs_only_filter_excludes_cursor_static() -> None:
    """Cursor docs-only rows are configuration-only — they shouldn't enter
    the noisy-on-docs-only denominator."""
    scorecards = [
        # Claude docs-only that DID propose Shipgate (noisy). Use a passing
        # detector signal so the test focuses on the denominator, not the
        # numerator semantics.
        _sc(
            agent="claude-code",
            variant="00-no-hints",
            negative_overlay="60-docs-only-negative",
            prompt="04-docs-only-negative",
            score=20,
            headline_pass=False,
        ),
        # Cursor static docs-only — should NOT count toward the metric.
        _sc(
            agent="cursor-static",
            variant="30-cursor-rule",
            negative_overlay="60-docs-only-negative",
            prompt="04-docs-only-negative",
            score=100,
        ),
    ]
    report = check_exit_criteria(scorecards)
    assert report.details["docs_only_cells"] == 1


def test_empty_behavioural_set_is_not_a_pass() -> None:
    """A pure cursor-static run cannot satisfy the Claude-uplift criteria."""
    scorecards = [
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
    ]
    report = check_exit_criteria(scorecards)
    assert report.materially_outperforms_no_hints is False
    assert report.near_perfect_activation is False
