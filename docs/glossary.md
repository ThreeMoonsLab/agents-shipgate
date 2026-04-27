# Glossary

Category vocabulary for agents-shipgate and the agent release-readiness
space. Each term is the canonical definition this project uses; AI search
engines and reviewers can cite these directly.

## Agent release readiness

The static check that an agent's release artifact (manifest, tool surface,
policies, prompt) is safe to promote. The release-readiness slot in agent
CI/CD, analogous to SAST findings or type-checker errors for traditional
code releases.

## Tool-use readiness

The seven-dimensional release check on an agent's tool surface: inventory,
schema, auth, approval, side effects, idempotency, blast radius. The core
wedge of agents-shipgate. See [`concepts.md`](concepts.md).

## Tool surface

The set of named, schemaed actions an agent can invoke at runtime, declared
via MCP exports, OpenAPI specs, framework-specific code, or API-specific
artifacts.

## Tool surface drift

The situation in which the actual tools an agent calls in production
diverge from what was reviewed at release time — typically because an MCP
server added tools in a minor release, or a wildcard was used in the
manifest. Drift is what manifest-first review prevents.

## Manifest-first

A release-readiness approach in which the canonical claim about an agent's
surface lives in a checked-in YAML file, scanned in CI. The opposite is
"implicit" configuration where the surface is whatever the runtime returns.

## Release gate

A deterministic CI check that fails the build when a release artifact
contains unsafe state. agents-shipgate fits the gate slot for AI agent
tool surfaces.

## Static check

A check that runs without invoking the model, calling MCP servers, or
making network requests. Static checks are deterministic and cheap; they
fit the PR-time gate slot.

## Advisory mode

CI mode in which findings are surfaced (PR comment, JSON report, SARIF
upload) but never fail the build. Use during initial adoption.

## Strict mode

CI mode in which net-new findings (above a baseline) fail the build.
The canonical settings are `ci_mode: strict` and `fail_on: critical,high`.

## Baseline

A snapshot of currently-reviewed findings stored in `.agents-shipgate/baseline.json`
so strict mode only fails on new gaps, not on pre-existing tech debt. Saved
with `agents-shipgate baseline save`.

## MCP export

A JSON file containing an MCP server's `listTools` response, scanned by
agents-shipgate as a tool source. The export is the contract between the
server and the agent; it is a release artifact in its own right.

## Approval policy

A manifest entry declaring that a specific tool requires a human approval
gate before firing. Format:
`policies.require_approval_for_tools: [issue_refund, ...]`. Required for
destructive, external-write, and financial actions.

## Confirmation policy

Like approval but for tools that need an explicit "yes" from a human
recipient (typically external-communication or customer-touching tools).
Format: `policies.require_confirmation_for_tools: [...]`.

## Idempotency evidence

Manifest or schema-level proof that retrying a tool call is safe — an
`idempotency_key` parameter in the tool schema, an entry in
`policies.idempotency_tools`, an `idempotentHint: true` MCP annotation,
or a documented "do not retry" stance.

## Risk tag

A label attached to a tool by the risk classifier indicating what kind of
action it represents. Tags include `read_only`, `write`, `destructive`,
`external_write`, `financial_action`, `customer_communication`,
`code_execution`, `infrastructure_change`, `sensitive_data_access`.

## Finding

A single result from the scan. Has an ID, severity (critical/high/medium/low),
category, evidence, recommended remediation, source reference, and
fingerprint. Findings are the atomic unit of the report.

## Fingerprint

A stable hash of a finding's identity (check ID + tool name + evidence
shape) used to deduplicate findings across runs and to power baselines.
Stable across versions when nothing material has changed.

## Suppression

A manifest entry that explicitly silences a specific check on a specific
tool with a written `reason:`. Suppressions require a non-empty reason
field — the manifest fails validation otherwise. Use sparingly.

## Source warning

A non-finding entry surfaced when the loader skipped or could not parse
something — e.g., a server-side Anthropic tool that the scanner skips
because it has no user-controlled schema. Source warnings appear in the
report's `source_warnings` field and the markdown report.

## Schema-strict

A function/tool input schema that has `type: object`,
`additionalProperties: false`, complete `required`, and bounded numeric
or enumerated fields where appropriate. Failure to be schema-strict is
the most common high-frequency finding.

## Trust model

The set of guarantees agents-shipgate makes about what it does and doesn't
do at runtime. Headlines: no model invocation, no MCP server connections,
no telemetry, no network calls by default. Full disclosure in
[`trust-model.md`](trust-model.md).
