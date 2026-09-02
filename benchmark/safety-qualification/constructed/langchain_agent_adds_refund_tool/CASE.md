# `langchain_agent_adds_refund_tool`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `langchain_crewai` |
| Target cell (hypothesis) | `langchain_crewai` × `blocked` |
| Slot | `langchain_crewai.blocked.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A LangChain agent in the `samples/simple_langchain_agent` shape: an `@tool` function and a `StructuredTool`, a literal `create_agent(tools=[...])`, and a reviewed inventory bound to the source. The base scans clean.

## The change

One pull request adds `issue_refund`, an `@tool` with a pydantic schema that calls `stripe.Refund.create` for the charge attached to a case, wires it into the agent's tools list, and does the bookkeeping: an inventory entry with `stripe:refunds:write`, and an `action_surface` row declaring `effect: financial_write` with scoped API-key authority. No approval policy, no idempotency key, no audit safeguard is declared.

## Why the design exhibits `blocked`

The change gives an autonomous agent the ability to move money with no human in the loop and no protection against a retried call refunding twice. Both controls are missing, not merely undeclared; a correct gate stops this change rather than asking a person to look at it.

## What the engine is expected to encounter

The LangChain `literal_registration` route (`langchain_function`, `medium`) lifted to `high` by the bound inventory; the added action is a declared financial write whose obligations (approval, idempotency, audit log) are all absent.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `issue_refund` is in the catalog at `high` with `effect=financial_write` and structural binding; the policy checks raise `SHIP-POLICY-APPROVAL-MISSING`, `SHIP-SIDEFX-IDEMPOTENCY-MISSING` and `SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING` against it. The mechanism is present.
