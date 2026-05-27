# Benchmark history

This directory holds optional JSON traces written by
`scripts/run_benchmarks.py --save`. Files match `run-<unix-ts>.json`
and are gitignored — historical runs are local-only.

To collect a stable baseline:

```bash
python scripts/run_benchmarks.py --iterations 10 --save --no-phases
```

That produces a single JSON record with median / p95 / stdev per
scenario. Stash these locally if you want to track a regression
across a branch series. They are not committed.
