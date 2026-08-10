"""Run the latency benchmark suite with richer reporting than pytest.

Pytest's ``tests/test_latency_budget.py`` is the CI gate — pass/fail
against a budget. This script is the **diagnostic** companion:

- Multiple measured iterations with median / p95 / stdev.
- Per-phase breakdown via the ``_perf`` instrumentation.
- JSON output for tracking benchmark history over time.
- Pretty console output.
- Optional cold-start subprocess mode (``--cold-start``) that includes
  Python interpreter + module-import overhead — closer to what users
  feel on CI.

Typical use:

    # All scenarios, 5 iterations, JSON output
    python scripts/run_benchmarks.py --iterations 5 --json out.json

    # Profile one scenario with phase breakdown
    python scripts/run_benchmarks.py --scenario large --iterations 10

    # Cold-start measurement (subprocess-per-run; slower but realistic)
    python scripts/run_benchmarks.py --cold-start --scenario medium

Results land under ``benchmark/perf/results/`` if ``--save`` is set,
named ``run-<unix-ts>.json``. The directory is gitignored except for
the README — historical runs are not part of the repo (they accrete).

This is not a competitive benchmark. It's a regression-detection
tool. Don't measure across machines and compare absolute numbers;
compare same-machine before/after for a single PR.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

# Make `agents_shipgate` importable when this script is run from a
# repo checkout without `pip install -e .` having taken effect (e.g.,
# in a fresh worktree). conftest.py handles this for tests; for a
# standalone script we add ``src/`` ourselves.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agents_shipgate import _perf  # noqa: E402
from agents_shipgate.cli.scan.orchestrator import run_scan  # noqa: E402

_BUDGETS_PATH = _REPO_ROOT / "benchmark" / "perf" / "budgets.yaml"
_SCENARIOS_ROOT = _REPO_ROOT / "benchmark" / "perf" / "scenarios"
_RESULTS_ROOT = _REPO_ROOT / "benchmark" / "perf" / "results"


@contextmanager
def _perf_session() -> Iterator[None]:
    """Enable `_perf` for one block; reset after."""
    _perf.reset()
    _perf.enable()
    try:
        yield
    finally:
        _perf.disable()


def _measure_inprocess(config_path: Path, output_dir: Path) -> tuple[float, int]:
    """Measure one in-process scan run.

    The hot path: imports already warm, Pydantic validators cached. This
    is what the pytest budget test measures.
    """
    start = time.perf_counter()
    report, _exit = run_scan(
        config_path=config_path,
        output_dir=output_dir,
        packet_enabled=False,
        plugins_enabled=False,
    )
    return time.perf_counter() - start, len(report.tool_inventory)


def _measure_cold_start(config_path: Path, output_dir: Path) -> tuple[float, int]:
    """Measure one cold-start scan via subprocess.

    Includes Python startup, module imports, and CLI dispatch — the
    full latency users feel on CI for a one-shot scan. Slower; use
    sparingly (each iteration spawns a Python process).
    """
    start = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agents_shipgate",
            "scan",
            "-c",
            str(config_path),
            "--no-packet",
            "--no-plugins",
            "--out",
            str(output_dir),
        ],
        capture_output=True,
        check=False,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_SRC)},
    )
    duration = time.perf_counter() - start
    if proc.returncode not in (0, 20):  # 20 == strict-mode gate failure (still a successful run)
        sys.stderr.write(
            f"\nERROR: scan subprocess returned exit {proc.returncode}:\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        )
        raise SystemExit(1)
    # Parse the report to get tool count — read directly to avoid
    # depending on stdout shape.
    report_json = output_dir / "report.json"
    if report_json.exists():
        data = json.loads(report_json.read_text(encoding="utf-8"))
        tool_count = len(data.get("tool_inventory", []))
    else:
        tool_count = -1
    return duration, tool_count


def _run_scenario(
    name: str,
    scenario: dict[str, Any],
    *,
    iterations: int,
    warmup: int,
    measure_fn: Callable[[Path, Path], tuple[float, int]],
    include_phases: bool,
) -> dict[str, Any]:
    """Run one scenario, return a result dict."""
    config_path = _SCENARIOS_ROOT / scenario["path"] / "shipgate.yaml"
    if not config_path.exists():
        return {
            "scenario": name,
            "status": "missing",
            "error": f"scenario not generated at {config_path}",
        }

    with tempfile.TemporaryDirectory(prefix=f"shipgate-bench-{name}-") as tmp_root:
        tmp = Path(tmp_root)

        # Warmup pass. Discarded.
        for i in range(warmup):
            measure_fn(config_path, tmp / f"warmup_{i}")

        # If phase breakdown requested, the in-process path captures
        # phase timings via _perf. Subprocess mode can't see them
        # (separate process). We do a final in-process pass after the
        # measured iterations to capture a representative phase
        # breakdown for the JSON output.
        durations: list[float] = []
        tool_counts: list[int] = []
        for i in range(iterations):
            duration, tools = measure_fn(config_path, tmp / f"run_{i}")
            durations.append(duration)
            tool_counts.append(tools)

        phase_timings: dict[str, float] | None = None
        if include_phases:
            with _perf_session():
                _measure_inprocess(config_path, tmp / "phase_capture")
                phase_timings = _perf.snapshot()

    durations_sorted = sorted(durations)
    median = statistics.median(durations)
    p95 = (
        durations_sorted[int(len(durations_sorted) * 0.95)]
        if len(durations_sorted) > 1
        else durations_sorted[0]
    )
    stdev = statistics.stdev(durations) if len(durations) > 1 else 0.0

    return {
        "scenario": name,
        "status": "ok",
        "config_path": str(config_path.relative_to(_REPO_ROOT)),
        "tool_count": tool_counts[0],
        "tool_count_stable": all(t == tool_counts[0] for t in tool_counts),
        "iterations": iterations,
        "warmup_iterations": warmup,
        "durations_seconds": durations,
        "median_seconds": median,
        "p95_seconds": p95,
        "stdev_seconds": stdev,
        "min_seconds": min(durations),
        "max_seconds": max(durations),
        "budget_seconds": scenario["budget_seconds"],
        "within_budget": median <= scenario["budget_seconds"],
        "phase_timings_seconds": phase_timings,
    }


def _print_result(result: dict[str, Any]) -> None:
    """Pretty-print one scenario result to stdout."""
    name = result["scenario"]
    if result["status"] != "ok":
        print(f"  {name:<10}  SKIPPED  {result.get('error', '')}")
        return

    median_ms = result["median_seconds"] * 1000
    p95_ms = result["p95_seconds"] * 1000
    stdev_ms = result["stdev_seconds"] * 1000
    budget_ms = result["budget_seconds"] * 1000
    mark = "OK " if result["within_budget"] else "OVER"
    print(
        f"  {name:<10}  {mark}  median {median_ms:>7.1f} ms  "
        f"p95 {p95_ms:>7.1f} ms  stdev {stdev_ms:>6.1f} ms  "
        f"(budget {budget_ms:>6.0f} ms, {result['tool_count']} tools)"
    )
    phases = result.get("phase_timings_seconds")
    if phases:
        print("        phases:")
        for phase, seconds in sorted(phases.items(), key=lambda kv: -kv[1]):
            print(f"          {phase:<24} {seconds * 1000:>8.1f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        default="all",
        help="Which scenario(s) to run. 'all' (default) or one of "
        "the keys under scenarios: in budgets.yaml.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Measured iterations per scenario (default: 5). Median is "
        "the headline number; p95 / stdev report stability.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warmup iterations (discarded). Default: 1.",
    )
    parser.add_argument(
        "--cold-start",
        action="store_true",
        help="Measure via subprocess so Python startup + import cost "
        "are included. Slower; closer to what CI feels.",
    )
    parser.add_argument(
        "--no-phases",
        action="store_true",
        help="Skip the phase-breakdown capture pass (faster).",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write the result JSON to this path.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Also save under benchmark/perf/results/run-<ts>.json.",
    )
    args = parser.parse_args()

    budgets = yaml.safe_load(_BUDGETS_PATH.read_text(encoding="utf-8"))
    scenarios = budgets["scenarios"]

    if args.scenario != "all":
        if args.scenario not in scenarios:
            sys.stderr.write(
                f"unknown scenario {args.scenario!r}. Known: {sorted(scenarios.keys())}\n"
            )
            return 2
        scenarios = {args.scenario: scenarios[args.scenario]}

    measure_fn = _measure_cold_start if args.cold_start else _measure_inprocess
    mode = "cold_start" if args.cold_start else "in_process"

    print(
        f"Running {len(scenarios)} scenario(s) in {mode} mode, "
        f"{args.iterations} measured iteration(s) "
        f"(+ {args.warmup} warmup)…"
    )
    print()

    started_at = time.time()
    results: list[dict[str, Any]] = []
    for name in sorted(scenarios.keys()):
        # Phase breakdown only makes sense for in-process runs.
        include_phases = not args.no_phases and not args.cold_start
        result = _run_scenario(
            name,
            scenarios[name],
            iterations=args.iterations,
            warmup=args.warmup,
            measure_fn=measure_fn,
            include_phases=include_phases,
        )
        results.append(result)
        _print_result(result)

    over_budget = [r for r in results if r["status"] == "ok" and not r["within_budget"]]
    print()
    if over_budget:
        print(f"FAIL  {len(over_budget)}/{len(results)} scenario(s) over budget.")
    else:
        ok_count = sum(1 for r in results if r["status"] == "ok")
        print(f"OK    {ok_count} scenario(s) within budget.")

    payload = {
        "mode": mode,
        "iterations": args.iterations,
        "warmup_iterations": args.warmup,
        "started_at_unix": int(started_at),
        "results": results,
    }
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"      JSON written to {args.json.relative_to(_REPO_ROOT) if args.json.is_absolute() and args.json.is_relative_to(_REPO_ROOT) else args.json}"
        )
    if args.save:
        _RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        save_path = _RESULTS_ROOT / f"run-{int(started_at)}.json"
        save_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"      saved {save_path.relative_to(_REPO_ROOT)}")

    return 1 if over_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
