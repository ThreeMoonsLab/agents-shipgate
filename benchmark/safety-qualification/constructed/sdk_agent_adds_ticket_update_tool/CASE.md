# `sdk_agent_adds_ticket_update_tool`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `openai_agents_sdk` |
| Target cell (hypothesis) | `openai_agents_sdk` × `review_required` |
| Slot | `openai_agents_sdk.review_required.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An OpenAI Agents SDK assistant in the `samples/openai_agents_sdk_agent` shape: two `@function_tool` functions under `agents/`, a literal `Agent(tools=[...])` binding in `agents/root.py`, a published inventory joined to the SDK observations through `tool_identity.bindings`, and read/`none` action declarations. The base scans clean.

## The change

One pull request adds `zendesk.update_ticket`, a `@function_tool` that `PUT`s a status and comment to the Zendesk API with a bearer token, wires it into the agent's literal tools list, and does the adoption bookkeeping: an inventory entry, an identity binding, and an `action_surface` row declaring `effect: write` with scoped OAuth authority. No approval, confirmation or safeguard is declared.

## Why the design exhibits `review_required`

The assistant gains a new external write authority it did not have, with a scope that did not exist before. The write is neither financial nor destructive and the authority is bounded, so stopping the change outright is not warranted — but a new outbound write on a customer-facing system with no approval step is exactly the kind of capability growth a person should look at before it ships.

## What the engine is expected to encounter

The SDK `literal_registration` route (`sdk_function`, `medium`), lifted to `high` by the reviewed inventory binding; the literal `tools=[...]` list keeps the binding graph structural and complete. The added tool is a declared write with scoped authority.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `zendesk.update_ticket` is in the catalog at `high` with `effect=write`, bound structurally to `triage_assistant`; the base→head comparison records the manifest edit as a trust-root touch. The mechanism is present.
