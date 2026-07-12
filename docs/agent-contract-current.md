# Current Agent Contract

The single, current statement of what AI coding agents and CI integrations should read from Agents Shipgate output. When the contract changes, update [STABILITY.md](../STABILITY.md) first, then this file. Other agent-facing surfaces (`AGENTS.md`, `llms.txt`, `.well-known/agents-shipgate.json`, the slash command, the skill, the FAQ) link here instead of restating field lists.

## Current versions

Verify the installed CLI contract locally before relying on hard-coded docs:

```bash
agents-shipgate contract --json
```

Runtime contract v13 proves the root-reachable agent-to-tool graph before
evaluating capabilities and publishes report v0.32, packet v0.10, capability
standard v0.4, capability lock v0.5, and capability-lock diff v0.6. Report
fields are additive over v0.31,
but the pre-1.0 meaning of `passed` is deliberately stricter. v10 added
`verify_required` to `agent_result_control_fields` and to the boundary result.
The runtime contract also exposes the local agent command spec:
`primary_commands{}`, `commands{}`, `default_paths{}`, `artifacts{}`,
`agent_read_order[]`, `verifier_read_order[]`, `merge_verdicts[]`,
`release_decisions[]`, `do_not_auto_assert[]`, `verifier_schema_version`,
`verify_run_schema_version`, `agent_handoff_schema_version`,
`agent_handoff_schema_path`, `agent_handoff_artifact`,
`codex_boundary_result_schema_version`, `attestation_schema_version`,
`registry_schema_version`, `org_evidence_bundle_schema_version`,
`host_grants_inventory_schema_version`, `agent_interface_operations[]`,
`exit_code_policy`, `mcp_tools[]`, and the legacy `agent_result_*` fields
retained for older protocol consumers. `primary_commands{}` is the prominent
entry surface and contains only `shipgate check`, `agents-shipgate verify`, and
`shipgate audit --host` flows; `commands{}` is compatibility/supporting metadata
and retains local verify commands for older consumers.
The short `shipgate verify` alias remains invokable for compatibility, but it is
not the promoted PR-gate spelling in `primary_commands{}`.
Contract v11 adds `action_effect` and `action_authority` to
`do_not_auto_assert[]`. They are reviewed human claims that can close semantic
evidence gaps; an agent may route the structured next action but must never
invent or auto-fill either declaration.
Downstream repos generated with
`init --agent-instructions=default` get the minimal local copy at
`.shipgate/agent-contract.json`.

- Latest release: `v0.15.0`
- In-tree runtime: `0.16.0b2` — see [pyproject.toml](../pyproject.toml)
- Runtime contract: `13`
- Current report schema: `0.32` — [`docs/report-schema.v0.32.json`](report-schema.v0.32.json)
- Current packet schema: `0.10` — [`docs/packet-schema.v0.10.json`](packet-schema.v0.10.json)
- Current verifier schema: `0.2` — [`docs/verifier-schema.v0.2.json`](verifier-schema.v0.2.json)
- Current verify-run schema: `shipgate.verify_run/v1` — [`docs/verify-run-schema.v1.json`](verify-run-schema.v1.json)
- Current agent handoff schema: `shipgate.agent_handoff/v2` — [`docs/agent-handoff-schema.v2.json`](agent-handoff-schema.v2.json)
- Current Codex boundary result schema: `shipgate.codex_boundary_result/v1` — [`docs/codex-boundary-result-schema.v1.json`](codex-boundary-result-schema.v1.json)
- Current preflight schema: `0.2` — [`docs/preflight-schema.v0.2.json`](preflight-schema.v0.2.json)
- Current capability standard: `0.4` — [`docs/capability-standard.md`](capability-standard.md)
- Current capability lock schema: `0.5` — [`docs/capability-lock-schema.v0.5.json`](capability-lock-schema.v0.5.json)
- Current capability lock diff schema: `0.6` — [`docs/capability-lock-diff-schema.v0.6.json`](capability-lock-diff-schema.v0.6.json)
- Current attestation schema: `0.4` — [`docs/attestation-schema.v0.4.json`](attestation-schema.v0.4.json)
- Current registry schema: `0.3` — [`docs/registry-schema.v0.3.json`](registry-schema.v0.3.json)
- Current org evidence bundle schema: `shipgate.org_evidence_bundle/v1` — [`docs/org-evidence-bundle-schema.v1.json`](org-evidence-bundle-schema.v1.json)
- Current host-grants inventory schema: `0.1` — [`docs/host-grants-inventory-schema.v0.1.json`](host-grants-inventory-schema.v0.1.json)
- Current governance benchmark catalog schema: `0.2` — [`docs/governance-benchmark-catalog-schema.v0.2.json`](governance-benchmark-catalog-schema.v0.2.json)
- Current governance benchmark result schema: `0.2` — [`docs/governance-benchmark-result-schema.v0.2.json`](governance-benchmark-result-schema.v0.2.json)
- Frozen-reference report schemas: frozen [`v0.31`](report-schema.v0.31.json), frozen [`v0.30`](report-schema.v0.30.json), frozen [`v0.29`](report-schema.v0.29.json), frozen [`v0.28`](report-schema.v0.28.json), frozen [`v0.27`](report-schema.v0.27.json), frozen [`v0.26`](report-schema.v0.26.json), frozen [`v0.25`](report-schema.v0.25.json), frozen [`v0.24`](report-schema.v0.24.json), frozen [`v0.23`](report-schema.v0.23.json), frozen [`v0.22`](report-schema.v0.22.json), frozen [`v0.21`](report-schema.v0.21.json), frozen [`v0.20`](report-schema.v0.20.json), frozen [`v0.19`](report-schema.v0.19.json), frozen [`v0.18`](report-schema.v0.18.json), frozen [`v0.17`](report-schema.v0.17.json), frozen [`v0.16`](report-schema.v0.16.json), frozen [`v0.15`](report-schema.v0.15.json), frozen [`v0.14`](report-schema.v0.14.json), frozen [`v0.13`](report-schema.v0.13.json), frozen [`v0.12`](report-schema.v0.12.json), frozen [`v0.11`](report-schema.v0.11.json), frozen [`v0.10`](report-schema.v0.10.json), frozen [`v0.9`](report-schema.v0.9.json), frozen [`v0.8`](report-schema.v0.8.json), frozen [`v0.7`](report-schema.v0.7.json), frozen [`v0.6`](report-schema.v0.6.json), older
- Frozen-reference packet schemas live in [`docs/INDEX.md`](INDEX.md#reference).
- Frozen experimental capability lock and governance benchmark result schemas live in [`docs/INDEX.md`](INDEX.md#reference).

## Two read entry points

There are two correct "read first" paths; which one applies depends on who is
reading. They are not two decisions — they are two entry points into the same
one decision engine.

- **PR / controller flow** — an autonomous coding agent deciding *continue,
  repair, or stop*. Prefer
  `agents-shipgate-reports/agent-handoff.json` for the compact
  `shipgate.agent_handoff/v2` view: lead with `gate.merge_verdict`, then read
  `controller` for imperative controls and `reproducibility.run_id` for the
  stable verify identity. `verifier.json` remains the authoritative controller
  substrate and `verify-run.json` remains the reproducibility record; finally
  confirm `report.json.release_decision.decision` for the release gate.
  `.well-known/agents-shipgate.json` → `agent_read_order` is the
  machine-readable cross-artifact order. `verifier_read_order` remains the
  intra-`verifier.json` field order.
- **Gate / CI flow** — deciding pass/fail, or any raw `report.json` consumer.
  Read `agents-shipgate-reports/report.json` → `release_decision.decision` (the
  next section). `.well-known` → `gating_signal` names this signal.

`merge_verdict` is a deterministic projection of `release_decision.decision`, so
the two can never disagree.

## Primary vs supporting surfaces

Primary gates are intentionally narrow. CI gates on
`report.json.release_decision.decision`. Coding agents handling committed PRs
read `agent-handoff.json.gate.merge_verdict` and `controller` first, with
`verifier.json.merge_verdict`, `applicability`, and `agent_controller` as the
authoritative detailed substrate. Everything else in the
verifier/report/packet family is supporting review evidence or a convenience
projection.

Treat legacy `agent_result_v1` / `agent-result.json` compatibility surfaces,
runtime trace/evidence fields, the Release Evidence Packet, `reviewer_summary`,
`verifier_summary`, `capability_review`, non-gating capability diff
projections, and `agents-shipgate skill ...` review output as
supporting/provisional surfaces. They may be useful for routing and review, but
they do not replace the gate above and must not introduce a second verdict.

`agents-shipgate preflight --workspace . --plan - --json` remains a supporting
proactive routing surface for coding agents before edits. It accepts a single
`PreflightPlanV1` object with `changed_files[]`, optional `diff_text`,
`capability_requests[]`, `host_permission_requests[]`, and
`context.{agent,task}`. The emitted `PreflightResultV2` reports protected
surfaces, forbidden shortcut actions, required evidence for proposed high-risk
capabilities, host-grant drift when a host baseline is present, deterministic
`signals[]`, `requires_verify`, `verification_command`, `allowed_next_commands[]`,
and `plan_summary`. It is not a second gate; it must never be read as passed or
mergeable. The release gate remains `release_decision.decision`.

## Read these first for release gating

In `agents-shipgate-reports/report.json`:

- `release_decision.decision` — `"blocked"` / `"review_required"` / `"insufficient_evidence"` / `"passed"`. Baseline-aware. **This is the gating signal.** Precedence is `blocked` → `review_required` (active high/critical named concern) → `insufficient_evidence` → `review_required` (known review concern) → `passed`. Starting in v0.29, `passed` means every in-scope action has complete, conflict-free static surface, effect, and authority evidence, all applicable controls were evaluated, and no policy condition requires review. It does not prove runtime behavior or enforcement. Any required semantic dimension that is unknown, inferred-only, protocol-defaulted, partial, conflicting, invalid, or incomplete prevents `passed`, even when every other action is healthy. Existing extraction thresholds remain: low-confidence tools at least `max(1, ceil(tool_count × 0.5))` or more than three source-loader warnings also degrade evidence. `insufficient_evidence` means the scan cannot confidently gate release from the available static evidence; it does not prove the agent is unsafe. Switch on the enum with a `review_required` fallback for unknown future values.
- `release_decision.blockers[]` — items that block release on this run.
- `release_decision.review_items[]` — items the human reviewer should look at; includes baseline-matched accepted debt.
- `release_decision.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}` (v0.29+) — the machine-readable verdict boundary. Emitted values are `true`, `false`, and the canonical static-verdict disclaimer respectively. Packet §1 mirrors them exactly. Preserve these fields in agent summaries; `passed` must never be rewritten as runtime verification or safety proof.
- `release_decision.{blockers,review_items}[].capability_refs` (v0.24+) — stable capability IDs copied from the originating finding when a policy or policy-pack rule matched a `CapabilityFactV1`. Empty for findings that are not capability-policy matches. This is audit metadata only; `release_decision.decision` remains the gate.
- `release_decision.{blockers,review_items}[].capability_trace_refs` (v0.25+) — stable local trace-evidence IDs copied from the originating finding when an existing trace/evidence check used declared local trace artifacts. Empty when no local trace row is relevant. This is audit metadata only; `release_decision.decision` remains the gate.
- `release_decision.evidence_coverage.semantic_coverage` (v0.29+) — `{total_actions, pass_eligible_actions, gap_count, review_concern_count, reason_counts}`. A non-zero semantic `gap_count` prevents `passed`; a non-zero `review_concern_count` prevents an automatic pass and routes known unscoped/ambient authority to review. Semantic gaps are not Findings and cannot be suppressed, baselined, severity-overridden, waived by `--no-heuristics`, or satisfied by `human_ack`.
- `release_decision.evidence_coverage.identity_coverage` (v0.30+) — `{total_observations, canonical_tools, bound_tools, pass_eligible_tools, ambiguous_name_count, gap_count, reason_counts}`. Provider-scoped observations remain separate unless an exact reviewed `tool_identity.bindings[]` entry joins them. Any ambiguous selector, invalid binding, or conflicting identity prevents `passed`.
- `release_decision.evidence_coverage.evidence_gaps[]` (v0.26+; semantic kinds added v0.29) — one structured row per measurable gap: `{kind, subject, source_type, source_ref, why, next_action}`. In addition to `low_confidence_tool` and `source_warning`, v0.29 adds `incomplete_surface`, `missing_effect_evidence`, `inferred_effect_only`, `conflicting_effect_evidence`, `missing_authority_evidence`, `partial_authority_evidence`, `conflicting_authority_evidence`, and `invalid_semantic_annotation`. Semantic next actions use `declare_action_effect`, `declare_action_authority`, `provide_complete_inventory`, or `resolve_semantic_conflict`, include accepted values and exact source/manifest pointers, and are always human-routed. Their declaration placeholders carry `suggested_patch_kind="manual"`, `auto_apply=false`, and `requires_human_review=true`; they are not Patch objects. Work the rows in order instead of guessing; Agents Shipgate never auto-asserts effect or authority.
- `loaded_policy_packs[].{source,sha256,sha256_status,owner}` (v0.27+) — policy-pack distribution and ownership metadata for organization audit. `sha256_status` is `"verified"` only when the manifest pin matched; otherwise it is `"unpinned"`. This is report metadata; normal pack matching and release gating still come from deterministic rules and `release_decision.decision`.
- `findings[].policy_routing` (v0.28+) — optional policy-pack owner, reviewers, and approval-routing metadata. This is non-enforcing reviewer/audit metadata, not `Finding.evidence`; it does not affect fingerprints, suppressions, baselines, `blocks_release`, or `release_decision`. Policy-pack `match` predicates and `block: true` remain the only policy-pack inputs that affect findings and release gating.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches what
  the CI process will exit with. For a semantic evidence gap, strict mode emits
  the consistent tuple `decision="insufficient_evidence"`,
  `would_fail_ci=true`, `exit_code=20`; advisory mode keeps exit `0` while
  preserving the same non-pass decision.
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
- `policy_routing | null` — optional policy-pack routing metadata with
  `owner`, `reviewers`, and `approval.{required,teams,min_approvals,enforced}`.
  `approval.enforced` is always `false`; Shipgate validates declared team names
  but does not verify external approval systems or make release decisions from
  these fields.

Deterministic match and gating `finding.evidence` keys remain stable for legacy
readers. Policy-pack routing keys that used to live in `Finding.evidence` now
live in `policy_routing`; old baseline fingerprints are still matched during
baseline comparison. Policy matching is capability-native internally, but
policy-pack behavior, suppressions, severity overrides, baselines, SARIF,
Markdown, and GitHub Action outputs remain compatible.

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
`verifier_json`, `verify_run_json`, `run_id`, `merge_verdict`,
`can_merge_without_human`, `agent_controller_must_stop`,
`agent_controller_stop_reason`, `agent_controller_completion_allowed`,
`merge_verdict`, `can_merge_without_human`, `trust_root_touched`,
`policy_weakened`, `capability_changes_added`,
`capability_changes_modified`, and `capability_changes_removed`. These are
review and routing aids only. `trust_root_touched` and `policy_weakened`
mirror `verifier_summary`; the capability counts mirror
`capability_change` (`modified` is `broadened + narrowed`). Keep using
`decision` as the preferred gating output.

When the action is asked to emit organization-governance artifacts, it also
exposes `attestation_json`, `org_evidence_bundle_json`, `host_grants_json`, and
`org_status_json` as artifact paths. These are ingestion and audit surfaces for
platform teams; they never create a second verdict.

For ongoing PR workflows, prefer:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

`verify` writes `verifier.json`, `verify-run.json`, `agent-handoff.json`, and
`pr-comment.md` alongside the head scan artifacts. `agent-handoff.json` is the
compact coding-agent projection over the verifier, verify-run, and report
artifacts; it does not gate independently. After a successful head scan it also writes the head static
capability lock to `agents-shipgate-reports/capabilities.lock.json`. When
`--base` is provided and the base scan can be materialized, verify writes
`agents-shipgate-reports/base.capabilities.lock.json`,
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
an exact `init --workspace <workspace> --write --ci --agent-instructions=default --json`
command for unconfigured repos, or an exact `verify` command for configured
repos using the supplied workspace/config/base/head/out arguments. Use it as the
first touch before a full scan. To evaluate just the run/skip trigger, run
`agents-shipgate trigger --base origin/main --head HEAD --json`.

`agents-shipgate verify` and `verify --preview` also write
`agents-shipgate-reports/verify-run.json` whenever the output directory can be
created. It carries `schema_version: "shipgate.verify_run/v1"`, a deterministic
`run_id` over `{tool, subject, inputs}` (outcome and artifact hashes are carried
separately), local input hashes (`config_sha256`, `baseline_sha256`,
`policy_packs[].sha256`), the outcome projection, and artifact hashes for
emitted files. It has no wall-clock timestamp and is not a second gate.

`agents-shipgate-reports/agent-handoff.json` carries
`schema_version: "shipgate.agent_handoff/v2"` and top-level sections
`gate`, `controller`, `next_action`, `human_review`, `fix_task`, `blocked_by[]`,
`remediation_plan[]`, `capability_review`, `reproducibility`, and `artifacts`.
`gate.decision` mirrors `release_decision.decision`; `gate.merge_verdict`
mirrors `verifier.json.merge_verdict`; and
`gate.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}`
mirrors the report/verifier static-only boundary. The values are locked to
`true`, `false`, and the canonical disclaimer. Finally,
`controller.completion_allowed` mirrors `can_merge_without_human`. Re-render it
from existing artifacts with:

```bash
agents-shipgate agent handoff --from agents-shipgate-reports/verifier.json --json
```

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
- `static_analysis_only`, `runtime_behavior_verified`, and
  `static_verdict_disclaimer` — locked to `true`, `false`, and the canonical
  non-runtime disclaimer. When a release decision is embedded, construction
  rejects any disagreement between these top-level values and the decision.
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
- `fix_task` — `{actor, safe_to_attempt, instructions[], allowed_repairs[],
  forbidden_repairs[], forbidden_shortcuts[], verification_command, patches[]}` or `null`.
  This is the deterministic repair boundary: `actor: coding_agent` with
  `safe_to_attempt: true` means the agent may attempt only the listed mechanical
  `allowed_repairs[]` and rerun `verification_command`; `actor: human` means the
  agent must not invent action effect, action authority, approval,
  idempotency, policy, waiver, baseline, or trust-root evidence to make the
  gate pass. `forbidden_repairs[]` explicitly
  lists reward-hacking moves such as suppressing findings, lowering severity,
  expanding baselines/waivers, weakening CI or policy, adding human ack, or
  inventing action-effect/action-authority/approval/idempotency evidence.
  `patches[]` (v0.13+) carries
  `{finding_id, check_id, patch}` rows with the
  machine-applicable suggested patches for the gating findings — populated
  only when verify ran with `--suggest-patches` and the task routes to the
  coding agent; repair aids, never gate inputs.
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
  `{id, change_type, change_bucket, subject_kind, subject, impact, rationale,
  source_path, source_start_line, related_finding_ids}`. `impact` mirrors the
  gate (`blocks_release`, `review_required`, `insufficient_evidence`, or
  informational values) and never introduces a finding-independent blocker.
- `mode` — `"advisory"` / `"strict"` / `"skipped"` / `"preview"`.

`verifier.json` also carries `trigger`, `base_status`, `head_status`, `base_ref`,
`head_ref`, `changed_files`, `base_notes`, the embedded `release_decision`, and an
`artifacts` map. When present, `artifacts.capability_lock_json`,
`artifacts.base_capability_lock_json`,
`artifacts.capability_lock_diff_json`, and
`artifacts.capability_lock_diff_markdown` are review artifacts only; they do not
change the gate. The matching GitHub Action outputs are `merge_verdict`,
`can_merge_without_human`, `agent_controller_must_stop`,
`agent_controller_stop_reason`, `agent_controller_completion_allowed`,
`trust_root_touched`, and
`capability_changes_{added,modified,removed}` (the original `decision`,
`blocker_count`, `review_item_count`, `ci_would_fail` outputs are preserved). See
[STABILITY.md §Verify Orchestrator](../STABILITY.md#verify-orchestrator) for the
authoritative contract.

The default Action PR comment style for the verifier-cycle minor is
`capability-review`: exactly two reviewer sections, a human summary and a
fenced JSON agent instruction block. The human summary leads with
`merge_verdict`, `can_merge_without_human`, capability delta, next actor, and
artifact links, including the semantic capability-lock diff summary when a base
lock is available. The agent block carries `first_next_action`, `fix_task`, and
`agent_controller` for coding-agent routing. Existing adopters that need the v1
findings-oriented comment during migration can set `pr_comment_style: findings`
for one minor release cycle.

The GitHub Action emits source-backed GitHub Actions job annotations by default
for active blockers and review items. `check_annotations: "false"` disables the
projection; `check_annotation_limit` caps the number emitted. The helper also
writes `agents-shipgate-reports/check-annotations.json` for audit/debug.

`verify` writes non-gating capability artifacts when static extraction succeeds:
`agents-shipgate-reports/capabilities.lock.json` for head, and when a base ref
is available, `base.capabilities.lock.json` plus `capability-lock-diff.json`.
These artifacts are review/integration surfaces only and cannot introduce a
second verdict.

## Read this for local boundary control

`shipgate check --agent <codex|claude-code|cursor> --workspace . --format
codex-boundary-json` is the local Codex boundary command. The command emits
exactly one stdout JSON object using
`schema_version: "shipgate.codex_boundary_result/v1"` and the schema in
[`codex-boundary-result-schema.v1.json`](codex-boundary-result-schema.v1.json).
The removed `--format agent-json` alias and `agent_result_v1` schema string are
breaking 0.14.0 changes; see [STABILITY.md](../STABILITY.md#migration-note-0-14-0).

Coding agents should switch on `decision`, `completion_allowed`, `must_stop`,
`first_next_action`, `human_review`, `repair`, `policy`, and `verify_required`. Do not derive an agent
decision from Markdown, PR comments, or natural language.

`verify_required` (contract v10, additive) is the machine-readable
check→verify deferral: `true` whenever the diff touches a tool surface —
declared or undeclared — that the boundary check does not gate. The evaluator
simultaneously escalates what would otherwise be a clean `allow` to
`decision="warn"`, so the observable pair is `decision="warn"` with
`verify_required=true`: "no boundary rule fired, but capability is not yet
gated" — run `agents-shipgate verify` and read `release_decision.decision`
before reporting completion. A plain `decision="allow"` always has
`verify_required=false`. It is a deterministic
projection of the same deferral that emits the
`capability_change_requires_verify` / `undeclared_capability_surface`
diagnostics — not a second verdict. Do not confuse this
local boundary result with `agents-shipgate verify`: verify writes
`agent-handoff.json`, `verifier.json`, and `verify-run.json`, and
`report.json` remains the full CI/reviewer substrate.

## Read these for release review

`agents-shipgate contract --json` exposes `manual_review_signals[]` as the
installed CLI's stable list of report/packet fields to inspect for human review
work. `findings[].provenance_kind` is included there as a filter/review signal
only; it never changes the release decision, severity, fingerprints, baselines,
or CI exit behavior.

The runtime contract also exposes stable non-gating integration fields:
`agent_handoff_schema_version`, `agent_handoff_schema_path`,
`agent_handoff_artifact`, `agent_interface_operations[]`, `exit_code_policy`,
`mcp_tools[]`,
`capability_lock_schema_version`, `capability_lock_diff_schema_version`,
`capability_standard_version`,
`governance_benchmark_catalog_schema_version`,
`governance_benchmark_result_schema_version`, and
`external_integration_surfaces[]`. These advertise capability lock/diff and
benchmark artifacts for integrations and research. They do not change the gate:
`release_decision.decision` remains the only release decision signal.

The capability/intent diff fields (v0.9+), used by reviewers to spot misalignment between declared agent intent and actual tool surface:

- `capability_facts[]` — every capability surfaced from the tool inventory. In v0.29 each newly emitted fact carries `semantic_assessment`, the normalized effect/authority claims, issues, conservative effect, and pass-eligibility state consumed by the gate.
- `declared_intentions[]` — what the manifest says the agent is supposed to do.
- `misalignments[]` — where capabilities exceed (or fall short of) declared intent.
- `release_consequence` — capability-aware roll-up of the release decision.
- `suggested_scenarios[]` — dynamic-validation scenarios derived from misalignments and findings.

The Action Surface Diff fields (v0.16+), reviewer-facing PR/release delta:

- `action_surface_facts.actions[]` — deterministic snapshot of the current agent action surface: action id, operation, effect, normalized risk tags, scopes, approval policy, safeguards, evidence, hashes, and (v0.29+) the same `semantic_assessment` projected onto the corresponding capability fact. `semantic_assessment.conservative_effect`, `action.effect`, and the capability effect must agree.
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

For reviewer-shaped output, also read the **Release Evidence Packet** at
`agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` when the
`[pdf]` extras are installed). The packet is a supporting/provisional reviewer
projection, not a second gate. Packet outputs are redacted by the same default
privacy layer as the report. The packet has fixed reviewer sections governed by
[`docs/packet-schema.v0.10.json`](packet-schema.v0.10.json) — see
[STABILITY.md §Release Evidence Packet](../STABILITY.md#release-evidence-packet-v010).
Packet schema `0.9` carries the report's evidence-backed semantic coverage and
gap remediation contract. Packet §1 also mirrors
`static_analysis_only=true`, `runtime_behavior_verified=false`, and
`static_verdict_disclaimer` from the report release decision. Frozen packet
schema `0.7` added capability-linked
trace summary and trace refs under `human_in_the_loop`; frozen schema `0.6`
preserved the v0.5
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

- [STABILITY.md](../STABILITY.md) — full alpha stability contract. Source of truth for everything above.
- [AGENTS.md](../AGENTS.md) — agent-facing instructions: install, run, single-turn flow, error semantics.
- [`docs/report-schema.v0.32.json`](report-schema.v0.32.json) — machine-validatable JSON Schema for the current report.
- [`docs/privacy.md`](privacy.md) and [`docs/report-sensitive-fields.json`](report-sensitive-fields.json) — default redaction behavior and sensitive-field inventory.
- [`docs/packet-schema.v0.10.json`](packet-schema.v0.10.json) — machine-validatable JSON Schema for the current packet.
- [`docs/checks.json`](checks.json) — check catalog, including `mvp_tier` for MVP/readiness triage.

## See also

- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer that walks the JSON in the order a new consumer should read it; complements this field index.
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may assert mechanically vs. what must defer to a human reviewer when surfacing findings from `report.json`.
