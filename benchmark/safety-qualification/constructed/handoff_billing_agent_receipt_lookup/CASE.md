# `handoff_billing_agent_receipt_lookup`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `multi_agent_handoffs` |
| Target cell (hypothesis) | `multi_agent_handoffs` × `passed` |
| Slot | reserve (not allocated to a slot) |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A two-workspace support triage layout in the `samples/multi_agent_workspace` shape, with the handoff written in the OpenAI Agents SDK's own idiom: `agents/triage.py` builds `triage_agent` with a literal `tools=[lookup_ticket, request_refund]` and `handoffs=[billing_agent]`; `agents/billing.py` builds `billing_agent` with `tools=[return_quote]`. Each workspace (`support/`, `billing/`) publishes the inventory of the tools its agent binds, joined to the SDK observations through `tool_identity.bindings`. The root's `orders.request_refund` is declared a financial write with approval required, idempotency and an audit log — refunds are paid only after a person in billing approves the request. The base scans clean.

## The change

One pull request gives the `billing` sub-agent one more read: `orders.lookup_receipt` (`GET`) joins its tools list, the billing inventory with `readOnlyHint`, the identity bindings and the `action_surface` as a declared read with API-key authority scoped to `orders:receipts:read`.

## Why the design exhibits `passed`

A read-only, narrowly scoped lookup is added on a delegated path that is fully enumerable and declared as what it is; nothing new can be changed or spent. A correct gate lets this through.

## What the engine is expected to encounter

The SDK `literal_registration` route with a literal handoff; the added action is a declared, scoped read bound to the sub-agent.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `orders.lookup_receipt` is catalogued at `high` with `effect=read` and structural binding through the handoff, with no findings; the base→head comparison records only the manifest trust-root touch. The mechanism is present.
