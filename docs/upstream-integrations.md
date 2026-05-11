# Upstream Integrations

Per-framework, 60-second instructions for dropping Agents Shipgate into a project that already uses one of the supported tool surfaces. This is the "coding agent's adoption checklist" — paste the minimal `shipgate.yaml`, run the canonical 4-call flow, and you're scanning.

> **Audience.** Coding agents adding Shipgate to a target repo for the first time, or repo maintainers picking up Shipgate. If you want the full architectural reference, see [`docs/manifest-v0.1.md`](manifest-v0.1.md) and [`docs/minimal-real-configs.md`](minimal-real-configs.md). This doc is the speedrun.

## The flow (every framework)

```bash
pipx install agents-shipgate
agents-shipgate detect --json                                          # 1. classify
agents-shipgate init --write --ci --json                               # 2. manifest + workflow
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json  # 3. scan + suggest
agents-shipgate apply-patches --from agents-shipgate-reports/report.json \
    --confidence high --apply                                          # 4. apply safe trivial fixes
```

Per-framework guidance below differs only in step 2's manifest shape and which `tool_sources` you declare. Step 1 (`detect`) auto-classifies and tells you which framework rows fired; step 3 reads them.

If you want a zero-install first step (just to confirm Shipgate is relevant), see [`docs/zero-install.md`](zero-install.md).

## OpenAI Agents SDK (`@function_tool`)

Drop-in for projects that decorate Python functions with `@function_tool` from `openai-agents`. Static AST extraction reads the decorators without importing the framework.

```yaml
# shipgate.yaml
version: "0.1"
project:
  name: refund-agent
agent:
  name: support-refund-agent
  declared_purpose:
    - issue refunds for verified support cases
  prohibited_actions:
    - send personal data to external services
environment:
  target: production_like
tool_sources:
  - id: agent
    type: openai_agents_sdk
    path: agent.py
policies:
  require_approval_for_tools: []
  require_confirmation_for_tools: []
  require_idempotency_for_tools: []
permissions:
  scopes: []
ci:
  mode: advisory
output:
  directory: agents-shipgate-reports
  formats: [markdown, json]
```

**Working fixture**: [`samples/support_refund_agent/`](../samples/support_refund_agent/) (this one combines OpenAI Agents SDK + OpenAPI + MCP). Run `agents-shipgate fixture run support_refund_agent` to scan it without writing any YAML.

**Pitfalls**:
- Tool factories (`make_tool(name=...)`) and decorators applied dynamically aren't visible to AST extraction. Provide an explicit MCP/OpenAPI export or a local tool inventory.
- `Agent(name="…")` literals get auto-detected; runtime `Agent(name=os.environ[...])` doesn't.

## LangChain / LangGraph (`@tool`, `create_agent`)

For projects using LangChain Core 0.3+ `@tool` decorators or `create_agent` / `create_react_agent` patterns.

```yaml
version: "0.1"
project:
  name: support-agent
agent:
  name: langchain-support-agent
  declared_purpose:
    - answer support questions and escalate to humans for refunds
environment:
  target: production_like
tool_sources:
  - id: lc
    type: langchain
    path: agent.py
policies:
  require_approval_for_tools: []
  require_confirmation_for_tools: []
ci:
  mode: advisory
```

**Working fixture**: [`samples/simple_langchain_agent/`](../samples/simple_langchain_agent/).

**Pitfalls**:
- LangGraph subgraphs and dynamically-built nodes aren't fully visible. Add `langchain.tool_inventories[]` only after you have written a local JSON inventory; missing non-optional artifact files stop the scan before findings are produced.
- LangChain ≤ 0.2 patterns (`AgentExecutor` with prebuilt tools) are partially supported; the static surface may show low-confidence extractions.

## CrewAI (`@tool`, `Agent` / `Crew` / `Task`)

```yaml
version: "0.1"
project:
  name: research-crew
agent:
  name: research-agent
  declared_purpose:
    - research a topic and produce a structured brief
environment:
  target: local
tool_sources:
  - id: crew
    type: crewai
    path: crew.py
policies:
  require_approval_for_tools: []
ci:
  mode: advisory
```

**Working fixture**: [`samples/simple_crewai_agent/`](../samples/simple_crewai_agent/).

**Pitfalls**:
- CrewAI's prebuilt-tool registry isn't visible to AST extraction. Add `crewai.tool_inventories[]` only after you have written a local JSON inventory; missing non-optional artifact files stop the scan before findings are produced.
- Multi-agent crews need each `Agent(role=…)` to be statically introspectable; runtime config-driven roles fall back to low confidence.

## Google ADK (Python + Agent Config YAML)

```yaml
version: "0.1"
project:
  name: adk-support-agent
agent:
  name: support-agent
  declared_purpose:
    - handle support cases
environment:
  target: production_like
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
policies:
  require_approval_for_tools: []
ci:
  mode: advisory
```

**Working fixture**: [`samples/google_adk_agent/`](../samples/google_adk_agent/).

**Pitfalls**:
- `OpenAPIToolset(...)` and `McpToolset(...)` need `tool_filter` declared; without it, the toolset counts as "unfiltered" and `SHIP-ADK-MCP-TOOLSET-UNFILTERED` fires high. Add the filter, then point `inventory_path` at a local tool inventory.
- Production targets need `google_adk.eval_sets` declared; otherwise `SHIP-ADK-EVAL-COVERAGE-MISSING` fires. Add the block only after the eval file exists, or mark the artifact `optional: true` during bring-up.

## MCP-only (no Python framework)

For repositories that ship an MCP server and nothing else — no Python wrapper, no SDK decorators. Detection registers as `is_agent_project: false`, but `suggested_sources` will list the MCP export, so adoption proceeds.

```yaml
version: "0.1"
project:
  name: refund-mcp-server
agent:
  name: refund-mcp
  declared_purpose:
    - expose refund operations over MCP
environment:
  target: production_like
tool_sources:
  - id: mcp
    type: mcp
    path: mcp/tools.json
policies:
  require_approval_for_tools: []
ci:
  mode: advisory
```

**Working fixture**: [`examples/golden-prs/mcp-only-tool-server/`](../examples/golden-prs/mcp-only-tool-server/) — golden PR with a complete walkthrough.

**Pitfalls**:
- `*` / wildcard tool entries in the MCP export trigger `SHIP-INVENTORY-WILDCARD-TOOLS` high. Replace with an explicit allowlist.
- The MCP export must be the resolved tool list, not a server config that points at runtime-loaded tools.

## OpenAPI-only (HTTP tool surface)

```yaml
version: "0.1"
project:
  name: support-api
agent:
  name: support-api-agent
  declared_purpose:
    - operate support tickets via the documented API
environment:
  target: production_like
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
policies:
  require_approval_for_tools: []
permissions:
  scopes: []
ci:
  mode: advisory
```

**Working fixture**: [`examples/golden-prs/openapi-support-agent/`](../examples/golden-prs/openapi-support-agent/).

**Pitfalls**:
- OpenAPI specs without `security` blocks don't declare auth scopes; `SHIP-AUTH-MISSING-SCOPE` fires high on every write operation. Add `security` per operation.
- Spec files larger than 10 MB are rejected. Split or downsample.

## OpenAI Messages API artifacts

For projects that drive a Messages API model with `tools/openai-tools.json`, `prompts/`, `policies/openai-*.yaml`, and `tests/openai-cases.json` artifacts. No Python framework needed.

```yaml
version: "0.1"
project:
  name: support-messages-agent
agent:
  name: support-messages-agent
  declared_purpose:
    - handle tier-1 support over the Messages API
environment:
  target: production_like
openai_api:
  prompt_files:
    - prompts/system.md
  tools:
    - path: tools/openai-tools.json
  response_formats:
    - path: schemas/response.schema.json
      downstream_critical_fields: [decision, status]
  model_config:
    path: openai-config.json
  test_cases:
    - path: tests/cases.openai.cases.json
  trace_samples:
    - path: traces/sample.jsonl
  policy_rules:
    - path: policies/openai-policy.yaml
ci:
  mode: advisory
```

**Working fixture**: [`samples/simple_openai_api_agent/`](../samples/simple_openai_api_agent/).

**Pitfalls**:
- The `response_formats[].downstream_critical_fields` array names the JSON keys that downstream code branches on. Listing them lets `SHIP-API-STRUCTURED-OUTPUT-READINESS` validate that those keys are constrained (enum, oneOf) rather than free-form.
- Trace samples must be JSON Lines with `approved` / `confirmed` / `decision` fields when the matching policy rule requires them — see [`docs/api-trace-evidence.md`](api-trace-evidence.md) for the canonical shape.

## Anthropic Messages API artifacts

```yaml
version: "0.1"
project:
  name: support-anthropic-agent
agent:
  name: support-anthropic-agent
  declared_purpose:
    - tier-1 support over Anthropic Messages API
environment:
  target: production_like
anthropic:
  prompt_files:
    - prompts/system.md
  tools:
    - path: tools/anthropic-tools.json
  policy_rules:
    - path: policies/anthropic-policy.yaml
ci:
  mode: advisory
```

**Working fixture**: [`samples/simple_anthropic_agent/`](../samples/simple_anthropic_agent/).

**Pitfalls**:
- Server-side built-in tool types (`computer_*`, `bash_*`, `web_search*`, `text_editor_*`) are skipped by checks like `SHIP-DOC-MISSING-DESCRIPTION` because the user can't fix the schema. A source warning surfaces instead.
- Tool names must match `^[a-zA-Z0-9_-]{1,64}$`; violations are warnings, not errors.

## Multi-framework projects (combined surface)

Real production agents often combine surfaces — Python SDK plus an external MCP server plus an OpenAPI API. Just declare each `tool_source`:

```yaml
tool_sources:
  - id: agent_sdk
    type: openai_agents_sdk
    path: agent.py
  - id: refund_api
    type: openapi
    path: refund-api.yaml
  - id: mcp_inventory
    type: mcp
    path: .agents-shipgate/mcp-export.json
```

Tool inventory and policy checks deduplicate across sources by `tool_name`, so the same tool surfaced via the SDK and the MCP export gets one finding, not two.

## CI integration (the same for every framework)

```yaml
# .github/workflows/agents-shipgate.yml
name: Agents Shipgate
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
jobs:
  shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.10.0
        with:
          ci_mode: advisory
          diff_base: target
          pr_comment: 'true'
```

`init --ci` writes a similar workflow into `.github/workflows/agents-shipgate.yml`. Switch to `ci_mode: strict` only after the team has reviewed the advisory output and saved a baseline (see [`baseline.md`](baseline.md)).

## See also

- [`zero-install.md`](zero-install.md) — verify relevance without installing anything.
- [`agent-recipes.md`](agent-recipes.md) — programmatic 4-call flow for coding agents.
- [`agent-action-guide.md`](agent-action-guide.md) — per-category recipe when you have a finding to act on.
- [`minimal-real-configs.md`](minimal-real-configs.md) — fuller per-framework configs with rationale.
- [`integrations.md`](integrations.md) — CI provider recipes (CircleCI, GitLab) beyond GitHub Actions.
- [`docs/manifest-v0.1.md`](manifest-v0.1.md) — full manifest schema reference.
