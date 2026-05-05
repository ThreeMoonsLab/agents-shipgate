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

- `report_schema_version: "0.8"` in current reports
- `baseline.path`
- `baseline.matched_count`
- `baseline.new_count`
- `baseline.resolved_count`
- `findings[].baseline_status`

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
