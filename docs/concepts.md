# Concepts

The mental model behind agents-shipgate, in one page.

For the product-level definition of an "agent release gate," see
[`category.md`](category.md). For the agent-facing
walkthrough, see [`AGENTS.md`](../AGENTS.md).

## Tool-use readiness

**Tool-use readiness** is the static check that an agent's tool surface
is ready for promotion. It is *not* "did the tool call succeed" (a
runtime concern) or "did the model pick the right tool" (an eval
concern). It is the question a release reviewer answers at PR time:

> Given the tool surface declared in this PR, do we have explicit
> approval policies, scope coverage, idempotency evidence, and review
> readiness for every action — *before* promotion?

Tool-use readiness has seven dimensions. agents-shipgate produces
findings against each one.

| Dimension | What it asks | Evidence in the manifest |
|---|---|---|
| **Inventory** | What tools can the agent call? | A complete, named list — no wildcards, no "whatever this MCP server returns" |
| **Schema** | What inputs does each tool accept? | Strict JSON schema — `additionalProperties: false`, complete `required`, bounded numeric fields |
| **Auth** | What scopes does each tool need? | Declared per-tool or in `permissions.scopes` — narrower than the service account's actual scopes |
| **Approval** | Who reviews destructive actions before they fire? | `policies.require_approval_for_tools: [...]` for every write/destructive/financial action |
| **Side effects** | What does this tool change in the world? | Risk tags on the tool: `write`, `destructive`, `external_write`, `financial_action`, `customer_communication` |
| **Idempotency** | Can it be retried safely? | Idempotency key in the schema, documented retry policy, or explicit "do not retry" |
| **Blast radius** | If this tool fires unexpectedly, how bad is it? | Owner declared, prohibited actions enumerated, scope of resources bounded |

## Tool surface

The **tool surface** is the set of named, schemaed actions an agent can
invoke at runtime. It is declared via:

- Model Context Protocol (MCP) exports
- OpenAPI specs
- Framework-specific code (OpenAI Agents SDK Python, Google ADK, LangChain/LangGraph, CrewAI)
- API-specific artifacts (Anthropic Messages API tools.json, OpenAI
  Agents API function schemas)

The tool surface is a **release artifact** in the same sense as a
service deployment's binary or an API contract: it's a checked-in,
diff-able statement of what the agent can do, and it should be reviewed
on every PR.

## Manifest-first

agents-shipgate is **manifest-first**: the canonical claim about an
agent's surface lives in a single `shipgate.yaml` checked into the
repo. Every tool source the manifest references is reviewed at scan
time. There is one place to look for "what does this agent ship with."

This is intentional. Implicit configurations (e.g. "use whatever the
MCP registry returns") fail the inventory dimension above. The manifest
is what makes the release gate reviewable.

## Static vs dynamic

agents-shipgate is **static**. It does not run the agent, invoke the
model, call MCP servers, or make any network calls by default. Every
finding is derived from the artifact diff alone.

Static analysis covers the release-readiness slice. Dynamic concerns —
behavior under unusual inputs, runtime tool routing, latency,
hallucination — belong in evals, observability, and runtime guardrails.
agents-shipgate is additive to those, not a replacement.

## Where this fits in the wider stack

| Guard | When it runs | What it catches |
|---|---|---|
| Tests | CI on every PR | Code paths in the agent's *code* |
| Evals | On a schedule or per release | Model behavior on curated inputs |
| **agents-shipgate** | CI on every PR | Tool surface, scopes, policies, prompt/surface alignment |
| Runtime guardrails / gateway | At call time | Per-call policy enforcement |
| Observability | Runtime | What actually happened in production |

Each catches something the others can't. Removing any of them is a
regression.

## Related reading

- [`category.md`](category.md) — the product-level "what is an agent release gate"
- [`checks.md`](checks.md) — every check the scanner runs
- [`manifest-v0.1.md`](manifest-v0.1.md) — full manifest schema
- [`trust-model.md`](trust-model.md) — local-only guarantees and disclosure process
- [`glossary.md`](glossary.md) — category vocabulary
