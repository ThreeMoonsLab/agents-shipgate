# Tool-Use Readiness Release Gate

A **Tool-Use Readiness release gate** is a local-first, static pre-flight
check that runs before an agent is promoted to staging, production-like, or
production environments.

Agents Shipgate is a local-first, static Tool-Use Readiness release gate for
AI agent tool surfaces. It focuses on what tools are attached, what schemas
they expose, what scopes they require, what policies the manifest declares,
and which release risks need human review.

## What It Is

- A CI-friendly scanner for `shipgate.yaml`, local MCP exports, local OpenAPI specs, and optional SDK AST metadata.
- A deterministic report of blockers, warnings, evidence, confidence, and recommended actions.
- A way to make agent release review repeatable before runtime traces exist.

## What It Is Not

- An LLM eval framework.
- An MCP runtime gateway.
- A runtime guardrail or policy enforcement proxy.
- An observability platform.
- A safety or compliance certification.

## Adjacent Categories

| Category | Difference |
| --- | --- |
| LLM evals | Evaluate behavior or outputs; Agents Shipgate reviews static tool-surface release risk. |
| Secret scanners | Detect leaked credentials; Agents Shipgate checks tool permissions, schemas, and policies. |
| Agent observability | Uses runtime traces after execution; Agents Shipgate runs before promotion. |
| MCP gateways | Control runtime access; Agents Shipgate produces release-review evidence. |
