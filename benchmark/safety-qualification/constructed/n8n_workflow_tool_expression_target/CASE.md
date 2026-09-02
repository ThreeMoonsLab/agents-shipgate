# `n8n_workflow_tool_expression_target`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `insufficient_evidence` |
| Slot | `n8n.insufficient_evidence.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request attaches a Call Workflow Tool, `escalate_to_regional_desk`, whose `workflowId` is an expression on the request body (`={{ $json.body.region.escalation_workflow_id }}`), plus an inventory entry and an `action_surface` row declaring `effect: write` with no credential of its own.

## Why the design exhibits `insufficient_evidence`

The sub-workflow the tool runs is chosen per request from the incoming payload, so the scan cannot resolve which workflow — and which of its nodes, credentials and calls — the agent reaches. A correct gate cannot enumerate the delegated surface and cannot pass it; the honest answer is missing evidence.

## What the engine is expected to encounter

The n8n `dynamic_construction — expression-backed tool name` route: a workflow target that is an n8n expression keeps the node in the catalog at `medium` and records the unresolved target as a dynamic fact (`unresolved_workflow`), raising `SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE`.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` the source warning reads "unresolved_workflow at workflows/support-orders.json#node:escalate-regional has dynamic tool surface: Call Workflow Tool target uses a runtime expression", `dynamic_tool_surface_count` goes 0 → 1 and the not-enumerable finding is raised. The mechanism is present.
