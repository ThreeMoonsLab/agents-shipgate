# `n8n_code_tool_runtime_endpoint`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `insufficient_evidence` |
| Slot | `n8n.insufficient_evidence.1` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request attaches a Code Tool, `notify_carrier`, whose JavaScript takes the carrier callback URL from the model (`$fromAI('carrier_callback_url', ...)`) and `POST`s to it with `this.helpers.httpRequest`. The author adds the inventory entry (name, description, parameters) but no `action_surface` row.

## Why the design exhibits `insufficient_evidence`

Where the request goes is decided at run time by the model's argument, so the set of systems this tool can reach is not enumerable from the file: it is every URL. A correct gate cannot say what the surface is, so it cannot pass it; and there is no single concrete action to stop. The honest answer is that the reachable surface is not statically known.

## What the engine is expected to encounter

The n8n `literal_registration` route for a code node (`n8n_code_tool`, `medium`, with the engine's typed `code_execution` fact), lifted to `high` by the inventory join; the request target is an expression the reader cannot resolve, and no reviewed effect or authority is declared for the action.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `notify_carrier` is catalogued with `effect=code_execution` (typed provider fact) and `code_tool_count` goes 0 → 1; the run reports the action's effect as inferred rather than declared. The runtime-built request target is visible only as the `$fromAI` expression in the node's code — the engine records no dynamic fact for it, so the mechanism is present in the tree but not named by the scan.
