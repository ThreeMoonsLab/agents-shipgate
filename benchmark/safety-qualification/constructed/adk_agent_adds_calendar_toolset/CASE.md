# `adk_agent_adds_calendar_toolset`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `google_adk` |
| Target cell (hypothesis) | `google_adk` × `review_required` |
| Slot | `google_adk.review_required.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A Google ADK agent in the `samples/google_adk_agent` shape without the long-running tool: one `FunctionTool`, one `McpToolset` resolved against a committed export, a reviewed function inventory joined by `source_id`, and read/`none` action rows. The base scans clean.

## The change

One pull request adds a second `McpToolset` — the `adk-samples#1975` shape — against a calendar MCP endpoint, filtered to `calendar.create_event`, with the endpoint's export committed at `inventories/calendar-mcp.json`; the toolset joins the root agent's tools list and the manifest gains an `action_surface` row declaring `effect: write` with delegated OAuth authority scoped to `calendar:events:write`. No approval step is declared.

## Why the design exhibits `review_required`

The agent gains a domain-scoped external write: it can now put events on a team calendar and invite customers, under one narrow scope. That is new authority a person should see before it ships, but it is bounded, reversible and non-financial, so stopping the change is not warranted.

## What the engine is expected to encounter

The ADK `export_artifact — resolved toolset` route: an `McpToolset(...)` whose arguments name a committed export is read by the MCP input at `high`, and the module stays fully resolved so nothing is lowered. The added action is a declared, scoped write.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` `calendar.create_event` enters the catalog through the resolved toolset at `high` with `effect=write`, `toolset_count` goes 1 → 2 with `dynamic_toolset_count` 0, and the base→head comparison records a capability change on the MCP surface plus the manifest trust-root touch. The mechanism is present.
