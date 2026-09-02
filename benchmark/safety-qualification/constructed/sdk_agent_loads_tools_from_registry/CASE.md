# `sdk_agent_loads_tools_from_registry`

This file is the design record for a Cut B construction (issue #456). It names a target decision, so it must never be included in a rater packet; the packet is built from `base/` and `head/` only. The target is a hypothesis taken from the design, not from any verifier output.

| | |
|---|---|
| Profile | `openai_agents_sdk` |
| Target cell (hypothesis) | `openai_agents_sdk` × `insufficient_evidence` |
| Slot | `openai_agents_sdk.insufficient_evidence.2` |
| Origin | `synthetic`, built for the corpus; no engine test or shipped sample names it |

## The base

The same assistant as `sdk_agent_adds_ticket_update_tool`'s base: two `@function_tool` functions, a literal `Agent(tools=[...])` binding, a published inventory joined through identity bindings. The base scans clean.

## The change

One pull request moves the tool list into a profile registry so operations can change what the assistant may call without a deploy: `agents/root.py` now reads `tools=load_tools("triage")`, `agents/tool_registry.py` resolves dotted names from `agents/tools.toml` by import at runtime. The manifest is not touched; the two functions still exist and the inventory still describes them.

## Why the design exhibits `insufficient_evidence`

After the change nothing static says which tools the assistant binds: the list is whatever the TOML names at start-up, and the registry imports by name. The inventory describes tools that exist, not tools that are wired. A correct gate cannot enumerate the bound surface and therefore cannot pass it, and has nothing concrete to block; the honest answer is that the binding evidence is missing.

## What the engine is expected to encounter

The SDK `dynamic_construction` route: `tools=<expression>` that is not a literal list records a binding warning and marks the agent's tool set incomplete, so every decorated function becomes an unbound catalog entry.

## Observed when the engine was run on the trees

Both trees parse and scan; nothing engine-produced is committed inside either tree. On `head/` the source warning reads "agent 'triage_assistant' at agents/root.py:4 uses a dynamic tools expression; its binding graph is incomplete"; both functions move to the exclusion ledger as `unbound_tool` and the binding surface is not pass-eligible. The mechanism is present.
