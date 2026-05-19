"""Typer CLI for the adoption harness.

Subcommands:

* ``sync-fixtures`` — materialize ``benchmark/repos/*`` from in-repo sources.
* ``run`` — execute the full pipeline against ``benchmark/matrix.yaml`` and
  write per-cell scorecards + a CSV row to ``benchmark/results/<run-id>.csv``.
* ``smoke`` — run a small set of mock-driver cells end-to-end without any
  live API calls. Used by PR CI.
* ``score`` — re-score an existing run's raw artifacts (useful for iterating
  on detectors without rerunning agents).
* ``report`` — print an exit-criteria summary against a results CSV.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import typer

from harness.adoption import context as ctx_mod
from harness.adoption import overlay as overlay_mod
from harness.adoption import workspace as ws_mod
from harness.adoption.drivers.base import DriverInputs
from harness.adoption.drivers.claude_code import ClaudeCodeDriver
from harness.adoption.drivers.codex import CodexDriver
from harness.adoption.drivers.cursor import CursorStaticDriver
from harness.adoption.drivers.mock import MockDriver
from harness.adoption.matrix import Cell, load_matrix
from harness.adoption.observer.fs_snapshot import snapshot
from harness.adoption.observer.redact import default_config, redact_tree
from harness.adoption.observer.transcript import TranscriptWriter
from harness.adoption.scorer import aggregate as agg_mod
from harness.adoption.scorer.rules import CellArtifacts, score_cell

app = typer.Typer(help=__doc__, no_args_is_help=True)


@app.command(name="sync-fixtures")
def sync_fixtures(force: bool = typer.Option(False, "--force", help="Overwrite existing.")) -> None:
    """Materialize ``benchmark/repos/*`` from samples/ + examples/."""
    from harness.adoption.scripts.sync_fixtures import materialize

    materialize(force=force)


@app.command()
def run(
    matrix: Path = typer.Option(
        Path("benchmark/matrix.yaml"),
        "--matrix",
        help="Path to matrix.yaml.",
        exists=True,
    ),
    out: Path = typer.Option(
        Path(".agents-private/adoption-sprint"),
        "--out",
        help="Output root for raw + redacted artifacts.",
    ),
    run_id: str | None = typer.Option(None, "--run-id"),
    results_csv: Path | None = typer.Option(
        None,
        "--results-csv",
        help="CSV row destination. Defaults to benchmark/results/<run-id>.csv.",
    ),
    budget_usd: float = typer.Option(
        20.0,
        "--budget-usd",
        help="Hard cap on cumulative cost_usd_estimate; aborts on overrun.",
    ),
    agent_filter: str | None = typer.Option(
        None,
        "--agent",
        help="Comma-separated agent filter, e.g. 'claude-code,cursor-static'.",
    ),
) -> None:
    """Execute the full pipeline against ``matrix.yaml``."""
    run_id = run_id or _default_run_id()
    cells = load_matrix(matrix)
    if agent_filter:
        wanted = {a.strip() for a in agent_filter.split(",") if a.strip()}
        cells = [c for c in cells if c.agent in wanted]
    if not cells:
        typer.echo("No cells selected.")
        raise typer.Exit(code=2)

    run_dir = out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("SHIPGATE_HARNESS_BUDGET_USD", str(budget_usd))
    guard = agg_mod.BudgetGuard.from_env(default_usd=budget_usd)

    scorecards: list = []
    for i, cell in enumerate(cells, start=1):
        typer.echo(f"[{i}/{len(cells)}] {cell.cell_id}")
        try:
            sc = _run_one_cell(cell=cell, run_id=run_id, run_dir=run_dir)
        except Exception as exc:  # noqa: BLE001 — cell-level catch
            typer.echo(f"  ! cell failed: {exc}")
            continue
        scorecards.append(sc)
        try:
            guard.record(sc)
        except agg_mod.BudgetExceeded as exc:
            typer.echo(f"  ! {exc}")
            break

    csv_path = results_csv or (Path("benchmark/results") / f"{run_id}.csv")
    agg_mod.write_csv(scorecards, out_path=csv_path)
    exit_report = agg_mod.check_exit_criteria(scorecards)
    (run_dir / "exit_criteria.json").write_text(
        json.dumps(exit_report.as_dict(), indent=2), encoding="utf-8"
    )
    typer.echo(f"wrote {csv_path} and {run_dir / 'exit_criteria.json'}")


@app.command()
def smoke() -> None:
    """Mock-driver end-to-end pipeline run. No live API calls. PR-CI safe."""
    fixtures_root = _repo_root() / "tests" / "harness" / "fixtures"
    if not fixtures_root.is_dir():
        typer.echo(f"missing fixtures at {fixtures_root}")
        raise typer.Exit(code=2)

    # Ensure benchmark/repos/ is materialized — smoke must work without
    # requiring the operator to run sync-fixtures first.
    archetype_dir = _repo_root() / "benchmark" / "repos" / "openai-agents-sdk"
    if not archetype_dir.is_dir():
        from harness.adoption.scripts.sync_fixtures import materialize as _materialize

        _materialize()

    run_id = _default_run_id(prefix="smoke")
    run_dir = _repo_root() / ".agents-private" / "adoption-sprint" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    scenarios = (
        ("mock_run_good", "openai-agents-sdk", "10-agents-md", None, "01-prepare-for-release"),
        ("mock_run_bad", "openai-agents-sdk", "00-no-hints", None, "01-prepare-for-release"),
    )
    scorecards = []
    for fixture_name, archetype, variant, negative, prompt in scenarios:
        fixture_dir = fixtures_root / fixture_name
        if not fixture_dir.is_dir():
            typer.echo(f"missing {fixture_dir}")
            continue
        cell = Cell(
            archetype=archetype,
            variant=variant,
            negative_overlay=negative,
            prompt=prompt,
            agent="mock",
            model="mock",
        )
        sc = _run_one_cell(
            cell=cell,
            run_id=run_id,
            run_dir=run_dir,
            driver_override=MockDriver(fixture_dir),
        )
        scorecards.append(sc)

    csv_path = run_dir / "smoke.csv"
    agg_mod.write_csv(scorecards, out_path=csv_path)
    typer.echo(f"smoke: {len(scorecards)} cell(s), CSV={csv_path}")
    for sc in scorecards:
        typer.echo(
            f"  {sc.cell_id}: score={sc.rubric_score} headline_pass={sc.headline_pass} "
            f"blockers={[b.kind for b in sc.blockers]}"
        )


@app.command()
def report(
    results_csv: Path = typer.Option(..., "--results-csv", exists=True),
) -> None:
    """Print an exit-criteria summary against a CSV file.

    Note: this loads the CSV's own ``score`` column rather than recomputing
    detectors. Use ``score`` to re-run detectors against captured artifacts.
    """
    typer.echo(f"reporting against {results_csv}")
    # Implementation deferred to v2; smoke + run already emit exit_criteria.json.
    typer.echo("(use exit_criteria.json from the run directory in v1)")


# --------------------------------------------------------------------------- internals


def _default_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"


def _select_driver(agent: str):
    if agent == "mock":
        raise RuntimeError(
            "mock driver requires a fixture path — use the smoke command, not run --agent=mock."
        )
    if agent == "claude-code":
        return ClaudeCodeDriver()
    if agent == "cursor-static":
        return CursorStaticDriver()
    if agent == "codex":
        return CodexDriver()
    raise ValueError(f"Unknown agent {agent!r}")


def _run_one_cell(
    *,
    cell: Cell,
    run_id: str,
    run_dir: Path,
    driver_override=None,
):
    """Prepare workspace, run driver, capture artifacts, score."""
    repo_root = _repo_root()
    archetype_dir = repo_root / "benchmark" / "repos" / cell.archetype
    artifacts_dir = run_dir / cell.cell_id
    raw_dir = artifacts_dir / "raw"
    redacted_dir = artifacts_dir / "redacted"

    ws = ws_mod.materialize(
        archetype_dir=archetype_dir,
        workspace_root=artifacts_dir,
        cell_id="workspace_root",
    )
    pre_snap = snapshot(ws.root)

    variant_dirs = [repo_root / "benchmark" / "setup-variants" / cell.variant]
    if cell.negative_overlay:
        variant_dirs.append(repo_root / "benchmark" / "setup-variants" / cell.negative_overlay)
    placeholders = ctx_mod.get_context(cell.archetype).as_placeholder_map()
    overlay_mod.apply_overlays(
        variant_dirs=variant_dirs,
        workspace_root=ws.root,
        placeholders=placeholders,
    )
    ws_mod.commit_overlay(ws, message=f"overlay {cell.variant}")

    prompt_path = repo_root / "benchmark" / "prompts" / f"{cell.prompt}.txt"
    prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""

    driver = driver_override or _select_driver(cell.agent)
    inputs = DriverInputs(
        workspace=ws.root,
        prompt_text=prompt_text,
        artifacts_dir=artifacts_dir,
        cell_id=cell.cell_id,
        agent_name=cell.agent,
        model=cell.model,
    )
    with TranscriptWriter(raw_dir) as writer:
        run_result = driver.run(inputs, writer)

    (raw_dir / "summary.md").write_text(run_result.summary_text, encoding="utf-8")
    (raw_dir / "final.diff").write_text(run_result.final_diff, encoding="utf-8")

    post_snap = snapshot(ws.root)
    fs_diff = pre_snap.diff(post_snap)

    redact_tree(raw_dir, redacted_dir, config=default_config())
    artifacts = CellArtifacts(
        cell=cell,
        artifacts_dir=artifacts_dir,
        redacted_dir=redacted_dir,
        pre_workspace_files=pre_snap.files,
        post_workspace_files=post_snap.files,
        fs_diff=fs_diff,
        workspace_dir=ws.root,
    )
    scorecard = score_cell(
        cell=cell,
        artifacts=artifacts,
        started_at=run_result.started_at,
        ended_at=run_result.ended_at,
        tokens_in=run_result.tokens_in,
        tokens_out=run_result.tokens_out,
        cost_usd_estimate=run_result.cost_usd_estimate,
        agent_version=cell.model or driver.name,
        driver_degraded=run_result.degraded,
        run_id=run_id,
        artifacts_dir_rel=str(artifacts_dir),
    )
    agg_mod.write_scorecard_json(scorecard, artifacts_dir / "scorecard.json")
    return scorecard


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "pyproject.toml").is_file() and (ancestor / "benchmark").is_dir():
            return ancestor
    raise RuntimeError(f"Could not locate repo root from {here}")


if __name__ == "__main__":
    app()
