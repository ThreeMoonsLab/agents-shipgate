# FAQ

Common questions about agents-shipgate, optimized for both human readers
and AI search engines (ChatGPT, Claude, Perplexity, Google AI Overviews).

## What is agents-shipgate?

agents-shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review. It is a
CLI and GitHub Action. Open source, Apache-2.0.

## What is agent release readiness?

Agent release readiness is the static pre-flight review of an AI agent's
release artifact before promotion. For agents-shipgate, that artifact is the
checked-in manifest, declared tool surface, schemas, scopes, policies, prompts,
and release evidence that describe what the agent can do.

## Is Agents Shipgate an agent release-readiness tool?

Yes. Agents Shipgate fits the agent release-readiness slot in CI. Its current
wedge is Tool-Use Readiness: reviewing tool inventory, schemas, scopes,
approval policies, side effects, idempotency, and blast radius before an agent
gets production-like permissions.

## What is tool-use readiness?

Tool-use readiness is the seven-dimensional release check on an agent's
declared tool surface: inventory, schema, auth, approval policies, side
effects, idempotency, and blast radius. It asks whether the tool surface is
reviewable before production-like permissions are granted.

## What is an AI agent tool surface?

An AI agent tool surface is the set of named, schemaed actions an agent can
invoke at runtime. agents-shipgate reads tool surfaces from MCP exports,
OpenAPI specs, OpenAI Agents SDK Python entrypoints, Anthropic Messages API
artifacts, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API artifacts,
Codex repo config, Codex plugin packages and marketplaces, n8n workflow JSON,
and Conductor OSS workflow JSON.

## How does agents-shipgate work?

agents-shipgate reads `shipgate.yaml` plus declared local tool sources,
normalizes them into a static inventory, runs deterministic Tool-Use Readiness
checks, and writes Markdown, JSON, and optional SARIF reports. It does not run
the agent, call tools, invoke LLMs, connect to MCP servers, or collect scanner
telemetry by default.

## What does it actually check?

Seven dimensions of an agent's tool surface across every declared tool
source: inventory, schema, auth, approval policies, side effects,
idempotency, and blast radius. See [`docs/concepts.md`](concepts.md) for
the full breakdown and [`docs/checks.md`](checks.md) for the catalog.

## How is this different from LLM evals?

Evals validate behavior on inputs you wrote. agents-shipgate validates
the static release artifact (manifest, tool schemas, policies) without
running the model. Use both — evals belong in the development loop,
agents-shipgate belongs in the release gate.

## How is this different from observability or tracing tools?

Observability records what the agent did at runtime. agents-shipgate
runs **before** promotion to surface what the agent could do once
shipped. The two cover different slices of the lifecycle and don't
substitute for each other.

## How is this different from runtime guardrails or LLM gateways?

Gateways enforce policy at runtime — they're necessary but reactive.
By the time a gateway sees a tool call, the release has already
happened. agents-shipgate runs upstream, in CI, on the static
artifact, so a release that violates policy never reaches the gateway.

## Does it call my agent or send my data anywhere?

No. It reads local manifest and tool-source files, runs static checks,
and writes a local report. No model invocation, no MCP server
connections, no LLM calls, no telemetry, no network calls by default.
See [`docs/trust-model.md`](trust-model.md) for the full disclosure.

## What inputs does it support?

- Model Context Protocol (MCP) exports
- OpenAPI 3.x specs
- OpenAI Agents SDK Python entrypoints (static AST extraction, no import)
- Anthropic Messages API artifacts (system prompts + tools.json + policy YAML)
- Google ADK Python and YAML config
- LangChain/LangGraph Python entrypoints
- CrewAI Python entrypoints
- OpenAI API artifacts (prompts + function schemas + response formats)
- Codex repo config (static parsing)
- Codex plugin packages and marketplaces (static parsing)
- n8n workflow JSON and source-control stubs (static parsing)
- Conductor OSS workflow JSON (static MCP-core extraction)

See [`docs/manifest-v0.1.md`](manifest-v0.1.md) for the full manifest
schema.

## What's the output format?

- **Markdown** — `agents-shipgate-reports/report.md`, for human review.
- **JSON** — `agents-shipgate-reports/report.json`, machine-readable
  (schema v0.35, current). Always parse this for programmatic use.
  For release gating, read `release_decision.decision`; the legacy
  `summary.status` field is baseline-blind (kept for v0.7 callers). A
  `passed` decision requires complete, conflict-free static surface, effect,
  and authority evidence for every action, evaluation of all applicable
  controls, and no policy condition requiring review. It is not runtime proof;
  see the [evidence-backed verdict contract](passed-verdict-contract.md).
- **SARIF** — `agents-shipgate-reports/report.sarif`, compatible with
  GitHub's code-scanning UI on the Files Changed view.
- **Release Evidence Packet** — `agents-shipgate-reports/packet.{md,json,html}`
  (and `packet.pdf` with the `[pdf]` extras). Reviewer-shaped fixed
  sections. See the next question.

## What is the Release Evidence Packet?

A reviewer-shaped synthesis of the scan, emitted alongside the report by
default. The packet is governed by [`docs/packet-schema.v0.12.json`](packet-schema.v0.12.json)
and has fixed reviewer sections (release decision, evidence matrix, capability/intent,
high-risk surface, tool-surface diff, action-surface diff, approval coverage,
idempotency risk, scope coverage, memory isolation, human-in-the-loop,
dynamic scenarios, and a
`not_proven` section that always lists prompt robustness, runtime
behavior, model correctness, and adversarial resistance verbatim). See
[STABILITY.md §Release Evidence Packet](../STABILITY.md#release-evidence-packet-v012).
Packet schema `0.9` adds semantic coverage and gap remediation, and packet §1
mirrors the report's `static_analysis_only: true`,
`runtime_behavior_verified: false`, and canonical
`static_verdict_disclaimer`. Frozen packet
schema `0.7` added capability-linked local trace evidence summary and trace
refs under the human-in-the-loop section. Frozen schema `0.6` preserved
the v0.5 `action_surface_diff` section and added two additive extensions:
`evidence_matrix`, a compact packet-only review aid derived from public
`report.json` fields (never contributes to `release_decision`, CI exit
behavior, severity, suppression, baseline matching, or `agent_summary`; blocker
and review-item cells are copied from `release_decision`); and
`ReleaseDecisionItem.{source, policy_evidence_source}` so packet §1 / §2 carry
the same reviewer-grade dual-source provenance pointers `Finding.source` /
`Finding.policy_evidence_source` expose in the report.
Skip emission with `--no-packet`; re-render later with
`agents-shipgate evidence-packet --from agents-shipgate-reports/packet.json`.

## Is it production-ready?

v0.15.0 is the latest published pre-1.0 beta. The in-tree runtime is
`0.16.0b7`, which adds content-addressed verification receipts and reproducible
distributed-execution boundaries on top of typed policy evidence, the
multi-host boundary, and root-reachable binding contracts. The manifest schema
remains stable across the 0.x series; see
[`STABILITY.md`](../STABILITY.md). Public preview.

## How do I add it to GitHub Actions?

See [`docs/quickstart.md`](quickstart.md) for the 5-minute integration.
Advisory mode is the default and never fails CI; strict mode with a
baseline lets you adopt the gate without flipping every existing PR red.

## Does it work without GitHub?

Yes. The CLI is the same on any platform. First-class recipes for GitLab CI
and CircleCI are in [`docs/integrations.md`](integrations.md); Jenkins remains
available as a lightweight snippet.

## How much does it cost?

Free and open source under Apache-2.0. No paid tier exists or is
planned for the core scanner.

## Can I write custom checks?

Yes. Plugins are off by default for trust reasons; opt in with
`AGENTS_SHIPGATE_ENABLE_PLUGINS=1`. Authoring guide is in the
[Plugin Authoring](https://github.com/ThreeMoonsLab/agents-shipgate/wiki/Plugin-Authoring)
wiki page.

## What does "release blocker" mean?

A finding with severity `critical` that is not in the baseline and not
suppressed. In strict mode with the default `fail_on: critical`, a
release blocker fails CI and prevents merge. In advisory mode, it's
surfaced in the PR comment but doesn't fail the build.

## Does it certify my agent as safe?

No. agents-shipgate is an advisory Tool-Use Readiness release gate, not a
safety or compliance certification. It produces evidence for human review; it
does not replace human review. See
[`docs/trust-model.md`](trust-model.md).

## Why "agents-shipgate" and not "agent-shipgate" or "agent shipcheck"?

The canonical name is `agents-shipgate` (plural, hyphenated). The
display form is `Agents Shipgate`. `agent shipcheck`, `agent-shipgate`,
and similar variants are not the product name and shouldn't appear
anywhere user-facing.
