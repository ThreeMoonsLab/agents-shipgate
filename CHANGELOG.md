# Changelog

## Unreleased

- Added JSON-first tool-surface diff for PR review. `agents-shipgate scan`
  now emits `report_schema_version: "0.10"` with always-present
  `tool_surface_facts` and `tool_surface_diff` fields. The diff explains
  added/removed/changed tools, high-risk tag changes, scope drift, enforcement
  control changes, policy drift, finding deltas, and accepted debt without
  changing `release_decision.decision`, strict/advisory exit behavior, or SARIF.
- Added `agents-shipgate scan --diff-from <path>` for comparing against a prior
  `report.json` or v0.3 baseline JSON. `--baseline` still controls finding
  baseline status and strict-mode filtering; `--diff-from` controls only
  `tool_surface_diff`.
- Baseline files now save as schema `0.3` with optional `tool_surface_facts`.
  Schema `0.2` baselines continue to load for accepted-debt matching but cannot
  enable surface diff by themselves.
- GitHub Action adds `diff_from`, `diff_base`, and `diff_enabled`. Setting
  `diff_base: target` performs a best-effort target-branch scan with the
  PR-side installed package and falls back to a disabled diff note on fetch,
  config, schema, or scan failures.
- Release Evidence Packet schema bumped to `0.2` with a compact
  `tool_surface_diff` section derived from the report JSON.
- Added optional manifest-level HITL validation evidence mode under
  `validation:`. The scanner now reads local approval traces, override logs,
  high-risk auto-approval exclusions, and promotion criteria to structure
  evidence gaps for reviewers; it does not generate those runtime artifacts or
  certify readiness.
- Tightened HITL evidence wording and provenance. `SHIP-EVIDENCE-*` findings
  now describe missing or incomplete local review evidence without implying
  runtime controls are absent, and include deterministic
  `evidence.source_provenance[]` entries. `source_provenance` is excluded from
  finding fingerprints, so adding provenance does not rotate existing HITL
  baselines or suppressions.
- Release Evidence Packet schema bumped to `0.3` with
  `human_in_the_loop.runtime_control_disclaimer`,
  `human_in_the_loop.source_provenance[]`, and
  `human_in_the_loop.provenance_mode`.
- Added `samples/hitl_evidence_covered_agent`, a refund-domain fixture with
  local approval trace, override log, high-risk exclusion, and promotion
  criteria evidence.
- Added four `SHIP-EVIDENCE-*` checks. Existing baselines may surface these as
  new findings after upgrade when a manifest opts into `validation:`.
- Add `agents-shipgate scenario suggest` (target: `0.9.1`), a YAML export that
  fans out `report.json.suggested_scenarios[]` into concrete
  per-finding/per-tool dynamic validation steps.
- Added ranked next-action diagnostics: `detect --json` and `doctor --json`
  now emit `diagnostics: [...]` and `next_actions: [...]` blocks alongside
  the existing single-string `next_action` field. Coding-agent callers can
  recover from common first-run failures (missing manifest, zero tools,
  unresolved `CHANGE_ME`, missing source files, MCP/OpenAPI artifact-only
  workspaces, dynamic toolsets, production targets without permissions, and
  three negative-control cases) without consulting human-facing docs. Errors
  emitted under `AGENTS_SHIPGATE_AGENT_MODE=1` carry the same `next_actions`
  array. Diagnostic catalog and schema in [docs/diagnostics.md](docs/diagnostics.md).
- Behavior change: when a required `tool_sources[].path` does not
  resolve (file missing OR resolves outside the manifest directory),
  `agents-shipgate doctor --json` exits **0** with
  `unresolved_sources: [...]` and a `SHIP-DIAG-MISSING-SOURCE-FILE`
  diagnostic so an agent gets a routable next action. The non-JSON
  `agents-shipgate doctor` form prints the same diagnostic in
  human-readable form and exits **3** so interactive users still see a
  loud failure. `agents-shipgate scan` is unchanged — it still raises
  `InputParseError(3)` on the same condition regardless of `--json`.
- `DetectResult` gains a `workspace_signals` block (Python file count,
  `pyproject.toml`/`requirements.txt` presence, conventional dir hits) used
  by the new diagnostic resolvers to discriminate negative-control cases.
  The block is additive; existing fields are unchanged.

## 0.8.0 - 2026-05-05

- Report schema bumped to `v0.8`. New top-level required `release_decision` block:
  `{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}`.
  - `decision` is one of `"blocked" | "review_required" | "passed"` and is the
    recommended release-gate signal for v0.8+ consumers.
  - `blockers` and `review_items` are reference-only entries
    (`id, fingerprint, check_id, severity, title, baseline_status`) — full
    Finding payloads stay in `findings[]`.
  - `release_decision` is **baseline-aware**: matched criticals appear in
    `review_items` (accepted debt), not `blockers`. Critical severity is
    **policy-independent** — even advisory CI surfaces a new critical as a
    blocker (with `would_fail_ci=false`).
  - `release_decision.fail_policy.exit_code` matches the process exit code
    one-for-one across all `ci_mode` × `fail_on` × `--baseline` combinations.
- `summary.status` is preserved byte-for-byte for backwards compatibility
  with v0.7 consumers. It stays baseline-blind (a baseline-matched critical
  still flips status to `release_blockers_detected`). The intentional
  divergence from `release_decision.decision` is documented in
  [STABILITY.md](STABILITY.md#release_decisiondecision-vs-summarystatus).
- `docs/report-schema.v0.8.json` added; `v0.7.json` retained as a frozen
  reference. JSON-schema validation catches missing `release_decision` on
  any emitted report.
- Markdown / GitHub Action / CLI summaries now lead with the Release
  Decision block (Decision → Reason → Blockers → Review items → Evidence
  coverage → Baseline delta → Fail policy). SARIF output is unchanged.
- GitHub Action exposes four new outputs: `decision`, `blocker_count`,
  `review_item_count`, `ci_would_fail`. Existing outputs (`status`,
  `critical_count`, `baseline_*`, `adk_*`, `report_*`, `exit_code`)
  unchanged.
- The release verdict path remains deterministic and LLM-free: no agent
  execution, tool call, model call, MCP connection, network access, or
  telemetry is added for v0.8.
- `exit_code_for_report()` refactored to share `effective_fail_on()` and
  `baseline_filtered_active()` helpers with `build_release_decision()`,
  so the standalone exit code and `release_decision.fail_policy.exit_code`
  cannot drift. New regression test pins this across the matrix.

## 0.7.0 - 2026-05-01

Adoption activation: makes the v0.6 features visible to humans and AI
coding agents on real repos, plus exposes per-check remediation
metadata so agents can route findings without re-walking the catalog.

- Agent-facing docs surface:
  - New "Should I run Shipgate on this PR?" trigger table in
    `AGENTS.md` with the soft-stop rule (don't skip MCP/OpenAPI-only
    repos that surface as `is_agent_project: false`).
  - New `docs/agent-recipes.md` — copy-pasteable AI-agent workflows
    for the canonical 4-call flow.
  - New `docs/autofix-policy.md` — four classes (safe / medium /
    manual / never), catalog-vs-Finding contract, strict derivation
    rule, three patch states, unknown-check-id fallback,
    `apply-patches --confidence` table, decision tree.
  - New `docs/minimal-real-configs.md` — per-framework references to
    runnable `samples/*` fixtures (no inline snippets to drift).
  - `docs/INDEX.md` cleanup: stale `report-schema.v0.5.json` link
    removed; current schema link now `report-schema.v0.7.json`.
  - `docs/quickstart.md` adds a "second 60 seconds" real-repo path.
- `CheckMetadata` extensions:
  - New `autofix_safe`, `requires_human_review`, `suggested_patch_kind`
    fields on every check (45 entries). `docs_url` populated for every
    check pointing at a stable `### SHIP-...` anchor in
    `docs/checks.md`. 7 new per-check sections added to `docs/checks.md`
    so every check has a stable anchor.
  - Catalog-level safety bools stay conservative — even checks whose
    generator usually produces a safe non-manual patch (stale-manifest
    removals, scope coverage) keep `autofix_safe: false` /
    `requires_human_review: true` because the generator can fall back
    to `ManualPatch` in edge cases (ambiguous duplicates, etc.).
    `suggested_patch_kind` is informational — describes what the
    generator targets when conditions are clean.
- `Finding` extensions + derivation:
  - Same four optional fields on every Finding, populated by
    `annotate_remediation` during scan. Three patch states handled
    distinctly:
    - `patches: None` (no `--suggest-patches`) → seed from
      CheckMetadata; safe-closed fallback for unknown check IDs
      (policy packs, third-party plugins).
    - `patches: []` (--suggest-patches ran but generator emitted
      nothing) → safe-closed shape with `suggested_patch_kind: "none"`.
      Does NOT fall back to catalog (the report carries no patches).
    - `patches: [...]` (non-empty) → strict derivation rule:
      `autofix_safe: true` ONLY when EVERY emitted patch is non-manual
      AND high-confidence. Mixed states fall to safe-closed.
  - `docs_url` always sourced from CheckMetadata (patches don't carry
    per-instance documentation URLs).
- Report schema bumped to `v0.7` per
  [STABILITY.md](STABILITY.md#stability-contract) ("`report_schema_version`
  bumps minor on additive changes"). `docs/report-schema.v0.7.json`
  added; `v0.6.json` retained as a frozen reference.
- `_run_id` excludes the four new derived fields plus `patches` so
  toggling `--suggest-patches` (or future enrichment fields) doesn't
  shift the hash. New regression test pins this.
- Plugin-loading isolation: every code path that reads the catalog
  during scan honors the scan's `plugins_enabled` setting, including
  the `_attach_patches` recommendation lookup.
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan --no-plugins`
  no longer loads plugins.
- Onboarding prompt rewrite: `prompts/add-shipgate-to-repo.md` now
  leads with the canonical 4-call flow (`detect → init --write --ci →
  scan --suggest-patches → apply-patches --json`) and includes the
  decision tree from `docs/autofix-policy.md`. Soft-stop rule
  documented inline. `apply-patches --json` flag added so the
  reporting step has structured data to read.
- Dual-copy prompt parity: byte-identical mirror between
  `prompts/` and `skills/agents-shipgate/prompts/` enforced by
  `tests/test_prompt_parity.py` so the two surfaces can't drift.
- Test coverage: 314 tests pass. New test files:
  `tests/test_remediation_metadata.py`,
  `tests/test_finding_remediation.py`,
  `tests/test_docs_links.py`,
  `tests/test_prompt_parity.py`,
  `tests/test_v07_metadata_roundtrip.py`.

## 0.6.0 - 2026-04-30

Agent-friendly adoption: compresses Shipgate setup into a single
tool-using turn for AI coding agents.

- Added `agents-shipgate detect` — read-only command that classifies a
  workspace as an agent project and reports which framework(s) it uses,
  with confidence and per-framework evidence.
- `agents-shipgate init` now auto-detects by default. Generated
  manifests are schema-valid (validated before write) and include
  framework-specific tool sources and config blocks (LangChain, CrewAI,
  Google ADK, OpenAI Agents SDK, Anthropic, OpenAI API). The legacy
  CHANGE_ME-heavy template is preserved under `--minimal`.
- Added `agents-shipgate init --ci` — opt-in flag that writes
  `.github/workflows/agents-shipgate.yml`. Orthogonal to `--write`:
  each gets its own overwrite-refusal check. Detects cross-workflow
  shipgate references and skips with a distinct message.
- Added `agents-shipgate scan --suggest-patches` — attaches Patch
  objects to every active finding (machine-applicable for the safe
  subset; ManualPatch for everything else). `Finding.patches` is
  absent when the flag is not set; non-opting JSON consumers see no
  contract change.
- Added `agents-shipgate apply-patches` — applies patches from a scan
  JSON report. File-grouped, single SHA per file, dry-run by default,
  containment-checked against the report's new `manifest_dir` field.
- v0.6 patch generators (manifest-target only):
  - High-confidence `RemovePointerPatch` for the 3 stale-manifest
    checks (SUPPRESSION, POLICY, RISK-OVERRIDE).
  - Medium-confidence `AppendPointerPatch` for
    `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (NOT applied at default
    `--confidence high` — adding scopes can encode policy choices).
  - Permanent `ManualPatch` (with anti-pattern instructions) for
    `SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING` — flipping
    approved/confirmed in a trace patches the evidence, not the agent.
- Bumped report schema to v0.6 (additive: optional `Finding.patches`
  array; new top-level `manifest_dir`). v0.5 schema retained for
  reference.
- Anthropic-specific glob coverage in `init`: tools and policies
  matching `tools/anthropic-tools.json` and
  `policies/anthropic-policy.yaml` now populate the `anthropic:` block
  automatically.
- Added end-to-end agent task `02_three_command_flow` exercising the
  full `detect → init → scan → apply-patches` pipeline.
- Added `ruamel.yaml>=0.18` as a dependency for round-trip-preserving
  YAML edits in `apply-patches`.

## 0.5.1 - 2026-04-29

- Polished launch-facing docs after the v0.5.0 release.
- Updated active examples and discovery metadata to the v0.5.1 release tag.
- Added curated launch marketing and presentation assets while excluding them
  from PyPI source distributions.
- Fixed stale baseline-mode CLI help text.

## 0.5.0 - 2026-04-28

- Added static LangChain/LangGraph and CrewAI Python adapters with manifest
  source types, supplemental inventories, framework report blocks, fixtures,
  and self-check coverage.
- Added framework-specific checks for dynamic LangChain/CrewAI tool surfaces
  and missing function-tool metadata.
- Promoted GitLab CI and CircleCI to first-class integration recipes with
  advisory, strict baseline, artifact, multi-config, and tool-source trigger
  examples.
- Added report schema v0.5 for additive LangChain/CrewAI framework fields.
- Added a framework adapter checklist for future static framework support.
- Deduplicated `source_warnings`; baselines from 0.4.x may report a small
  number of resolved warning entries on first run after upgrade.

## 0.4.0 - 2026-04-27

- Added declarative YAML policy packs with manifest, CLI, report, SARIF, and GitHub Action support.
- Split `SHIP-API-OPERATIONAL-READINESS` into atomic OpenAI API operational readiness check IDs.
- Kept `SHIP-API-OPERATIONAL-READINESS` as a deprecated compatibility alias for suppressions, severity overrides, baseline matching, and check metadata.
- Removed the legacy top-level `check_severity_overrides` alias; use `checks.severity_overrides`.
- Added report schema v0.4 with `loaded_policy_packs` and stabilized Google ADK warnings in the framework surface.
- Added an internal framework adapter seam and documented runtime inventory as design-only.

## 0.3.0 - 2026-04-26

- Added static Google ADK support through `tool_sources[].type: google_adk` and supplemental `google_adk` manifest artifacts.
- Added ADK Python AST and Agent Config YAML extraction for agents, function tools, toolsets, callbacks/plugins, sub-agents, eval references, and explicit local inventories.
- Added six ADK readiness checks covering dynamic toolsets, unfiltered MCP toolsets, missing function metadata, long-running contracts, guardrail evidence, and production eval coverage.
- Added SARIF output via `--format sarif` and GitHub Action SARIF/baseline/ADK outputs.
- Added report schema v0.3 with a generic `frameworks.google_adk` surface summary.
- Added reusable local trace normalization for explicit trace/eval artifacts.

## 0.2.0 - 2026-04-26

- Added manifest-aware checks, deterministic report metadata, check severity overrides, `fail_on`, `init`, `doctor`, `explain`, multi-config scan support, and check entry-point hooks.
- Renamed the project to Agents Shipgate and hardened v0.1 release-readiness behavior.

## 0.1.0

- Initial Agents Shipgate MVP.
- Manifest-first scan over local MCP JSON, OpenAPI specs, and optional OpenAI Agents SDK AST metadata.
- Markdown and JSON reports.
- Advisory and strict CI modes.
- GitHub composite action.
