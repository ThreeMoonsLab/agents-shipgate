# Roadmap

## v0.2

- Improve onboarding with `agents-shipgate init`, `doctor`, and richer examples.
- Stabilize JSON report compatibility and finding fingerprints.
- Add baseline save/apply workflow.
- Harden OpenAPI parsing through property-based tests and fuzzing.
- Add MCP fuzzing, plugin-loader tests, coverage reporting, and YAML resource-limit tests.
- Add SBOM generation, release signing, and dependency audit checks.
- Expand manifest-aware checks.

## v0.3

- Add Google ADK static adapter MVP for Tool-Use Readiness:
  - Support `google_adk` as a manifest tool source.
  - Parse ADK Agent Config YAML and statically extract Python `LlmAgent` definitions, function tools, `OpenAPIToolset`, `McpToolset`, callbacks, plugins, sub-agents, and eval file references.
  - Normalize discovered ADK tools into the existing `Tool` schema and reuse MCP/OpenAPI loaders where possible.
  - Add ADK checks for dynamic or unresolved toolsets, unfiltered MCP toolsets, missing function-tool metadata, long-running tool contracts, guardrail evidence, and eval coverage.
- Add SARIF output.
- Add baseline diff mode for PRs.
- Add optional trace normalization.
- Add GitLab CI, CircleCI, and Jenkins examples.

## v0.4

- Add external policy/check packs.
- Harden multi-framework adapter support:
  - Introduce a shared framework adapter interface where it reduces duplication.
  - Stabilize ADK report schema fields.
  - Consider an explicit runtime inventory command that is trust-gated and never enabled by default.
  - Investigate TypeScript, Go, and Java ADK support after the Python MVP.
- Revisit container image distribution if demand appears and the image has CI coverage.

## Google ADK Support Plan

- Phase 1: Add a static-only Python and Agent Config loader. Do not import user ADK code.
- Phase 2: Integrate ADK OpenAPI and MCP toolsets through the existing loaders when users provide local specs or inventories.
- Phase 3: Add ADK-specific readiness checks and a Google ADK surface summary in reports.
- Phase 4: Consider optional dynamic inventory export only as an explicit command; keep it out of default CI.
- Phase 5: Refactor shared framework adapter seams after the ADK MVP validates the shape.

ADK support must preserve the default trust model: no `adk run`, `adk web`, `adk eval`, MCP connection, tool call, model call, or network call by default. ADK callbacks and plugins are static guardrail evidence only, not proof of runtime enforcement. Dynamic toolsets must produce warnings or findings unless the user provides explicit MCP, OpenAPI, or tool inventory inputs.
