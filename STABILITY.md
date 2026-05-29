# Stability Contract · 0.x

What agents and CI integrations can rely on across versions of Agents Shipgate.

This document is the contract. If the runtime ever diverges from what's documented here, that's a bug — please file an issue.

---

## What WILL NOT change in 0.x

### CLI command surface

These commands and flags are stable across all `0.x.y` releases. They will only change in a major version bump (`1.0.0`):

| Command | Stable flags |
|---|---|
| `agents-shipgate scan` | `-c`, `--config`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--diff-from`, `--changed-files`, `--no-plugins`, `--strict-plugins`, `--no-heuristics`, `--verbose`, `--workspace`, `--packet`/`--no-packet`, `--packet-format` |
| `agents-shipgate verify` | `--workspace`, `--config`, `--base`, `--head`, `--ci-mode`, `--fail-on`, `--baseline`, `--baseline-mode`, `--diff-from`, `--out`, `--format`, `--policy-pack`, `--no-plugins`, `--strict-plugins`, `--no-heuristics`, `--suggest-patches`, `--verbose` |
| `agents-shipgate evidence-packet` | `--from`, `--out`, `--format`, `--json` |
| `agents-shipgate scenario suggest` | `--from`, `--out` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json` |
| `agents-shipgate doctor` | `-c`, `--config`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate contract` | `--json` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate explain-finding` (v0.12+) | `<fingerprint>`, `--from`, `--no-plugins`, `--json` |
| `agents-shipgate findings` (v0.20+) | `--from` (default: `agents-shipgate-reports/report.json`), `--provenance-kind`, `--include-suppressed`, `--json` |
| `agents-shipgate trigger` (v0.11+) | `--workspace`, `--changed-files`, `--diff`, `--base`, `--head`, `--manifest-present`/`--no-manifest-present`, `--user-requested`, `--list-rules`, `--json` |
| `agents-shipgate bootstrap` | `--workspace`, `--confidence`, `--no-ci`, `--no-apply`, `--json` |
| `agents-shipgate list-checks` | `--json`, `--no-plugins` |
| `agents-shipgate baseline save` | `-c`, `--config`, `--out` |
| `agents-shipgate baseline verify` (v0.11+) | `--baseline`, `--audit-log`, `--strict`, `--json`, `--verbose` |
| `agents-shipgate fixture list` | `--json` |
| `agents-shipgate fixture run` | `<name>`, `--ci-mode`, `--out` |
| `agents-shipgate fixture copy` | `<name>`, `--to` |
| `agents-shipgate fixture verify` | `<name>` |
| `agents-shipgate self-check` | `--json` |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass — advisory mode or strict mode with no `fail_on` matches |
| `2` | Manifest config error (missing/typo/invalid) |
| `3` | Input parse error (malformed YAML/JSON, file too large, path traversal blocked) |
| `4` | Other Agents Shipgate error |
| `6` | Baseline integrity failure (v0.11+) — `agents-shipgate baseline verify --strict` detected `SHIP-BASELINE-INTEGRITY-MISMATCH`. Only the standalone `baseline verify` command emits this code; `scan` continues to use `20` for gate failure regardless of integrity-mode. |
| `20` | Strict-mode gate failure (≥ 1 unsuppressed finding hit `fail_on`, or ≥ 1 active unbaselined finding sets `blocks_release`) |

### Runtime contract JSON

`agents-shipgate contract --json` emits the installed CLI's local contract.
Only the JSON form is stable; human-readable output is informational and may
change in any minor release. The command is local-only: it does not scan a
workspace, write files, call tools, perform network checks, or look up releases.

Stable JSON fields:

- `contract_version` — version of the contract-command payload shape.
- `cli_version` — installed Agents Shipgate version.
- `report_schema_version` — current report schema version from
  `ReadinessReport`.
- `packet_schema_version` — current packet schema version from
  `EvidencePacket`.
- `gating_signal` — always `release_decision.decision` in this contract.
- `manual_review_signals[]` — stable report/packet fields an agent should read
  when surfacing human review work.

Package versions and schema versions are intentionally separate contract
counters. `agents-shipgate` may bump `report_schema_version`,
`baseline_schema_version`, or `packet_schema_version` inside a package release
when the JSON contract changes. Consumers that need a specific report or packet
shape should check `agents-shipgate contract --json` instead of inferring schema
support from the package version alone.

Signal paths use dotted notation; `[]` denotes an array field.

### JSON report fields (stable)

In `agents-shipgate-reports/report.json`, the following are guaranteed:

- `report_schema_version` — bumps minor on additive changes, major on breaking
- `release_decision.{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}` (v0.8+)
- `release_decision.fail_policy.{ci_mode, fail_on, new_findings_only, would_fail_ci, exit_code}`
- `release_decision.blockers[].{id, fingerprint, check_id, severity, title, baseline_status, blocks_release}` and `release_decision.review_items[].{id, fingerprint, check_id, severity, title, baseline_status, blocks_release}` (reference-only — both arrays share the same item shape; full Finding payload is in `findings[]`)
- `capability_facts[].{id, tool_name, source_type, source_ref, capability, risk_tags, auth_scopes, owner, included_reason, control_status, related_findings}` (v0.9+)
- `declared_intentions[].{id, kind, text, source, intent_tags}` (v0.9+)
- `misalignments[].{id, kind, severity, tool_name, capability_refs, intention_refs, finding_refs, policy_requirement, gap, release_implication}` (v0.9+)
- `release_consequence.{decision, summary, blocker_misalignment_count, review_misalignment_count, fail_policy}` (v0.9+)
- `suggested_scenarios[].{id, scenario_type, title, given, expected_control, source_misalignments, source_findings}` (v0.9+)
- `tool_surface_facts.{tools, scopes, controls, policies}` (v0.10+) — deterministic current facts used for static tool-surface comparison
- `tool_surface_diff.{enabled, base, summary, tools, high_risk_effects, scopes, controls, metadata_changes, policy_drift, finding_deltas, notes}` (v0.10+) — lower-level explanatory diff data only; it never changes `release_decision.decision` or exit behavior by itself
- `summary.{critical_count, high_count, medium_count, low_count, info_count, suppressed_count, status, human_review_recommended}`
- `findings[].{id, fingerprint, check_id, severity, category, title, recommendation, suppressed}`
- `findings[].tool_name` (string or null)
- `findings[].source.{type, ref, location}` (when available)
- `findings[].source.{path, start_line, end_line, start_column, pointer}` (v0.11+) — minimal source provenance for the common tool-source loaders (OpenAPI, MCP, OpenAI tool artifacts, Anthropic tool artifacts). Optional and additive: keys are emitted only when the loader populates them. Reviewers can use `path` + `start_line` to jump to evidence; `pointer` is an RFC 6901 JSON pointer into the source file. JSON inputs do not carry line numbers in v0.11.
- `findings[].agent_action` (v0.12+) — deterministic projection of `patches`, `autofix_safe`, and `requires_human_review`. Enum: `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`. The first four cover the actionable cases; `informational` covers suppressed findings or non-actionable advisories. `suppress_with_reason` is reserved for future check classes that explicitly mark themselves as suppressible — the v0.12 deterministic projection does not emit it. New consumers should read `agent_action` first and treat the underlying flags as advisory.
- `agent_summary.{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}` (v0.12+) — top-level deterministic projection of `release_decision` + per-finding `agent_action`. Lets a coding agent read one block instead of traversing arrays. `first_recommended_action` is `{kind: "command" | "info", command: string | null, why: string}`; the `command` form carries an actual CLI invocation, the `info` form is a "surface this to the user" hint. Same inputs always produce the same output; this block cannot disagree with the underlying `release_decision` and `findings[].agent_action`.
- `codex_plugin_surface.{plugins, marketplaces, skills, apps, mcp_server_stubs, hook_stubs, mcp_inventory_files, component_path_issues, warnings}` (v0.13+) — static Codex plugin package and marketplace facts. Only explicit MCP inventory tools enter `tool_inventory[]`; apps, hooks, skills, and MCP server declarations stay in this surface block.
- `findings[].provenance_kind` (v0.15+) — records *how a finding was produced*; independent of `confidence`, which records how *sure* we are. It is a reviewer triage/filter signal only: it never changes `release_decision`, severity, fingerprints, baselines, or CI exit behavior. Use `agents-shipgate findings --from agents-shipgate-reports/report.json --provenance-kind keyword_heuristic,regex_heuristic --json` to filter active findings by provenance class. Enum: `static_declaration | ast_extraction | keyword_heuristic | regex_heuristic | policy_pack`. `static_declaration` covers manifest, MCP, OpenAPI schema facts, and declarative framework inputs like ADK YAML agent configs or LangChain/CrewAI inventory JSON files — high-trust structural data. `ast_extraction` covers findings against Tools parsed from user Python source by a framework extractor (LangChain function/structured tools, CrewAI function/class tools, ADK Python toolsets); these are subject to extraction error and agents that distrust AST quality can filter them as a class. Framework checks that fire against both AST-extracted and declaratively loaded tools (ADK's per-tool checks) pick the label per tool from `tool.source_type`. `keyword_heuristic` covers token-list matches (broad scope, read-only prompts, free-text parameter names); `regex_heuristic` covers regex matches (secrets, prompt injection); `policy_pack` covers findings emitted by externally loaded policy packs. Built-in checks set the value via the required kwarg on the `tool_finding`/`agent_finding` helpers; third-party plugin checks that construct `Finding(...)` directly and omit the field are coerced to `static_declaration` by `annotate_remediation` so the wire schema stays satisfied. Required + non-nullable on the wire; the field is Python-Optional only so older v0.12/v0.13 reports loaded by `explain-finding` and minimal synthetic test fixtures keep working.
- `findings[].blocks_release` (v0.16+) — explicit release-policy blocking bit. Built-in and user-defined Action Surface Diff policies, plus declarative policy-pack rules with `block: true`, set it for findings that must block release when active and unbaselined; ordinary severity-based gating still works for existing checks.
- `action_surface_facts.actions[]` (v0.16+) — deterministic current action snapshot: action id, operation, effect, normalized risk tags, scopes, approval policy, safeguards, evidence, input fields, and stable hashes.
- `action_surface_diff.{enabled, base, summary, added, removed, modified, notes}` (v0.16+) — reviewer-facing delta for what the agent can do vs. a prior report or v0.4 baseline. Policy findings derived from this diff can set `findings[].blocks_release=true` and affect `release_decision.decision` and strict-mode exit behavior.
- `release_decision.contribution_rules[].{finding_id, fingerprint, check_id, category, rule, rationale}` (v0.17+) — deterministic per-finding audit of how each finding contributed to the release decision. Required + always present (defaults to `[]` for legacy reports loaded via `explain-finding`). Exactly one row per `report.findings` entry, including suppressed findings, so the audit set is exhaustive over the full findings list. `category` enum: `blocker | review_item | excluded`. `rule` enum: `policy_block_new | severity_block_new | policy_baseline_accepted | severity_baseline_accepted | review_required | sub_threshold | suppressed`. The (rule, category) pairs the gate can produce are exhaustively documented in [Release decision truth table](#release-decision-truth-table) below — reading the contribution rule is sufficient to predict the outcome for that finding without re-deriving the decision logic. The audit cannot disagree with `release_decision.{blockers,review_items}[]`: the same classification powers both. Adding `contribution_rules` does not change any existing behavior — `decision`, `blockers[]`, `review_items[]`, `fail_policy.exit_code`, and strict-mode exit codes are byte-identical to v0.16.
- `baseline.{matched_count, new_count, resolved_count, path}` (when `--baseline` is used)
- `tool_inventory[].{name, source_type, source_ref, risk_tags, auth_scopes, owner, confidence}`
- `loaded_plugins[].{name, value, distribution, version, check_id}`
- `loaded_plugins[].{validation_status, validation_errors, runtime_errors}` (v0.17+ / M5; `dynamic_default_not_supported` added v0.18) — plugin validation provenance, required + present on every entry. `validation_status` is one of `valid | load_failed | bad_signature | bad_metadata | dynamic_default_not_supported | id_collision | bad_floor`; the two error lists are always present and empty for clean plugins. Invalid plugins still appear in this array (with `check_id: null` for entries that failed before metadata parsing), so reviewers can see what was skipped without reading scanner logs. Plugin findings whose `check_id` does not match the declared metadata are dropped at runtime and recorded under `runtime_errors`. `dynamic_default_not_supported` (v0.18+) rejects plugins declaring `AGENTS_SHIPGATE_METADATA.dynamic_default=True` — plugins have no path to wire into `core/dynamic_defaults.py`'s aggregator, so a swing check would never receive a manifest-effective default and would be silently bypassable.
- `policy_audit.severity_overrides_applied[].{check_id, default_severity, applied_severity, manifest_path, reason, tier_crossed, direction, expires}` (v0.17+ / M1) — top-of-report audit envelope for severity overrides applied during scan. Always present on emitted scans (empty when no overrides applied); required + non-nullable on the wire. `direction` is one of `downgrade | upgrade | same`. `tier_crossed=true` indicates the override crossed a severity tier boundary (critical / high / medium-low); tier-crossing downgrades require a matching `checks.acknowledge_overrides` entry, which is reflected in `reason`. `expires` is an ISO-8601 date carried from the matching acknowledgement (or the rich-form override entry); on/past this date the manifest fails to load with exit 2.
- `privacy_audit.{enabled, rules_version, sensitive_field_inventory_version, redacted_occurrence_count, redacted_paths, output_surfaces, notes}` (v0.18+) — top-level audit envelope proving the default-on privacy layer ran before public artifacts were emitted. `redacted_paths[]` contains `{path, count, kinds}` aggregate rows only; it never includes raw values or raw-value hashes. Redaction is best-effort pattern/key based and does not claim complete secret-scanner coverage.
- `reviewer_summary.{verdict, headline, tool_surface_changes, capability_misalignments, action_surface_changes, evidence_matrix_gaps, severity_overrides_applied, severity_overrides_tier_crossed, privacy_redactions, baseline_integrity_issues, first_recommended_surface}` (v0.20+) — top-level deterministic projection of the reviewer lens surfaces and audit envelopes; the reviewer-side parallel to `agent_summary`. Required + always present on emitted scans (mirroring the `agent_summary` contract). `verdict` mirrors `release_decision.decision` and is added/removed in lockstep with `AgentSummary.verdict` and `ReleaseDecisionStatus`. `first_recommended_surface` is `{kind, name, path, why}` where `kind` ∈ `{release_decision, lens, audit, evidence_matrix}` and `name` ∈ `{tool_surface_diff, capability_intent_diff, action_surface_diff, evidence_matrix, policy_audit, privacy_audit, baseline_integrity, release_decision}`; the pointer is `null` only when verdict is `passed` AND every count above is zero. The priority order encoded by `first_recommended_surface` is documented in [`docs/agent-contract-current.md`](docs/agent-contract-current.md). Same inputs always produce the same output; this block cannot disagree with the underlying lens/audit data.
- `heuristics_filter.{enabled, excluded_provenance_kinds, filtered_finding_count, filtered_by_kind}` (v0.21+) — top-level audit envelope describing the `--no-heuristics` CLI filter pass. Required + always present on emitted scans regardless of whether the flag was set (envelope shape is stable). When `enabled` is `False` the count fields are zero and no findings have been mutated by the filter. When `enabled` is `True`, every finding whose `provenance_kind` is in `excluded_provenance_kinds` has been marked `suppressed=True` with `suppression_reason="filtered by --no-heuristics"` BEFORE the release decision is built — those findings remain in `findings[]` for transparency but no longer gate release. `excluded_provenance_kinds` is the stable list `["keyword_heuristic", "regex_heuristic"]` (the only two `ProvenanceKind` values describing token/regex matches; `static_declaration`, `ast_extraction`, and `policy_pack` are never filtered). The filter never un-suppresses a finding; manifest-driven suppression reasons are preserved verbatim when they overlap with the filter (the envelope still counts the overlap so reviewers see the filter's effective scope).
- `verifier_summary.{verdict, by_severity, by_reason_code, capability_delta_summary, protected_surface_touched, policy_weakened, human_ack_required, human_ack_satisfied, top_reason_codes}` (v0.22+) — top-level **composition** for the AI-coding-workflow verifier; the controller-facing one-fetch surface. Required + always present on emitted scans. Derives no independent verdict: `verdict` mirrors `release_decision.decision` and moves in lockstep with `AgentSummary.verdict` / `ReviewerSummary.verdict` / `ReleaseDecisionStatus`. `by_severity` / `by_reason_code` are active-finding histograms (the complete per-code map); `capability_delta_summary` (`{added, removed, broadened, narrowed}`) equals the `capability_change` member-list lengths by construction; `top_reason_codes[]` is the ranked top-five highlight (`{reason_code, count}`, ranked severity desc → count desc → code asc — the full set stays in `by_reason_code`). This block cannot introduce a finding-independent blocker.
- `capability_change.{enabled, added, removed, broadened, narrowed}` (v0.22+) — diff-derived capability delta projected over `action_surface_diff` / `tool_surface_diff`. Required + always present (`enabled: false` with empty lists when no base diff is available). Each member is `{id, direction, subject_kind, tool, action, scope, before_scope, after_scope, risk_tags, release_impact, provenance_kind, confidence, rationale, related_finding_ids}`; member lists are sorted by `(subject_kind, tool, action, scope, id)`. A reviewer-facing projection — it never gates on its own.
- `protected_surface_changes[]` (v0.22+) — list of touched release trust roots, each `{path, kind, glob, related_finding_ids}`, sorted by `(kind, path)`. Derived from active `SHIP-VERIFY-*` findings, so every `related_finding_ids` entry resolves to a real `findings[]` id and the rollup cannot disagree with the gate. Always present (empty `[]` on a plain scan or when no trust root is touched).
- `effective_policy.{ci_mode, fail_on, suppressed_check_ids, waiver_scopes, severity_overrides, baseline_integrity_mode, baseline_fingerprints, ci_gate_present}` (v0.22+) — normalized (not text-diff) snapshot of the release-policy surface for base-vs-head weakening comparison. Required + always present. Every list/dict is emitted sorted (`fail_on` by severity tier rank) for byte-stable output; derived from the manifest plus accepted-debt fingerprints.
- `human_ack.{required, satisfied, acks, outstanding}` (v0.22+) — declared human-acknowledgement state. Required + always present (default `required=false`, `satisfied=true`, empty lists). Within the static boundary, acknowledgement is declared evidence only — never inferred. A trust-root weakening (`SHIP-VERIFY-POLICY-WEAKENED`, `-CI-GATE-REMOVED`, `-BASELINE-OR-WAIVER-EXPANDED`) makes a surface `required`; `satisfied` only when a matching `human_ack` entry exists in `shipgate.yaml`. `acks[]` are `{owner, reason, affected_surface, expires, source}`; `outstanding[]` lists required-but-unacknowledged surfaces. The ack section lives in `shipgate.yaml` (a trust root) so adding one trips `SHIP-VERIFY-TRUST-ROOT-TOUCHED`.

### Privacy and redaction

Reports, packets, SARIF, Markdown, GitHub step summaries, `explain-finding`
payloads, and JSON logs are redacted by default. The sanitizer runs locally and
does not upload artifacts. Redaction uses the shared rules in
`agents_shipgate.core.privacy` and the report-field inventory in
[`docs/report-sensitive-fields.json`](docs/report-sensitive-fields.json).
False positives are allowed in favor of privacy; local routing metadata such as
source paths, JSON pointers, and scopes remains structurally present with only
secret-like substrings replaced.

v0.18 changes public fingerprints for findings whose identity evidence contains
a recognized secret pattern because the public `findings[].fingerprint` is now
computed from redacted evidence. During `--baseline` scans, Shipgate also checks
the pre-v0.18 raw fingerprint in memory so existing baselines continue matching
without emitting raw hashes. After reviewing the v0.18 report, re-run
`agents-shipgate baseline save` to migrate the baseline to redacted public
fingerprints and remove the compatibility dependency.

### Severity-override floor

`checks.severity_overrides` continues to accept the legacy scalar form
(`SHIP-XYZ: medium`) and additionally accepts a rich form
(`SHIP-XYZ: { severity, reason, expires }`). Reviewers should prefer the
rich form for any tier-crossing or release-critical override.

Some built-in checks declare a per-check **hard floor**
(`CheckMetadata.floor_severity`). When set, a manifest override that
resolves to a weaker severity than the floor is rejected as a config
error (exit 2). The floor is hard — `acknowledge_overrides` does NOT
bypass it. Use `agents-shipgate list-checks --json` to inspect each
check's floor.

`checks.acknowledge_overrides[]` (v0.17+) — required for severity
overrides whose application crosses a severity tier boundary as a
downgrade. Stable shape: `{check_id, reason, expires?}`. Within-tier
downgrades (e.g., medium → low) and any upgrade never require ack.
Tiers (stable within `0.x`): `critical / high / medium-low`. Expired
ack entries are a manifest config error.

**Dynamic-severity check classes** (v0.17+; formalized v0.18). Catalog
checks whose emitted finding severity depends on user-declared
manifest values declare `CheckMetadata.dynamic_default=True`. Today
the only such built-in is `SHIP-ACTION-POLICY-VIOLATION` (emits at
`action_surface.policies[].severity`). Policy-pack rule IDs flow
through the same `extra_known_check_defaults` mechanism but live
outside the catalog. The severity-override resolver uses
`max(catalog default, manifest-effective default)` as the
tier-crossing comparison base, so a `severity: critical` action
policy with override `high` cannot appear same-tier against the
catalog's `high` default. The
`policy_audit.severity_overrides_applied[].default_severity` row
reports the effective (dynamic-aware) default so reviewers see the
real before/after.

Two contract rules pin the design (v0.18):

- Built-in checks marked `dynamic_default=True` MUST also declare
  `floor_severity` — enforced by a `CheckMetadata` model validator.
  A swing check without a floor has no safety net against silent
  downgrade bypass.
- Plugins cannot declare `dynamic_default=True` — the plugin
  validation pipeline rejects them with status
  `dynamic_default_not_supported`. Plugins have no path to wire into
  `core/dynamic_defaults.py`'s aggregator and so would never receive
  the manifest-effective default needed for tier-crossing comparison.

Adding a new built-in dynamic-severity check requires (1) setting
`dynamic_default=True` in `CHECK_METADATA` (forces the floor), and
(2) adding an aggregator overlay branch in
`core/dynamic_defaults.py:dynamic_check_defaults`. The seed loop in
step 1 of that aggregator auto-includes every `dynamic_default=True`
catalog entry, so the resolver's internal-consistency guard cannot
false-positive on user input that overrides a swing check without
declaring the corresponding manifest section.

### Scenario Suggestion YAML

`agents-shipgate scenario suggest --from agents-shipgate-reports/report.json`
projects `report.json.suggested_scenarios[]` into
`suggested-scenarios.yaml`. It is a concrete fan-out of the JSON report's
scenario contract, not a separate scenario engine.

Stable YAML fields:

- `scenarios[].{id, scenario_type, derived_from, finding_id, source_scenario_id, source_misalignment_id, tool, adversarial_goal, expected_control}`

Suppressed findings are omitted. Baseline-matched findings are included because
they represent accepted debt, not resolved risk. `adversarial_goal` text may
evolve in minor releases; the field itself remains stable. Rows follow the
source `suggested_scenarios[]` order, then sort within each source scenario by
severity, check ID, tool, finding ID, and misalignment ID.

#### `release_decision.decision` vs `summary.status`

These are **intentionally different signals**, kept apart for backwards compatibility:

| Field | Baseline-aware? | Recommended for release gating? |
|---|---|---|
| `release_decision.decision` | yes — baseline-matched criticals appear in `review_items`, not `blockers` | **yes (v0.8+)** |
| `summary.status` | no — any unsuppressed critical flips status to `release_blockers_detected` | preserved for v0.7 callers |

#### Release decision truth table

The classification below is the contract for how every active finding lands in `release_decision.{blockers, review_items}[]` and which `contribution_rules[].rule` (v0.17+) fires for it. Same shape as the v0.8 implementation: this section documents existing behavior, it does not change it. Suppressed findings (`finding.suppressed=true`) are excluded entirely from the active set and audited as `category="excluded", rule="suppressed"`.

Notation: `fail_on` is `release_decision.fail_policy.fail_on` after `ci_mode` resolution (advisory → empty, strict → `["critical"]`, plus any explicit `--fail-on` override). `blocker_severities` = `{critical} ∪ fail_on`. `review_tier` = `{critical, high, medium}` (or any severity when `requires_human_review=true`).

| `blocks_release` | severity | baseline_status | severity in `blocker_severities`? | severity in `review_tier`? | category | `rule` | strict-mode exit |
|---|---|---|---|---|---|---|---|
| true | any | new / null | n/a | n/a | **blocker** | `policy_block_new` | 20 |
| true | any | matched | n/a | yes | review_item | `policy_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| true | any | matched | n/a | no | excluded | `policy_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| true | any | resolved | n/a | n/a | excluded | (not produced; resolved findings are absent from the active set) | 0 |
| false | any | new / null | yes | n/a | **blocker** | `severity_block_new` | 20 |
| false | any | matched | yes | yes | review_item | `severity_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| false | any | matched | yes | no | excluded | `severity_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| false | any | new / null | no | yes | review_item | `review_required` | 0 |
| false | any | matched | no | yes | review_item | `review_required` | 0 |
| false | any | new / null / matched | no | no | excluded | `sub_threshold` | 0 |

**Why baseline-matched policy findings drop to `review_items`, not `blockers`.** `blocks_release=true` represents an explicit *policy* decision (Action Surface Diff rule, `action_surface:` manifest entry, or policy-pack rule with `block: true`) that the finding must block release **on first appearance**. A baseline accepts technical debt that already passed prior review — the project agreed to ship with that finding present. Treating baselined policy debt as a hard blocker would defeat the purpose of `baseline save`. The baseline-aware drop is symmetric for severity-driven blockers and policy blockers: both land in `review_items` once accepted into the baseline, both become hard blockers if newly introduced.

**Why `severity ∈ blocker_severities + matched + below review_tier` lands in `excluded`, not `review_items`.** A finding whose severity isn't in `{critical, high, medium}` (and which doesn't carry `requires_human_review=true`) has nothing for a human reviewer to act on per the v0.8 contract — it's been baselined and isn't severe enough to warrant attention. v0.17 records this in the audit so the (rare) edge case isn't silently invisible, but the `blockers[]`/`review_items[]` lists themselves are unchanged.

**Why exit code 20 depends on `--baseline-mode`.** `release_decision.{blockers, review_items}[]` always include the full set computed against `report.findings` (with suppressed excluded). The strict-mode exit code, however, is computed from `baseline_filtered_active(report, new_findings_only=...)` — when `--baseline-mode new-findings` is set (the default for the GitHub Action when `baseline:` is provided), baseline-matched policy and severity blockers are filtered out before the exit check, so exit is `0`. With `new_findings_only=False`, a matched policy blocker still triggers exit 20. The `release_decision` block remains baseline-aware in all cases; only the exit-code path changes mode.

Concretely: a scan with one baseline-matched critical and zero new findings produces `summary.status = "release_blockers_detected"` AND `release_decision.decision = "review_required"`. Both are correct under their respective contracts. New consumers should read `release_decision.decision`.

#### Evidence-only decision states

Finding blockers take precedence over evidence quality. If
`release_decision.blockers[]` is non-empty, the decision is `blocked` even when
the scan also has low-confidence tools or source warnings.

When there are no blockers, `insufficient_evidence` means the static inputs are
not strong enough for Shipgate to gate release confidently. It does **not**
prove the agent is unsafe. The decision fires when low-confidence tools are at
least `max(1, ceil(tool_count × 0.5))`, or when source-loader warnings exceed
`3`. One to three source warnings without blockers route to `review_required`
so a human still sees the degraded source coverage.

The intended recovery is to provide clearer local evidence — for example an MCP
export, OpenAPI spec, explicit local tool inventory, broader OpenAI Agents SDK
source path, or validation trace — and rerun the scan.

### Check IDs

Once a check ID ships in a tagged release (`SHIP-POLICY-APPROVAL-MISSING`, `SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING`, etc.), it will not be:

- Renamed
- Removed (only deprecated, with at least one minor-version cycle)
- Repurposed (the conditions under which it fires may *narrow* but never broaden in a way that breaks existing suppressions)

New check IDs may be added in any minor release. If your CI pins severities by check ID, expect new checks to surface as new findings.

### Check catalog metadata

`agents-shipgate list-checks --json`, `agents-shipgate explain <CHECK_ID>
--json`, and `docs/checks.json` expose `CheckMetadata.mvp_tier` for
display/triage only. Current values are `core`, `adapter`, `evidence`,
`lifecycle`, and `hygiene`. This field does not affect check execution,
severity, fingerprints, baselines, `release_decision`, or CI exit behavior.

### Static Python extraction

OpenAI Agents SDK, CrewAI, and LangChain/LangGraph AST extractors share the
same runtime/context parameter skip list: `self`, `cls`, `ctx`, `context`,
`config`, `runtime`, `run_manager`, and `callbacks`. Those names are treated as
framework plumbing and are omitted from normalized tool input schemas. Google
ADK uses its own static extractor skip list: `self`, `ctx`, `context`, and
`tool_context`. For OpenAI Agents SDK sources, file and directory mode both emit
manifest-relative POSIX `source_ref` values; directory mode scans only immediate
`*.py` files in sorted order.

### Fingerprint algorithm

`fingerprint = "fp_" + sha256(check_id | tool_name | canonical_evidence)[:16]`

Where `canonical_evidence`:
- Sorts dict keys recursively
- Sorts list items by JSON repr
- **Excludes** the `default_severity` audit-evidence key (so applying `severity_overrides` does not change identity)
- **Excludes** the `source_provenance` evidence key (so adding local HITL provenance does not rotate existing baselines or suppressions)

Fingerprints are stable across runs on the same input. They are the identity primitive used by suppressions and baselines.

### Trust-model invariants

The scanner does not, under any circumstances:

- Execute or import user code (the SDK loaders use `ast.parse` only)
- Make HTTP requests
- Connect to MCP servers
- Invoke LLMs
- Send telemetry

The no-execute / no-import property is enforced by two complementary
tests on every CI run, not by convention:

- **[`tests/test_adapter_static_only.py`](tests/test_adapter_static_only.py)** —
  AST scan of every `.py` file under `src/agents_shipgate/` (v0.18+
  widened scope from `src/agents_shipgate/inputs/` only). The scan
  rejects:
  - Bare-name calls to `exec` / `eval` / `__import__` / `compile`.
  - Attribute calls to `importlib.import_module`,
    `importlib.util.spec_from_file_location`,
    `importlib.util.module_from_spec`,
    `importlib.machinery.SourceFileLoader`,
    `runpy.run_path`, `runpy.run_module`,
    `subprocess.{run, call, Popen, check_call, check_output}`,
    `os.system`, `os.popen`, and every variant under the
    `os.exec*` / `os.spawn*` / `os.posix_spawn*` prefixes.
  - Module imports of `runpy`, `subprocess`, `importlib`,
    `importlib.util`, `importlib.machinery`, and `builtins` — in any
    `import X`, `import X as Y`, `import X.child`, or
    `from X.child import …` form.
  - Wildcard `from os import *`.

  `importlib.metadata` is intentionally allowed: the plugin registry
  uses it for entry-point discovery, and discovery happens against the
  *installed* environment, not user workspace files. `importlib.resources`
  is allowed (v0.18+) at the import line **only** so name-aliases get
  built; every `importlib.resources.<attr>(...)` call site is forbidden
  via the `importlib.resources.` prefix in `FORBIDDEN_ATTR_CALL_PREFIXES`
  and must carry a per-call-site `ALLOWED_EXCEPTIONS` entry with snippet
  pinning. This covers `files`, `read_text`, `read_binary`, `path`,
  `open_text`, `open_binary`, `is_resource`, `contents`, `as_file`, and
  any future addition under the module — all of which take an
  anchor-package argument and could bypass the dynamic-import lint if
  left unrestricted. Aliased re-exports (`import os as oo`,
  `from os import system as sh`, `import os; import pathlib as os`) are
  resolved through union-of-bindings alias maps so a later import
  cannot erase an earlier forbidden binding. The lint runs as a
  dedicated CI step labeled *Trust-model invariant lint* before the
  main test suite so a regression is visible at the top of CI logs.

  **Meta-CLI surfaces (allowlisted, audited).** First-party meta-CLI
  surfaces are pinned **per call site** in
  [`tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`](tests/test_adapter_static_only.py)
  by a four-tuple `(relative_path, surface, line, snippet)` where
  `snippet` is the canonical `ast.unparse` of the offending AST node.
  Each entry carries a prose rationale and pins a single call:

  - **`cli/bootstrap.py`** — one `subprocess.run` call shells the
    installed agents-shipgate CLI to chain
    `detect → init → scan → apply-patches`.
  - **`cli/discovery/artifacts.py`** — two `subprocess.run` calls
    invoke `git rev-parse` + `git ls-files` to enumerate user-repo
    files. Reads git metadata only.
  - **`triggers.py`** — three `subprocess.run` calls (`git diff
    --name-only`, `git diff`, `git ls-files --others
    --exclude-standard`) for trigger evaluation. Reads diff content
    only. **Plus** one `importlib.resources.files('agents_shipgate')`
    call to resolve the bundled trigger catalog.
  - **`cli/verify/git.py`** — one shared `subprocess.run` helper invokes
    local `git rev-parse`, `git diff`, and `git archive` for verify
    base/head orchestration. It never fetches, uses fixed argv, captures
    output, and never executes user code.
  - **`fixtures.py`** — one `importlib.resources.files('agents_shipgate')`
    call to resolve the bundled fixture directory.
  - **`cli/discovery/agent_instructions/adoption_kit.py`** — one
    `importlib.resources.files('agents_shipgate')` call to resolve bundled
    first-party adoption-kit files from the installed wheel. Downstream
    customization is explicit repo-local file reading through
    `--agent-instructions-kit`, never dynamic imports or network fetches.
  - **`cli/trigger.py`** — imports `subprocess` only to catch
    `subprocess.CalledProcessError` from the shared
    `triggers._git_diff_context` git probe. The `agents-shipgate trigger`
    subcommand issues no `subprocess.run` call of its own; git runs in
    `triggers.py` exclusively, and only when `--base`/`--head` is passed.
  - **`cli/self_check.py`** — one `__import__(module_name)` call
    validates that supplied modules import cleanly. Runs only under
    `agents-shipgate self-check`, never during scan.

  Per-call-site pinning means **adding a second occurrence of an
  already-allowlisted surface in the same file STILL requires a new
  entry**. Changing the call's argv shape (the `snippet` changes)
  also fails the test, forcing a reviewer to confirm the change is
  benign. The literal-anchor invariant for
  `importlib.resources.files('agents_shipgate')` is enforced by
  snippet pinning: a future `files(user_var)` call would not match.

  Three contract tests pin the audit trail:
  `test_allowlist_entry_matches_real_surface` (every entry matches a
  real violation on all four fields),
  `test_no_unallowlisted_forbidden_surface_in_scanner` (every
  observed violation has a matching entry), and
  `test_allowed_exceptions_pin_subprocess_run_per_call_site` (the
  multi-call files have distinct entries per call site, regression-
  testing the structural fix from the v0.18 PR #2 review).
- **[`tests/test_fixture_no_import.py`](tests/test_fixture_no_import.py)** —
  per-adapter live-load tests. Each adapter (LangChain, CrewAI, OpenAI Agents
  SDK, Google ADK, MCP, OpenAPI, Anthropic, OpenAI API, n8n, Codex plugin) is
  driven against a fixture whose Python content (or a sibling `trap.py`, for
  declarative adapters) raises `RuntimeError` at module load. Each test
  additionally snapshots `sys.modules` and asserts no module whose `__file__`
  resolves under the fixture root ends up cached after the scan — a stronger
  property than relying on the runtime raise alone.

If a contributor introduces a real need for one of the forbidden surfaces,
update this section in the same PR. The intent is not "we tried to forbid X"
— it is that X is *structurally absent* from the scanner's parsing path.

Plugins are off by default. `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` enables loading; `--no-plugins` overrides at the CLI level. When loaded, every plugin is enumerated in `report.loaded_plugins`, and every third-party adapter (v0.20+) is enumerated in `report.loaded_adapters`.

Plugin validation (v0.17+ / M5). Every entry point is checked against five load-time gates before it can run:

1. **load** — `entry_point.load()` must not raise.
2. **signature** — the loaded object must be callable and accept exactly one required positional parameter (`ScanContext`); extra defaulted positional / keyword-only parameters are allowed.
3. **metadata** — `AGENTS_SHIPGATE_METADATA` must be present and parseable as `CheckMetadata`. Both `id` and `check_id` are accepted as the identifier key (v0.17 alias); newer plugins should prefer `check_id` for symmetry with `Finding.check_id`.
4. **id_collision** — the plugin's check ID must not shadow a built-in (including legacy aliases) or a previously-registered plugin.
5. **bad_floor** — `floor_severity` must not exceed `default_severity` on the same metadata block.

Plugins that pass every gate run with the same trust as built-ins. Runtime validation additionally drops findings whose `Finding.check_id` does not match the plugin's declared `id`/`check_id`, drops non-`Finding` items, and captures any exception raised during the plugin call into `loaded_plugins[].runtime_errors`. The scan continues regardless; `--strict-plugins` elevates any non-`valid` plugin or non-empty `runtime_errors` to exit code 4.

#### Third-party adapter discovery (v0.20+)

Third-party adapters register through the `agents_shipgate.adapters` Python entry-point group and provide a class (or instance) satisfying the `ToolSourceAdapter` Protocol — a `source_type: str` ClassVar, a `scope: Literal["per_source", "per_scan"]` ClassVar, an `artifact_class: type | None` ClassVar, and a `load(source, base_dir, manifest)` method returning `LoadedAdapterResult`. Discovery is gated by the same `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` env var as plugin checks; `--no-plugins` forces it off.

Every discovered entry point is checked against four load-time gates before it can register on the scan's adapter registry:

1. **load** — `entry_point.load()` must not raise. Captured as `validation_status="load_failed"`.
2. **bad_protocol** — the loaded value (a class is instantiated with no args; an instance is used directly) must have all three ClassVars (`source_type` non-empty string, `scope`, `artifact_class`) and a callable `load` method that accepts the three positional arguments `(source, base_dir, manifest)`: at least three positional slots (or `*args`), no more than three required positional parameters, and no required keyword-only parameters. Captured as `validation_status="bad_protocol"`.
3. **bad_scope** — `scope` must be exactly `"per_source"` or `"per_scan"`. Out-of-range values would be silently skipped by the dispatcher. Captured as `validation_status="bad_scope"`.
4. **source_type_collision** — the adapter's `source_type` must not shadow a built-in (`mcp`, `openapi`, `langchain`, etc.) or another third-party adapter discovered earlier in the same scan. **This is the load-bearing trust rule** — without it, a malicious plugin could displace a built-in adapter and intercept every scan targeting that source type. Captured as `validation_status="source_type_collision"`.

**Per-scan registry contract.** Adapters that pass every gate register on a **per-scan clone** of the global `REGISTRY` (built at the start of each `run_scan` / `inspect_sources` via `AdapterRegistry.clone()`), NOT on the global itself. The global stays builtin-only across the lifetime of the process. This guarantees two trust invariants:

- **`--no-plugins` is per-scan honest.** A later in-process scan with `plugins_enabled=False` sees a fresh builtin-only clone — no third-party adapters carried over from a prior enabled scan.
- **Collision detection is per-scan honest.** The collision set is the clone's builtins-only state, so two consecutive scans of the same valid third-party adapter both classify as `validation_status="valid"`, never as `source_type_collision` against the adapter's own previous registration.

The dispatcher walks the per-scan registry in the same pass-1 (per-source, in `tool_sources[]` declared order) / pass-2 (per-scan, in canonical registry order) loops it uses for built-ins. Two trust mechanisms protect the dispatch path:

- **Artifact-class smuggling prevention.** The dispatcher's `_absorb` step fires `TypeError` if any adapter (built-in or third-party) declares one `artifact_class` but returns an artifact of another type. This is the structural counterpart to the `Finding.check_id` smuggling rule for plugin checks.
- **Runtime-error capture for third-party adapters.** Third-party adapters that raise at runtime do NOT abort the scan. The dispatcher routes their `load()` call through `run_validated_adapter` (from `inputs/adapter_validation.py`), which catches every exception, captures it into `loaded_adapters[].runtime_errors` on the matching row, and signals the dispatcher to skip absorbing the (None) result. Built-in adapters keep the direct call shape — a built-in raising means the scanner itself is broken and must abort loudly.

`doctor` (`inspect_sources`) uses the same per-scan registry clone + discovery + dispatcher path as `scan`, so manifests referencing third-party `tool_sources[].type` values are introspectable. The doctor payload surfaces `loaded_adapters[]` alongside the existing `policy_packs` field.

`--strict-plugins` (v0.17+) covers BOTH plugin and adapter failures from v0.20+ — any non-`valid` `loaded_plugins[]` row, any non-empty `loaded_plugins[].runtime_errors`, any non-`valid` `loaded_adapters[]` row, OR any non-empty `loaded_adapters[].runtime_errors` elevates the scan to exit code 4. Default behavior remains lenient — failures are recorded in the respective provenance arrays and the scan proceeds.

**Manifest `tool_sources[].type`.** The field is `str` (relaxed from a closed `Literal` in v0.20) so manifests can reference third-party per-source adapters by name. Built-in source types are enumerated in `BUILTIN_TOOL_SOURCE_TYPES` for documentation and tooling; per-scan-only built-ins (`n8n`, `openai_api`, `anthropic_api`, `validation`) are still rejected at manifest-load time with a routable error pointing the user to the dedicated top-level manifest section. Unknown source types — both genuine third-party names with no registered adapter and typos of built-in names — fail with `ConfigError` (exit 2) when the dispatcher's `AdapterRegistry.require` cannot resolve them. The exit-2 contract is unchanged from prior releases; the failure layer (manifest-load vs dispatch) may differ.

### Manifest Schema

The manifest schema version (`version: "0.1"`) is independent of the CLI
version and package version. Manifest schema changes follow their own
deprecation cycle, and the manifest loader is intentionally strict: older CLIs
reject unknown top-level fields instead of silently ignoring release policy.
Manifests that use `action_surface:` require a CLI whose
`agents-shipgate contract --json` reports `report_schema_version >= 0.16`.

### Baseline Integrity (v0.5)

Baseline schema bumps to `0.5`. The wire shape adds an optional
`findings[].provenance` block per entry recording when and by which scanner
the entry was added:

```json
{
  "fingerprint": "fp_…",
  "check_id": "SHIP-…",
  "tool_name": "…",
  "severity": "high",
  "title": "…",
  "provenance": {
    "scanner_version": "0.11.0",
    "run_id": "agents_shipgate_…",
    "recorded_at": "2026-05-15T14:23:00Z",
    "reason": null,
    "expires": null
  }
}
```

`provenance` is optional on the wire so older v0.2/v0.3/v0.4 baselines still
load. The integrity check flags legacy-no-provenance entries as
`SHIP-BASELINE-INTEGRITY-MISMATCH` until they are re-stamped by re-running
`agents-shipgate baseline save`. `provenance.reason` and `provenance.expires`
are reviewer-set and free-form / ISO-8601 date respectively.

Each `agents-shipgate baseline save` appends one JSON line to
`<baseline-dir>/baseline-audit.log`. The log row is **stable**:

- `audit_schema_version: "0.1"`
- `timestamp` — ISO-8601 UTC
- `run_id` — scan's run_id (matches `BaselineProvenance.run_id` for any
  fingerprints added in this save)
- `scanner_version` — Agents Shipgate version that wrote the row
- `baseline_path` — string path saved at the time of the row
- `hash_before` — `"sha256:…"` of the prior baseline file content, or `null`
  when this was the first save
- `hash_after` — `"sha256:…"` of the new baseline file content
- `added_fingerprints[]`, `removed_fingerprints[]` — sorted deltas

The audit log is append-only and intentionally co-located with the baseline so
a single `.agents-shipgate/` directory carries both. Commit both files
together; reviewers can `git log .agents-shipgate/baseline-audit.log` to see
when fingerprints joined the baseline.

`manifest.baseline.integrity_mode` controls behavior when `scan --baseline X`
detects an integrity issue. Stable values:

- `off` — no integrity checks. Back-compat escape hatch for repos that have
  not migrated to v0.5 baselines yet.
- `warn` (default in v0.11) — integrity findings emitted but
  `blocks_release: false`; release decision is unaffected.
- `strict` — `SHIP-BASELINE-INTEGRITY-MISMATCH` carries
  `blocks_release: true` and `agents-shipgate baseline verify` exits `6` on
  the same condition.

New stable check IDs (v0.11+):

- `SHIP-BASELINE-INTEGRITY-MISMATCH` (critical) — file hash mismatch, missing
  audit log, audit log empty or malformed, entry references unknown `run_id`,
  or entry loaded from a legacy schema without provenance.
- `SHIP-BASELINE-ENTRY-EXPIRED` (high) — `provenance.expires` < today.
- `SHIP-BASELINE-ENTRY-STALE` (low) — deprecated check ID in the entry, or
  the entry matched no active finding (scan-aware; resolved-not-pruned).

Integrity findings bypass `checks.ignore` (suppression) and
`checks.severity_overrides`. Silencing tamper detection would defeat the
trust property the audit log defends. They flow through the regular report
pipeline otherwise (fingerprinting, baseline-status assignment, remediation
annotation).

The audit log is **tamper-evident, not tamper-proof**: a well-resourced
adversary who atomically rewrites both the baseline JSON and the audit log
defeats `verify`. The goal is to make casual or accidental edits observably
wrong in code review.

### Verify Orchestrator

`agents-shipgate verify` is the canonical ongoing-PR command. It evaluates the
published trigger catalog against the local diff, optionally scans a locally
available base tree into an isolated temporary directory, and then runs exactly
one authoritative head scan. When `--head` is provided, the head scan uses an
isolated archive of that ref; when omitted, it scans the checked-out workspace.
`report.json.release_decision.decision` remains the only release gate;
`verifier.json` is an orchestration artifact.

`verify` never fetches. Callers that want base diff enrichment must make the
base ref available before invoking the command, for example with
`actions/checkout` `fetch-depth: 0` or an explicit `git fetch origin <base>` in
CI. If the requested base ref or PR diff context is unavailable, verify records
`base_status` in `verifier.json`, skips a head-only scan, emits
`merge_verdict: "unknown"`, and exits 2. If the base tree is available but the
base manifest or base scan is unavailable, verify records `base_status`, disables
diff enrichment, and leaves the head release decision and exit code unchanged.

The head scan writes `report.md`, `report.json`, `report.sarif`, `packet.json`,
`verifier.json`, and `pr-comment.md`. `verify` intentionally requests packet
JSON only, regardless of manifest `output.packet.formats`; `pr-comment.md` is
the human PR surface. Use `agents-shipgate scan` when you want the manifest's
full packet renderer set (`packet.md`, `packet.html`, or `packet.pdf`).

`agents-shipgate verify --preview --json` is a lightweight relevance check: it
runs no scan, requires no manifest, exits 0, and emits a `verifier.json` with
`mode: "preview"` and a `first_next_action` carrying the next recommended
action. That action may be `none` for irrelevant diffs, `detect`/`init` for
relevant unconfigured repos, or `verify` for configured repos. Use it as the
first touch on a repo or PR before committing to a full scan.

`verifier.json` is governed by [`docs/verifier-schema.v0.1.json`](docs/verifier-schema.v0.1.json)
(`verifier_schema_version` stays `"0.1"` within `0.x`; minor field additions are
additive). It remains an orchestration artifact: `release_decision.decision` in
`report.json` is still the only release gate, and every verifier field is either
a mirror or a deterministic projection of report data. Stable additive fields a
consumer may read:

- `merge_verdict` — `mergeable` / `human_review_required` /
  `insufficient_evidence` / `blocked` / `unknown`. A deterministic projection of
  `release_decision.decision` (`passed`→`mergeable`,
  `review_required`→`human_review_required`,
  `insufficient_evidence`→`insufficient_evidence`, `blocked`→`blocked`, missing
  decision→`unknown`). It cannot disagree with the gate. Switch on the enum with
  an `unknown`/`human_review_required` fallback for unrecognized future values.
- `can_merge_without_human` — `bool`; whether the PR can merge without human
  review.
- `decision` — mirror of `release_decision.decision` (or `null` when no scan
  ran).
- `headline` — single-sentence, PR-comment-friendly summary (or `null`).
- `human_review` — `{required: bool, why: str|null}`.
- `first_next_action` — `{actor: "coding_agent"|"human", kind, command, why}`.
  The `actor` distinguishes work a coding agent may do mechanically from a
  decision that requires a human.
- `trust_root_touched` — `bool`; `true` when the PR changed a release-gate trust
  root (`shipgate.yaml`, the Shipgate CI workflow, `AGENTS.md`/`CLAUDE.md`,
  policy packs, prompts, baselines, waivers, and the other surfaces listed under
  the trust-root protection design). Backed by the deterministic
  `SHIP-VERIFY-TRUST-ROOT-TOUCHED` check, whose findings flow through the normal
  decision engine.
- `capability_review` — deterministic reviewer-facing projection of
  `capability_change`, with `{trust_root_touched, policy_weakened,
  capability_changes_added, capability_changes_removed,
  capability_changes_modified, top_changes[]}`. `top_changes[]` carries the
  highest-signal capability deltas with `{id, title, impact, rationale,
  related_finding_ids}`. `impact` mirrors the gate; this block never introduces a
  finding-independent blocker.
- `mode` — `"advisory"` / `"strict"` / `"skipped"` / `"preview"`.

`verifier.json` also carries `trigger` (the run/skip evaluation), `base_status`,
`head_status`, `base_ref`, `head_ref`, `changed_files`, `base_notes`, the full
embedded `release_decision`, and an `artifacts` map
(`{verifier_json, pr_comment, report_json, report_markdown, report_sarif,
packet_json}`). The corresponding GitHub Action outputs are `merge_verdict`,
`can_merge_without_human`, `trust_root_touched`, and
`capability_changes_{added,modified,removed}`; the original `decision`,
`blocker_count`, `review_item_count`, and `ci_would_fail` outputs are preserved.

Successful base reports are cached under git metadata
(`git rev-parse --git-path agents-shipgate/base-scans/...`), not under the
working tree or report output directory. The cache is a local-iteration
optimization, safe to miss on ephemeral CI, and verify prunes stale entries
best-effort after writes.

### Verify Check IDs

New stable check IDs (v0.22+, category `verify` — trust-root protection
for AI coding workflows). All emit **only** when a `VerificationContext`
is present (`scan --changed-files …` or the `verify` command); a plain
`scan` emits nothing. Like `SHIP-VERIFY-TRUST-ROOT-TOUCHED` (v0.21), they
are category `verify`, so they bypass `checks.ignore` suppression and
declare a `floor_severity` (a manifest override below the floor is a
config error, exit 2). They are ordinary `Finding`s routed through
`release_decision` — never a second verdict.

- `SHIP-VERIFY-POLICY-WEAKENED` (high, floor high) — base-vs-head normalized
  effective policy weakened: CI mode downgraded, fail-on severity set
  loosened, or a severity override lowered across a tier. Fail-safe: when no
  base snapshot is available but a policy/manifest trust root was touched, it
  emits a review-required `base_snapshot_unavailable` finding rather than
  passing silently.
- `SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` (high, floor high) — a new
  suppression, a widened waiver scope, or a larger accepted-debt baseline
  versus the base report.
- `SHIP-VERIFY-CI-GATE-REMOVED` (critical, floor high) — a Shipgate CI
  workflow path is in the changed files and no longer exists on disk (the PR
  deleted the gate).
- `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED` (medium, floor medium) — an
  agent-instruction trust root changed; Shipgate cannot statically prove the
  instructions were not weakened, so it routes to human review.
- `SHIP-VERIFY-TRIGGER-CATALOG-DRIFT` (medium, floor medium) — the trigger
  catalog that decides when Shipgate runs changed; routed to human review to
  rule out gate evasion.

### Tool-Surface Diff

`agents-shipgate scan --diff-from <path>` accepts a prior `report.json` or a
v0.4 baseline JSON with `tool_surface_facts` and `action_surface_facts`. If both `--baseline` and
`--diff-from` are provided, `--baseline` continues to drive finding baseline
status, strict-mode filtering, and `release_decision.baseline_delta`;
`--diff-from` drives `tool_surface_diff` and `action_surface_diff`.

If `--diff-from` is absent and `--baseline` points at a v0.4 baseline with
surface facts, the baseline snapshot is used as the diff reference. v0.3
baselines can still enable `tool_surface_diff` but not `action_surface_diff`.
Older v0.2 baselines still load for accepted-debt gating, but they cannot
enable either surface diff and emit disabled diff notes instead.

The diff is static evidence only. It does not fetch branches in the CLI,
infer runtime routing, or execute tools. Action Surface Diff policy findings
can affect release gating through `findings[].blocks_release`; Tool Surface
Diff remains explanatory only.

### Release Evidence Packet (v0.6)

`agents-shipgate-reports/packet.json` is governed by [`docs/packet-schema.v0.6.json`](docs/packet-schema.v0.6.json). v0.6 adds the top-level `evidence_matrix` section (PR #104) and the optional `ReleaseDecisionItem.source` and `ReleaseDecisionItem.policy_evidence_source` pointers for reviewer-grade dual-source provenance (PR #103). v0.5 stays as the frozen reference at [`docs/packet-schema.v0.5.json`](docs/packet-schema.v0.5.json); pre-v0.6 packets validate against it. Within `0.x`:

- `packet_schema_version` is a real field on every emitted packet; minor bumps are additive.
- The reviewer sections (release_decision, evidence_matrix, capability_intent, high_risk_surface, tool_surface_diff, action_surface_diff, approval_coverage, idempotency_risk, scope_coverage, memory_isolation, human_in_the_loop, dynamic_scenarios, not_proven) are always present.
- `evidence_matrix.rows[]` is a compact, packet-only review summary derived from public `report.json` fields. It never contributes to `release_decision`, CI exit behavior, severity, suppression, baseline matching, or `agent_summary`; its blocker and review-item references are copied from `release_decision`.
- The 13 `evidence_matrix.rows[].domain` identities are stable within `0.x`. Adding source paths or check mappings is additive; removing a row, renaming a domain, or dropping an existing check/source mapping requires a packet schema bump.
- `human_in_the_loop.runtime_control_disclaimer` is always present and applies to covered and gap states: local HITL evidence is not runtime-enforcement proof.
- `human_in_the_loop.source_provenance[]` is deterministic, local-only provenance for validation evidence when available. Packets rebuilt from `report.json` may set `provenance_mode: "unavailable"` when no finding-level provenance survived.
- `release_decision.verdict` always derives from `release_decision.decision`. CI behavior (`fail_policy`) is rendered separately as metadata, never as the verdict.
- `not_proven.unconditional` always lists the four canonical disclaimers verbatim — prompt robustness, runtime behavior, model correctness, adversarial resistance.
- The packet is a local artifact (`agents-shipgate-reports/packet.{md,json,html}`, optionally `packet.pdf` with the `[pdf]` extras). There is no hosted/SaaS surface.

### Fixture names

Fixture names listed by `agents-shipgate fixture list` are stable. Names will not be renamed. New fixtures may be added.

### Agent-skill paths

The following paths are part of the public agent surface and will not move within `0.x`:

- [`prompts/`](prompts/) — task-shaped recipes, individual filenames are stable
- [`.claude/commands/shipgate.md`](.claude/commands/shipgate.md) — Claude Code `/shipgate` slash command
- [`skills/agents-shipgate/SKILL.md`](skills/agents-shipgate/SKILL.md) — Claude Code skill. Frontmatter `name` is fixed at `agents-shipgate` (deliberately distinct from the `/shipgate` command so the skill cannot preempt it). Trigger phrases in `description` may broaden additively but will not narrow.
- [`skills/agents-shipgate/prompts/`](skills/agents-shipgate/prompts/) and [`skills/agents-shipgate/ci-recipes/`](skills/agents-shipgate/ci-recipes/) — bundled supporting files the skill references via relative paths. Filenames listed in `SKILL.md` are stable.

The body content of these files may change to reflect new prompts; the entry-point paths will not.

---

## What MAY change additively in any minor release

These are not stable — assume they may grow but not shrink:

- **Risk-tag taxonomy.** New tags may appear (e.g. `infrastructure_change`, `code_execution`). Existing tags' meanings will not change.
- **`capability_facts[].capability` vocabulary.** Values are an open vocabulary seeded from risk tags plus review sentinels such as `wildcard_tool_surface` and `unknown`.
- **Report `frameworks.{name}` blocks.** New framework summaries (e.g. `frameworks.langchain`) may appear.
- **Manifest fields.** New optional fields under existing sections.
- **Check default severities.** May tighten over time. To pin a severity for your repo, use `checks.severity_overrides`.
- **`release_decision.decision` enum values.** New states (e.g., `insufficient_evidence` added at `report_schema_version` 0.14) may be added. Consumers that switch on the enum MUST fall back to `review_required` for unrecognized values — that is the safe default. Existing values' meanings will not change. New states do not change CI exit codes (exit 20 still requires a `fail_on` match on actual findings).
- **`agent_summary.verdict` enum values.** Mirror `release_decision.decision`; same additivity and fallback rule.
- **`reviewer_summary.verdict` enum values.** Mirror `release_decision.decision` and `agent_summary.verdict`; same additivity and fallback rule. The three enums move in lockstep — adding a value to one without the others is a contract violation.
- **`reviewer_summary.first_recommended_surface.{kind, name}` enum values.** New surface kinds and names may be added (e.g., when a sixth reviewer lens or fourth audit envelope ships). Consumers that switch on `name` MUST fall back to "ignore the pointer and read every documented surface" for unrecognized values. The priority order between surfaces may also be revised additively when a new surface is added — the contract is the deterministic projection, not the specific ranking.
- **`verifier_summary.verdict` enum values** (v0.22+). Mirrors `release_decision.decision`; same additivity and fallback rule. It joins `agent_summary.verdict` and `reviewer_summary.verdict` in the lockstep set — adding a value to one without the others is a contract violation.
- **`capability_change` member enum values** (v0.22+): `direction` (`added | removed | broadened | narrowed`), `subject_kind` (`tool | action | scope | policy | ci | baseline | agent_instruction | manifest | unknown`), and `release_impact` (`none | informational | review_required | blocks_release | insufficient_evidence`). New values may be added additively; consumers that switch on them MUST fall back to a conservative default (treat unknown `release_impact` as `review_required`, unknown `subject_kind` as `unknown`).
- **`protected_surface_changes[].kind`** (v0.22+) — the trust-root surface bucket (e.g. `manifest`, `policy`, `ci_gate`, `agent_instructions`, `trigger_catalog`). New buckets may be added as new trust-root classes ship; treat unknown kinds as "a protected surface was touched — review it".

---

## What MAY change in any minor release

These are explicitly NOT part of the public contract:

- **Internal module layout** under `src/agents_shipgate/`. Importing from non-public modules will break.
- **Legacy internal schema imports** such as `agents_shipgate.core.models`,
  `agents_shipgate.config.schema`, `agents_shipgate.core.patches`, and
  `agents_shipgate.packet.models`. Public wire-contract models live under
  `agents_shipgate.schemas.*`; internal scan/domain containers live under
  `agents_shipgate.core.*` and are not a stable consumer API.
- **Markdown report layout.** Section ordering, exact wording, and table format may change. Parse the JSON report instead.
- **Risk classifier keyword sets** in `core/risk_hints.py`. False positives are tuned over time. To pin specific behavior, use `risk_overrides.tools.{tool}.{tags,remove_tags}` in your manifest.
- **Default `init` template.** The starter manifest format may grow new sections.
- **`CheckMetadata.evidence_fields`** content. New keys may be added to a check's evidence dict.

If you need stability guarantees beyond what's listed here, please open an issue describing the use case.

---

## Versioning

We follow [SemVer](https://semver.org/) loosely:

- **Patch** (`0.5.x`): bug fixes only. No new features, no breaking changes.
- **Minor** (`0.x.0`): new features (new checks, new input loaders, new flags). Adheres to this contract.
- **Major** (`1.0.0`): may break the contract. Will be announced with a migration guide.

The current version is in [`pyproject.toml`](pyproject.toml). Changelog is in [`CHANGELOG.md`](CHANGELOG.md).

---

## Reporting a contract violation

If you encounter behavior that contradicts this document — for example, an unsuppressed finding for a deprecated check ID, or a stable JSON field that disappeared — please [open an issue](https://github.com/ThreeMoonsLab/agents-shipgate/issues/new) with:

1. The version of `agents-shipgate` (`agents-shipgate --version`)
2. The expected behavior per this document
3. The observed behavior (output, error message, JSON fragment)

Stability bugs are prioritized.
