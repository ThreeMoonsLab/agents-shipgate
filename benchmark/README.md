# Agents Shipgate · Adoption Benchmark

A frozen, reproducible benchmark for measuring whether coding agents (Claude Code, Codex, Cursor) discover and use Agents Shipgate when given realistic prompts in realistic target repos.

The harness in [`docs/agent-adoption-harness.md`](../docs/agent-adoption-harness.md) is the design rubric; this directory is the executable form: vendored archetypes, frozen prompts, ordered setup variants, a tester-facing runbook, and a public results CSV that moves with every release.

**Automated runner.** The v1 automated runner lives at [`harness/adoption/`](../harness/adoption/). Operational doc: [`docs/adoption-harness-automated.md`](../docs/adoption-harness-automated.md). The matrix it executes is committed at [`matrix.yaml`](matrix.yaml).

## Why this exists

The four root barriers identified in the agent-adoption strategy include "no closed-loop validation." Without a public, repeatable score that moves when AGENTS.md / triggers / prompts / skill change, every adoption-improving edit is a guess. This benchmark closes that loop.

## Layout

| Path | Contents |
|---|---|
| [`repos/`](repos/) | Vendored or submoduled target repos — one per archetype, pinned to a specific commit |
| [`prompts/`](prompts/) | The four canonical prompts. None mention Agents Shipgate by name |
| [`setup-variants/`](setup-variants/) | Each variant adds a different Shipgate hint to a target repo (no hint, AGENTS.md, CLAUDE.md, Cursor rule, existing manifest) |
| [`runner.md`](runner.md) | Tester-facing runbook for executing the matrix |
| [`results/`](results/) | One CSV per release; leaderboard README |
| [`upstream-prs.md`](upstream-prs.md) | Tracker for the upstream-framework PR work that drives discovery without local hints |

## Matrix

```
agents:    Claude Code, Codex, Cursor (live) + cursor-static (lint only)
prompts:   01-prepare-for-release, 02-review-tool-pr, 03-improve-readiness, 04-docs-only-negative
archetypes: openai-agents-sdk, mcp-only, openapi-only, langgraph,
            adk-dynamic-toolset, crewai, clean-read-only, n8n,
            non-agent-negative-control
variants:  00-no-hints, 10-agents-md, 20-claude-md, 30-cursor-rule,
            40-shipgate-yaml, 50-advisory-workflow
negative_overlays: 60-docs-only-negative   (composable; not paired with 40)
```

That's a large theoretical matrix. The v1 automated runner samples 24 paid Claude cells (4 archetypes × 3 variants × 2 prompts) plus 12 free `cursor-static` cells (4 archetypes × {`00-no-hints`, `30-cursor-rule`, `30-cursor-rule` + `60-docs-only-negative`}); see [`matrix.yaml`](matrix.yaml) for the explicit list. Cursor static coverage is intentionally a different variant subset from Claude — the static driver can only meaningfully score the rule's own activation. Manual runs may still fill any unfilled cells.

## Scoring

Each cell scores against the 100-point rubric in [`docs/agent-adoption-harness.md` § 100-Point Rubric](../docs/agent-adoption-harness.md#100-point-rubric). The CSV records the per-cell score; the leaderboard README aggregates by (agent, variant) and (archetype, variant).

## Acceptance bar (per the actionable plan)

- W2 baseline run: 16 cells (Claude Code × `00-no-hints` and `10-agents-md` × 8 archetypes), saved to `results/2026-W2-baseline.csv`.
- W3 re-run: same matrix after `agent_action` lands; delta column added to the leaderboard.
- W4 retro: does the `00-no-hints` score beat the W2 baseline by ≥ 15 points? Yes → discovery additions are working. No → the bottleneck is upstream-framework authority, not docs.

## Privacy

Test repos and prompts are public. Per-run notes that contain prompts you don't want to publish go under `.agents-private/adoption-sprint/`, which `.gitignore` excludes. Public CSVs hold scores and structured failure modes only.
