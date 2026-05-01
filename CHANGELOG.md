# Changelog

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
