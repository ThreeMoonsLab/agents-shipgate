"""Mock-driver end-to-end smoke test.

Exercises the full pipeline (workspace → overlay → mock driver → observer →
redactor → scorer → CSV) against two canned fixtures: one that should pass
cleanly, one that trips multiple blockers. The fixtures live under
``tests/harness/fixtures/``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.adoption import context as ctx_mod
from harness.adoption import overlay as overlay_mod
from harness.adoption import workspace as ws_mod
from harness.adoption.drivers.base import DriverInputs
from harness.adoption.drivers.mock import MockDriver
from harness.adoption.matrix import Cell
from harness.adoption.observer.fs_snapshot import snapshot
from harness.adoption.observer.redact import default_config, redact_tree
from harness.adoption.observer.transcript import TranscriptWriter
from harness.adoption.scorer.rules import CellArtifacts, score_cell

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "harness" / "fixtures"


@pytest.fixture(autouse=True)
def _no_git_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ~/.gitconfig templates from polluting the per-cell workspace."""
    monkeypatch.setenv("GIT_TEMPLATE_DIR", "")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture()
def fake_archetype(tmp_path: Path) -> Path:
    """A tiny archetype with one OpenAPI spec — enough to satisfy the 40-overlay."""
    archetype = tmp_path / "archetype"
    archetype.mkdir()
    (archetype / "README.md").write_text("# fake-archetype\n", encoding="utf-8")
    (archetype / "specs").mkdir()
    (archetype / "specs" / "support-tools.openapi.yaml").write_text(
        "openapi: '3.0.3'\ninfo: {title: x, version: '1'}\npaths: {}\n",
        encoding="utf-8",
    )
    return archetype


def _run_cell(
    *,
    fixture_dir: Path,
    archetype_dir: Path,
    cell: Cell,
    run_dir: Path,
):
    artifacts_dir = run_dir / cell.cell_id
    ws = ws_mod.materialize(
        archetype_dir=archetype_dir,
        workspace_root=artifacts_dir,
        cell_id="workspace_root",
    )
    pre_snap = snapshot(ws.root)
    variant_dir = REPO_ROOT / "benchmark" / "setup-variants" / cell.variant
    overlay_mod.apply_overlay(
        variant_dir=variant_dir,
        workspace_root=ws.root,
        placeholders=ctx_mod.ARCHETYPE_CONTEXTS["openai-agents-sdk"].as_placeholder_map(),
    )
    ws_mod.commit_overlay(ws, message=f"overlay {cell.variant}")
    raw_dir = artifacts_dir / "raw"
    with TranscriptWriter(raw_dir) as writer:
        result = MockDriver(fixture_dir).run(
            DriverInputs(
                workspace=ws.root,
                prompt_text="(mock)",
                artifacts_dir=artifacts_dir,
                cell_id=cell.cell_id,
                agent_name="mock",
                model="mock",
            ),
            writer,
        )
    (raw_dir / "summary.md").write_text(result.summary_text, encoding="utf-8")
    (raw_dir / "final.diff").write_text(result.final_diff, encoding="utf-8")
    post_snap = snapshot(ws.root)
    redacted_dir = artifacts_dir / "redacted"
    redact_tree(raw_dir, redacted_dir, config=default_config())
    return score_cell(
        cell=cell,
        artifacts=CellArtifacts(
            cell=cell,
            artifacts_dir=artifacts_dir,
            redacted_dir=redacted_dir,
            pre_workspace_files=pre_snap.files,
            post_workspace_files=post_snap.files,
            fs_diff=pre_snap.diff(post_snap),
            workspace_dir=ws.root,
        ),
        started_at=result.started_at,
        ended_at=result.ended_at,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd_estimate=result.cost_usd_estimate,
        agent_version="mock",
        driver_degraded=result.degraded,
        run_id="test-smoke",
        artifacts_dir_rel=str(artifacts_dir),
    )


def test_mock_good_passes(tmp_path: Path, fake_archetype: Path) -> None:
    cell = Cell(
        archetype="openai-agents-sdk",
        variant="10-agents-md",
        negative_overlay=None,
        prompt="01-prepare-for-release",
        agent="mock",
        model="mock",
    )
    scorecard = _run_cell(
        fixture_dir=FIXTURES / "mock_run_good",
        archetype_dir=fake_archetype,
        cell=cell,
        run_dir=tmp_path / "runs",
    )
    assert scorecard.headline_pass, f"unexpected blockers: {[b.kind for b in scorecard.blockers]}"
    assert scorecard.rubric_score >= 70, f"rubric={scorecard.rubric_score}"


def test_mock_bad_trips_blockers(tmp_path: Path, fake_archetype: Path) -> None:
    cell = Cell(
        archetype="openai-agents-sdk",
        variant="00-no-hints",
        negative_overlay=None,
        prompt="01-prepare-for-release",
        agent="mock",
        model="mock",
    )
    scorecard = _run_cell(
        fixture_dir=FIXTURES / "mock_run_bad",
        archetype_dir=fake_archetype,
        cell=cell,
        run_dir=tmp_path / "runs",
    )
    blocker_kinds = {b.kind for b in scorecard.blockers}
    assert not scorecard.headline_pass
    assert "no_runtime_trace_synthesis" in blocker_kinds
    # Either respects_manual_review or no_broad_scope_expansion must fire on the bad fixture.
    assert blocker_kinds & {"respects_manual_review", "no_broad_scope_expansion"}
