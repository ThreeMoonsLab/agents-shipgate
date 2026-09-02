# `n8n_agent_adds_crm_mcp_client`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `review_required` |
| Slot | reserve (not allocated to a slot) |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request attaches an MCP Client Tool node, `crm_tools`, against a CRM MCP endpoint with a bearer credential and an explicit allowlist of one tool, `crm.update_contact`; the inventory gains that tool (a `crm:contacts:write` scoped write) and the manifest an `action_surface` row declaring `effect: write` with scoped OAuth authority. No approval step is added.

## Why the design exhibits `review_required`

The agent gains a bounded external write through a remote MCP server: one named tool, one scope, enumerable from the node's allowlist. New write authority with no approval is something a person should see, but it is neither financial nor destructive, so stopping it is not warranted.

## What the engine is expected to encounter

The n8n `literal_registration` route for an MCP client node with `selected` mode (`n8n_mcp_client_tool`, `medium`), lifted to `high` by the inventory join; no wildcard selection, so no dynamic fact is recorded.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `mcp_client_tool_count` goes 0 → 1, `crm.update_contact` is catalogued at `high` with `effect=write` and structural binding, and no wildcard or dynamic-surface finding is raised. The mechanism is present.
