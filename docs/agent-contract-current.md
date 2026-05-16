# Current Agent Contract

The single, current statement of what AI coding agents and CI integrations should read from Agents Shipgate output. When the contract changes, update [STABILITY.md](../STABILITY.md) first, then this file. Other agent-facing surfaces (`AGENTS.md`, `llms.txt`, `.well-known/agents-shipgate.json`, the slash command, the skill, the FAQ) link here instead of restating field lists.

## Current versions

Verify the installed CLI contract locally before relying on hard-coded docs:

```bash
agents-shipgate contract --json
```

- Latest release: `v0.10.0` (see [pyproject.toml](../pyproject.toml) for the in-tree version)
- Runtime contract: `1`
- Current report schema: `0.17` — [`docs/report-schema.v0.17.json`](report-schema.v0.17.json)
- Current packet schema: `0.5` — [`docs/packet-schema.v0.5.json`](packet-schema.v0.5.json)
- Frozen-reference report schemas: [`v0.16`](report-schema.v0.16.json), [`v0.15`](report-schema.v0.15.json), [`v0.14`](report-schema.v0.14.json), [`v0.13`](report-schema.v0.13.json), [`v0.12`](report-schema.v0.12.json), [`v0.11`](report-schema.v0.11.json), [`v0.10`](report-schema.v0.10.json), [`v0.9`](report-schema.v0.9.json), [`v0.8`](report-schema.v0.8.json), [`v0.7`](report-schema.v0.7.json), [`v0.6`](report-schema.v0.6.json), older

## Read these first for release gating

In `agents-shipgate-reports/report.json`:

- `release_decision.decision` — `"blocked"` / `"review_required"` / `"insufficient_evidence"` / `"passed"`. Baseline-aware. **This is the gating signal.** `insufficient_evidence` (added v0.14) fires when evidence coverage is degraded past threshold (at least half of scanned tools are low-confidence — `ceil(N × 0.5)` with a minimum of 1, so 1-of-1 and 1-of-2 trip — or 4+ source-loader warnings); switch on the enum with a `review_required` fallback for unknown future values.
- `release_decision.blockers[]` — items that block release on this run.
- `release_decision.review_items[]` — items the human reviewer should look at; includes baseline-matched accepted debt.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches what the CI process will exit with.
- `release_decision.reason` — one-sentence explanation suitable for a PR comment.
- `release_decision.contribution_rules[]` (v0.17+) — deterministic per-finding audit explaining how each `report.findings` entry was classified. Exactly one row per finding (including suppressed). Each row carries `{finding_id, fingerprint, check_id, category, rule, rationale}`. `category` ∈ `{blocker, review_item, excluded}`; `rule` ∈ `{policy_block_new, severity_block_new, policy_baseline_accepted, severity_baseline_accepted, review_required, sub_threshold, suppressed}`. Reading the contribution rule is sufficient to predict the gate outcome for that finding without re-deriving the decision logic — the closed grammar of `(rule, category)` pairs is documented in [STABILITY.md "Release decision truth table"](../STABILITY.md#release-decision-truth-table). The audit cannot disagree with `blockers[]` / `review_items[]` (the same classification powers both).

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

The Action Surface Diff fields (v0.16+), reviewer-facing PR/release delta:

- `action_surface_facts.actions[]` — deterministic snapshot of the current agent action surface: action id, operation, effect, normalized risk tags, scopes, approval policy, safeguards, evidence, and hashes.
- `action_surface_diff.{enabled, base, summary, added, removed, modified, notes}` — what changed vs. a base report or v0.4 baseline. Policy findings generated from this diff can set `findings[].blocks_release=true` and appear in `release_decision.blockers`.
- `findings[].blocks_release` and `release_decision.{blockers,review_items}[].blocks_release` — explicit release-policy blockers from Action Surface Diff policies and policy-pack rules with `block: true`. Advisory CI may still exit 0; strict CI exits nonzero when an active unbaselined release blocker is present.

The tool-surface diff fields (v0.10+), lower-level explanatory data:

- `tool_surface_facts.{tools, scopes, controls, policies}` — current static facts about the tool surface.
- `tool_surface_diff.{enabled, base, summary, tools, high_risk_effects, scopes, controls, metadata_changes, policy_drift, finding_deltas, notes}` — what changed vs. a base ref. Disabled diffs render as `enabled: false` with a `notes` reason.

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

Codex plugin surface block (v0.13+), explanatory only — never a release-gate
input by itself:

- `codex_plugin_surface.{plugins, marketplaces, skills, apps, mcp_server_stubs, hook_stubs, mcp_inventory_files, component_path_issues, warnings}` — local static plugin package and marketplace facts.
- Only explicit MCP inventory tools from `codex_plugins.mcp_tool_inventories` appear in `tool_inventory[]`; apps, hooks, skills, and MCP server declarations stay in `codex_plugin_surface`.

Per-finding `provenance_kind` enum (v0.15+), additive classification — read this when you want to filter findings by the kind of rule that fired, independent of `confidence` (sureness):

- `static_declaration` — declared metadata: manifest, MCP export, OpenAPI schema, ADK YAML agent config, LangChain/CrewAI inventory JSON. High-trust structural facts.
- `ast_extraction` — Tool parsed from user Python source by a framework extractor (LangChain function/structured tools, CrewAI function/class tools, ADK Python toolsets). Subject to extraction errors; agents that distrust AST quality may filter these as a class.
- `keyword_heuristic` — matched a keyword list (broad-scope tokens, read-only/approval prompt terms, free-text parameter names). Higher false-positive risk than declarative facts.
- `regex_heuristic` — matched a regex (secret-like values in descriptions, prompt-injection patterns). Highest false-positive risk; pair with the recommendation before acting.
- `policy_pack` — emitted by an external policy pack rule. The rule's own confidence applies — Shipgate does not second-guess the pack.

Provenance generally follows the rule's own trigger (e.g., a rule that checks for a declared manifest field is `static_declaration` even when the underlying Tool was AST-extracted). For framework checks that fire across both AST and declarative tool sources (ADK's per-tool checks against `google_adk_function` AND `google_adk_config` tools), the label tracks the underlying tool's source. Third-party plugin checks that don't yet set the field land at `static_declaration` by default — pre-v0.15 plugins continue to validate against the v0.15 wire schema. Use `findings[].source.type` for the precise underlying tool source.

For reviewer-shaped output, also read the **Release Evidence Packet** at `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` when the `[pdf]` extras are installed). The packet has fixed reviewer sections governed by [`docs/packet-schema.v0.5.json`](packet-schema.v0.5.json) — see [STABILITY.md §Release Evidence Packet](../STABILITY.md#release-evidence-packet-v05).
Packet schema `0.5` preserves the v0.4 HITL fields
(`human_in_the_loop.runtime_control_disclaimer` and
`human_in_the_loop.source_provenance[]`) and adds
`action_surface_diff` so packet-only consumers can see release-blocking action
changes. The `release_decision.verdict` label includes
`INSUFFICIENT EVIDENCE` when the report decision is insufficient evidence.

## Don't use for new gating

- `summary.status` — preserved for v0.7 callers, **baseline-blind**. A baseline-matched critical flips this to `release_blockers_detected` even though `release_decision.decision` correctly classifies it as `review_required`. New consumers should not gate on `summary.status`. See [STABILITY.md §`release_decision.decision` vs `summary.status`](../STABILITY.md#release_decisiondecision-vs-summarystatus).

## Per-finding contextual explanation (v0.12+)

For prose summaries of a single finding (PR comments, chat replies, commit messages), use:

```bash
agents-shipgate explain-finding <FINGERPRINT> \
    --from agents-shipgate-reports/report.json --json
```

The payload is the full `Finding` shape (every field on `findings[]` in `report.json`, including `source`, `patches`, `confidence`, `agent_id`, etc.) overlaid with three derived fields:

- `metadata` — full `CheckMetadata` for the check_id (rationale, fires_when, evidence_fields, docs_url) when the check is in the catalog; null for unknown ids (third-party plugins, future checks).
- `explanation` — a deterministic 3–5 sentence prose summary suitable for direct quotation. Names the affected tool, the severity, the recommended fix, and an action-aware closing sentence keyed to `agent_action`. Same inputs always produce the same output.
- `source_report` — **absolute** path (always; relative `--from` values are resolved before serialization) to the report file the explanation was sourced from; round-trippable for caching and audit.

`explain-finding` requires `report_schema_version >= 0.12` because the action-aware explanation depends on per-finding `agent_action`. Pre-v0.12 reports are rejected with `input_parse_error` and a `next_action` pointing at the canonical scan command. The Pydantic `ReadinessReport` model is intentionally looser than this command's contract (so test fixtures can construct minimal findings); the version gate is what enforces v0.12 semantics on emitted reports.

Companion prompt: [`prompts/explain-finding-to-user.md`](../prompts/explain-finding-to-user.md). Use it when you need to translate a finding for a human who has never read the Shipgate docs. Keep `agents-shipgate explain <CHECK_ID>` for static catalog metadata (no specific finding); use `explain-finding` whenever you have a fingerprint and want the evidence-tied prose.

## Authoritative references

- [STABILITY.md](../STABILITY.md) — full 0.x stability contract. Source of truth for everything above.
- [AGENTS.md](../AGENTS.md) — agent-facing instructions: install, run, single-turn flow, error semantics.
- [`docs/report-schema.v0.17.json`](report-schema.v0.17.json) — machine-validatable JSON Schema for the current report.
- [`docs/packet-schema.v0.5.json`](packet-schema.v0.5.json) — machine-validatable JSON Schema for the current packet.
- [`docs/checks.json`](checks.json) — check catalog.

## See also

- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer that walks the JSON in the order a new consumer should read it; complements this field index.
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may assert mechanically vs. what must defer to a human reviewer when surfacing findings from `report.json`.
