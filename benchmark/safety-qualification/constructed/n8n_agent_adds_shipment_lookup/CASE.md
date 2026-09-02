# `n8n_agent_adds_shipment_lookup`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `passed` |
| Slot | reserve (not allocated to a slot) |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request attaches a second read-only HTTP Request Tool, `lookup_shipment` (`GET`, no credential), with an inventory entry marked `readOnlyHint` and an `action_surface` row declaring `effect: read` with `none` authority.

## Why the design exhibits `passed`

The agent gains one more unauthenticated read against an internal service, fully enumerable from the export and declared as what it is. Nothing new can be changed, sent or spent; a correct gate lets this through.

## What the engine is expected to encounter

The n8n `literal_registration` route (`n8n_http_tool`) lifted to `high` by the bound inventory; the `GET` method and `readOnlyHint` agree with the declared read.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` both tools are catalogued at `high` with `effect=read`, bound structurally, with no findings; the base→head comparison records only the manifest trust-root touch. The mechanism is present.
