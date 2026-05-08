# Current Agent Contract

The single, current statement of what AI coding agents and CI integrations should read from Agents Shipgate output. When the contract changes, update [STABILITY.md](../STABILITY.md) first, then this file. Other agent-facing surfaces (`AGENTS.md`, `llms.txt`, `.well-known/agents-shipgate.json`, the slash command, the skill, the FAQ) link here instead of restating field lists.

## Current versions

Verify the installed CLI contract locally before relying on hard-coded docs:

```bash
agents-shipgate contract --json
```

- Latest release: `v0.8.0` (see [pyproject.toml](../pyproject.toml) for the in-tree version)
- Runtime contract: `1`
- Current report schema: `0.10` — [`docs/report-schema.v0.10.json`](report-schema.v0.10.json)
- Current packet schema: `0.3` — [`docs/packet-schema.v0.3.json`](packet-schema.v0.3.json)
- Frozen-reference report schemas: [`v0.9`](report-schema.v0.9.json), [`v0.8`](report-schema.v0.8.json), [`v0.7`](report-schema.v0.7.json), [`v0.6`](report-schema.v0.6.json), older

## Read these first for release gating

In `agents-shipgate-reports/report.json`:

- `release_decision.decision` — `"blocked"` / `"review_required"` / `"passed"`. Baseline-aware. **This is the gating signal.**
- `release_decision.blockers[]` — items that block release on this run.
- `release_decision.review_items[]` — items the human reviewer should look at; includes baseline-matched accepted debt.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches what the CI process will exit with.
- `release_decision.reason` — one-sentence explanation suitable for a PR comment.

The action exposes these as outputs `decision`, `blocker_count`, `review_item_count`, `ci_would_fail` (v0.8+).

## Read these for release review

`agents-shipgate contract --json` exposes `manual_review_signals[]` as the
installed CLI's stable list of report/packet fields to inspect for human review
work.

The capability/intent diff fields (v0.9+), used by reviewers to spot misalignment between declared agent intent and actual tool surface:

- `capability_facts[]` — every capability surfaced from the tool inventory.
- `declared_intentions[]` — what the manifest says the agent is supposed to do.
- `misalignments[]` — where capabilities exceed (or fall short of) declared intent.
- `release_consequence` — capability-aware roll-up of the release decision.
- `suggested_scenarios[]` — dynamic-validation scenarios derived from misalignments and findings.

The tool-surface diff fields (v0.10+), explanatory only — never a release-gate input:

- `tool_surface_facts.{tools, scopes, controls, policies}` — current static facts about the tool surface.
- `tool_surface_diff.{enabled, base, summary, tools, high_risk_effects, scopes, controls, metadata_changes, policy_drift, finding_deltas, notes}` — what changed vs. a base ref. Disabled diffs render as `enabled: false` with a `notes` reason; the release decision is unaffected.

For reviewer-shaped output, also read the **Release Evidence Packet** at `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` when the `[pdf]` extras are installed). The packet has ten always-present sections governed by [`docs/packet-schema.v0.3.json`](packet-schema.v0.3.json) — see [STABILITY.md §Release Evidence Packet](../STABILITY.md#release-evidence-packet-v03).
In packet schema `0.3`, `human_in_the_loop.runtime_control_disclaimer`
clarifies that local HITL evidence is not runtime-enforcement proof, and
`human_in_the_loop.source_provenance[]` traces local validation artifacts when
available.

## Don't use for new gating

- `summary.status` — preserved for v0.7 callers, **baseline-blind**. A baseline-matched critical flips this to `release_blockers_detected` even though `release_decision.decision` correctly classifies it as `review_required`. New consumers should not gate on `summary.status`. See [STABILITY.md §`release_decision.decision` vs `summary.status`](../STABILITY.md#release_decisiondecision-vs-summarystatus).

## Authoritative references

- [STABILITY.md](../STABILITY.md) — full 0.x stability contract. Source of truth for everything above.
- [AGENTS.md](../AGENTS.md) — agent-facing instructions: install, run, single-turn flow, error semantics.
- [`docs/report-schema.v0.10.json`](report-schema.v0.10.json) — machine-validatable JSON Schema for the current report.
- [`docs/packet-schema.v0.3.json`](packet-schema.v0.3.json) — machine-validatable JSON Schema for the current packet.
- [`docs/checks.json`](checks.json) — check catalog.
