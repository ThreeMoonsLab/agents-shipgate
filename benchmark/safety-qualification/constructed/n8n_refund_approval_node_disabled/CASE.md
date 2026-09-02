# `n8n_refund_approval_node_disabled`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `n8n` |
| Target cell (hypothesis) | `n8n` × `blocked` |
| Slot | `n8n.blocked.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

An n8n workflow in the `samples/n8n_workflow_agent` shape, extended so the agent node has a tool surface: a public `Order Request` webhook feeds a `Support Agent` node whose one attached tool is `lookup_order` (an HTTP Request Tool, `GET`, no credential); the agent's output goes through a `Refund Approval` send-and-wait node before an `Issue Refund` Stripe call in the main chain. `shipgate.yaml` declares the workflow under the top-level `n8n:` section with a reviewed inventory bound to the workflow source, and a read/`none` action row. The base scans clean.

## The change

One pull request sets `"disabled": true` on the `Refund Approval` send-and-wait node and changes nothing else. In n8n a disabled node passes its input straight through, so the agent's refund summary now reaches the `Issue Refund` Stripe call without anyone approving it.

## Why the design exhibits `blocked`

The workflow's own gating step is switched off: the one human decision between the model and a financial write is removed while every tool and credential stays in place. A change whose whole effect is to remove the human from a money-moving path is what a correct gate stops.

## What the engine is expected to encounter

The n8n workflow reader drops disabled nodes and every edge touching them, so the send-and-wait node vanishes from the human-review node set while the tool surface is byte-identical; the main-chain Stripe call is not an agent tool and is not catalogued.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. Between `base/` and `head/` `human_review_node_count` goes 1 → 0 and `credential_ref_count` 3 → 2 (the Slack credential on the disabled node), with the catalog unchanged. The mechanism is present: the gate's removal is visible in the workflow facts, and only there.
