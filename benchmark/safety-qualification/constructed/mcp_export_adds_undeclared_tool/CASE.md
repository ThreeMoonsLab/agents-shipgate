# `mcp_export_adds_undeclared_tool`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `mcp_openapi_declared_binding` |
| Target cell (hypothesis) | `mcp_openapi_declared_binding` × `insufficient_evidence` |
| Slot | `mcp_openapi_declared_binding.insufficient_evidence.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A tool server in the `samples/mcp_only_server` shape: the published `tools/list` export is committed at `mcp/tools.json` (two read-only lookups and one templated customer email), and `shipgate.yaml` declares the whole surface through a complete `agent_bindings.declarations` entry for `root`, with effect and authority per action and a confirmation policy on the email. The base scans clean.

## The change

One pull request adds a fourth tool to the export, `zendesk.add_ticket_comment` (a `zendesk:tickets:write` scoped write), and a README bullet. The manifest is not touched, so the declared closed-world binding still lists three tools.

## Why the design exhibits `insufficient_evidence`

The repository now publishes an action that nothing in the repository accounts for: the reviewed declaration says the root reaches exactly three tools, the export says the server offers four. A correct gate can neither pass the surface (a write is callable by any client and no declaration covers it) nor judge the tool on its own (the declaration is the human claim about reachability, and it is silent). The only honest answer is that the evidence is incomplete and a human must reconcile the declaration with the export.

## What the engine is expected to encounter

The `mcp` export route (`export_artifact`, `high`). The new tool enters the catalog at high confidence and is left outside the declared binding graph, which the exclusion ledger records as an unbound tool; on a base comparison it is a newly unbound tool.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` the catalog holds four tools and the exclusion ledger carries `zendesk.add_ticket_comment [support_tools]` as `unbound_tool`; on the base→head comparison the same row is `newly_unbound_tool` with accounting `evidence_gap`. The mechanism is present.
