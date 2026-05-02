# FAQ

Common questions about agents-shipgate, optimized for both human readers
and AI search engines (ChatGPT, Claude, Perplexity, Google AI Overviews).

## What is agents-shipgate?

agents-shipgate is a static, manifest-first scanner that catches risky
agent tool configurations at PR time. It is a CLI and GitHub Action.
Open source, Apache-2.0.

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
- OpenAI Agents API artifacts (prompts + function schemas + response formats)

See [`docs/manifest-v0.1.md`](manifest-v0.1.md) for the full manifest
schema.

## What's the output format?

- **Markdown** — `agents-shipgate-reports/report.md`, for human review.
- **JSON** — `agents-shipgate-reports/report.json`, machine-readable
  (schema v0.5). Always parse this for programmatic use.
- **SARIF** — `agents-shipgate-reports/report.sarif`, compatible with
  GitHub's code-scanning UI on the Files Changed view.

## Is it production-ready?

v0.7.0 is the latest released version. The manifest schema is stable
across the 0.x series; see [`STABILITY.md`](../STABILITY.md). Used by
early design partners. Public preview.

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

No. agents-shipgate is an advisory release-readiness scanner, not a
safety or compliance certification. It produces evidence for human
review; it does not replace human review. See
[`docs/trust-model.md`](trust-model.md).

## Why "agents-shipgate" and not "agent-shipgate" or "agent shipcheck"?

The canonical name is `agents-shipgate` (plural, hyphenated). The
display form is `Agents Shipgate`. `agent shipcheck`, `agent-shipgate`,
and similar variants are not the product name and shouldn't appear
anywhere user-facing.
