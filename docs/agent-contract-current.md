# Current Agent Contract

The single, current statement of what AI coding agents and CI integrations should read from Agents Shipgate output. When the contract changes, update [STABILITY.md](../STABILITY.md) first, then this file. Other agent-facing surfaces (`AGENTS.md`, `llms.txt`, `.well-known/agents-shipgate.json`, the slash command, the skill, the FAQ) link here instead of restating field lists.

## Current versions

Verify the installed CLI contract locally before relying on hard-coded docs:

```bash
agents-shipgate contract --json
```

- Latest release: `v0.10.0` (see [pyproject.toml](../pyproject.toml) for the in-tree version)
- Runtime contract: `1`
- Current report schema: `0.12` — [`docs/report-schema.v0.12.json`](report-schema.v0.12.json)
- Current packet schema: `0.3` — [`docs/packet-schema.v0.3.json`](packet-schema.v0.3.json)
- Frozen-reference report schemas: [`v0.11`](report-schema.v0.11.json), [`v0.10`](report-schema.v0.10.json), [`v0.9`](report-schema.v0.9.json), [`v0.8`](report-schema.v0.8.json), [`v0.7`](report-schema.v0.7.json), [`v0.6`](report-schema.v0.6.json), older

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

Source provenance fields on `findings[].source` (v0.11+), additive and optional:

- `path`, `start_line`, `end_line`, `start_column`, `pointer` — manifest-relative file path, 1-based line/column, and RFC 6901 JSON pointer for the offending tool. Populated for OpenAPI, MCP, OpenAI tool artifacts, and Anthropic tool artifacts when the source is YAML. JSON inputs carry `path` and `pointer` but no line in v0.11.

Per-finding `agent_action` enum (v0.12+), deterministic projection — read this **first** when deciding what to do with a finding so you don't have to synthesize an action from `patches`/`autofix_safe`/`requires_human_review`/`suggested_patch_kind`:

- `auto_apply` — `apply-patches --confidence high` will resolve cleanly. Every patch is non-manual and high-confidence.
- `propose_patch_for_review` — at least one non-manual patch is attached and machine-applicable, but the full patch set is not auto-safe. Two shapes land here: (a) every non-manual patch is medium- or low-confidence, and (b) a high-confidence non-manual patch sits alongside one or more `ManualPatch` siblings (the non-manual is safe to apply, but the manual instructions still need a human). In both cases the agent should ask the user before `--apply` and surface any manual instructions verbatim.
- `escalate_to_human` — no machine-applicable patch. Either every patch is `ManualPatch`, or `patches` is empty/absent and the check requires human review.
- `suppress_with_reason` — reserved for future check classes that explicitly mark themselves as suppressible. Not emitted by the v0.12 deterministic projection; the schema accepts it so callers can extend.
- `informational` — no action required (suppressed finding or non-actionable advisory).

Top-level `agent_summary` block (v0.12+), one-fetch summary shaped for direct agent consumption — read this when you want the headline numbers without traversing arrays:

- `verdict` — mirrors `release_decision.decision`.
- `headline` — single-sentence verdict + counts; suitable for a PR comment lead. The headline uses `needs_human_review` (action-driven) for "require human review" wording, so a `review_required` verdict with only auto-applicable findings reads honestly as "auto-applicable; none require human input" rather than falsely claiming N findings need review.
- `blocker_count` — mirrors `len(release_decision.blockers)`.
- `review_item_count` — mirrors `len(release_decision.review_items)`; **severity-driven** (medium-and-up severity findings that aren't blockers, plus baseline-matched accepted debt). Use this when reporting release-review debt to the human reviewer.
- `auto_appliable_patches` — number of active findings with `agent_action == "auto_apply"`.
- `needs_human_review` — **action-driven**: number of active findings with `agent_action ∈ {"escalate_to_human", "propose_patch_for_review"}`. Both kinds need explicit human attention before any change applies — full escalations have no machine path, and proposed patches ship at medium/low confidence and require an explicit `--apply` after the user confirms. Use this when reasoning about what work an agent must do.
- **`review_item_count` and `needs_human_review` track different populations and can diverge.** A medium-severity stale-suppression finding lands in `release_decision.review_items` (severity rule) but its `agent_action` is `auto_apply` (high-confidence patch attached), so it's counted in `review_item_count` and `auto_appliable_patches` but **not** in `needs_human_review`.
- `first_recommended_action` — `{kind, command|null, why}`; deterministic next step. `kind: "command"` carries an actual CLI invocation; `kind: "info"` is a "surface this to the user" hint with no command. The agent_summary block is a deterministic projection — same inputs, same output, no agent-side aggregation needed.

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
- [`docs/report-schema.v0.12.json`](report-schema.v0.12.json) — machine-validatable JSON Schema for the current report.
- [`docs/packet-schema.v0.3.json`](packet-schema.v0.3.json) — machine-validatable JSON Schema for the current packet.
- [`docs/checks.json`](checks.json) — check catalog.

## See also

- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer that walks the JSON in the order a new consumer should read it; complements this field index.
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may assert mechanically vs. what must defer to a human reviewer when surfacing findings from `report.json`.
