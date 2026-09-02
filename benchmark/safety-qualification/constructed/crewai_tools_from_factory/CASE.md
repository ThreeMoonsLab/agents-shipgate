# `crewai_tools_from_factory`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `langchain_crewai` |
| Target cell (hypothesis) | `langchain_crewai` × `insufficient_evidence` |
| Slot | `langchain_crewai.insufficient_evidence.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

A CrewAI crew in the `samples/simple_crewai_agent` shape without the prebuilt file tool: one `@tool` function and one `BaseTool` subclass in `crew.py`, a literal `Agent(tools=[...])`, and a reviewed inventory bound to the source through `crewai.tool_inventories[].source_id`. The base scans clean.

## The change

One pull request lets each deployment pick its tool set: `crew.py` gains a `TOOL_FACTORIES` table and `build_tools(profile)`, which reads `tool_profiles.yaml` and instantiates the named factories, and the agent is built with `tools=build_tools("support_reader")`. The tool definitions and the inventory are unchanged.

## Why the design exhibits `insufficient_evidence`

The crew's tool list is now the result of a call over a config file, so no statically enumerable surface exists: the two tools still exist in the file but nothing static binds them to the agent, and the same factory could return any subset — or, after a one-line YAML change, something else. A correct gate cannot prove what the crew wires and cannot pass an inventory that describes definitions rather than bindings; the honest answer is missing evidence.

## What the engine is expected to encounter

The CrewAI `dynamic_construction` route: a tools list that comes from a runtime call records a dynamic tool surface and adds nothing to the binding graph; the reviewed inventory keeps the tools in the catalog but cannot bind them.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` the source warning reads "CrewAI agent at crew.py:42 has dynamic tool surface: tool list comes from a runtime call", `dynamic_tool_surface_count` goes 0 → 1, and both tools land in the exclusion ledger as `unbound_tool` (the inventory suppresses the check finding but not the binding gap). The mechanism is present.
