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

- `report_schema_version: "0.22"` in current reports
- `baseline.path`
- `baseline.matched_count`
- `baseline.new_count`
- `baseline.resolved_count`
- `findings[].baseline_status`
- `tool_surface_facts` in baselines saved with schema `0.3+`
- `action_surface_facts` in baselines saved with schema `0.4+`

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

Report schema v0.18 computes public fingerprints after the default privacy
redaction pass. Findings whose evidence contains a recognized secret-like value
therefore get a new public fingerprint. To avoid surprise CI failures during
upgrade, `scan --baseline` also compares the pre-v0.18 raw fingerprint in memory
without writing it to public artifacts. After reviewing the v0.18 report, run
`agents-shipgate baseline save` again to rewrite the baseline with redacted
public fingerprints.

## Baseline Schema Versions

`agents-shipgate baseline save` now writes baseline schema `0.4`. It preserves
the existing finding fields, keeps the optional `tool_surface_facts` snapshot
from schema `0.3`, and adds an optional `action_surface_facts` snapshot so
`agents-shipgate scan --baseline .agents-shipgate/baseline.json` can also show
tool-surface and action-surface diffs when `--diff-from` is not supplied.

Schema `0.3` baselines still load and can drive `tool_surface_diff`, but they
do not contain `action_surface_facts`; using a `0.3` baseline as the only diff
reference yields `action_surface_diff.enabled=false` with a note. Schema `0.2`
baselines still load for accepted-debt matching and strict-mode filtering, but
they cannot enable either surface diff. Newer baselines are not guaranteed to
load in older package versions.

`--baseline` and `--diff-from` have separate jobs: `--baseline` drives
`findings[].baseline_status`, strict-mode filtering, and
`release_decision.baseline_delta`; `--diff-from` drives `tool_surface_diff` and
`action_surface_diff`.

Scope deltas distinguish tool-required scopes from manifest-declared scopes.
If the same literal scope moves between those kinds, the diff reports one
removed scope and one added scope so the JSON preserves the source of the
change.
