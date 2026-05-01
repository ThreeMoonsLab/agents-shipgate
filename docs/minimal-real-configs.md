# Minimal real configs by framework

Each row points at a runnable `samples/*` fixture rather than inlining a
manifest snippet. The fixture is the source of truth — reading it, you
can copy whichever sections apply to your repo. This avoids the "doc
example fell out of sync with the schema" hazard.

For the full manifest field reference, see
[`manifest-v0.1.md`](manifest-v0.1.md).

| Framework | Reference fixture | Manifest highlight |
|---|---|---|
| OpenAI Agents SDK (`@function_tool` Python) | [`samples/support_refund_agent`](../samples/support_refund_agent/) — `agents/refund_agent.py` is the Python entrypoint | `tool_sources[*].type: openai_agents_sdk` with `path: agents/refund_agent.py` |
| MCP export (single agent) | [`samples/support_refund_agent`](../samples/support_refund_agent/) — `.agents-shipgate/mcp-tools.json` | `tool_sources[*].type: mcp` |
| MCP export (multi-agent monorepo) | [`samples/multi_agent_workspace`](../samples/multi_agent_workspace/) — `support/` and `billing/` each have their own `tools.json` | one `shipgate.yaml` per agent, each with its own `tool_sources` |
| OpenAPI tool surface | [`samples/support_refund_agent`](../samples/support_refund_agent/) — `specs/support-tools.openapi.yaml` is a real OpenAPI 3.x spec referenced from the fixture's `shipgate.yaml` | `tool_sources[*].type: openapi` with `path: specs/support-tools.openapi.yaml` |
| OpenAI API artifacts (Messages API) | [`samples/simple_openai_api_agent`](../samples/simple_openai_api_agent/) | `manifest.openai_api` block (prompts, tools, schemas, traces, policies) |
| Anthropic Messages API | [`samples/simple_anthropic_agent`](../samples/simple_anthropic_agent/) | `manifest.anthropic` block (prompts, tools, policy_rules) |
| Google ADK | [`samples/google_adk_agent`](../samples/google_adk_agent/) | `tool_sources[*].type: google_adk` plus `manifest.google_adk` config |
| LangChain / LangGraph | [`samples/simple_langchain_agent`](../samples/simple_langchain_agent/) | `tool_sources[*].type: langchain` |
| CrewAI | [`samples/simple_crewai_agent`](../samples/simple_crewai_agent/) | `tool_sources[*].type: crewai` |

## How to use this page

1. Find your framework in the table.
2. Open the fixture's `shipgate.yaml`. That's a working manifest
   `agents-shipgate scan` accepts as-is.
3. Copy the `tool_sources` entry (or `manifest.<framework>` block) and
   point its `path` at your file(s).
4. Run [`agents-shipgate init --workspace . --write`](../AGENTS.md#install-canonical)
   for everything else (project name, agent name, environment defaults).

## What the fixtures verify

Every sample is exercised by the test suite — running
`pytest tests/` against the repo scans these fixtures end-to-end.
That means:

- Every manifest in this table is schema-valid against
  [`manifest-v0.1.json`](manifest-v0.1.json).
- Every tool source path exists and parses.
- Every framework adapter loads the declared sources without error.

If your manifest looks like one of these and `scan` rejects it, file
an issue — the docs and the schema have drifted.

## When to add a new minimal config

If you bring up Shipgate against a framework not listed here, contribute
a fixture. The
[`framework-adapter-checklist.md`](framework-adapter-checklist.md) is
the full new-framework playbook; the minimum to land in this table is:

- A `samples/<framework_name>_minimal/` directory with a working
  `shipgate.yaml`.
- The fixture passes `agents-shipgate scan -c shipgate.yaml
  --ci-mode advisory` in the test suite.
- A row added to this table pointing at it.

---

## See also

- [`agent-recipes.md`](agent-recipes.md) — what to do once your
  manifest is in place
- [`quickstart.md`](quickstart.md) — 60-second introduction
- [`AGENTS.md`](../AGENTS.md) — full agent-facing instructions
