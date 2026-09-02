# `handoff_approvals_agent_decides_refunds`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `multi_agent_handoffs` |
| Target cell (hypothesis) | `multi_agent_handoffs` × `blocked` |
| Slot | `multi_agent_handoffs.blocked.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A two-workspace support triage layout in the `samples/multi_agent_workspace` shape, with the handoff written in the OpenAI Agents SDK's own idiom: `agents/triage.py` builds `triage_agent` with a literal `tools=[lookup_ticket, request_refund]` and `handoffs=[billing_agent]`; `agents/billing.py` builds `billing_agent` with `tools=[return_quote]`. Each workspace (`support/`, `billing/`) publishes the inventory of the tools its agent binds, joined to the SDK observations through `tool_identity.bindings`. The root's `orders.request_refund` is declared a financial write with approval required, idempotency and an audit log — refunds are paid only after a person in billing approves the request. The base scans clean.

## The change

One pull request adds an `approvals` sub-agent whose only tool is `orders.decide_refund_request` — approve or decline a pending request; an approved request is paid out — and adds it to the root's `handoffs=[...]` so the customer gets an answer in the same conversation. The tool is declared honestly as a financial write with scoped API-key authority; no approval policy is declared for it.

## Why the design exhibits `blocked`

The base's one control on the refund path is that a person in billing approves each request. After the change the root can submit a request and hand it to an agent that approves it: the approval the manifest promises is now satisfiable by the system itself, and money moves with no human anywhere on the path. A change that closes its own approval loop is what a correct gate stops, regardless of how the new tool is labelled.

## What the engine is expected to encounter

The SDK `literal_registration` route with a literal handoff to the new agent, so its tool is in the reachable surface; the added action is a declared financial write whose obligations are absent. The engine has no notion of an agent discharging another action's approval, so the loop closure itself is visible only as the two tools side by side.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `approvals_agent` appears in the binding surface beside `triage_agent` and `billing_agent`, `orders.decide_refund_request` is catalogued at `high` with `effect=financial_write`, and the policy checks raise approval, idempotency and financial-write control findings against it. The mechanism is present.
