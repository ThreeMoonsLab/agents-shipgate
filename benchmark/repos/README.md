# Benchmark Archetype Repos

Eight target-repo archetypes covering the framework / shape combinations the harness needs to evaluate. Each archetype is either:

- **Vendored** — a `git clone` snapshot pinned to a specific commit, committed into this directory, OR
- **Submoduled** — a git submodule pinned to a specific commit.

Vendoring keeps the benchmark reproducible without requiring submodule fetches; submoduling keeps the size of this repo down. We use vendoring for the smaller archetypes and submoduling for the larger ones.

## The 9 archetypes

| Slug | Framework / shape | Source | Pinning recipe |
|---|---|---|---|
| `openai-agents-sdk` | OpenAI Agents SDK refund/email tools | `samples/support_refund_agent` (vendored from this repo) | `cp -r ../../samples/support_refund_agent ./openai-agents-sdk` |
| `mcp-only` | MCP server with no Python framework | `samples/mcp_only_server` | `cp -r ../../samples/mcp_only_server ./mcp-only` |
| `openapi-only` | OpenAPI-described tool surface | `samples/openapi_only_agent` | `cp -r ../../samples/openapi_only_agent ./openapi-only` |
| `langgraph` | LangGraph agent | `samples/simple_langchain_agent` | `cp -r ../../samples/simple_langchain_agent ./langgraph` |
| `adk-dynamic-toolset` | Google ADK with dynamic toolsets | `samples/google_adk_agent` | `cp -r ../../samples/google_adk_agent ./adk-dynamic-toolset` |
| `crewai` | CrewAI agent | `samples/simple_crewai_agent` | `cp -r ../../samples/simple_crewai_agent ./crewai` |
| `clean-read-only` | Read-only agent (negative tool-surface case) | `samples/clean_read_only_agent` | `cp -r ../../samples/clean_read_only_agent ./clean-read-only` |
| `n8n` | n8n workflow JSON with HTTP + Code nodes | `samples/n8n_workflow_agent` | `cp -r ../../samples/n8n_workflow_agent ./n8n` |
| `non-agent-negative-control` | Repo with no agent at all | A small public Python library (e.g. `python-attrs/attrs` at a pinned commit) | `git submodule add https://github.com/python-attrs/attrs.git non-agent-negative-control && cd non-agent-negative-control && git checkout <pinned-sha> && cd .. && git add non-agent-negative-control` |

Eight of the nine reuse the bundled `samples/` fixtures (pinned by this repo's git history). The negative-control archetype is the only true external dependency — pin a specific commit when adding it.

### Automation

The canonical materializer is [`harness/adoption/scripts/sync_fixtures.py`](../../harness/adoption/scripts/sync_fixtures.py); run `python -m harness.adoption sync-fixtures` to apply the table above in one shot. The script skips the submoduled negative-control archetype (do that step by hand).

## Why these specifically

- **`openai-agents-sdk`** — the largest deployed framework; if Shipgate adoption fails here, the headline number is wrong.
- **`mcp-only`** and **`openapi-only`** — exercise the artifact-only path where there's no Python framework to import. Catches "agent reads the Python tree and gives up" failures.
- **`langgraph`** — second-largest framework; LangGraph 0.2+ AST extraction is a Shipgate first-class adapter.
- **`adk-dynamic-toolset`** — the hardest detection case (factory-built toolsets); validates that Shipgate's diagnostics route the agent to a fix when zero tools are extracted.
- **`crewai`** — covers the third major framework family.
- **`clean-read-only`** — a real agent project with no write tools. Tests whether Shipgate is correctly proposed as advisory rather than skipped.
- **`non-agent-negative-control`** — a library with no agent at all. The negative-control prompt should produce a no-op for any cell using this archetype; Shipgate proposed here is a failure regardless of variant.

## Reproducibility

Pin every archetype to a specific commit. When a vendored archetype is updated, bump the benchmark schema version in [`../results/README.md`](../results/README.md) so the comparability across runs is explicit.
