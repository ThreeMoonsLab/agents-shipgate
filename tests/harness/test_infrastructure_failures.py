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


def test_infrastructure_failure_redacts_secrets_in_error_message(tmp_path: Path) -> None:
    """A driver error containing an sk- token MUST appear as [REDACTED:...]
    in the scorecard — never as the raw secret. This pins the contract that
    scorecards are built from redacted inputs only, including the failure
    path."""
    leaked = "AuthenticationError: header bearer sk-test-1234567890abcdef00 invalid"
    sc = cli_mod._infrastructure_failure_scorecard(
        cell=_cell(),
        run_id="test-run",
        run_dir=tmp_path,
        error=leaked,
    )
    blob = sc.model_dump_json()
    assert "sk-test-1234567890abcdef00" not in blob, "raw API token leaked into scorecard"
    assert "[REDACTED:" in blob, "redaction marker missing"


def test_rescore_cell_replays_artifacts(tmp_path: Path) -> None:
    """The score subcommand's _rescore_cell helper must replay captured
    artifacts through the current scorer and produce a fresh scorecard."""
    from harness.adoption.scorer.aggregate import write_scorecard_json

    # Build a fake cell directory the way `run` would have left it.
    cell_dir = tmp_path / "openai-agents-sdk__10-agents-md__01-prepare-for-release__claude-code"
    (cell_dir / "redacted").mkdir(parents=True)
    (cell_dir / "redacted" / "transcript.jsonl").write_text(
        '{"text": "ok"}\n', encoding="utf-8"
    )
    (cell_dir / "redacted" / "commands.jsonl").write_text("", encoding="utf-8")
    (cell_dir / "redacted" / "file_ops.jsonl").write_text("", encoding="utf-8")
    (cell_dir / "redacted" / "summary.md").write_text("done", encoding="utf-8")
    (cell_dir / "redacted" / "final.diff").write_text("", encoding="utf-8")

    prior = ScorecardV1(
        run_id="prior-run",
        cell_id=cell_dir.name,
        archetype="openai-agents-sdk",
        variant="10-agents-md",
        prompt_id="01-prepare-for-release",
        agent="claude-code",
        model="claude-opus-4-7",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        duration_s=1.0,
        criteria={},
        blockers=[],
        rubric_score=72,
        headline_pass=True,
        artifacts_dir=str(cell_dir),
    )
    write_scorecard_json(prior, cell_dir / "scorecard.json")

    new_sc = cli_mod._rescore_cell(cell_dir)
    assert new_sc is not None
    assert new_sc.archetype == "openai-agents-sdk"
    assert new_sc.variant == "10-agents-md"
    assert new_sc.prompt_id == "01-prepare-for-release"
    # The rescore produces a scorecard with fresh detector results.
    assert new_sc.criteria, "rescored scorecard should populate criteria"


def test_mark_infrastructure_failure_redacts_secrets(tmp_path: Path) -> None:
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
    cli_mod._mark_infrastructure_failure(
        sc,
        "RateLimitError: bearer sk-proj-leaktest1234567890abc returned 429",
    )
    blob = sc.model_dump_json()
    assert "sk-proj-leaktest1234567890abc" not in blob
    assert "[REDACTED:" in blob
