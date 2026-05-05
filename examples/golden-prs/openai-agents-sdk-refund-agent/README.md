# Golden PR: OpenAI Agents SDK Refund Agent

Reference sample: [`samples/support_refund_agent`](../../../samples/support_refund_agent/).

## Initial Risky Surface

The agent includes an SDK entrypoint and release tool sources for support
refund work. The risky release path includes `stripe.create_refund`, which can
create an external financial side effect.

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

Expected advisory summary:

- Decision: `blocked`
- Blockers: 2
- Review items: 16
- Fail policy: advisory mode does not fail CI

## Blockers

- `SHIP-POLICY-APPROVAL-MISSING` on `stripe.create_refund`
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` on `stripe.create_refund`

## Top Review Items

- `SHIP-AUTH-MANIFEST-BROAD-SCOPE` on manifest scopes

## Safe Patch vs Human-Review Boundary

Safe patches may clean stale manifest entries when the report marks them
high-confidence and non-manual.

Do not auto-add approval, confirmation, or idempotency evidence. A human owner
must decide whether the runtime approval gate exists, how it is enforced, and
which evidence belongs in `shipgate.yaml`.

## Recommended Agent PR Summary

```md
## Agents Shipgate

Release decision: `blocked`
Reason: 2 active findings block release.

Blockers: 2
Review items: 16

Top findings:
1. `SHIP-POLICY-APPROVAL-MISSING` - `stripe.create_refund` needs approval policy evidence.
2. `SHIP-SIDEFX-IDEMPOTENCY-MISSING` - `stripe.create_refund` needs idempotency evidence.
3. `SHIP-AUTH-MANIFEST-BROAD-SCOPE` - review manifest scopes before promotion.

Autofix:
- Applied: 0
- Human review required: approval and idempotency controls.
```
