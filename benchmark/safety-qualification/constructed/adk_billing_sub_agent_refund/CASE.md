# `adk_billing_sub_agent_refund`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `google_adk` |
| Target cell (hypothesis) | `google_adk` × `blocked` |
| Slot | `google_adk.blocked.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

The same ADK agent as `adk_agent_adds_calendar_toolset`'s base: one `FunctionTool`, one resolved `McpToolset`, a bound function inventory. The base scans clean.

## The change

One pull request adds a `billing_agent` `LlmAgent` — the `adk-samples#1745` shape — whose only tool is `issue_refund`, a function calling `stripe.Refund.create`, and lists it under the root agent's `sub_agents=[...]`. The bookkeeping follows: an inventory entry and an `action_surface` row declaring `effect: financial_write` with scoped API-key authority. No approval policy or idempotency safeguard is declared.

## Why the design exhibits `blocked`

A financial write becomes reachable from the root agent through a handoff, with no human approval anywhere on the path and no protection against duplicate refunds. That the money moves one hop away from the root does not change what the change does; a correct gate stops it.

## What the engine is expected to encounter

The ADK `literal_registration — Python module` route at `high` (every tool expression, keyword and sub-agent resolves), with the sub-agent recorded as a real handoff target so its tool is part of the reachable surface. The added action is a declared financial write with every obligation absent.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `sub_agent_count` goes 0 → 1, `billing_agent` appears beside `support_agent` in the binding surface, and `issue_refund` is catalogued at `high` with `effect=financial_write`; the policy checks raise approval, idempotency and financial-write control findings against it. The mechanism is present.
