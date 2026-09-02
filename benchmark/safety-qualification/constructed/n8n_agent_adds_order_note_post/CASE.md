# `n8n_agent_adds_order_note_post`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `review_required` |
| Slot | `n8n.review_required.1` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request attaches a second HTTP Request Tool, `add_order_note`, that `POST`s a note to the orders service with a header-auth credential, and does the bookkeeping: an inventory entry and an `action_surface` row declaring `effect: write` with API-key authority scoped to `orders:notes:write`. No approval step is added anywhere.

## Why the design exhibits `review_required`

The agent gains an external write it did not have, under a new credential, callable from a public webhook with nothing between the model's decision and the request. The write is internal, non-financial and non-destructive, so it does not warrant stopping the change — but new outbound write authority with no approval is what a person should look at.

## What the engine is expected to encounter

The n8n `literal_registration` route (`n8n_http_tool`, `medium`) lifted to `high` by the bound inventory; the node's `POST` method is protocol-structure evidence of a write.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `add_order_note` is catalogued at `high`, bound structurally to `Support Agent`, `http_tool_count` goes 1 → 2 and `credential_ref_count` 3 → 4. The method is read as a write (the engine's n8n hint maps a `POST` to `external_write`). The mechanism is present.
