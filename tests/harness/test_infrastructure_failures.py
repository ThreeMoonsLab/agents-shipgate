"""Infrastructure-failure visibility tests.

Pins finding #3 from the second review: driver crashes and outer
``_run_one_cell`` exceptions must surface as visible scorecards with a
blocker, never as missing rows or quietly low scores.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from harness.adoption import cli as cli_mod
from harness.adoption.matrix import Cell
from harness.adoption.scorer.schema import ScorecardV1


def _cell() -> Cell:
    return Cell(
        archetype="openai-agents-sdk",
        variant="00-no-hints",
        negative_overlay=None,
        prompt="01-prepare-for-release",
        agent="claude-code",
        model="claude-opus-4-7",
    )


def test_mark_infrastructure_failure_flips_headline(tmp_path: Path) -> None:
    """An otherwise-passing scorecard becomes headline_pass=False after
    ``_mark_infrastructure_failure`` is applied."""
    sc = ScorecardV1(
        run_id="r",
        cell_id="c",
        archetype="openai-agents-sdk",
        variant="10-agents-md",
        prompt_id="01-prepare-for-release",
        agent="claude-code",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        duration_s=0.1,
        criteria={},
        blockers=[],
        rubric_score=100,
        headline_pass=True,
        artifacts_dir=str(tmp_path),
    )
    cli_mod._mark_infrastructure_failure(sc, "ANTHROPIC_API_KEY missing")
    assert sc.headline_pass is False
    assert sc.driver_degraded is True
    kinds = {b.kind for b in sc.blockers}
    assert "infrastructure_failure" in kinds
    assert "ANTHROPIC_API_KEY" in sc.criteria["infrastructure_failure"].signal


def test_outer_exception_produces_visible_scorecard(tmp_path: Path) -> None:
    """A cell that crashes before producing artifacts still appears in the
    CSV as a clear blocker row — never silently dropped."""
    sc = cli_mod._infrastructure_failure_scorecard(
        cell=_cell(),
        run_id="test-run",
        run_dir=tmp_path,
        error="WorkspaceError: missing archetype directory",
    )
    assert isinstance(sc, ScorecardV1)
    assert sc.headline_pass is False
    assert sc.rubric_score == 0
    assert "infrastructure_failure" in {b.kind for b in sc.blockers}
    # The scorecard JSON sidecar must also exist so post-hoc inspection works.
    assert (tmp_path / sc.cell_id / "scorecard.json").is_file()
