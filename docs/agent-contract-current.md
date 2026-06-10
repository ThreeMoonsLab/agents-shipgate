# Current Agent Contract

The single, current statement of what AI coding agents and CI integrations should read from Agents Shipgate output. When the contract changes, update [STABILITY.md](../STABILITY.md) first, then this file. Other agent-facing surfaces (`AGENTS.md`, `llms.txt`, `.well-known/agents-shipgate.json`, the slash command, the skill, the FAQ) link here instead of restating field lists.

## Current versions

Verify the installed CLI contract locally before relying on hard-coded docs:

```bash
agents-shipgate contract --json
```

- Latest release: `v0.13.0` (see [pyproject.toml](../pyproject.toml) for the in-tree version)
- Runtime contract: `2`
- Current report schema: `0.26` — [`docs/report-schema.v0.26.json`](report-schema.v0.26.json)
- Current packet schema: `0.7` — [`docs/packet-schema.v0.7.json`](packet-schema.v0.7.json)
- Current verifier schema: `0.1` — [`docs/verifier-schema.v0.1.json`](verifier-schema.v0.1.json)
- Current capability standard: `0.1` — [`docs/capability-standard.md`](capability-standard.md)
- Current capability lock schema: `0.2` — [`docs/capability-lock-schema.v0.2.json`](capability-lock-schema.v0.2.json)
- Current capability lock diff schema: `0.3` — [`docs/capability-lock-diff-schema.v0.3.json`](capability-lock-diff-schema.v0.3.json)
- Current governance benchmark catalog schema: `0.2` — [`docs/governance-benchmark-catalog-schema.v0.2.json`](governance-benchmark-catalog-schema.v0.2.json)
- Current governance benchmark result schema: `0.2` — [`docs/governance-benchmark-result-schema.v0.2.json`](governance-benchmark-result-schema.v0.2.json)
- Frozen-reference report schemas: [`v0.24`](report-schema.v0.24.json), [`v0.23`](report-schema.v0.23.json), [`v0.22`](report-schema.v0.22.json), [`v0.21`](report-schema.v0.21.json), [`v0.20`](report-schema.v0.20.json), [`v0.19`](report-schema.v0.19.json), [`v0.18`](report-schema.v0.18.json), [`v0.17`](report-schema.v0.17.json), [`v0.16`](report-schema.v0.16.json), [`v0.15`](report-schema.v0.15.json), [`v0.14`](report-schema.v0.14.json), [`v0.13`](report-schema.v0.13.json), [`v0.12`](report-schema.v0.12.json), [`v0.11`](report-schema.v0.11.json), [`v0.10`](report-schema.v0.10.json), [`v0.9`](report-schema.v0.9.json), [`v0.8`](report-schema.v0.8.json), [`v0.7`](report-schema.v0.7.json), [`v0.6`](report-schema.v0.6.json), older
- Frozen-reference packet schemas live in [`docs/INDEX.md`](INDEX.md#reference).
- Frozen experimental capability lock and governance benchmark result schemas live in [`docs/INDEX.md`](INDEX.md#reference).

## Two read entry points

There are two correct "read first" paths; which one applies depends on who is
reading. They are not two decisions — they are two entry points into the same
one decision engine.

- **PR / controller flow** — an autonomous coding agent deciding *continue,
  repair, or stop*. Read `agents-shipgate-reports/verifier.json`: lead with
  `merge_verdict`, then read `agent_controller` for the imperative controls (see
  "In `verifier.json`, read these additive fields" below).
  `.well-known/agents-shipgate.json` → `verifier_read_order` is the
  machine-readable form of this order.
- **Gate / CI flow** — deciding pass/fail, or any raw `report.json` consumer.
  Read `agents-shipgate-reports/report.json` → `release_decision.decision` (the
  next section). `.well-known` → `gating_signal` names this signal.

`merge_verdict` is a deterministic projection of `release_decision.decision`, so
the two can never disagree.

## Read these first for release gating

In `agents-shipgate-reports/report.json`:

- `release_decision.decision` — `"blocked"` / `"review_required"` / `"insufficient_evidence"` / `"passed"`. Baseline-aware. **This is the gating signal.** Blockers take precedence. If there are no blockers, `insufficient_evidence` (added v0.14) fires when evidence coverage is degraded past threshold: low-confidence tools are at least `max(1, ceil(tool_count × 0.5))`, or source-loader warnings exceed `3`. One to three source warnings without blockers route to `review_required`. `insufficient_evidence` means the scan cannot confidently gate release from the available static evidence; it does not prove the agent is unsafe. Switch on the enum with a `review_required` fallback for unknown future values.
- `release_decision.blockers[]` — items that block release on this run.
- `release_decision.review_items[]` — items the human reviewer should look at; includes baseline-matched accepted debt.
- `release_decision.{blockers,review_items}[].capability_refs` (v0.24+) — stable capability IDs copied from the originating finding when a policy or policy-pack rule matched a `CapabilityFactV1`. Empty for findings that are not capability-policy matches. This is audit metadata only; `release_decision.decision` remains the gate.
- `release_decision.{blockers,review_items}[].capability_trace_refs` (v0.25+) — stable local trace-evidence IDs copied from the originating finding when an existing trace/evidence check used declared local trace artifacts. Empty when no local trace row is relevant. This is audit metadata only; `release_decision.decision` remains the gate.
- `release_decision.evidence_coverage.evidence_gaps[]` (v0.26+) — one structured row per measurable evidence gap: `{kind, subject, source_type, source_ref, why, next_action}` with `kind` ∈ `{low_confidence_tool, source_warning}` and `next_action` carrying `{kind, command, path, why, expects}` (`kind` ∈ `{declare_tool_inventory, provide_source, review_warning}`). When `decision` is `insufficient_evidence`, work the gaps in order instead of guessing: `declare_tool_inventory` rows point at the advisory `suggested-inventory.json` skeleton scan writes next to `report.json` and name the exact `*.tool_inventories` manifest key to reference it from. Deterministic projection of the coverage counts; never gates independently.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches what the CI process will exit with.
- `release_decision.reason` — one-sentence explanation suitable for a PR comment.
- `release_decision.contribution_rules[]` (v0.17+) — deterministic per-finding audit explaining how each `report.findings` entry was classified. Exactly one row per finding (including suppressed). Each row carries `{finding_id, fingerprint, check_id, category, rule, rationale}`. `category` ∈ `{blocker, review_item, excluded}`; `rule` ∈ `{policy_block_new, severity_block_new, policy_baseline_accepted, severity_baseline_accepted, review_required, sub_threshold, suppressed}`. Reading the contribution rule is sufficient to predict the gate outcome for that finding without re-deriving the decision logic — the closed grammar of `(rule, category)` pairs is documented in [STABILITY.md "Release decision truth table"](../STABILITY.md#release-decision-truth-table). The audit cannot disagree with `blockers[]` / `review_items[]` (the same classification powers both).
- `privacy_audit` (v0.18+) — confirms the default redaction pass ran before public artifacts were written. Read `enabled`, `rules_version`, `sensitive_field_inventory_version`, `redacted_occurrence_count`, `redacted_paths[]`, and `output_surfaces[]`. `redacted_paths[]` contains structural paths and counts only, never raw values or raw hashes.
- `reviewer_summary` (v0.20+) — deterministic projection of the reviewer lens surfaces and audit envelopes; the reviewer-side parallel to `agent_summary`. Read this block first when triaging a scan for a human reviewer. Carries `verdict` (mirrors `release_decision.decision`), `headline` (≤200 chars, PR-comment-friendly), per-lens activity counts (`tool_surface_changes`, `capability_misalignments`, `action_surface_changes`, `evidence_matrix_gaps`), per-audit-envelope counts (`severity_overrides_applied`, `severity_overrides_tier_crossed`, `privacy_redactions`, `baseline_integrity_issues`), and `first_recommended_surface: ReviewerSurfacePointer | None` — a deterministic pointer naming which lens/audit to open first (`{kind, name, path, why}` where `kind` ∈ `{release_decision, lens, audit, evidence_matrix}` and `name` ∈ `{tool_surface_diff, capability_intent_diff, action_surface_diff, evidence_matrix, policy_audit, privacy_audit, baseline_integrity, release_decision}`). Same inputs always produce the same output; this block cannot disagree with the underlying lens/audit data.
- `heuristics_filter` (v0.21+) — top-level audit envelope describing the `--no-heuristics` CLI filter pass. Always present, even when the flag is unset (`enabled: False` with zero counts), so the report shape is stable. Carries `enabled: bool`, `excluded_provenance_kinds: list[str]` (`["keyword_heuristic", "regex_heuristic"]`), `filtered_finding_count: int`, and `filtered_by_kind: dict[str, int]` (per-kind breakdown). When `enabled: True`, findings whose `provenance_kind` is in the excluded list have been marked `suppressed=True` with `suppression_reason="filtered by --no-heuristics"` BEFORE the release decision was built — they remain in `findings[]` for transparency but no longer gate release. The filter never un-suppresses a finding; manifest-driven suppression reasons are preserved when they overlap with the filter. Useful for security/GRC reviewers who want declared-only findings.
- `verifier_summary` (v0.22+) — top-level **composition** for one-fetch controller consumption (the AI-coding-workflow verifier surface). It derives **no independent verdict**: `verdict` mirrors `release_decision.decision` exactly (Principle: one decision engine). Carries `by_severity: dict[str,int]` and `by_reason_code: dict[str,int]` (active-finding histograms — the complete per-code map), `capability_delta_summary: {added, removed, broadened, narrowed}` (equal by construction to the `capability_change` member-list lengths), `protected_surface_touched: bool`, `policy_weakened: bool`, `human_ack_required: bool`, `human_ack_satisfied: bool`, and `top_reason_codes: list[{reason_code, count}]` — the ranked top-five highlight (severity desc → count desc → code asc; the full set stays in `by_reason_code`). This block cannot introduce a finding-independent blocker.

In `findings[]`, v0.24 adds capability-native policy evidence for built-in
policy checks and policy packs:

- `capability_refs: list[str]` — stable `CapabilityFactV1.id` values that
  matched the rule. It is emitted as an empty list for findings that are not
  capability-policy matches.
- `capability_policy_evidence | null` — optional typed audit metadata with the
  matched capability identity, effect, authority, controls, semantic hashes,
  matched predicates, and source provenance. It is explanatory only and is not
  included in finding fingerprint inputs.

Existing `finding.evidence` keys remain stable for legacy readers. Policy
matching is capability-native internally, but policy-pack behavior, suppressions,
severity overrides, baselines, SARIF, Markdown, and GitHub Action outputs remain
compatible.

In `findings[]`, v0.25 adds opt-in trace/provenance references for existing
trace/evidence checks:

- `capability_trace_refs: list[str]` — stable IDs from the top-level
  `capability_runtime_evidence` block. It is emitted as an empty list for
  findings that are not linked to a local trace row.
- `provenance_kind: "runtime_trace"` — used only for findings derived from
  declared local trace artifacts. It is not filtered by `--no-heuristics`.

The top-level `capability_runtime_evidence` block is a deterministic audit
projection over local trace artifacts declared in `openai_api.trace_samples`,
`google_adk.trace_samples`, `validation.evidence.approval_traces`, and
`validation.evidence.agent_traces`. It carries summary counts, matched and
unmatched `CapabilityTraceEvidenceV1` rows, source provenance, and notes. Trace
normalization keeps only allowlisted scalar fields and discards prompts,
messages, tool arguments, tool outputs, and arbitrary payload bodies. The block
is empty when no trace inputs are declared. It is not part of capability locks,
fingerprints, baselines, run IDs, or release gating.

The remaining v0.22 verifier blocks are reviewer-facing projections / declared inputs — none gates independently (`release_decision.decision` stays the only gate). They populate with real values only under `verify` mode (a `VerificationContext` from `agents-shipgate verify` or an equivalent scan context); a plain `scan` emits their stable empty shape:

- `capability_change` (v0.22+, semantic metadata v0.23+) — the diff-derived capability delta, grouped into `{enabled, added, removed, broadened, narrowed}` member lists over `action_surface_diff` / `tool_surface_diff`. Each `CapabilityChangeMember` carries `{id, direction, subject_kind, tool, action, scope, before_scope, after_scope, before_capability_id, after_capability_id, changed_hashes, semantic_direction, semantic_changes, risk_tags, release_impact, provenance_kind, confidence, rationale, related_finding_ids}`. `broadened` = more effective capability (wider scope, escalated effect, removed control); `narrowed` = less (removed scope, added control). `semantic_direction` explains the proven capability-level movement (`added | removed | broadened | narrowed | mixed | unknown | evidence_only`), and `semantic_changes[]` gives field-level reasons when a base action snapshot is available. `enabled: false` when no base diff is available.
- `protected_surface_changes` (v0.22+) — list of touched release trust roots, each `{path, kind, glob, related_finding_ids}`. Derived from the active `SHIP-VERIFY-*` findings, so every row's `related_finding_ids` resolves to a real `findings[]` entry and the rollup can never disagree with the gate. A row means "a protected file was touched"; purely-semantic weakenings with no file path stay in `findings[]` and surface via `verifier_summary` flags.
- `effective_policy` (v0.22+) — normalized (not text-diff) snapshot of the release-policy surface for base-vs-head weakening comparison: `{ci_mode, fail_on[], suppressed_check_ids[], waiver_scopes[], severity_overrides{}, baseline_integrity_mode, baseline_fingerprints[], ci_gate_present}`. Every list/dict is sorted for byte-stable output; derived purely from the manifest (plus accepted-debt fingerprints).
- `human_ack` (v0.22+) — declared human-acknowledgement state, `{required, satisfied, acks[], outstanding[]}`. Within the static boundary, acknowledgement is **declared evidence only — never inferred** (human authority cannot be synthesized). A trust-root weakening (`SHIP-VERIFY-POLICY-WEAKENED`, `-CI-GATE-REMOVED`, `-BASELINE-OR-WAIVER-EXPANDED`) makes a surface `required`; it is `satisfied` only by a matching `human_ack` entry in `shipgate.yaml` (owner + reason + affected surface, optional expiry). `required == (acks-covering-required) + outstanding`. The acknowledgement section lives in `shipgate.yaml` — itself a trust root — so a coding agent cannot add its own ack without tripping `SHIP-VERIFY-TRUST-ROOT-TOUCHED`.

New `SHIP-VERIFY-*` reason codes (v0.22+, category `verify` — suppression-immune and floor-protected; emit only under `verify` mode): `SHIP-VERIFY-POLICY-WEAKENED` (base-vs-head policy weakened; fail-safe to review when the base is unavailable), `SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` (suppression/waiver/baseline broadened), `SHIP-VERIFY-CI-GATE-REMOVED` (Shipgate CI workflow deleted), `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED` (agent-instruction trust root changed; routed to human review), `SHIP-VERIFY-TRIGGER-CATALOG-DRIFT` (trigger catalog changed). They are ordinary `Finding`s routed through `release_decision` — never a second verdict.

The action exposes these as outputs `decision`, `blocker_count`, `review_item_count`, `ci_would_fail` (v0.8+).
For verifier-cycle PR workflows it also exposes additive outputs
`should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
`merge_verdict`, `can_merge_without_human`, `trust_root_touched`,
`policy_weakened`, `capability_changes_added`,
`capability_changes_modified`, and `capability_changes_removed`. These are
review and routing aids only. `trust_root_touched` and `policy_weakened`
mirror `verifier_summary`; the capability counts mirror
`capability_change` (`modified` is `broadened + narrowed`). Keep using
`decision` as the preferred gating output.

For ongoing PR workflows, prefer:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

`verify` writes `verifier.json` and `pr-comment.md` alongside the head scan
artifacts. After a successful head scan it also writes the head static
capability lock to `agents-shipgate-reports/capabilities.lock.json`. When
`--base` is provided and the base tree contains the reviewed committed lock at
`.agents-shipgate/capabilities.lock.json`, verify writes
`agents-shipgate-reports/capability-lock-diff.json` and
`agents-shipgate-reports/capability-lock-diff.md`. The packet artifact is
intentionally `packet.json` only; use `scan` for manifest-driven packet
Markdown/HTML/PDF rendering. Read
`verifier.json.base_status` to understand whether base diff enrichment ran;
do not use it as a release verdict. The release gate is still
`report.json.release_decision.decision`. `verify` never fetches, so CI callers
must make the base ref available before invocation. Supplying `--head` makes
verify scan an isolated archive of that ref; omitting it scans the checked-out
workspace. If an explicit `--base` ref or PR diff cannot be inspected, verify
skips a head-only scan; `verifier.json.merge_verdict` is `unknown` and the
command exits 2.

`agents-shipgate verify --preview --json` is a lightweight relevance check — no
scan, no manifest required, exits 0. It emits a `verifier.json` with
`mode: "preview"` and a `first_next_action` carrying the next recommended action:
`none` for irrelevant diffs, `detect`/`init` for relevant unconfigured repos, or
`verify` for configured repos. Use it as the first touch before a full scan. To
evaluate just the run/skip trigger, run
`agents-shipgate trigger --base origin/main --head HEAD --json`.

In `agents-shipgate-reports/verifier.json`, read these additive fields
(`verifier_schema_version` stays `"0.1"`; full schema
[`docs/verifier-schema.v0.1.json`](verifier-schema.v0.1.json)). **Lead with
`merge_verdict`.** Every field below is a mirror or deterministic projection of
`report.json`; `release_decision.decision` remains the gate.

- `merge_verdict` — `"mergeable"` / `"human_review_required"` /
  `"insufficient_evidence"` / `"blocked"` / `"unknown"`. Deterministic projection
  of `release_decision.decision` (`passed`→`mergeable`,
  `review_required`→`human_review_required`,
  `insufficient_evidence`→`insufficient_evidence`, `blocked`→`blocked`, missing
  decision→`unknown`). It cannot disagree with the gate; switch on the enum with
  an `unknown`/`human_review_required` fallback for future values.
- `applicability` (v0.12.0+) — `"verified"` / `"not_applicable"` / `"unknown"`.
  Disambiguates a `mergeable` verdict: `"verified"` means Shipgate evaluated the
  change and produced a release decision; `"not_applicable"` means the head scan
  was skipped (nothing to gate — do **not** read this as "verified safe");
  `"unknown"` means the scan could not complete. Orthogonal to `merge_verdict`;
  additive and locked to `"verified"` whenever a `release_decision` is present.
- `can_merge_without_human` — `bool`.
- `decision` — mirror of `release_decision.decision` (or `null` when no scan ran).
- `headline` — single-sentence, PR-comment-friendly summary (or `null`).
- `human_review` — `{required: bool, why: str|null}`.
- `first_next_action` — `{actor: "coding_agent"|"human", kind, command, why}`.
  The `actor` separates mechanical coding-agent work from human-only decisions.
- `fix_task` — `{actor, safe_to_attempt, instructions[], forbidden_shortcuts[],
  verification_command}` or `null`. This is the deterministic repair boundary:
  `actor: coding_agent` with `safe_to_attempt: true` means the agent may attempt
  the listed mechanical fix and rerun `verification_command`; `actor: human`
  means the agent must not invent approval, idempotency, policy, waiver,
  baseline, or trust-root evidence to make the gate pass.
- `agent_controller` (v0.12.0+) — `null` for `--preview`; otherwise the
  imperative restatement of the verdict for autonomous control:
  `{completion_allowed, must_stop, stop_reason, allowed_next_commands[],
  forbidden_file_edits[], forbidden_actions[], user_message_template}`.
  `completion_allowed` is locked to `can_merge_without_human` (never a second
  verdict); `must_stop` is `true` only when the agent can neither finish nor
  safely repair; `stop_reason` ∈ `{self_approval_prohibited, blocked_findings,
  insufficient_evidence, human_review_required, scan_incomplete}`.
  `forbidden_file_edits[]` is a standing deny-list of whole-file trust roots (CI
  gate, agent instructions, policy packs) — **not** an allow-list — and
  deliberately excludes `shipgate.yaml` / `.agents-shipgate` (key-level, covered
  by `forbidden_actions[]`) and the tool surface under review. Both forbidden
  lists are present on every verdict, including `mergeable`, so a passing run is
  never read as "anything goes".
- `trust_root_touched` — `bool`; `true` when the PR changed a release-gate trust
  root (`shipgate.yaml`, the Shipgate CI workflow, `AGENTS.md`/`CLAUDE.md`,
  policy packs, prompts, baselines, waivers, etc.). Backed by the
  `SHIP-VERIFY-TRUST-ROOT-TOUCHED` check.
- `capability_review` — reviewer-facing projection of `capability_change` with
  `{trust_root_touched, policy_weakened, capability_changes_added,
  capability_changes_removed, capability_changes_modified, top_changes[]}`.
  `top_changes[]` carries the highest-signal capability deltas with
  `{id, title, impact, rationale, related_finding_ids}`. `impact` mirrors the
  gate (`blocks_release`, `review_required`, `insufficient_evidence`, or
  informational values) and never introduces a finding-independent blocker.
- `mode` — `"advisory"` / `"strict"` / `"skipped"` / `"preview"`.

`verifier.json` also carries `trigger`, `base_status`, `head_status`, `base_ref`,
`head_ref`, `changed_files`, `base_notes`, the embedded `release_decision`, and an
`artifacts` map. When present, `artifacts.capability_lock`,
`artifacts.capability_lock_diff_json`, and
`artifacts.capability_lock_diff_markdown` are review artifacts only; they do not
change the gate. The matching GitHub Action outputs are `merge_verdict`,
`can_merge_without_human`, `trust_root_touched`, and
`capability_changes_{added,modified,removed}` (the original `decision`,
`blocker_count`, `review_item_count`, `ci_would_fail` outputs are preserved). See
[STABILITY.md §Verify Orchestrator](../STABILITY.md#verify-orchestrator) for the
authoritative contract.

The default Action PR comment style for the verifier-cycle minor is
`capability-review`: verdict/decision first, then the semantic capability-lock
diff when a base lock is available; otherwise the top
`capability_review.top_changes[]` projection, followed by trust-root warnings,
required next steps, and artifact links. Existing adopters that need the v1
findings-oriented comment during migration can set `pr_comment_style: findings`
for one minor release cycle.

## Read this for coding-agent control

`shipgate check --agent codex --workspace . --format agent-json` is the
canonical local coding-agent command. Use `--agent claude-code` or
`--agent cursor` for those runtimes. The command emits exactly one stdout JSON
object using `schema_version: "agent_result_v1"` and the schema in
[`agent-result-schema.v1.json`](agent-result-schema.v1.json).

Coding agents should switch on `decision`, `completion_allowed`, `must_stop`,
`first_next_action`, `repair`, and `human_review`. Do not derive an agent
decision from Markdown, PR comments, or natural language. `agents-shipgate
verify` and `report.json` remain the full CI/reviewer substrate.

## Read these for release review

`agents-shipgate contract --json` exposes `manual_review_signals[]` as the
installed CLI's stable list of report/packet fields to inspect for human review
work. `findings[].provenance_kind` is included there as a filter/review signal
only; it never changes the release decision, severity, fingerprints, baselines,
or CI exit behavior.

Runtime contract `2` also exposes stable non-gating integration fields:
`capability_lock_schema_version`, `capability_lock_diff_schema_version`,
`capability_standard_version`,
`governance_benchmark_catalog_schema_version`,
`governance_benchmark_result_schema_version`, and
`external_integration_surfaces[]`. These advertise capability lock/diff and
benchmark artifacts for integrations and research. They do not change the gate:
`release_decision.decision` remains the only release decision signal.

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
- `runtime_trace` — derived from declared local trace artifacts. Audit evidence only; never filtered by `--no-heuristics`.

Provenance generally follows the rule's own trigger (e.g., a rule that checks for a declared manifest field is `static_declaration` even when the underlying Tool was AST-extracted). For framework checks that fire across both AST and declarative tool sources (ADK's per-tool checks against `google_adk_function` AND `google_adk_config` tools), the label tracks the underlying tool's source. Third-party plugin checks that don't yet set the field land at `static_declaration` by default — pre-v0.15 plugins continue to validate against the v0.15 wire schema. Use `findings[].source.type` for the precise underlying tool source.

To filter operationally, use:

```bash
agents-shipgate findings --from agents-shipgate-reports/report.json \
  --provenance-kind keyword_heuristic,regex_heuristic --json
```

The command reads active findings by default; add `--include-suppressed` when a
reviewer needs suppressed entries in the same provenance summary.

For reviewer-shaped output, also read the **Release Evidence Packet** at `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` when the `[pdf]` extras are installed). Packet outputs are redacted by the same default privacy layer as the report. The packet has fixed reviewer sections governed by [`docs/packet-schema.v0.7.json`](packet-schema.v0.7.json) — see [STABILITY.md §Release Evidence Packet](../STABILITY.md#release-evidence-packet-v07).
Packet schema `0.7` adds capability-linked trace summary and trace refs under
`human_in_the_loop`. Packet schema `0.6` preserved the v0.5
`action_surface_diff` section and added two independent additive extensions:

- `evidence_matrix` (PR #104) — a compact packet-only review aid
  derived from public `report.json` fields. The matrix never contributes
  to `release_decision`, CI exit behavior, severity, suppression,
  baseline matching, or `agent_summary`; its blocker and review-item
  cells are copied from `release_decision`.
- `ReleaseDecisionItem.source` and `ReleaseDecisionItem.policy_evidence_source`
  (PR #103) — packet §1 / §2 re-renders carry the same dual-source
  provenance that `Finding.source` / `Finding.policy_evidence_source`
  expose in the report.

It preserves every v0.5 field
(`human_in_the_loop.runtime_control_disclaimer`,
`human_in_the_loop.source_provenance[]`, `action_surface_diff`). The
`release_decision.verdict` label includes `INSUFFICIENT EVIDENCE` when
the report decision is insufficient evidence.

## Don't use for new gating

- `summary.status` — preserved for v0.7 callers, **baseline-blind**. A baseline-matched critical flips this to `release_blockers_detected` even though `release_decision.decision` correctly classifies it as `review_required`. New consumers should not gate on `summary.status`. See [STABILITY.md §`release_decision.decision` vs `summary.status`](../STABILITY.md#release_decisiondecision-vs-summarystatus).

## Per-finding contextual explanation (v0.12+)

For prose summaries of a single finding (PR comments, chat replies, commit messages), use:

```bash
agents-shipgate explain-finding <FINGERPRINT> \
    --from agents-shipgate-reports/report.json --json
```

The payload is the full `Finding` shape (every field on `findings[]` in `report.json`, including `source`, `patches`, `confidence`, `agent_id`, etc.) overlaid with three derived fields:

- `metadata` — full `CheckMetadata` for the check_id (rationale, fires_when, evidence_fields, docs_url, `mvp_tier`) when the check is in the catalog; null for unknown ids (third-party plugins, future checks). `mvp_tier` is display/triage metadata only and never affects gating.
- `explanation` — a deterministic 3–5 sentence prose summary suitable for direct quotation. Names the affected tool, the severity, the recommended fix, and an action-aware closing sentence keyed to `agent_action`. Same inputs always produce the same output.
- `source_report` — **absolute** path (always; relative `--from` values are resolved before serialization) to the report file the explanation was sourced from; round-trippable for caching and audit.

`explain-finding` requires `report_schema_version >= 0.12` because the action-aware explanation depends on per-finding `agent_action`. Pre-v0.12 reports are rejected with `input_parse_error` and a `next_action` pointing at the canonical scan command. The Pydantic `ReadinessReport` model is intentionally looser than this command's contract (so test fixtures can construct minimal findings); the version gate is what enforces v0.12 semantics on emitted reports.

Companion prompt: [`prompts/explain-finding-to-user.md`](../prompts/explain-finding-to-user.md). Use it when you need to translate a finding for a human who has never read the Shipgate docs. Keep `agents-shipgate explain <CHECK_ID>` for static catalog metadata (no specific finding); use `explain-finding` whenever you have a fingerprint and want the evidence-tied prose.

## Authoritative references

- [STABILITY.md](../STABILITY.md) — full 0.x stability contract. Source of truth for everything above.
- [AGENTS.md](../AGENTS.md) — agent-facing instructions: install, run, single-turn flow, error semantics.
- [`docs/report-schema.v0.26.json`](report-schema.v0.26.json) — machine-validatable JSON Schema for the current report.
- [`docs/privacy.md`](privacy.md) and [`docs/report-sensitive-fields.json`](report-sensitive-fields.json) — default redaction behavior and sensitive-field inventory.
- [`docs/packet-schema.v0.7.json`](packet-schema.v0.7.json) — machine-validatable JSON Schema for the current packet.
- [`docs/checks.json`](checks.json) — check catalog, including `mvp_tier` for MVP/readiness triage.

## See also

- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer that walks the JSON in the order a new consumer should read it; complements this field index.
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may assert mechanically vs. what must defer to a human reviewer when surfacing findings from `report.json`.
