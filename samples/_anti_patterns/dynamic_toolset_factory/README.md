# Anti-pattern · dynamic toolset factory

This fixture demonstrates the failure mode named in [`agent-recipes.md` Recipe 2](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-recipes.md#recipe-2--add-shipgate-to-a-repo-that-already-has-tool-surfaces): tools created by a runtime factory wrapper are invisible to static AST extraction.

Unlike its siblings (`missing_purpose/`, `path_traversal/`, `empty_suppression_reason/`, `misplaced_field/`), this manifest is **valid** — the scan completes successfully — but it produces a high-severity inventory finding rather than a config error. The scan completes with `release_decision.decision: "review_required"`.

## Expected behavior

```bash
$ agents-shipgate scan -c shipgate.yaml --ci-mode advisory
Decision: review_required
Reason: 1 finding requires human review before shipping.
Counts: critical=0, high=1, medium=0, low=0, suppressed=0

Top findings:
- SHIP-INVENTORY-NOT-ENUMERABLE - Tool surface cannot be enumerated
```

Exit code: `0` (advisory mode; no blockers).

## Why this is an anti-pattern

The agent has three real tools at runtime — `search_orders`, `lookup_user`, `issue_refund` (the last is the kind of high-risk write tool that should normally trigger a `SHIP-POLICY-APPROVAL-MISSING` finding). But the static extractor walks the AST without executing the module, so it never sees:

- `make_tool(...)` wrapping each function
- The `TOOLS` list assembled at module import time
- `Agent(name=..., tools=TOOLS)` with the runtime-bound tools

It sees only the function definitions and the `Agent` constructor call. With no `@function_tool` decorator and no static `tools=[func1, func2]` literal it can resolve, the inventory comes back empty — and `SHIP-INVENTORY-NOT-ENUMERABLE` fires high to fail safe.

This is the most common cause of "Shipgate didn't find my tools" in real adoption. It's a fail-closed by design: a release gate that silently misses a high-risk tool is worse than one that loudly says "I see nothing."

## Recovery (the fix)

[`docs/agent-recipes.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-recipes.md) names three recovery paths. Pick whichever matches the runtime architecture:

1. **Provide an MCP export** — if the agent already speaks MCP at runtime, dump the resolved tool list to JSON and declare it as a tool source:
   ```yaml
   tool_sources:
     - id: agent
       type: openai_agents_sdk
       path: factory.py
     - id: mcp_inventory
       type: mcp
       path: .agents-shipgate/mcp-export.json
   ```

2. **Provide an OpenAPI spec** — if the tools wrap an HTTP API:
   ```yaml
   tool_sources:
     - id: api
       type: openapi
       path: openapi.yaml
   ```

3. **Provide a local tool inventory** — frameworks that support `tool_inventories[]` (LangChain, CrewAI, Google ADK) accept a static JSON that lists the resolved tools:
   ```yaml
   langchain:
     tool_inventories:
       - inventories/langchain-tools.json
   ```

Whichever you choose, the inventory file lists the tools by `name` with their `description`, `parameters`, and `risk_tags` so Shipgate can run its full check suite on the same surface the agent exposes at runtime.

The agent-action-guide names the same recovery in its [`inventory` section](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-action-guide.md#inventory--narrow-the-surface).

## What you'll see

- `agents-shipgate doctor -c shipgate.yaml` reports `Total tools: 0` and surfaces `SHIP-DIAG-ZERO-TOOLS`. With `--json`, the payload includes `unresolved_sources: []` and `total_tools: 0`; the diagnostic's nested `next_actions[]` includes an edit recommendation to add an explicit MCP export, OpenAPI spec, or local tool inventory.
- `agents-shipgate detect --json --workspace .` returns `is_agent_project: false` and `frameworks: []`. The fixture defines a local `class Agent` rather than importing `from agents import Agent`, so the AST walker sees no SDK import to detect. The `Agent(name="dynamic-toolset-agent")` call is still surfaced as an entry in `agent_name_candidates` (source: `Agent_name_literal`), but that's a weaker signal that doesn't flip `is_agent_project`. This is a deliberate detail of the fixture: anti-pattern samples shouldn't pretend to be real agent projects — they isolate one failure mode at a time, and the failure mode here is *inventory enumeration*, not *project detection*.
- `report.json.findings[]` contains exactly one high-severity entry: `SHIP-INVENTORY-NOT-ENUMERABLE` with `tool_name: null`.
- The Release Evidence Packet's §1 verdict is `REVIEW REQUIRED`; §2 shows `(no tools observed)`, and §9 lists the `SHIP-INVENTORY-NOT-ENUMERABLE` manual review item. §10 remains the standard "what this packet did NOT prove" section rather than a duplicate inventory summary.
