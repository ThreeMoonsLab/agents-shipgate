"""Infrastructure-failure visibility tests.

Pins finding #3 from the second review: driver crashes and outer
``_run_one_cell`` exceptions must surface as visible scorecards with a
blocker, never as missing rows or quietly low scores.
"""
from __future__ import annotations

import shutil
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


def test_mark_infrastructure_failure_flips_headline(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path  # local alias for the existing body
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


def test_outer_exception_produces_visible_scorecard(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path
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


def test_infrastructure_failure_redacts_secrets_in_error_message(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path
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


def test_rescore_keeps_setup_time_infra_failure_with_no_redacted_dir(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path
    """Round-six regression: when a cell crashed at setup (so no redacted/
    was ever written), rescore must still emit a row showing the failure.
    Previously _rescore_cell returned None and the broken cell vanished from
    the rescored CSV — exactly the silent-pass mode round three was meant
    to prevent."""
    cell_dir = tmp_path / "openai-agents-sdk__00-no-hints__01-prepare-for-release__claude-code"
    cell_dir.mkdir(parents=True)
    # NB: no redacted/ — that's the setup-time-failure state.
    prior = cli_mod._infrastructure_failure_scorecard(
        cell=_cell(),
        run_id="prior-run",
        run_dir=tmp_path,
        error="WorkspaceError: archetype directory missing",
    )
    # The helper writes scorecard.json into the cell_dir it computes from
    # the Cell.cell_id, which matches what we set up here.
    sc_path = tmp_path / prior.cell_id / "scorecard.json"
    assert sc_path.is_file()

    new_sc = cli_mod._rescore_cell(tmp_path / prior.cell_id)
    assert new_sc is not None, "rescore must not drop setup-time infra failures"
    assert new_sc.headline_pass is False
    assert "infrastructure_failure" in {b.kind for b in new_sc.blockers}


def test_artifacts_dir_is_repo_relative_in_failure_scorecard(tmp_path: Path) -> None:
    """The CSV ``transcript_path`` column must be a repo-relative path under
    .agents-private/, not an absolute host path."""
    # Use a subdirectory under .agents-private/ in the actual repo so the
    # relativisation has a real anchor. tmp_path is outside the repo, so we
    # accept either repo-relative OR absolute fallback — the contract is
    # "use relative when possible".
    repo_relative_dir = (
        Path(cli_mod._repo_root()) / ".agents-private" / "adoption-sprint" / "review-test"
    )
    repo_relative_dir.mkdir(parents=True, exist_ok=True)
    try:
        sc = cli_mod._infrastructure_failure_scorecard(
            cell=_cell(),
            run_id="r",
            run_dir=repo_relative_dir,
            error="boom",
        )
        assert not sc.artifacts_dir.startswith("/"), (
            f"artifacts_dir must be repo-relative when artifacts live in the repo; "
            f"got {sc.artifacts_dir!r}"
        )
        assert ".agents-private/adoption-sprint" in sc.artifacts_dir
    finally:
        shutil.rmtree(repo_relative_dir, ignore_errors=True)


def test_rescore_preserves_infrastructure_failure(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path
    """A prior infrastructure_failure must survive rescoring — rescore
    re-runs behavioural detectors only and cannot retroactively heal a
    driver crash. This pins the round-five regression."""
    from harness.adoption.scorer.aggregate import write_scorecard_json
    from harness.adoption.scorer.schema import Blocker, CriterionResult

    cell_dir = tmp_path / "openai-agents-sdk__10-agents-md__01-prepare-for-release__codex"
    (cell_dir / "redacted").mkdir(parents=True)
    (cell_dir / "redacted" / "transcript.jsonl").write_text("", encoding="utf-8")
    (cell_dir / "redacted" / "commands.jsonl").write_text("", encoding="utf-8")
    (cell_dir / "redacted" / "file_ops.jsonl").write_text("", encoding="utf-8")
    (cell_dir / "redacted" / "summary.md").write_text("", encoding="utf-8")
    (cell_dir / "redacted" / "final.diff").write_text("", encoding="utf-8")

    prior = ScorecardV1(
        run_id="prior-run",
        cell_id=cell_dir.name,
        archetype="openai-agents-sdk",
        variant="10-agents-md",
        prompt_id="01-prepare-for-release",
        agent="codex",
        model="codex-v2-stub",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        duration_s=0.0,
        criteria={
            "infrastructure_failure": CriterionResult(
                status="fail",
                severity="blocker",
                signal="Codex driver is a v2 stub",
            ),
        },
        blockers=[
            Blocker(kind="infrastructure_failure", detail="Codex driver is a v2 stub"),
        ],
        rubric_score=0,
        headline_pass=False,
        driver_degraded=True,
        artifacts_dir=str(cell_dir),
    )
    write_scorecard_json(prior, cell_dir / "scorecard.json")

    new_sc = cli_mod._rescore_cell(cell_dir)
    assert new_sc is not None
    assert new_sc.headline_pass is False, "rescore must not heal a broken run"
    assert "infrastructure_failure" in {b.kind for b in new_sc.blockers}


def test_rescore_cell_replays_artifacts(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path
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


def test_unknown_model_refuses_to_start(repo_tmp_path: Path) -> None:
    """Pins round-fourteen finding P2.2: an unknown model name must fail
    preflight rather than letting the run proceed with (0,0) pricing
    (which would render the budget cap ineffective)."""
    from harness.adoption.drivers.base import DriverInputs
    from harness.adoption.drivers.claude_code import ClaudeCodeDriver
    from harness.adoption.observer.transcript import TranscriptWriter

    raw = repo_tmp_path / "raw"
    raw.mkdir()
    with TranscriptWriter(raw) as writer:
        result = ClaudeCodeDriver().run(
            DriverInputs(
                workspace=repo_tmp_path,
                prompt_text="(probe)",
                artifacts_dir=repo_tmp_path,
                cell_id="probe-unknown-model",
                agent_name="claude-code",
                model="claude-typo-9000",  # not in PRICE_TABLE_USD_PER_M
                budget_usd=10.0,
            ),
            writer,
        )
    assert result.error is not None
    assert "unknown model" in result.error.lower()
    assert result.degraded is True
    assert result.cost_usd_estimate == 0.0


def test_mark_infrastructure_failure_redacts_secrets(repo_tmp_path: Path) -> None:
    tmp_path = repo_tmp_path
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
