# Stability Contract · 0.x

What agents and CI integrations can rely on across versions of Agents Shipgate.

This document is the contract. If the runtime ever diverges from what's documented here, that's a bug — please file an issue.

---

## What WILL NOT change in 0.x

### CLI command surface

These commands and flags are stable across all `0.x.y` releases. They will only change in a major version bump (`1.0.0`):

| Command | Stable flags |
|---|---|
| `agents-shipgate scan` | `-c`, `--config`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--diff-from`, `--no-plugins`, `--verbose`, `--workspace`, `--packet`/`--no-packet`, `--packet-format` |
| `agents-shipgate evidence-packet` | `--from`, `--out`, `--format`, `--json` |
| `agents-shipgate scenario suggest` | `--from`, `--out` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json` |
| `agents-shipgate doctor` | `-c`, `--config`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate contract` | `--json` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate explain-finding` (v0.12+) | `<fingerprint>`, `--from`, `--no-plugins`, `--json` |
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
| `20` | Strict-mode gate failure (≥ 1 unsuppressed finding hit `fail_on`) |

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

Signal paths use dotted notation; `[]` denotes an array field.

### JSON report fields (stable)

In `agents-shipgate-reports/report.json`, the following are guaranteed:

- `report_schema_version` — bumps minor on additive changes, major on breaking
- `release_decision.{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}` (v0.8+)
- `release_decision.fail_policy.{ci_mode, fail_on, new_findings_only, would_fail_ci, exit_code}`
- `release_decision.blockers[].{id, fingerprint, check_id, severity, title, baseline_status}` and `release_decision.review_items[].{id, fingerprint, check_id, severity, title, baseline_status}` (reference-only — both arrays share the same item shape; full Finding payload is in `findings[]`)
- `capability_facts[].{id, tool_name, source_type, source_ref, capability, risk_tags, auth_scopes, owner, included_reason, control_status, related_findings}` (v0.9+)
- `declared_intentions[].{id, kind, text, source, intent_tags}` (v0.9+)
- `misalignments[].{id, kind, severity, tool_name, capability_refs, intention_refs, finding_refs, policy_requirement, gap, release_implication}` (v0.9+)
- `release_consequence.{decision, summary, blocker_misalignment_count, review_misalignment_count, fail_policy}` (v0.9+)
- `suggested_scenarios[].{id, scenario_type, title, given, expected_control, source_misalignments, source_findings}` (v0.9+)
- `tool_surface_facts.{tools, scopes, controls, policies}` (v0.10+) — deterministic current facts used for static tool-surface comparison
- `tool_surface_diff.{enabled, base, summary, tools, high_risk_effects, scopes, controls, metadata_changes, policy_drift, finding_deltas, notes}` (v0.10+) — explanatory diff data only; it never changes `release_decision.decision` or exit behavior
- `summary.{critical_count, high_count, medium_count, low_count, info_count, suppressed_count, status, human_review_recommended}`
- `findings[].{id, fingerprint, check_id, severity, category, title, recommendation, suppressed}`
- `findings[].tool_name` (string or null)
- `findings[].source.{type, ref, location}` (when available)
- `findings[].source.{path, start_line, end_line, start_column, pointer}` (v0.11+) — minimal source provenance for the common tool-source loaders (OpenAPI, MCP, OpenAI tool artifacts, Anthropic tool artifacts). Optional and additive: keys are emitted only when the loader populates them. Reviewers can use `path` + `start_line` to jump to evidence; `pointer` is an RFC 6901 JSON pointer into the source file. JSON inputs do not carry line numbers in v0.11.
- `findings[].agent_action` (v0.12+) — deterministic projection of `patches`, `autofix_safe`, and `requires_human_review`. Enum: `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`. The first four cover the actionable cases; `informational` covers suppressed findings or non-actionable advisories. `suppress_with_reason` is reserved for future check classes that explicitly mark themselves as suppressible — the v0.12 deterministic projection does not emit it. New consumers should read `agent_action` first and treat the underlying flags as advisory.
- `agent_summary.{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}` (v0.12+) — top-level deterministic projection of `release_decision` + per-finding `agent_action`. Lets a coding agent read one block instead of traversing arrays. `first_recommended_action` is `{kind: "command" | "info", command: string | null, why: string}`; the `command` form carries an actual CLI invocation, the `info` form is a "surface this to the user" hint. Same inputs always produce the same output; this block cannot disagree with the underlying `release_decision` and `findings[].agent_action`.
- `baseline.{matched_count, new_count, resolved_count, path}` (when `--baseline` is used)
- `tool_inventory[].{name, source_type, source_ref, risk_tags, auth_scopes, owner, confidence}`
- `loaded_plugins[].{name, value, distribution, version, check_id}`

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

Plugins are off by default. `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` enables loading; `--no-plugins` overrides at the CLI level. When loaded, every plugin is enumerated in `report.loaded_plugins`.

### Manifest schema

The manifest schema version (`version: "0.1"`) is independent of the CLI version. Manifest schema changes follow their own deprecation cycle. A `0.1`-shaped manifest will load correctly across all `0.x.y` CLI releases.

### Tool-Surface Diff

`agents-shipgate scan --diff-from <path>` accepts a prior `report.json` or a
v0.3 baseline JSON with `tool_surface_facts`. If both `--baseline` and
`--diff-from` are provided, `--baseline` continues to drive finding baseline
status, strict-mode filtering, and `release_decision.baseline_delta`;
`--diff-from` drives only `tool_surface_diff`.

If `--diff-from` is absent and `--baseline` points at a v0.3 baseline with
`tool_surface_facts`, the baseline snapshot is used as the diff reference.
Older v0.2 baselines still load for accepted-debt gating, but they cannot
enable a surface diff and emit a disabled diff note instead.

The diff is static evidence only. It does not fetch branches in the CLI,
infer runtime routing, execute tools, or change release gating.

### Release Evidence Packet (v0.3)

`agents-shipgate-reports/packet.json` is governed by [`docs/packet-schema.v0.3.json`](docs/packet-schema.v0.3.json). Within `0.x`:

- `packet_schema_version` is a real field on every emitted packet; minor bumps are additive.
- The reviewer sections (release_decision, capability_intent, high_risk_surface, tool_surface_diff, approval_coverage, idempotency_risk, scope_coverage, memory_isolation, human_in_the_loop, dynamic_scenarios, not_proven) are always present.
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
