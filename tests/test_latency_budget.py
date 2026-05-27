"""Latency budget enforcement for ``run_scan``.

Runs the synthetic scenarios under ``benchmark/perf/scenarios/`` and
asserts the median wallclock per scan is at or below the per-scenario
budget declared in ``benchmark/perf/budgets.yaml``.

**What this catches:** refactors that 2-3x the scan latency. A check
that newly walks every tool's parameter list O(n²), an adapter that
re-parses the same YAML for every source, a sanitizer that compiles a
regex per iteration — these regressions land here.

**What this does NOT catch:** sub-10% perf drift, Python interpreter
startup cost (we measure in-process; `run_scan` is the unit), packet
PDF rendering (the optional weasyprint extra), or anything that's
slow only in cold-cache conditions.

Marked ``@pytest.mark.perf`` — devs working on non-perf changes can
skip with ``pytest -m 'not perf'``. CI runs them by default. They are
in-process to remove Python startup variance from the measurement;
the richer cold-start benchmark lives in ``scripts/run_benchmarks.py``.

When a budget fails, the test prints the per-phase breakdown via the
``_perf`` instrumentation — that tells you *where* the regression is
without needing to drop into a profiler.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from agents_shipgate import _perf
from agents_shipgate.cli.scan.orchestrator import run_scan

# These imports + the seeded scenario content + a writable output dir
# are everything we need. No fixtures from conftest.py.


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUDGETS_PATH = _REPO_ROOT / "benchmark" / "perf" / "budgets.yaml"
_SCENARIOS_ROOT = _REPO_ROOT / "benchmark" / "perf" / "scenarios"


def _load_budgets() -> dict:
    """Read budgets.yaml.

    Skips the entire test module (not just one test) if the file is
    absent — that way a checkout that excludes ``benchmark/`` (e.g.,
    a slim package install path) doesn't fail; it just doesn't run
    the budget gate.
    """
    if not _BUDGETS_PATH.exists():
        pytest.skip(f"budgets file missing: {_BUDGETS_PATH}")
    return yaml.safe_load(_BUDGETS_PATH.read_text(encoding="utf-8"))


_BUDGETS = _load_budgets()


@pytest.fixture
def perf_session() -> Iterator[None]:
    """Enable `_perf` for the duration of one test; reset after.

    The instrumentation accumulates across iterations within a test
    so the diagnostic phase breakdown shown on failure reflects the
    *total* time across all measured runs — which is more useful than
    the last one. ``reset()`` between scenarios prevents cross-test
    bleed.
    """
    _perf.reset()
    _perf.enable()
    try:
        yield
    finally:
        _perf.disable()


def _measure_one(config_path: Path, output_dir: Path) -> tuple[float, int]:
    """Run one scan and return (wallclock_seconds, tool_inventory_size).

    Uses ``time.perf_counter`` — the OS-level high-resolution timer.
    The tool count is returned so the test can assert the scenario
    actually loaded what budgets.yaml says it should (catches a
    silent regeneration that changed scenario shape).
    """
    start = time.perf_counter()
    report, _exit_code = run_scan(
        config_path=config_path,
        output_dir=output_dir,
        packet_enabled=False,  # packet PDF is the optional extra; not part of the gate.
        plugins_enabled=False,  # plugins are off by default for perf budget.
    )
    return time.perf_counter() - start, len(report.tool_inventory)


def _phase_breakdown_lines() -> list[str]:
    """Format the accumulated `_perf` snapshot for failure messages."""
    snapshot = _perf.snapshot()
    if not snapshot:
        return ["(no phase timings recorded — _perf not active)"]
    lines = ["    Phase timings (accumulated across iterations, ms):"]
    for name, seconds in sorted(snapshot.items(), key=lambda kv: -kv[1]):
        lines.append(f"      {name:<24}  {seconds * 1000:>8.1f} ms")
    return lines


@pytest.mark.perf
@pytest.mark.parametrize("size", sorted(_BUDGETS["scenarios"].keys()))
def test_scenario_within_budget(
    size: str, perf_session: None, tmp_path: Path
) -> None:
    """Median of N measured iterations must be ≤ budget.

    Parameterised so a single failing scenario doesn't mask others —
    you'll see ``test_scenario_within_budget[small]`` PASS while
    ``test_scenario_within_budget[large]`` FAIL, instead of one
    aggregate red.
    """
    scenario = _BUDGETS["scenarios"][size]
    config_path = _SCENARIOS_ROOT / scenario["path"] / "shipgate.yaml"
    if not config_path.exists():
        pytest.skip(
            f"scenario {size!r} not generated at {config_path}; "
            f"run `python benchmark/perf/generate_scenarios.py`"
        )

    warmups = int(_BUDGETS["warmup_iterations"])
    measured = int(_BUDGETS["measured_iterations"])
    budget = float(scenario["budget_seconds"])

    # Warmup pass: primes Python imports, file cache, Pydantic validator
    # caches. Discarded — the first run is always slower for reasons
    # unrelated to scan logic.
    for i in range(warmups):
        _measure_one(config_path, tmp_path / f"warmup_{i}")

    # Reset perf state AFTER warmup so phase timings reflect only the
    # measured iterations.
    _perf.reset()

    durations: list[float] = []
    tool_counts: list[int] = []
    for i in range(measured):
        duration, tools = _measure_one(config_path, tmp_path / f"measure_{i}")
        durations.append(duration)
        tool_counts.append(tools)

    median = statistics.median(durations)

    # Tool-count sanity: catches a silent change to scenario content
    # that would invalidate the budget.
    expected_min = int(scenario["expected_min_tools"])
    expected_max = int(scenario["expected_max_tools"])
    observed_tools = tool_counts[0]
    assert all(t == observed_tools for t in tool_counts), (
        f"tool count varied across iterations: {tool_counts!r}"
    )
    assert expected_min <= observed_tools <= expected_max, (
        f"scenario {size!r} loaded {observed_tools} tools, "
        f"expected {expected_min}-{expected_max}. "
        "Regenerate scenarios or update budgets.yaml expected_*_tools."
    )

    if median > budget:
        breakdown = "\n".join(_phase_breakdown_lines())
        durations_ms = [round(d * 1000, 1) for d in durations]
        pytest.fail(
            f"\nLatency budget exceeded for scenario {size!r}:\n"
            f"    median wallclock = {median:.3f}s  "
            f"(budget {budget:.3f}s)\n"
            f"    iterations (ms)  = {durations_ms}\n"
            f"    tools loaded     = {observed_tools}\n"
            f"{breakdown}\n"
            "\n"
            "Action: profile the regression via\n"
            "    python scripts/run_benchmarks.py --scenario "
            f"{size} --iterations 5 --json\n"
            "If the slowdown is intentional (new mandatory work), bump\n"
            "the per-scenario budget in benchmark/perf/budgets.yaml.\n"
        )


@pytest.mark.perf
def test_scenarios_scale_sublinearly(perf_session: None, tmp_path: Path) -> None:
    """The large scenario should NOT be wildly out of proportion.

    20x the tool count should produce at most ~10x the scan latency.
    A linear-or-worse blowup means a check is O(n²) over tools — the
    most common perf regression in this codebase.

    The threshold is set deliberately loose (10x) so this catches only
    egregious quadratic behavior, not the natural per-tool cost of an
    extra phase. Tighten when stable budgets are well-calibrated.
    """
    sizes = ["small", "large"]
    medians: dict[str, float] = {}
    tool_counts: dict[str, int] = {}

    for size in sizes:
        if size not in _BUDGETS["scenarios"]:
            pytest.skip(f"scenario {size!r} missing from budgets.yaml")
        scenario = _BUDGETS["scenarios"][size]
        config_path = _SCENARIOS_ROOT / scenario["path"] / "shipgate.yaml"
        if not config_path.exists():
            pytest.skip(f"scenario {size!r} not generated at {config_path}")

        # One warmup, three measured. Same shape as the budget test.
        _measure_one(config_path, tmp_path / f"{size}_warmup")
        durations = [
            _measure_one(config_path, tmp_path / f"{size}_run_{i}")[0]
            for i in range(3)
        ]
        medians[size] = statistics.median(durations)
        report, _ = run_scan(
            config_path=config_path,
            output_dir=tmp_path / f"{size}_final",
            packet_enabled=False,
            plugins_enabled=False,
        )
        tool_counts[size] = len(report.tool_inventory)

    tool_ratio = tool_counts["large"] / max(tool_counts["small"], 1)
    latency_ratio = medians["large"] / max(medians["small"], 0.001)

    # If latency scales faster than tools (i.e. > 1 per tool), something
    # is super-linear. The 1.0 threshold is the line we don't want to
    # cross. The ratio of latency_ratio / tool_ratio is the "per-tool
    # overhead growth factor" — we want it well under 1.
    growth_factor = latency_ratio / tool_ratio

    assert growth_factor < 1.0, (
        f"\nScan latency is growing super-linearly with tool count:\n"
        f"    small:  {tool_counts['small']:>4} tools, median {medians['small']:.3f}s\n"
        f"    large:  {tool_counts['large']:>4} tools, median {medians['large']:.3f}s\n"
        f"    tool_count ratio   = {tool_ratio:.2f}\n"
        f"    latency ratio      = {latency_ratio:.2f}\n"
        f"    growth_factor      = {growth_factor:.2f}  (want < 1.0)\n"
        "\n"
        "A growth_factor ≥ 1.0 means the scan is O(n) or worse per tool — most\n"
        "likely a check that's quadratic over the tool list. Profile via\n"
        "    python scripts/run_benchmarks.py --scenario large --json\n"
        "and look for a phase whose share of total time grew vs. small.\n"
    )
