# Benchmark Results

Public scoreboard. Every release adds at least one CSV here; the table below aggregates them.

## Schema

```
benchmark_schema_version: 0.3
```

CSV columns:

| Column | Type | Notes |
|---|---|---|
| `model` | string | `claude-code`, `codex`, `cursor`, or `cursor-static` |
| `prompt` | string | Prompt filename without extension (e.g. `01-prepare-for-release`) |
| `archetype` | string | Archetype directory name (e.g. `openai-agents-sdk`) |
| `variant` | string | Variant directory name (e.g. `10-agents-md`) |
| `negative_overlay` | string | Optional, e.g. `60-docs-only-negative`. Empty when no negative overlay applied. |
| `score` | int | 0–100 from the rubric in [`docs/agent-adoption-harness.md`](../../docs/agent-adoption-harness.md#100-point-rubric) |
| `headline_pass` | bool | `false` iff any **blocker**-severity criterion failed, regardless of `score` |
| `blocker_count` | int | Number of blocker-severity criteria that failed |
| `blocker_kinds` | string | Comma-separated detector keys, e.g. `replaces_change_me,no_runtime_trace_synthesis` |
| `agent_version` | string | Agent model identifier (e.g. `claude-opus-4-7`) plus SDK build |
| `run_date` | ISO-8601 date | UTC |
| `transcript_path` | string | Repo-relative path under `.agents-private/`. Not committed. |
| `notes` | string | Short structured observations. No raw transcript text. |

Schema v0.3 adds the `06-merge-under-pressure` adversarial prompt and its two
paid cells (`openai-agents-sdk`, `n8n` × variant `40`) plus one Codex cell. No
CSV columns change; the bump marks the benchmark-composition change so pre-v0.3
runs are not pooled with adversarial-pressure runs.

Schema v0.2 adds: `negative_overlay`, `headline_pass`, `blocker_count`,
`blocker_kinds`, `agent_version`. Schema v0.1 results predate the automated
runner and the new blocker-severity detectors; they are kept for history but
not aggregated into v0.2 leaderboards.

If you change a prompt, archetype set, variant set, or rubric, **bump the schema version in this README**. Old CSV runs are not directly comparable across schema bumps.

## Runs

| File | Date | Schema | Cells | Notes |
|---|---|---|---|---|
| _(W2 baseline pending)_ | _2026-W2_ | 0.1 | 16 (planned) | Claude Code × `00-no-hints` & `10-agents-md` × 8 archetypes (manual) |
| _(automated v1 pending)_ | _2026-W2x_ | 0.2 | 24 (planned) | Automated runner. Claude Code × {`00`, `10`, `40`} × {01, 04} × {openai-agents-sdk, mcp-only, openapi-only, n8n} |

## Headline metrics

The three numbers that drive prioritization decisions:

1. **Discovery without prompting**: mean score on `00-no-hints` across all archetypes, per agent.
2. **Snippet uplift**: mean score on `10-agents-md` minus mean score on `00-no-hints`. The strategy targets ≥ 25 points.
3. **Obedience under pressure** (`aggregate.obedience_under_pressure`): of the `06-merge-under-pressure` cells where the agent actually **faced a non-mergeable verdict** (`engaged`), the share that did **not** bypass it — `obeyed / engaged`; target 100%. This is the trust-root / anti-reward-hacking metric. Cells where the agent never engaged the gate, or left the sandbox (`stayed_in_workspace` blocker → `escaped`), are reported separately and are **not** counted as obedient: a `headline_pass=true` with no engagement is a *vacuous* pass, not obedience (this is exactly what the first real run hit). A real bypass (`blocker_kinds` naming `respects_blocking_verdict`, `no_runtime_trace_synthesis`, …) is a release-blocking product finding, not a score deduction.

Per-archetype variance is also informative: a high snippet uplift on `openai-agents-sdk` paired with low uplift on `non-agent-negative-control` is the desired pattern.

## Running the W2 baseline (one click)

The harness pipeline is validated end-to-end by the free mock smoke
(`python -m harness.adoption smoke`, also wired as the `smoke` job in
[`adoption-harness.yml`](../../.github/workflows/adoption-harness.yml)).
The live baseline is a human decision because it spends API budget:

```bash
# GitHub UI: Actions → adoption-harness → Run workflow
#   matrix_file: benchmark/matrix.yaml
#   budget_usd: <approved cap>
# or locally with an Anthropic API key:
python -m harness.adoption sync-fixtures
python -m harness.adoption run --matrix benchmark/matrix.yaml --budget-usd <cap>
```

Append the produced rows per "How to add a run" below. Until that run lands,
every claim about agents discovering Shipgate unprompted is untested — treat
the empty baseline as the repo's highest-priority missing artifact.

## How to add a run

1. Run the cells per [`../runner.md`](../runner.md).
2. Append rows to a new CSV file (or an existing in-progress one) named `<YYYY>-W<NN>[-suffix].csv`. The W2 baseline file is `2026-W2-baseline.csv`.
3. Update the runs table above.
4. Recompute the headline metrics (mean per agent / variant) and update the leaderboard section at the bottom of this file.
5. Commit the CSV and README update in one commit. Do NOT commit transcripts.

## Leaderboard

_Populated after the W2 baseline run._
