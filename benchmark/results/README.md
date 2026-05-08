# Benchmark Results

Public scoreboard. Every release adds at least one CSV here; the table below aggregates them.

## Schema

```
benchmark_schema_version: 0.1
```

CSV columns:

| Column | Type | Notes |
|---|---|---|
| `model` | string | `claude-code`, `codex`, or `cursor` |
| `prompt` | string | Prompt filename without extension (e.g. `01-prepare-for-release`) |
| `archetype` | string | Archetype directory name (e.g. `openai-agents-sdk`) |
| `variant` | string | Variant directory name (e.g. `10-agents-md`) |
| `score` | int | 0–100 from the rubric in [`docs/agent-adoption-harness.md`](../../docs/agent-adoption-harness.md#100-point-rubric) |
| `run_date` | ISO-8601 date | UTC |
| `transcript_path` | string | Repo-relative path under `.agents-private/`. Not committed. |
| `notes` | string | Short structured observations. No raw transcript text. |

If you change a prompt, archetype set, variant set, or rubric, **bump the schema version in this README**. Old CSV runs are not directly comparable across schema bumps.

## Runs

| File | Date | Schema | Cells | Notes |
|---|---|---|---|---|
| _(W2 baseline pending)_ | _2026-W2_ | 0.1 | 16 (planned) | Claude Code × `00-no-hints` & `10-agents-md` × 8 archetypes |

## Headline metrics

The two numbers that drive prioritization decisions:

1. **Discovery without prompting**: mean score on `00-no-hints` across all archetypes, per agent.
2. **Snippet uplift**: mean score on `10-agents-md` minus mean score on `00-no-hints`. The strategy targets ≥ 25 points.

Per-archetype variance is also informative: a high snippet uplift on `openai-agents-sdk` paired with low uplift on `non-agent-negative-control` is the desired pattern.

## How to add a run

1. Run the cells per [`../runner.md`](../runner.md).
2. Append rows to a new CSV file (or an existing in-progress one) named `<YYYY>-W<NN>[-suffix].csv`. The W2 baseline file is `2026-W2-baseline.csv`.
3. Update the runs table above.
4. Recompute the headline metrics (mean per agent / variant) and update the leaderboard section at the bottom of this file.
5. Commit the CSV and README update in one commit. Do NOT commit transcripts.

## Leaderboard

_Populated after the W2 baseline run._
