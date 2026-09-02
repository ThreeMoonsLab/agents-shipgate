# `handoff_billing_agent_account_note`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `multi_agent_handoffs` |
| Target cell (hypothesis) | `multi_agent_handoffs` × `review_required` |
| Slot | `multi_agent_handoffs.review_required.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A two-workspace support triage layout in the `samples/multi_agent_workspace` shape, with the handoff written in the OpenAI Agents SDK's own idiom: `agents/triage.py` builds `triage_agent` with a literal `tools=[lookup_ticket, request_refund]` and `handoffs=[billing_agent]`; `agents/billing.py` builds `billing_agent` with `tools=[return_quote]`. Each workspace (`support/`, `billing/`) publishes the inventory of the tools its agent binds, joined to the SDK observations through `tool_identity.bindings`. The root's `orders.request_refund` is declared a financial write with approval required, idempotency and an audit log — refunds are paid only after a person in billing approves the request. The base scans clean.

## The change

One pull request gives the `billing` sub-agent a write tool the root does not have: `accounts.add_note` (`POST` to the accounts service) joins `billing_agent`'s tools list, the billing workspace inventory, the identity bindings and the `action_surface` as a declared `write` with API-key authority scoped to `accounts:notes:write`. The root agent's own wiring is untouched.

## Why the design exhibits `review_required`

New write authority appears one handoff away from the root: anything the root delegates to billing can now leave a note on a customer account. The write is bounded and non-financial, so it does not warrant stopping the change, but authority added on a delegated path that the root's own tool list does not show is what a person should look at.

## What the engine is expected to encounter

The SDK `literal_registration` route with literal `handoffs=[...]`, so the handoff edge is structural and the sub-agent's tools are in the reachable surface; the added action is a declared, scoped write bound to the sub-agent.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `accounts.add_note` is catalogued at `high` with `effect=write`, bound structurally through the handoff (the binding surface lists `triage_agent` and `billing_agent`); the base→head comparison records the manifest trust-root touch. The mechanism is present.
