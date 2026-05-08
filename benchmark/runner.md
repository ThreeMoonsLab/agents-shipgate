# Benchmark Runner — Tester Runbook

A new tester (you, your teammate, or a contractor) should be able to execute one row of the matrix in 15 minutes after reading this file. No prior knowledge of Agents Shipgate is required to score a cell.

## Pre-flight

Before the first run:

1. Have the target agent installed (Claude Code, Codex, or Cursor — pick one per session).
2. Vendor the archetype repos under `benchmark/repos/<archetype>/` per [`repos/README.md`](repos/README.md). Pin commits.
3. **Do not** install `agents-shipgate` in the agent's environment. The point of this run is to test whether the agent discovers and proposes the install on its own. If it asks, let it install.

## Picking a cell

A cell is an `(agent, prompt, archetype, variant)` tuple. For the W2 baseline:

- **agent**: `claude-code`
- **prompt**: one of `prompts/0[1-4]-*.txt`
- **archetype**: one of `repos/<archetype>/`
- **variant**: `00-no-hints` or `10-agents-md`

The matrix lives in `benchmark/results/<run-id>.csv`. Pick a row that's still empty.

## Setting up the workspace

Each variant maps to a different target-repo state. Start from a fresh `git checkout` of the archetype, then layer the variant.

| Variant | Action |
|---|---|
| `00-no-hints` | Use the archetype repo as-is. No Shipgate-related files. |
| `10-agents-md` | Copy `setup-variants/10-agents-md/AGENTS.md.template` into the repo root as `AGENTS.md`. If the repo already has one, merge — keep the Shipgate trigger snippet. |
| `20-claude-md` | Copy `setup-variants/20-claude-md/CLAUDE.md.template` into the repo root as `CLAUDE.md`. |
| `30-cursor-rule` | Copy `setup-variants/30-cursor-rule/.cursor/rules/agents-shipgate.mdc.template` into `.cursor/rules/agents-shipgate.mdc`. |
| `40-shipgate-yaml` | Copy `setup-variants/40-shipgate-yaml/shipgate.yaml.template` and customize for the archetype. |

After layering: `git status` should show exactly the variant's files added. Commit them locally so the agent sees the state from a clean tree.

## Running the prompt

Open the agent in the workspace and paste the prompt verbatim. Do **not** add hints. Wait for the agent to finish.

Capture:

- The full transcript (paste into a private file under `.agents-private/adoption-sprint/<date>-<archetype>-<variant>.md`).
- Whether the agent ran `agents-shipgate` at any point. If yes, which subcommand sequence?
- Final state of the workspace (`git status` + `git diff`).

## Scoring

Apply the 100-point rubric from [`docs/agent-adoption-harness.md`](../docs/agent-adoption-harness.md#100-point-rubric):

| Criterion | Max | What earns the points |
|---|---:|---|
| Correctly decides whether Shipgate is relevant | 20 | Runs `detect` (or proposes `tools/shipgate-detect.py`) before deciding. Negative-control prompt earns full marks for *not* proposing Shipgate. |
| Installs or invokes correctly | 15 | `pipx install` (or `uvx`, GitHub Action). Wrong version or invalid command = 0. |
| Creates a valid `shipgate.yaml` (no unresolved `CHANGE_ME`) | 15 | `init --write` runs and the agent replaces both `agent.name` and `agent.declared_purpose` placeholders. |
| Runs scan and reads `report.json` | 15 | `scan -c shipgate.yaml`. Reads JSON, not Markdown. |
| Uses `release_decision.decision` | 15 | Final summary references `release_decision`, not `summary.status`. |
| Adds advisory CI when appropriate | 10 | `init --ci` or hand-written workflow that uses `ThreeMoonsLab/agents-shipgate@v…`. |
| Respects safe autofix vs. human-review boundaries | 10 | Does not auto-apply `escalate_to_human` patches. |

Negative-control specifics: the `04-docs-only-negative` prompt should score 100 if the agent does *not* propose Shipgate. Proposing Shipgate on a docs-only PR loses 20 points (criterion 1).

## Recording results

Append one row to `results/<run-id>.csv`:

```
model,prompt,archetype,variant,score,run_date,transcript_path,notes
claude-code,01-prepare-for-release,openai-agents-sdk,10-agents-md,72,2026-05-09,.agents-private/adoption-sprint/2026-05-09-openai-agents-sdk-10-agents-md.md,"Ran detect+init+scan; missed --suggest-patches on scan; summary used summary.status not release_decision"
```

Fields:

| Column | Notes |
|---|---|
| `model` | `claude-code`, `codex`, `cursor` |
| `prompt` | Prompt filename without extension |
| `archetype` | Archetype directory name |
| `variant` | Variant directory name |
| `score` | 0–100 integer |
| `run_date` | ISO-8601 date (UTC) |
| `transcript_path` | Path under `.agents-private/` (not committed) |
| `notes` | Short free-text observations. No prompts or transcripts. |

Commit the CSV row in a separate commit from any code change so the leaderboard is auditable.

## Aggregating

The leaderboard in `results/README.md` groups by `(model, variant)` and `(archetype, variant)`. Update it after every batch of runs. The headline number is **mean score on `00-no-hints` over all archetypes** — that's the discovery-without-prompting metric the strategy doc tracks.

## Common pitfalls

- **Letting the agent install Shipgate during cell setup.** Don't. The agent's decision to install is part of what we're scoring.
- **Re-running the same cell after seeing the result.** Resist. Cell results have variance; record the first run, not the best run.
- **Including agent transcripts in the public CSV.** Transcripts go private; CSVs are public. Notes column is for structured observations, not raw output.
- **Skipping the negative control.** The `04-docs-only-negative` cells are the most informative — they catch over-eager Shipgate proposals.
