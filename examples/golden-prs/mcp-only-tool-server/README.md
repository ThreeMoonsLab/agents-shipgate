# Golden PR: MCP-Only Tool Server

Reference sample: [`samples/support_refund_agent/.agents-shipgate/mcp-tools.json`](../../../samples/support_refund_agent/.agents-shipgate/mcp-tools.json).

## Initial Risky Surface

The repo exposes an MCP tool inventory without Python framework code. In this
shape, `detect --json` may report `is_agent_project: false`, but
`suggested_sources` should still be treated as a valid Shipgate onboarding
signal.

## Commands

```bash
agents-shipgate detect --workspace . --json
agents-shipgate init --workspace . --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
```

Then read:

```bash
agents-shipgate-reports/report.json
```

## Release Decision

Use `release_decision.decision` from JSON. Do not scrape Markdown or infer
release status from severity counts alone.

## Top Findings To Expect

Depending on the inventory, likely findings include:

- missing tool descriptions
- missing auth scopes
- missing approval or confirmation policy for side-effecting tools
- missing idempotency evidence for retryable writes

## Safe Patch vs Human-Review Boundary

Safe patches are limited to high-confidence mechanical manifest cleanup.

Do not auto-invent MCP scopes, approval policies, or idempotency guarantees.
Those are release decisions that must come from the tool owner.

## Recommended Agent PR Summary

```md
## Agents Shipgate

Release decision: `<blocked|review_required|passed>`
Blockers: <n>
Review items: <n>

MCP-only note:
- This repo can be a valid Shipgate target even without Python framework detection.
- Review `suggested_sources` from `detect --json` and `tool_sources` in `shipgate.yaml`.

Human review:
- Confirm auth scopes and control policies for side-effecting MCP tools.
```
