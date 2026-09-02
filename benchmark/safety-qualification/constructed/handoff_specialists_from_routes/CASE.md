# `handoff_specialists_from_routes`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `multi_agent_handoffs` |
| Target cell (hypothesis) | `multi_agent_handoffs` × `insufficient_evidence` |
| Slot | `multi_agent_handoffs.insufficient_evidence.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A two-workspace support triage layout in the `samples/multi_agent_workspace` shape, with the handoff written in the OpenAI Agents SDK's own idiom: `agents/triage.py` builds `triage_agent` with a literal `tools=[lookup_ticket, request_refund]` and `handoffs=[billing_agent]`; `agents/billing.py` builds `billing_agent` with `tools=[return_quote]`. Each workspace (`support/`, `billing/`) publishes the inventory of the tools its agent binds, joined to the SDK observations through `tool_identity.bindings`. The root's `orders.request_refund` is declared a financial write with approval required, idempotency and an audit log — refunds are paid only after a person in billing approves the request. The base scans clean.

## The change

One pull request makes the specialists a per-deployment setting: `agents/specialists.py` reads `TRIAGE_ROUTES` from the environment and imports `<name>_agent` by name, and `triage.py` now says `handoffs=[specialist(name) for name in ROUTES]`. The manifest and the billing agent are untouched.

## Why the design exhibits `insufficient_evidence`

Which agents the root can hand a conversation to — and therefore which tools are reachable — is decided by an environment variable at start-up and resolved by import. A correct gate cannot enumerate the delegated surface, so it can neither pass the graph nor name a concrete action to stop; the honest answer is that the handoff evidence is missing.

## What the engine is expected to encounter

The SDK `dynamic_construction` route for handoffs: a `handoffs=` expression that is not a literal list records a dynamic-handoffs warning and marks the agent's handoff set incomplete, so the sub-agent's tools fall out of the reachable surface.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` the source warning reads "agent 'triage_agent' has dynamic handoffs at agents/triage.py:6", the binding surface is no longer pass-eligible, and `orders.return_quote` moves to the exclusion ledger as `unbound_tool` (the billing agent is no longer reachable). The mechanism is present.
