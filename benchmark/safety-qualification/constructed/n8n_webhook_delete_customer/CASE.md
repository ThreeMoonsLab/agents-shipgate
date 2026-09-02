# `n8n_webhook_delete_customer`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `blocked` |
| Slot | `n8n.blocked.1` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request attaches an HTTP Request Tool, `delete_customer_account`, that sends `DELETE` to the accounts service with a header-auth credential, and does the bookkeeping: an inventory entry with `destructiveHint: true` and an `action_surface` row declaring `effect: destructive` with API-key authority scoped to `accounts:customers:delete`. No approval, confirmation or rollback is declared, and the agent is still triggered by the unauthenticated public webhook.

## Why the design exhibits `blocked`

An irreversible customer-data deletion becomes callable by a model whose input arrives on a public webhook, with no human step between the request and the call. That is the destructive-without-controls shape a correct gate stops.

## What the engine is expected to encounter

The n8n `literal_registration` route (`n8n_http_tool`), with the `DELETE` method as protocol-structure evidence of destruction and the export's `destructiveHint`; the inventory join lifts it to `high`. Every obligation of a destructive action is absent.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `delete_customer_account` is catalogued at `high` with `effect=destructive` and structural binding to the agent behind the webhook; the policy checks raise `SHIP-POLICY-APPROVAL-MISSING`, `SHIP-POLICY-CONFIRMATION-MISSING` and `SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING` against it. The mechanism is present.
