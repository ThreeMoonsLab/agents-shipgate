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
from harness.adoption.observer.fs_snapshot import FsDiff, snapshot
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
    cell_timeout_s = int(os.environ.get("SHIPGATE_HARNESS_CELL_TIMEOUT_S", "600"))
    for i, cell in enumerate(cells, start=1):
        typer.echo(f"[{i}/{len(cells)}] {cell.cell_id}")
        # Pre-cell budget check so a single expensive cell can't blow past
        # the cap before we get a chance to abort.
        remaining = guard.cap_usd - guard.spent_usd
        if guard.cap_usd >= 0 and remaining <= 0:
            typer.echo(f"  ! budget exhausted before cell {i}; spent={guard.spent_usd:.4f}")
            break
        try:
            sc = _run_one_cell(
                cell=cell,
                run_id=run_id,
                run_dir=run_dir,
                timeout_s=cell_timeout_s,
                remaining_budget_usd=remaining if guard.cap_usd >= 0 else None,
            )
        except Exception as exc:  # noqa: BLE001 — cell-level catch
            # Record a visible failure scorecard so infrastructure breakage
            # (missing API keys, SDK crashes, workspace setup failures) can
            # never be silently absorbed into the leaderboard.
            typer.echo(f"  ! cell failed: {exc}")
            sc = _infrastructure_failure_scorecard(
                cell=cell,
                run_id=run_id,
                run_dir=run_dir,
                error=repr(exc),
            )
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

    # Exit nonzero so CI (and operators) see broken runs as broken runs.
    #   - any infrastructure_failure blocker → exit 4 (driver/setup crash)
    #   - any failed exit criterion → exit 3 (matrix did not meet thresholds)
    # A zero exit means "every cell ran AND exit criteria passed."
    infra_failures = [sc for sc in scorecards if any(b.kind == _INFRA_BLOCKER_KIND for b in sc.blockers)]
    if infra_failures:
        typer.echo(f"FAIL: {len(infra_failures)} cell(s) had infrastructure failures.")
        raise typer.Exit(code=4)
    if not all(
        [
            exit_report.materially_outperforms_no_hints,
            exit_report.near_perfect_activation,
            exit_report.not_noisy_on_docs_only,
        ]
    ):
        typer.echo("FAIL: one or more exit criteria not met (see exit_criteria.json).")
        raise typer.Exit(code=3)


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
def score(
    run_dir: Path = typer.Option(
        ...,
        "--run-dir",
        exists=True,
        file_okay=False,
        help="Path to a previous run's directory under .agents-private/adoption-sprint/",
    ),
    results_csv: Path | None = typer.Option(
        None,
        "--results-csv",
        help="CSV destination (default: <run-dir>/rescored.csv).",
    ),
) -> None:
    """Re-run detectors against a previous run's captured artifacts.

    Used to iterate on detectors without rerunning agents. Reads each
    cell's ``scorecard.json`` for archetype/variant metadata, replays the
    redacted artifacts through the current ``score_cell`` implementation,
    and writes a fresh CSV.
    """
    cell_dirs = sorted(p for p in run_dir.iterdir() if p.is_dir() and (p / "scorecard.json").is_file())
    if not cell_dirs:
        typer.echo(f"No cell directories with scorecard.json under {run_dir}")
        raise typer.Exit(code=2)
    new_scorecards: list = []
    for cell_dir in cell_dirs:
        sc = _rescore_cell(cell_dir)
        if sc is not None:
            new_scorecards.append(sc)
    out = results_csv or (run_dir / "rescored.csv")
    agg_mod.write_csv(new_scorecards, out_path=out)
    exit_report = agg_mod.check_exit_criteria(new_scorecards)
    (run_dir / "rescored_exit_criteria.json").write_text(
        json.dumps(exit_report.as_dict(), indent=2), encoding="utf-8"
    )
    typer.echo(f"rescored {len(new_scorecards)} cell(s) → {out}")


@app.command()
def report(
    results_csv: Path = typer.Option(..., "--results-csv", exists=True),
) -> None:
    """Print a human-readable summary of a results CSV.

    Loads the CSV's score / headline_pass / blocker columns and aggregates
    by (agent, variant). Does NOT recompute detectors — use ``score`` for
    that against a run directory.
    """
    import csv

    rows = list(csv.DictReader(results_csv.open(encoding="utf-8")))
    if not rows:
        typer.echo(f"empty CSV at {results_csv}")
        return
    typer.echo(f"results: {results_csv} ({len(rows)} rows)")
    by_group: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row.get("model", "?"), row.get("variant", "?"))
        by_group.setdefault(key, []).append(row)
    typer.echo("")
    typer.echo(f"{'agent':<20} {'variant':<22} {'n':>3} {'mean_score':>10} {'pass_rate':>10} {'blockers':>9}")
    typer.echo("-" * 80)
    for (agent, variant), group in sorted(by_group.items()):
        n = len(group)
        scores = [int(r.get("score") or 0) for r in group]
        passes = sum(1 for r in group if str(r.get("headline_pass", "")).lower() == "true")
        blockers = sum(int(r.get("blocker_count") or 0) for r in group)
        mean_score = sum(scores) / n if n else 0.0
        pass_rate = passes / n if n else 0.0
        typer.echo(
            f"{agent:<20} {variant:<22} {n:>3d} {mean_score:>10.1f} "
            f"{pass_rate:>10.0%} {blockers:>9d}"
        )


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
    timeout_s: int = 600,
    remaining_budget_usd: float | None = None,
):
    """Prepare workspace, run driver, capture artifacts, score.

    ``timeout_s`` is a per-cell wall-clock cap honoured by the driver
    (Claude Code wraps the SDK loop in ``anyio.move_on_after``). The mock
    and cursor-static drivers ignore it — they're effectively instantaneous.

    ``remaining_budget_usd`` is informational: it lets a driver short-circuit
    a long loop when it knows it would exceed the cap on this cell alone.
    """
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
    # Snapshot AFTER overlays so fs_diff captures only agent-created files —
    # otherwise overlay templates would show up as "new" in the diff and
    # leak into trace-synthesis detection.
    pre_snap = snapshot(ws.root)

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
        timeout_s=timeout_s,
        budget_usd=remaining_budget_usd,
    )
    with TranscriptWriter(raw_dir) as writer:
        run_result = driver.run(inputs, writer)

    (raw_dir / "summary.md").write_text(run_result.summary_text, encoding="utf-8")
    (raw_dir / "final.diff").write_text(run_result.final_diff, encoding="utf-8")

    post_snap = snapshot(ws.root)
    fs_diff = pre_snap.diff(post_snap)
    # Persist the snapshots so `score` can rebuild a faithful CellArtifacts
    # without re-running the agent. fs_snapshot files are SAFE to put under
    # raw/ (just sha256 hashes — never file contents) but we keep them
    # outside the redacted/ mirror by writing alongside as sidecar JSON.
    _persist_fs_snapshots(artifacts_dir, pre_snap.files, post_snap.files)

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
        driver_degraded=run_result.degraded or bool(run_result.error),
        run_id=run_id,
        artifacts_dir_rel=str(artifacts_dir),
    )
    # If the driver reported a runtime error, mark the scorecard as an
    # infrastructure failure so it never masquerades as a low-but-valid score.
    if run_result.error:
        _mark_infrastructure_failure(scorecard, run_result.error)
    agg_mod.write_scorecard_json(scorecard, artifacts_dir / "scorecard.json")
    return scorecard


# --------------------------------------------------------------------------- rescore


def _persist_fs_snapshots(
    artifacts_dir: Path, pre_files: dict[str, str], post_files: dict[str, str]
) -> None:
    """Write pre/post FS snapshots so ``score`` can rebuild a faithful CellArtifacts.

    Snapshots contain only relative paths and sha256 digests — no file
    contents. Safe to leave outside the ``redacted/`` mirror.
    """
    snap_dir = artifacts_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "pre.json").write_text(
        json.dumps({"files": pre_files}, indent=2), encoding="utf-8"
    )
    (snap_dir / "post.json").write_text(
        json.dumps({"files": post_files}, indent=2), encoding="utf-8"
    )


def _load_fs_snapshots(
    artifacts_dir: Path,
) -> tuple[dict[str, str], dict[str, str], bool]:
    """Load pre/post snapshots written by ``_persist_fs_snapshots``.

    Returns ``(pre_files, post_files, ok)``. ``ok`` is False when the
    sidecar files are missing — older runs predate this change. Callers
    should fall back to N/A for filesystem-dependent criteria when ``ok``
    is False.
    """
    snap_dir = artifacts_dir / "snapshots"
    pre_path = snap_dir / "pre.json"
    post_path = snap_dir / "post.json"
    if not (pre_path.is_file() and post_path.is_file()):
        return {}, {}, False
    pre_files = json.loads(pre_path.read_text(encoding="utf-8")).get("files") or {}
    post_files = json.loads(post_path.read_text(encoding="utf-8")).get("files") or {}
    return pre_files, post_files, True


# Detectors that depend on fs_diff. When pre-state isn't available during
# rescore (older runs predate snapshot persistence), these are forced N/A
# rather than potentially false-flagging.
_FS_DEPENDENT_CRITERIA: frozenset[str] = frozenset({"no_runtime_trace_synthesis"})


def _rescore_cell(cell_dir: Path):
    """Rebuild a CellArtifacts from a previous run's captured files and re-score.

    Returns None if the cell directory is missing required artifacts.

    Preserves any ``infrastructure_failure`` blocker from the prior
    scorecard — rescoring re-runs behavioural detectors but cannot
    retroactively heal a broken run.
    """
    scorecard_path = cell_dir / "scorecard.json"
    if not scorecard_path.is_file():
        typer.echo(f"  ! {cell_dir.name}: missing scorecard.json, skipping")
        return None
    prior = json.loads(scorecard_path.read_text(encoding="utf-8"))
    cell = Cell(
        archetype=prior["archetype"],
        variant=prior["variant"],
        negative_overlay=prior.get("negative_overlay"),
        prompt=prior["prompt_id"],
        agent=prior["agent"],
        model=prior.get("model"),
    )
    redacted_dir = cell_dir / "redacted"
    workspace_dir = cell_dir / "workspace_root" / "workspace"
    if not redacted_dir.is_dir():
        typer.echo(f"  ! {cell.cell_id}: missing redacted/, skipping (was the cell an infra failure?)")
        return None

    pre_files, post_files, snapshots_ok = _load_fs_snapshots(cell_dir)
    if not snapshots_ok:
        # Back-compat: older runs lack the sidecar. Approximate by snapshotting
        # whatever's on disk now and report it as both pre and post so fs_diff
        # is empty — keeps filesystem-dependent detectors honest (they fall to
        # n_a rather than over-flagging).
        if workspace_dir.is_dir():
            post_snap = snapshot(workspace_dir)
            post_files = post_snap.files
            pre_files = dict(post_files)
        fs_diff = FsDiff(added=[], removed=[], changed=[])
    else:
        fs_diff = FsDiff(
            added=sorted(set(post_files) - set(pre_files)),
            removed=sorted(set(pre_files) - set(post_files)),
            changed=sorted(
                p for p in set(pre_files) & set(post_files) if pre_files[p] != post_files[p]
            ),
        )
    artifacts = CellArtifacts(
        cell=cell,
        artifacts_dir=cell_dir,
        redacted_dir=redacted_dir,
        pre_workspace_files=pre_files,
        post_workspace_files=post_files,
        fs_diff=fs_diff,
        workspace_dir=workspace_dir,
    )
    from datetime import datetime as _dt

    started = _dt.fromisoformat(prior["started_at"].replace("Z", "+00:00"))
    ended = _dt.fromisoformat(prior["ended_at"].replace("Z", "+00:00"))
    scorecard = score_cell(
        cell=cell,
        artifacts=artifacts,
        started_at=started,
        ended_at=ended,
        tokens_in=prior.get("tokens_in", 0) or 0,
        tokens_out=prior.get("tokens_out", 0) or 0,
        cost_usd_estimate=prior.get("cost_usd_estimate", 0.0) or 0.0,
        agent_version=prior.get("model") or prior.get("agent"),
        driver_degraded=prior.get("driver_degraded", False),
        run_id=prior["run_id"],
        artifacts_dir_rel=str(cell_dir),
    )

    # Mark filesystem-dependent criteria N/A when we couldn't load real
    # snapshots — without pre-state we have no reliable signal for
    # trace-synthesis.
    if not snapshots_ok:
        for key in _FS_DEPENDENT_CRITERIA:
            if key in scorecard.criteria:
                scorecard.criteria[key] = type(scorecard.criteria[key])(
                    status="n_a",
                    severity=scorecard.criteria[key].severity,
                    signal=(
                        f"{key}: rescored without persisted fs snapshots; "
                        "pre-state unknown."
                    ),
                )
                scorecard.blockers = [
                    b for b in scorecard.blockers if b.kind != key
                ]
        scorecard.headline_pass = not scorecard.blockers

    # Carry forward any infrastructure_failure blocker from the prior run.
    # Rescoring re-runs behavioural detectors only — it cannot retroactively
    # heal a driver crash, missing API key, or workspace setup error. If we
    # didn't preserve it, `score` could convert a broken run into a passing-
    # looking CSV. Run this AFTER any other adjustments so headline_pass
    # ends up False (which `_mark_infrastructure_failure` enforces).
    for prior_blocker in prior.get("blockers") or []:
        if prior_blocker.get("kind") == _INFRA_BLOCKER_KIND:
            error = prior_blocker.get("detail") or "carried from prior scorecard"
            _mark_infrastructure_failure(scorecard, error)
            break

    return scorecard


# --------------------------------------------------------------------------- failure scorecards


_INFRA_BLOCKER_KIND = "infrastructure_failure"


def _redact_error(error: str) -> str:
    """Run a driver error message through the harness redactor.

    Driver exceptions can carry API keys (e.g., HTTP error responses from
    the Claude SDK include the bearer token in the request log), absolute
    paths under ``$HOME``, and ``.env.harness`` values. We MUST scrub these
    before storing the message anywhere the scorecard JSON or public CSV
    can see.
    """
    from harness.adoption.observer.redact import default_config, redact_string

    return redact_string(error, config=default_config())


def _mark_infrastructure_failure(scorecard, error: str) -> None:
    """In-place: add an ``infrastructure_failure`` blocker to a scorecard.

    Driver-reported errors (missing API key, SDK crash, timeout) must surface
    as a blocker so the leaderboard cannot silently treat them as a normal
    low-scoring cell. The error string is redacted before storage.
    """
    from harness.adoption.scorer.schema import Blocker, CriterionResult

    safe = _redact_error(error)
    scorecard.criteria[_INFRA_BLOCKER_KIND] = CriterionResult(
        status="fail",
        severity="blocker",
        signal=f"Driver runtime error: {safe[:200]}",
    )
    scorecard.blockers.append(
        Blocker(kind=_INFRA_BLOCKER_KIND, detail=safe[:500], evidence_ref=None)
    )
    scorecard.headline_pass = False
    scorecard.driver_degraded = True
    if not scorecard.notes:
        scorecard.notes = f"{_INFRA_BLOCKER_KIND}: {safe[:120]}"


def _infrastructure_failure_scorecard(
    *,
    cell: Cell,
    run_id: str,
    run_dir: Path,
    error: str,
):
    """Build a minimal scorecard for a cell that crashed before producing one.

    Used by the outer ``run`` loop when ``_run_one_cell`` raises. The
    resulting scorecard is written to ``benchmark/results/<run-id>.csv``
    with ``headline_pass=False`` and ``blocker_kinds=infrastructure_failure``
    so the failure is impossible to miss. The error string is redacted
    before any field is populated.
    """
    from datetime import UTC, datetime

    from harness.adoption.scorer.schema import Blocker, CriterionResult, ScorecardV1

    safe = _redact_error(error)
    now = datetime.now(UTC)
    artifacts_dir = run_dir / cell.cell_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    scorecard = ScorecardV1(
        run_id=run_id,
        cell_id=cell.cell_id,
        archetype=cell.archetype,
        variant=cell.variant,
        negative_overlay=cell.negative_overlay,
        prompt_id=cell.prompt,
        agent=cell.agent,
        model=cell.model,
        started_at=now,
        ended_at=now,
        duration_s=0.0,
        criteria={
            _INFRA_BLOCKER_KIND: CriterionResult(
                status="fail",
                severity="blocker",
                signal=f"Cell setup or scoring crashed: {safe[:200]}",
            ),
        },
        blockers=[Blocker(kind=_INFRA_BLOCKER_KIND, detail=safe[:500])],
        rubric_score=0,
        headline_pass=False,
        driver_degraded=True,
        artifacts_dir=str(artifacts_dir),
        notes=f"{_INFRA_BLOCKER_KIND}: {safe[:120]}",
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
