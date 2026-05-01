# Roadmap

> **Naming.** This project is **Agents Shipgate** (display name) / `agents-shipgate` (package, CLI, repo). See [`AGENTS.md` § Naming (canonical)](AGENTS.md#naming-canonical) for the full convention.

Agents Shipgate is currently in the `0.5.x` line. The `v0.2` through `v0.5`
items are complete and retained here as release history. Active public planning
starts at `v0.6.0`.

## Completed

### v0.2

- Improved onboarding with `agents-shipgate init`, `doctor`, `self-check`, fixtures, and richer examples.
- Stabilized JSON report compatibility, finding fingerprints, and agent-friendly JSON command output.
- Added baseline save/apply workflow for strict CI adoption.
- Hardened OpenAPI, MCP, plugin-loader, YAML resource-limit, and coverage test paths.
- Added SBOM generation, release signing workflow, and dependency audit checks.
- Expanded manifest-aware checks and severity overrides.

### v0.3

- Added Google ADK static adapter MVP for Tool-Use Readiness:
  - Supported `google_adk` as a manifest tool source.
  - Parsed ADK Agent Config YAML and statically extracted Python `Agent` / `LlmAgent` definitions, function tools, `OpenAPIToolset`, `McpToolset`, callbacks, plugins, sub-agents, eval references, and explicit local inventories.
  - Normalized discovered ADK tools into the existing `Tool` schema and reused MCP/OpenAPI loaders where possible.
  - Added ADK checks for dynamic or unresolved toolsets, unfiltered MCP toolsets, missing function-tool metadata, long-running tool contracts, guardrail evidence, and eval coverage.
- Added SARIF output.
- Added baseline diff mode for PRs.
- Added optional trace normalization.
- Added GitLab CI, CircleCI, and Jenkins examples.

### v0.4

- Added external policy/check packs:
  - Declarative YAML policy packs under `checks.policy_packs`.
  - CLI and GitHub Action policy-pack overrides for CI.
  - Policy-pack findings flow through suppressions, severity overrides, baselines, Markdown, JSON, and SARIF.
- Hardened multi-framework adapter support:
  - Introduced a shared framework adapter interface where it reduces duplication.
  - Stabilized ADK report schema fields.
  - Documented explicit runtime inventory as a future trust-gated command; it is not part of default CI.
  - Kept TypeScript, Go, and Java ADK support as post-Python-MVP investigation items.
- Split bundled OpenAI API operational readiness findings into atomic check IDs.
- Removed the legacy top-level `check_severity_overrides` alias.
- Revisited container image distribution and kept it deferred until there is an exercised build-and-test path.

### v0.5.0 LangChain/CrewAI And Focused CI

- Added static Python coverage for LangChain/LangGraph and CrewAI while preserving the default static trust model.
- Promoted GitLab CI and CircleCI from examples to first-class integration recipes:
  - documented strict/advisory gating;
  - baseline artifact handling;
  - SARIF or native security report guidance where supported;
  - copy-pasteable workflows for monorepos and multi-manifest scans.
- Added a framework adapter checklist so new platform support is consistent:
  - static extraction only by default;
  - no agent execution, model call, tool call, network call, or MCP connection;
  - deterministic tool inventory normalization;
  - source warnings for dynamic or unresolved tools;
  - framework surface summary in JSON, Markdown, and SARIF-compatible metadata.
- Bumped the additive report schema to `report_schema_version: "0.5"` while
  keeping manifest `version: "0.1"`.

### v0.6.0 Agent-Friendly Adoption

Goal: compress the 5-step setup (install → init → edit YAML → scan →
read findings → wire CI) into a single tool-using turn for AI coding
agents.

- Added `agents-shipgate detect` for read-only workspace classification
  (which framework, which agent-name candidates, which suggested
  sources).
- Made `agents-shipgate init` auto-detect by default; framework-specific
  manifests are produced for LangChain, CrewAI, Google ADK, OpenAI
  Agents SDK, Anthropic, and OpenAI API. `--minimal` preserves the
  pre-v0.6 template byte-exact.
- Added `agents-shipgate init --ci` for opt-in workflow generation,
  with cross-workflow shipgate detection.
- Added `agents-shipgate scan --suggest-patches` and
  `agents-shipgate apply-patches` for machine-applicable manifest fixes
  (stale-manifest removals at high confidence; scope-coverage appends
  at medium confidence opt-in).
- Bumped report schema to v0.6 (additive: per-finding `patches`,
  top-level `manifest_dir`).

## Open

### v0.7.0 Cross-Platform CI Expansion

Goal: broaden CI documentation after the v0.6 agent-friendly adoption
work lands.

- Add or harden recipes for additional CI platforms:
  - Jenkins;
  - Buildkite;
  - Azure Pipelines;
  - Bitbucket Pipelines;
  - local pre-commit / pre-push usage;
  - generic POSIX shell integration for unsupported CI systems.
- Improve cross-platform release-gate documentation:
  - reference architecture for advisory-to-strict rollout;
  - baseline management across CI providers;
  - recommended artifact retention;
  - failure-mode examples for security review and platform engineering teams.

### v0.6.x Source-Provenance Enrichment (incremental)

Once we have origin (file path, line index for JSONL, list index for
arrays) threaded through finding evidence, expand the patch generator
catalog beyond manifest-only. Candidate generators:

- `SHIP-API-RETRY-POLICY-MISSING` and `SHIP-API-TIMEOUT-MISSING`
  targeting policy-rule files (likely with a new `create_file` patch
  kind).
- Trace-event metadata enrichments — but never approval/confirmation
  flips, which stay manual permanently.

### Later Candidates

- Expand agent-platform coverage beyond the v0.5 framework adapters:
  - AutoGen multi-agent tool surfaces;
  - Semantic Kernel plugins/functions;
  - LlamaIndex tools and workflows;
  - TypeScript/JavaScript agent frameworks where static extraction is practical;
  - additional Google ADK language surfaces after the Python adapter remains stable.
- Optional trust-gated runtime inventory export as an explicit command, separate from default static CI.
- Container image distribution if the image has CI coverage, security scanning, and release signing.
- Homebrew or other package-manager distribution if CLI usage warrants it.
- Public versions of the private release-readiness research themes once they are shaped into concrete checks and schema changes.

## Google ADK Support Principles

ADK support is read-only by default: local file parsing only; no `adk run`,
`adk web`, `adk eval`, MCP connection, tool call, model call, or network call.
ADK callbacks and plugins are static guardrail evidence only, not proof of
runtime enforcement. Dynamic toolsets must produce warnings or findings unless
the user provides explicit MCP, OpenAPI, or tool inventory inputs.
