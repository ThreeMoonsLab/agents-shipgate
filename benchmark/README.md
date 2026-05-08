# Agents Shipgate · Adoption Benchmark

A frozen, reproducible benchmark for measuring whether coding agents (Claude Code, Codex, Cursor) discover and use Agents Shipgate when given realistic prompts in realistic target repos.

The harness in [`docs/agent-adoption-harness.md`](../docs/agent-adoption-harness.md) is the design; this directory is the executable form: vendored archetypes, frozen prompts, ordered setup variants, a tester-facing runbook, and a public results CSV that moves with every release.

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
agents:    Claude Code, Codex, Cursor
prompts:   01-prepare-for-release, 02-review-tool-pr, 03-improve-readiness, 04-docs-only-negative
archetypes: openai-agents-sdk, mcp-only, openapi-only, langgraph,
            adk-dynamic-toolset, crewai, clean-read-only,
            non-agent-negative-control
variants:  00-no-hints, 10-agents-md, 20-claude-md, 30-cursor-rule, 40-shipgate-yaml
```

That's 3 × 4 × 8 × 5 = 480 cells per release. We don't need to fill every cell every time — Week 2 baselines two variants × eight archetypes for one agent (64 cells), and subsequent weeks expand as the adoption-improving work lands.

## Scoring

Each cell scores against the 100-point rubric in [`docs/agent-adoption-harness.md` § 100-Point Rubric](../docs/agent-adoption-harness.md#100-point-rubric). The CSV records the per-cell score; the leaderboard README aggregates by (agent, variant) and (archetype, variant).

## Acceptance bar (per the actionable plan)

- W2 baseline run: 16 cells (Claude Code × `00-no-hints` and `10-agents-md` × 8 archetypes), saved to `results/2026-W2-baseline.csv`.
- W3 re-run: same matrix after `agent_action` lands; delta column added to the leaderboard.
- W4 retro: does the `00-no-hints` score beat the W2 baseline by ≥ 15 points? Yes → discovery additions are working. No → the bottleneck is upstream-framework authority, not docs.

## Privacy

Test repos and prompts are public. Per-run notes that contain prompts you don't want to publish go under `.agents-private/adoption-sprint/`, which `.gitignore` excludes. Public CSVs hold scores and structured failure modes only.
