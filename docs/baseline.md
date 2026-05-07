# Baseline Workflow

Agents Shipgate supports a local baseline for teams adopting the scanner
after findings already exist.

## Save Current Findings

```bash
agents-shipgate baseline save \
  --config shipgate.yaml \
  --out .agents-shipgate/baseline.json
```

The baseline contains active, unsuppressed findings only. Suppressed findings
are intentionally excluded because they already carry an explicit review reason
in `shipgate.yaml`.

Severity values in the baseline are the report-visible severities after
`checks.severity_overrides` are applied. Matching still uses fingerprints, not
severity, so changing an override does not create a new baseline identity.

## Apply The Baseline

```bash
agents-shipgate scan \
  --config shipgate.yaml \
  --baseline .agents-shipgate/baseline.json \
  --ci-mode strict
```

When a baseline is present, strict mode fails only on new unsuppressed findings
that match the active `fail_on` policy. Existing matched findings remain visible
in the report. Resolved findings are counted in the `baseline` summary but do not
fail CI.

## JSON Fields

Reports keep the v0.1 payload contract and add baseline fields:

- `report_schema_version: "0.10"` in current reports
- `baseline.path`
- `baseline.matched_count`
- `baseline.new_count`
- `baseline.resolved_count`
- `findings[].baseline_status`
- `tool_surface_facts` in baselines saved with schema `0.3`

The public baseline comparison mode is `new-findings`:

```bash
agents-shipgate scan \
  --config shipgate.yaml \
  --baseline .agents-shipgate/baseline.json \
  --baseline-mode new-findings
```

Baseline matching uses `findings[].fingerprint`. The fingerprint algorithm is
documented as v1: `sha256(check_id | tool_name | canonical evidence)[:16]`,
rendered as `fp_<digest>`. It intentionally excludes severity overrides, report
paths, warnings, timestamps, `default_severity` audit evidence, and baseline
status.

## Baseline Schema Versions

`agents-shipgate baseline save` now writes baseline schema `0.3`. It preserves
the existing finding fields and adds an optional `tool_surface_facts` snapshot so
`agents-shipgate scan --baseline .agents-shipgate/baseline.json` can also show a
tool-surface diff when `--diff-from` is not supplied.

Schema `0.2` baselines still load for accepted-debt matching and strict-mode
filtering. They do not contain `tool_surface_facts`, so using a `0.2` baseline
as the only diff reference yields `tool_surface_diff.enabled=false` with a note.
Newer baselines are not guaranteed to load in older package versions.

`--baseline` and `--diff-from` have separate jobs: `--baseline` drives
`findings[].baseline_status`, strict-mode filtering, and
`release_decision.baseline_delta`; `--diff-from` drives only
`tool_surface_diff`.

Scope deltas distinguish tool-required scopes from manifest-declared scopes.
If the same literal scope moves between those kinds, the diff reports one
removed scope and one added scope so the JSON preserves the source of the
change.
