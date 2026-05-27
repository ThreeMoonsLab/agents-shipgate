# Latency budget &amp; performance benchmarks

> Not to be confused with [`benchmark/`](../) parent directory — that's the
> *agent-adoption* benchmark (does Claude/Codex/Cursor discover Shipgate?).
> This subdirectory is the *scan-performance* benchmark (does `run_scan` stay
> fast?). Both live under `benchmark/` because they're both
> "non-test reproducible measurement" surfaces; their concerns don't overlap.

The release gate lives on the CI critical path. A regression that doubles
scan latency makes every PR slower and erodes trust. This suite catches
that.

## What's here

| File / dir | Purpose |
|---|---|
| `generate_scenarios.py` | Deterministic synthetic-input generator (small / medium / large) |
| `scenarios/` | Generated MCP JSON + OpenAPI YAML + `shipgate.yaml` per size |
| `budgets.yaml` | Per-scenario wallclock ceilings + iteration counts |
| `results/` | Optional historical runs from `run_benchmarks.py --save` (gitignored) |

## The CI gate

[`tests/test_latency_budget.py`](../../tests/test_latency_budget.py) is the
enforcer. It runs each scenario `measured_iterations` times (after
`warmup_iterations` discarded warmups), takes the **median**, and asserts
it is ≤ the budget in `budgets.yaml`.

It is marked `@pytest.mark.perf` so devs can skip locally with
`pytest -m 'not perf'`. CI runs it by default.

A second test, `test_scenarios_scale_sublinearly`, asserts that scan
latency grows *slower than* tool count — i.e. there is no O(n²) check
quietly walking every-tool × every-tool. This catches the most common
shape of perf regression in this codebase.

### When a budget fails

The test prints the per-phase breakdown (via the opt-in `_perf`
instrumentation in `agents_shipgate._perf`). That immediately tells you
which of the nine `run_scan` phases regressed. Typical phases ranked by
cost on `medium`:

| Phase | Share of total (medium) |
|---|---|
| `write_outputs` | ~44% |
| `build_final_report` | ~23% |
| `sanitize_for_output` | ~17% |
| `load_inputs` | ~10% |
| `run_checks_and_decide` | ~2% |
| everything else | <2% |

**Important:** the check engine itself is *fast*. Refactor candidates
for perf wins live overwhelmingly in the output pipeline (markdown
rendering, lens computation, sanitization). Don't optimize checks
until you have profiler evidence they're the bottleneck.

## Running the benchmark

```bash
# Just the CI gate
python -m pytest tests/test_latency_budget.py -m perf -v

# Richer reporting: median / p95 / stdev + phase breakdown
python scripts/run_benchmarks.py --iterations 5

# Cold-start mode (includes Python import overhead — what CI feels)
python scripts/run_benchmarks.py --cold-start --iterations 3

# Save a JSON record under benchmark/perf/results/
python scripts/run_benchmarks.py --iterations 10 --save
```

## Regenerating scenarios

Scenarios are committed for reproducibility — you do **not** need to
regenerate them to run the benchmark. Regenerate only when:

- A schema change makes the existing scenarios invalid.
- You want to vary tool counts, framework mix, or risk-tag distribution
  for an experiment.

```bash
python benchmark/perf/generate_scenarios.py            # all three
python benchmark/perf/generate_scenarios.py --size large
```

The generator is seeded — output is byte-identical across runs.

## Tuning budgets

`budgets.yaml` carries explicit headroom. The baseline measurements
captured at authoring time (in-process, MacBook M1) were:

| Scenario | Measured median | Current budget | Headroom |
|---|---:|---:|---:|
| small  |  43 ms | 3.0 s | ~70× |
| medium | 214 ms | 5.0 s | ~23× |
| large  | 720 ms | 10.0 s | ~14× |

That looks loose. It is loose **on purpose**: GitHub Actions free-tier
runners can be 3-5× slower than dev workstations, and the gate must
not flake. The 10-20× envelope catches a catastrophic regression
(scan went from 0.7s → 11s on large) while ignoring noise.

When you tighten budgets:

1. Run `python scripts/run_benchmarks.py --iterations 10 --save` on
   a stable host, ideally on a CI-class machine.
2. Take the p95, multiply by ~1.5 for safety, round up.
3. Edit `budgets.yaml`.

Never tighten budgets based on a single fast measurement. Variance
matters more than central tendency for a CI gate.

## What this catches (and what it doesn't)

| Catches | Misses |
|---|---|
| Refactor that 2-3× a phase | Sub-10% drift |
| O(n²) check that walks tool × tool | Cold-start regression (run `--cold-start` for this) |
| New mandatory work added to `run_scan` | Per-finding rendering bloat (drowns in dominant phases) |
| Lens computation accidentally inside a check loop | Plugin / third-party adapter slowdowns (gate runs `--no-plugins`) |

The complementary surfaces are:

- [`tests/test_large_sample.py`](../../tests/test_large_sample.py) — a single
  *realistic* large sample (`samples/large_multi_framework_agent`, ~65 tools
  across 5 sources) with a 10s end-to-end budget plus structural-shape
  tripwires (loaded source count, finding-band counts, release-decision
  anchor). That suite asserts *realism*; this one asserts *scale + phase
  attribution*. Both are intentionally kept distinct: theirs catches "a
  realistic scan got 3× slower or the finding set shifted shape," ours
  catches "a refactor made `write_outputs` quadratic across the tool list."
- `tests/test_property_loaders.py` — adapter correctness under hostile input.
- `tests/test_adapter_static_only.py` — trust contract.
- `benchmark/` (parent) — agent adoption / discovery.

Five surfaces together cover correctness, trust, two angles of performance
(realistic + scaling), and adoption.

## Phase names (stable contract)

The phase strings emitted by `_perf` and shown in the breakdown are
**part of the benchmark contract**. Renaming them in
`cli/scan/orchestrator.py` will silently break dashboard scripts and
historical comparisons. The current set:

- `prepare`
- `load_inputs`
- `build_tools_and_agent`
- `load_diff_references`
- `run_checks_and_decide`
- `plan_outputs`
- `sanitize_for_output`
- `build_final_report`
- `write_outputs`

Adding a new phase is fine (just add the `with _perf.phase(...)` block).
Renaming an existing one needs a corresponding edit here.
