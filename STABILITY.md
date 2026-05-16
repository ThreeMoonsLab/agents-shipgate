# Stability Contract · 0.x

What agents and CI integrations can rely on across versions of Agents Shipgate.

This document is the contract. If the runtime ever diverges from what's documented here, that's a bug — please file an issue.

---

## What WILL NOT change in 0.x

### CLI command surface

These commands and flags are stable across all `0.x.y` releases. They will only change in a major version bump (`1.0.0`):

| Command | Stable flags |
|---|---|
| `agents-shipgate scan` | `-c`, `--config`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--diff-from`, `--no-plugins`, `--strict-plugins`, `--verbose`, `--workspace`, `--packet`/`--no-packet`, `--packet-format` |
| `agents-shipgate evidence-packet` | `--from`, `--out`, `--format`, `--json` |
| `agents-shipgate scenario suggest` | `--from`, `--out` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json` |
| `agents-shipgate doctor` | `-c`, `--config`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate contract` | `--json` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate explain-finding` (v0.12+) | `<fingerprint>`, `--from`, `--no-plugins`, `--json` |
| `agents-shipgate bootstrap` | `--workspace`, `--confidence`, `--no-ci`, `--no-apply`, `--json` |
| `agents-shipgate list-checks` | `--json`, `--no-plugins` |
| `agents-shipgate baseline save` | `-c`, `--config`, `--out` |
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
- `findings[].provenance_kind` (v0.15+) — records *how a finding was produced*; independent of `confidence`, which records how *sure* we are. Enum: `static_declaration | ast_extraction | keyword_heuristic | regex_heuristic | policy_pack`. `static_declaration` covers manifest, MCP, OpenAPI schema facts, and declarative framework inputs like ADK YAML agent configs or LangChain/CrewAI inventory JSON files — high-trust structural data. `ast_extraction` covers findings against Tools parsed from user Python source by a framework extractor (LangChain function/structured tools, CrewAI function/class tools, ADK Python toolsets); these are subject to extraction error and agents that distrust AST quality can filter them as a class. Framework checks that fire against both AST-extracted and declaratively loaded tools (ADK's per-tool checks) pick the label per tool from `tool.source_type`. `keyword_heuristic` covers token-list matches (broad scope, read-only prompts, free-text parameter names); `regex_heuristic` covers regex matches (secrets, prompt injection); `policy_pack` covers findings emitted by externally loaded policy packs. Built-in checks set the value via the required kwarg on the `tool_finding`/`agent_finding` helpers; third-party plugin checks that construct `Finding(...)` directly and omit the field are coerced to `static_declaration` by `annotate_remediation` so the wire schema stays satisfied. Required + non-nullable on the wire; the field is Python-Optional only so older v0.12/v0.13 reports loaded by `explain-finding` and minimal synthetic test fixtures keep working.
- `findings[].blocks_release` (v0.16+) — explicit release-policy blocking bit. Built-in and user-defined Action Surface Diff policies, plus declarative policy-pack rules with `block: true`, set it for findings that must block release when active and unbaselined; ordinary severity-based gating still works for existing checks.
- `action_surface_facts.actions[]` (v0.16+) — deterministic current action snapshot: action id, operation, effect, normalized risk tags, scopes, approval policy, safeguards, evidence, input fields, and stable hashes.
- `action_surface_diff.{enabled, base, summary, added, removed, modified, notes}` (v0.16+) — reviewer-facing delta for what the agent can do vs. a prior report or v0.4 baseline. Policy findings derived from this diff can set `findings[].blocks_release=true` and affect `release_decision.decision` and strict-mode exit behavior.
- `release_decision.contribution_rules[].{finding_id, fingerprint, check_id, category, rule, rationale}` (v0.17+) — deterministic per-finding audit of how each finding contributed to the release decision. Required + always present (defaults to `[]` for legacy reports loaded via `explain-finding`). Exactly one row per `report.findings` entry, including suppressed findings, so the audit set is exhaustive over the full findings list. `category` enum: `blocker | review_item | excluded`. `rule` enum: `policy_block_new | severity_block_new | policy_baseline_accepted | severity_baseline_accepted | review_required | sub_threshold | suppressed`. The (rule, category) pairs the gate can produce are exhaustively documented in [Release decision truth table](#release-decision-truth-table) below — reading the contribution rule is sufficient to predict the outcome for that finding without re-deriving the decision logic. The audit cannot disagree with `release_decision.{blockers,review_items}[]`: the same classification powers both. Adding `contribution_rules` does not change any existing behavior — `decision`, `blockers[]`, `review_items[]`, `fail_policy.exit_code`, and strict-mode exit codes are byte-identical to v0.16.
- `baseline.{matched_count, new_count, resolved_count, path}` (when `--baseline` is used)
- `tool_inventory[].{name, source_type, source_ref, risk_tags, auth_scopes, owner, confidence}`
- `loaded_plugins[].{name, value, distribution, version, check_id}`
- `loaded_plugins[].{validation_status, validation_errors, runtime_errors}` (v0.17+ / M5) — plugin validation provenance, required + present on every entry. `validation_status` is one of `valid | load_failed | bad_signature | bad_metadata | id_collision | bad_floor`; the two error lists are always present and empty for clean plugins. Invalid plugins still appear in this array (with `check_id: null` for entries that failed before metadata parsing), so reviewers can see what was skipped without reading scanner logs. Plugin findings whose `check_id` does not match the declared metadata are dropped at runtime and recorded under `runtime_errors`.

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

### Check IDs

Once a check ID ships in a tagged release (`SHIP-POLICY-APPROVAL-MISSING`, `SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING`, etc.), it will not be:

- Renamed
- Removed (only deprecated, with at least one minor-version cycle)
- Repurposed (the conditions under which it fires may *narrow* but never broaden in a way that breaks existing suppressions)

New check IDs may be added in any minor release. If your CI pins severities by check ID, expect new checks to surface as new findings.

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
  AST scan of every source under `src/agents_shipgate/inputs/`. The scan
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
  under `checks/` (not `inputs/`) uses it for entry-point discovery,
  and discovery happens against the *installed* environment, not user
  workspace files. Aliased re-exports (`import os as oo`,
  `from os import system as sh`, `import os; import pathlib as os`) are
  resolved through union-of-bindings alias maps so a later import
  cannot erase an earlier forbidden binding. The lint runs as a
  dedicated CI step labeled *Trust-model invariant lint* before the
  main test suite so a regression is visible at the top of CI logs.
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

Plugins are off by default. `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` enables loading; `--no-plugins` overrides at the CLI level. When loaded, every plugin is enumerated in `report.loaded_plugins`.

Plugin validation (v0.17+ / M5). Every entry point is checked against five load-time gates before it can run:

1. **load** — `entry_point.load()` must not raise.
2. **signature** — the loaded object must be callable and accept exactly one required positional parameter (`ScanContext`); extra defaulted positional / keyword-only parameters are allowed.
3. **metadata** — `AGENTS_SHIPGATE_METADATA` must be present and parseable as `CheckMetadata`. Both `id` and `check_id` are accepted as the identifier key (v0.17 alias); newer plugins should prefer `check_id` for symmetry with `Finding.check_id`.
4. **id_collision** — the plugin's check ID must not shadow a built-in (including legacy aliases) or a previously-registered plugin.
5. **bad_floor** — `floor_severity` must not exceed `default_severity` on the same metadata block.

Plugins that pass every gate run with the same trust as built-ins. Runtime validation additionally drops findings whose `Finding.check_id` does not match the plugin's declared `id`/`check_id`, drops non-`Finding` items, and captures any exception raised during the plugin call into `loaded_plugins[].runtime_errors`. The scan continues regardless; `--strict-plugins` elevates any non-`valid` plugin or non-empty `runtime_errors` to exit code 4.

### Manifest Schema

The manifest schema version (`version: "0.1"`) is independent of the CLI
version and package version. Manifest schema changes follow their own
deprecation cycle, and the manifest loader is intentionally strict: older CLIs
reject unknown top-level fields instead of silently ignoring release policy.
Manifests that use `action_surface:` require a CLI whose
`agents-shipgate contract --json` reports `report_schema_version >= 0.16`.

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

### Release Evidence Packet (v0.5)

`agents-shipgate-reports/packet.json` is governed by [`docs/packet-schema.v0.5.json`](docs/packet-schema.v0.5.json). Within `0.x`:

- `packet_schema_version` is a real field on every emitted packet; minor bumps are additive.
- The reviewer sections (release_decision, capability_intent, high_risk_surface, tool_surface_diff, action_surface_diff, approval_coverage, idempotency_risk, scope_coverage, memory_isolation, human_in_the_loop, dynamic_scenarios, not_proven) are always present.
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

---

## What MAY change in any minor release

These are explicitly NOT part of the public contract:

- **Internal module layout** under `src/agents_shipgate/`. Importing from non-public modules will break.
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
