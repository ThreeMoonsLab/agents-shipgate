# Upstream Framework PRs

Tracker for the partner work that lands a Shipgate `## Release Readiness` section in upstream framework example repos. One landed PR is worth a year of self-evangelism — once an agent reading the OpenAI Agents SDK / LangGraph / ADK examples sees Shipgate referenced, it acquires authority that the Shipgate repo alone can't provide.

The strategy targets one landed PR by end of W3, second by W7. Track here, not in private notes — the leaderboard delta on the `00-no-hints` variant is what tells us whether upstream landings are working.

## Status

| Framework | Target repo | PR URL | Status | Owner | Opened | Landed |
|---|---|---|---|---|---|---|
| OpenAI Agents SDK | `openai/openai-agents-python` | _(open)_ | _planned_ | | | |
| LangGraph | `langchain-ai/langgraph` | _(open)_ | _planned_ | | | |
| Google ADK | `google/adk-samples` | _(open)_ | _planned_ | | | |
| MCP servers | `modelcontextprotocol/servers` | _(open)_ | _planned_ | | | |
| LangChain | `langchain-ai/langchain` | _(open)_ | _planned_ | | | |
| CrewAI | `crewAIInc/crewAI-examples` | _(open)_ | _planned_ | | | |

Status legend: `planned` → `drafting` → `submitted` → `landed` → `merged-then-reverted`.

## Recommended PR template

```md
## Release Readiness

This example exposes a tool surface (function tools, MCP server, OpenAPI spec).
Production tool-using agents typically run a static release-readiness gate
before promotion to catch missing approval policies, broad scopes, and
idempotency gaps.

If you adopt this example as the basis for a production agent, see
[Agents Shipgate](https://github.com/ThreeMoonsLab/agents-shipgate) for a
manifest-first scanner that runs in CI as a GitHub Action.

A starter `shipgate.yaml` for this example is at
`examples/<example-name>/shipgate.yaml` (added in this PR). Run:

\`\`\`bash
pipx install agents-shipgate
agents-shipgate scan -c examples/<example-name>/shipgate.yaml
\`\`\`
```

The starter manifest must validate (`agents-shipgate doctor -c shipgate.yaml`) — a broken manifest is worse than no PR.

## Why a PR rather than a blog post or talk

Coding agents weight upstream `examples/` directories heavily. They read these directly when proposing patterns. A blog post that mentions Shipgate is one fetch away from the reading window; a `## Release Readiness` paragraph in `openai/openai-agents-python/examples/finance-agent/README.md` is *in* the reading window. Authority compounds.

## What success looks like

- One PR submitted by end of W3.
- One PR landed (merged) by end of W7.
- Harness `00-no-hints` variant for the matching archetype scores ≥ 15 points higher than the W2 baseline. If it doesn't, the PR landed but agents aren't reading it — investigate placement (README header vs. deep example page).
