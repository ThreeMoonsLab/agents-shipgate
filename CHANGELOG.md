# Changelog

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
