# Golden PR: OpenAPI Support Agent

Reference sample: [`samples/support_refund_agent/specs/support-tools.openapi.yaml`](../../../samples/support_refund_agent/specs/support-tools.openapi.yaml).

## Initial Risky Surface

The agent calls support/refund APIs described by OpenAPI. The risky path is a
POST operation that creates a payment refund.

## Commands

```bash
agents-shipgate scan -c samples/support_refund_agent/shipgate.yaml \
  --suggest-patches --format json
```

Then read:

```bash
agents-shipgate-reports/report.json
```

## Release Decision

Expected advisory summary when using the support refund sample:

- Decision: `blocked`
- Blockers: 2
- Review items: 16

## Top Findings

- `SHIP-SCHEMA-MISSING-BOUNDS` when refund amount lacks a maximum.
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` when operation scopes are not covered by the manifest.
- `SHIP-POLICY-APPROVAL-MISSING` when refund creation lacks approval evidence.
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` when retryable writes lack idempotency evidence.

## Safe Patch vs Human-Review Boundary

Schema bounds can often be fixed in the OpenAPI spec by the API owner.

Approval, confirmation, broad-scope, prohibited-action, and idempotency
findings are human release decisions. The agent may explain the finding and
point to the relevant operation, but it must not assert a control exists.

## Recommended Agent PR Summary

```md
## Agents Shipgate

Release decision: `blocked`
Blockers: 2
Review items: 16

OpenAPI focus:
- Add explicit amount bounds to refund parameters.
- Align operation scopes with `permissions.scopes`.
- Provide approval and idempotency evidence before promotion.
```
