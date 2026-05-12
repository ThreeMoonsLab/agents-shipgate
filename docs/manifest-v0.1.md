# `shipgate.yaml` v0.1

`shipgate.yaml` is mandatory and is the source of truth for v0.1.

A manifest is valid when it declares at least one of:

- `tool_sources` for MCP, OpenAPI, optional OpenAI Agents SDK metadata, Google ADK static metadata, LangChain/LangGraph Python, or CrewAI Python.
- `openai_api` for simple OpenAI API apps that use prompt files, OpenAI tool schemas, and structured output schemas directly.
- `google_adk` for explicit Google ADK Agent Config, eval, trace, or inventory artifacts.
- `langchain` and `crewai` for supplemental Python entrypoints and explicit local tool inventories.

Agent scope text can come from `agent.declared_purpose`, `agent.instructions_preview`, or `openai_api.prompt_files`.

## Minimal Manifest

```yaml
version: "0.1"

project:
  name: support-refund-agent

agent:
  name: refund-assistant
  declared_purpose:
    - answer refund policy questions

environment:
  target: production_like

tool_sources:
  - id: support_openapi
    type: openapi
    path: specs/support-tools.openapi.yaml
```

## Minimal OpenAI API Manifest

```yaml
version: "0.1"

project:
  name: support-refund-api

agent:
  name: refund-api-assistant

environment:
  target: production_like

openai_api:
  prompt_files:
    - prompts/support_refund.md
  tools:
    - path: tools/openai-tools.json
  response_formats:
    - path: schemas/refund_decision.schema.json
      downstream_critical_fields:
        - decision
        - needs_review
  model_config:
    path: openai-config.json
  policy_rules:
    - path: policies/openai-api-policy.yaml
```

## Supported Tool Sources

- `openapi`: local OpenAPI 3.0/3.1 YAML or JSON.
- `mcp`: local exported MCP tools JSON.
- `openai_agents_sdk`: optional static Python AST extraction.
- `google_adk`: static Google ADK Python entrypoint or Agent Config YAML.
- `langchain`: static LangChain/LangGraph Python entrypoint.
- `crewai`: static CrewAI Python entrypoint.
- `codex_plugin`: static Codex plugin package or marketplace metadata.

When two sources declare the same tool name, Agents Shipgate keeps the higher-fidelity source, merges non-schema metadata such as annotations, auth scopes, risk hints, and owner, and emits a source warning. Current precedence is OpenAI API artifacts, then OpenAPI, then Google ADK/LangChain/CrewAI inventories, then MCP JSON, then SDK/ADK/LangChain/CrewAI static extraction. Low-confidence framework stubs rank below static custom function/class tools.

## Google ADK Artifacts

`google_adk` is local-only and static-only. Agents Shipgate parses Python AST and Agent Config YAML. It does not import ADK code, run `adk`, connect to MCP servers, call models, or call tools.

Prefer declaring ADK Python entrypoints and Agent Config files as `tool_sources`
with `type: google_adk`. Use the top-level `google_adk` block for supplemental
release evidence such as eval files, trace samples, and explicit MCP inventory
exports. The top-level `google_adk.python_entrypoints` and
`google_adk.agent_configs` fields are accepted for compatibility and batch
imports, but `tool_sources` keeps the primary scanned surface visible beside
MCP and OpenAPI inputs.

```yaml
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py

google_adk:
  python_entrypoints:
    - agents/support_agent.py
  agent_configs:
    - agents/root_agent.yaml
  eval_sets:
    - evals/support.eval.json
  tool_inventories:
    - inventories/adk-mcp-tools.json
  trace_samples:
    - traces/adk-tool-calls.jsonl
```

Supported static ADK signals:

- Python `Agent` / `LlmAgent` definitions with literal `tools=[...]`.
- Plain function tools referenced in an agent tools list.
- `FunctionTool(func=...)` and `LongRunningFunctionTool(func=...)` wrappers.
- `OpenAPIToolset` when a local spec path can be resolved from a literal path or `Path("...").read_text()`.
- `McpToolset` metadata, including static `tool_filter` and explicit `inventory_path` / `tool_inventory_path` hints.
- Agent Config YAML `tools`, `sub_agents`, callbacks, plugins, and local config references.

Dynamic ADK toolsets remain visible in reports as warnings/findings. Provide explicit MCP/OpenAPI/tool inventory inputs when static extraction cannot enumerate runtime tools.

Static spec resolution intentionally covers simple literal-path idioms such as
`Path("specs/support.openapi.yaml").read_text()` and `open("spec.yaml").read()`.
Module-relative expressions such as `Path(__file__).parent / "spec.yaml"` or
assignment-then-read patterns may be reported as dynamic. In those cases,
declare the same spec or MCP inventory explicitly in `tool_sources` or
`google_adk.tool_inventories`.

## LangChain And CrewAI Artifacts

LangChain/LangGraph and CrewAI support is local-only and static-only. Agents
Shipgate parses Python AST and does not import framework packages, call models,
run crews/graphs/agents, connect to MCP servers, call tools, or execute
subprocesses.

Prefer declaring primary framework entrypoints in `tool_sources` so the scanned
surface is visible beside MCP and OpenAPI inputs. Use the top-level
`langchain` and `crewai` blocks for supplemental batch entrypoints or explicit
local MCP-style inventories that document dynamic or prebuilt runtime tool
surfaces.

```yaml
tool_sources:
  - id: support_langchain
    type: langchain
    path: agents/langchain_agent.py
  - id: support_crewai
    type: crewai
    path: agents/support_crew.py

langchain:
  python_entrypoints:
    - agents/graph.py
  tool_inventories:
    - inventories/langchain-tools.json

crewai:
  python_entrypoints:
    - agents/crew.py
  tool_inventories:
    - inventories/crewai-tools.json
```

Supported static LangChain/LangGraph signals:

- `@tool` decorators from `langchain.tools` and `langchain_core.tools`, including aliases.
- `StructuredTool.from_function(...)`.
- Static tool lists passed to `create_agent`, `create_react_agent`, `ToolNode`, and `bind_tools`.
- Same-file Pydantic `args_schema` classes with simple annotated fields and `Field(...)` descriptions.

Supported static CrewAI signals:

- `@tool` decorators from `crewai.tools`, including aliases.
- `BaseTool` subclasses with `name`, `description`, `_run`, and same-file `args_schema`.
- Static `Agent(..., tools=[...])` and `Crew(...)` references.
- `crewai_tools.*Tool()` prebuilt references as low-confidence stubs plus source warnings.

Dynamic framework tool surfaces such as `tools=get_tools()`, list
comprehensions, loop-built lists, unresolved imported toolkits, or unresolved
external schema classes remain visible as source warnings and framework
findings unless an explicit local inventory resolves the surface.

## Codex Plugin Artifacts

`codex_plugin` is local-only and static-only. Agents Shipgate parses Codex
plugin package metadata and companion files, but does not install plugins,
execute hooks, launch MCP servers, authenticate connectors, call tools, call
models, or make network requests.

Use `mode: package` when `path` points at a plugin root directory. A direct
`.codex-plugin/plugin.json` path is accepted for compatibility but normalized
with a warning; the canonical manifest form is the package root. Use
`mode: marketplace` when `path` points at `.agents/plugins/marketplace.json`.
Marketplace entries with `source.source: local` resolve under the manifest
directory.

```yaml
tool_sources:
  - id: browser_plugin
    type: codex_plugin
    mode: package
    path: plugins/browser-use

  - id: repo_marketplace
    type: codex_plugin
    mode: marketplace
    path: .agents/plugins/marketplace.json

codex_plugins:
  mcp_tool_inventories:
    - plugin: browser-use
      server: browser-use
      path: inventories/browser-use-tools.json
```

Supported static Codex plugin signals:

- `.codex-plugin/plugin.json` identity and component paths.
- `skills/**/SKILL.md` frontmatter as instruction metadata, not tools.
- `.mcp.json` server declarations as non-executed MCP server stubs.
- `.app.json` connector declarations as app surface stubs.
- Hook config files referenced by `plugin.json`; literal `command`, `cmd`,
  `run`, `shell`, or `script` fields are recorded as code-execution hook stubs.
- Local MCP inventory files declared in `codex_plugins.mcp_tool_inventories`.

Only tools loaded from explicit MCP inventories enter `tool_inventory[]` with
`source_type: codex_plugin_mcp_inventory`. Apps, hooks, skills, and MCP server
declarations are reported under `codex_plugin_surface`, not as tools.

## n8n

n8n support is configured through the top-level `n8n:` block, not through
`tool_sources`. The adapter reads local workflow JSON exports/source-control
files and optional stubs only; it does not call a live n8n instance or execute
workflows.

```yaml
n8n:
  workflows:
    - path: workflows/
  credential_stubs:
    - path: credentials/
  variable_stubs:
    - path: variables.json
  data_table_schemas:
    - path: data-tables/
  execution_samples:
    - path: evidence/n8n-executions/
      optional: true
  eval_sets:
    - path: evaluations/
      optional: true
  tool_inventories:
    - path: tools/mcp-tools.json
      optional: true
```

Only agent-callable n8n surfaces are normalized into tools: AI Agent tool
sub-nodes, MCP Client Tool selections, MCP Server Trigger exposed tools, Call
n8n Workflow Tool entrypoints, Custom Code Tool nodes, HTTP Request Tool nodes,
and explicit local inventories. Webhook, Chat Trigger, and Manual Trigger nodes
are recorded as ingress evidence, not tools.

Inactive workflows (`active: false`) are recorded but not treated as live tool
or ingress surfaces. Workflow JSON is still scanned for secret-like values.

n8n discovery treats one workflow-shaped JSON file as a strong signal and
auto-initializes an `n8n:` block for that workspace. Standard source-control
stub paths such as `credentials/`, `variables.json`, `data-tables/`, and
`evaluations/` are included in generated manifests when present.

n8n workflow tool names are scoped to the workflow file for tool identity, so
two workflows may both expose a tool called `Lookup Customer` without one
silently replacing the other. For provenance, treat `source_ref` as
adapter-specific display text; the structured navigation fields are
`source_path` plus `source_pointer`.

Human-review nodes such as n8n Send-and-Wait are recorded for reviewer context
only. They do not satisfy `policies.approval`; high-risk n8n tools need
explicit policy declarations in the manifest.

## OpenAI API Artifacts

`openai_api` is for simple API apps that do not have MCP/OpenAPI/SDK tool metadata. It is local-only: Agents Shipgate reads files and never calls OpenAI APIs.

Supported fields:

```yaml
openai_api:
  prompt_files:
    - prompts/support_refund.md

  tools:
    - path: tools/openai-tools.json

  function_schemas:
    - name: create_refund
      path: schemas/create_refund.parameters.schema.json

  response_formats:
    - path: schemas/refund_decision.schema.json
      downstream_critical_fields:
        - decision
        - refund_amount
        - needs_review

  model_config:
    path: openai-config.json

  test_cases:
    - path: tests/openai-api-cases.json

  trace_samples:
    - path: traces/sample.jsonl

  policy_rules:
    - path: policies/openai-api-policy.yaml
```

Accepted OpenAI API tool shapes:

- A tools array: `[{"type": "function", "name": "...", "parameters": {...}}]`.
- An object with `tools: [...]`.
- Responses-style function tools: `{ "type": "function", "name": "...", "parameters": {...}, "strict": true }`.
- Chat-style function tools: `{ "type": "function", "function": { "name": "...", "parameters": {...}, "strict": true } }`.

`function_schemas` accepts either an OpenAI function object or a pure parameters JSON Schema plus `name` in the manifest.

`response_formats` accepts either a pure JSON Schema or an OpenAI `json_schema` wrapper.

OpenAI API policy rule files supplement manifest policies:

```yaml
approval_required: [create_refund]
confirmation_required: [send_customer_email]
idempotency_required: [create_refund]
retry_policy:
  max_attempts: 2
timeouts:
  tool_call_ms: 10000
tool_output_schemas:
  create_refund:
    success_fields: [refund_id, status]
    failure_fields: [error_code, message]
```

Trace samples are JSON arrays or JSONL with simple normalized fields such as `tool_name`, `approved`, `confirmed`, `success`, and `error`. Unsupported raw logs produce source warnings rather than blockers.

## Validation Evidence Artifacts

The optional top-level `validation` block declares local human-in-the-loop
evidence for review workflows. It does not cause Agents Shipgate to run an
agent, shorten validation, certify safety, or decide auto-approval readiness.
It only tells the scanner which local evidence files a reviewer expects for
the declared review posture.

```yaml
validation:
  mode: human_in_the_loop
  target_review_posture: limited_auto_approval # recommendation_only | limited_auto_approval
  required_evidence:
    approval_trace_required: true
    override_reason_required: true
    high_risk_auto_approval_exclusion_required: true
  evidence:
    approval_traces:
      - path: validation/approval-traces.jsonl
    override_logs:
      - path: validation/override-log.jsonl
    high_risk_exclusions:
      - path: validation/high-risk-exclusions.yaml
    promotion_criteria:
      - path: validation/promotion-criteria.yaml
```

Defaults are conservative and opt-in: omitting `validation` emits no HITL
evidence checks, and every `required_evidence` flag defaults to `false`.
When `target_review_posture: limited_auto_approval` is declared, the scanner
expects all three canonical evidence flags to be explicitly true and expects a
local promotion criteria file documenting the same posture and flags.
If you target `limited_auto_approval` without setting all three canonical
evidence flags to `true`, only the promotion-criteria finding surfaces; the
underlying approval trace, override reason, and high-risk exclusion checks stay
disabled until their flags are enabled.

### Producing validation evidence

Agents Shipgate reads these files; it does not generate them. They normally
come from runtime middleware, SDK hooks, gateway logs, or an internal ops
workflow that records approvals and overrides while the agent is exercised.
Keep the files local to the manifest directory; paths outside that directory
are rejected.

HITL evidence findings are evidence gaps, not runtime-control conclusions.
Missing local evidence does not prove an approval, override, exclusion, or
promotion control is absent. Present local evidence does not certify runtime
enforcement. Reports and packets include `source_provenance[]` entries so a
reviewer can trace each HITL evidence source back to local files:

- `type`: `approval_trace`, `override_log`, `high_risk_exclusion`,
  `promotion_criteria`, or `manifest_requirement`
- `ref`: relative local path, or the manifest filename
- `location`: `ref#<json-pointer>`; whole-file sources use `path#`
- `status`: `requirement_only`, `expected_but_absent`, `source_load_failed`,
  `loaded`, or `loaded_with_warnings`
- `detail`: deterministic local context, with no timestamps or absolute paths

`approval_traces` are JSON arrays or JSONL. They use the same normalized trace
fields as OpenAI API traces:

```json
{"tool_name":"issue_refund","approved":true,"success":true}
```

A JSON object without a recognized list key is treated as one trace event for
compatibility with the existing trace loader; prefer arrays or JSONL for
multi-event files.

`override_logs` are JSON arrays or JSONL. The scanner reads only the
framework-neutral fields below and preserves other fields for the producing
system:

```json
{"tool_name":"issue_refund","action":"override","reason":"manager approved","actor":"ops-lead","timestamp":"2026-05-06T17:00:00Z"}
```

`action` is a closed enum and must be one of `override`, `bypass`, or
`auto_approve`. Events with any other action value, such as `denied`, are
reported as loader warnings and do not count as override reason evidence.
`reason` must be non-empty for each normalized override event.

`high_risk_exclusions` are YAML or JSON only:

```yaml
high_risk_auto_approval_exclusions:
  - tool: issue_refund
    reason: financial actions remain manual
    owner: support-ops
```

`promotion_criteria` are YAML or JSON only:

```yaml
target_review_posture: limited_auto_approval
required_evidence:
  approval_trace_required: true
  override_reason_required: true
  high_risk_auto_approval_exclusion_required: true
```

## Anthropic Messages API Artifacts

`anthropic` is for agents built on the Anthropic Messages API tool-use surface (https://docs.anthropic.com/en/docs/build-with-claude/tool-use). It is local-only: Agents Shipgate reads files and never calls Anthropic APIs.

Supported fields:

```yaml
anthropic:
  prompt_files:
    - prompts/support_refund.md

  tools:
    - path: tools/anthropic-tools.json

  policy_rules:
    - path: policies/anthropic-policy.yaml
```

Anthropic tool definitions are flat objects (no OpenAI-style `function` wrapper):

```json
{
  "tools": [
    {
      "name": "create_refund",
      "description": "Create a refund for a customer payment.",
      "input_schema": {
        "type": "object",
        "properties": {"payment_id": {"type": "string"}},
        "required": ["payment_id"]
      },
      "cache_control": {"type": "ephemeral"}
    }
  ]
}
```

Tool names are validated against Anthropic's documented regex `^[a-zA-Z0-9_-]{1,64}$`; violations surface as source warnings (the static linter does not block). Server-side built-in tool types (`type: "computer_*"`, `"bash_*"`, `"web_search*"`, `"text_editor_*"`) have no user-controlled `input_schema` and are skipped with a warning so checks like `SHIP-DOC-MISSING-DESCRIPTION` and `SHIP-SCHEMA-MISSING-BOUNDS` do not fire on managed schemas the user cannot fix.

`cache_control` values are captured verbatim into `tool.annotations.anthropicCacheControl`. They have no influence on risk classification in v0.4.

`policy_rules` files share the same shape as the OpenAI API policy file (`approval_required`, `confirmation_required`, `idempotency_required`). They feed `SHIP-POLICY-APPROVAL-MISSING`, `SHIP-POLICY-CONFIRMATION-MISSING`, and `SHIP-SIDEFX-IDEMPOTENCY-MISSING` checks alongside the manifest's top-level `policies` block.

The framework-agnostic checks (`SHIP-INVENTORY-*`, `SHIP-DOC-*`, `SHIP-SCHEMA-*`, `SHIP-AUTH-*`, `SHIP-SCOPE-*`, `SHIP-POLICY-*`, `SHIP-SIDEFX-*`, `SHIP-MANIFEST-*`) all fire on Anthropic tools without any extra configuration. From the `SHIP-API-*` family, `SHIP-API-FUNCTION-SCHEMA-STRICTNESS` and `SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH` apply; the others key on OpenAI-specific data (response formats, retry policy, trace samples) and intentionally do not fire on Anthropic-only manifests. No new `SHIP-ANTHROPIC-*` check IDs are introduced.

## MCP Tools JSON Contract

Preferred shape:

```json
{
  "tools": [
    {
      "name": "support.search_kb",
      "description": "Search support knowledge base articles.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": { "type": "string" }
        },
        "required": ["query"]
      },
      "outputSchema": {},
      "annotations": {
        "readOnlyHint": true,
        "idempotentHint": true
      },
      "auth": {
        "scopes": ["support:kb:read"]
      },
      "owner": "support-platform"
    }
  ]
}
```

The root may also be a JSON array of tool objects.

Wildcard exposure can be represented as:

```json
{ "tools": "*", "wildcard": true }
```

If `wildcard: true` is combined with a non-empty `tools` array, the source is rejected. Use wildcard exposure or explicit tools, not both.

## Suppressions

Suppressions require a reason and match by `check_id` plus optional `tool` name.

```yaml
checks:
  ignore:
    - check_id: SHIP-SCHEMA-BROAD-FREE-TEXT
      tool: support.search_kb
      reason: "Search query intentionally accepts free text."
```

Suppressed findings remain in the JSON report with `suppressed: true`.

## Policy Packs

v0.4 supports local declarative YAML policy packs for organization-specific
rules. Policy packs are static data: Agents Shipgate reads YAML and never
imports Python or executes pack code.

```yaml
checks:
  policy_packs:
    - id: org-release
      path: policies/org-release.yaml
      optional: false
```

Policy pack paths use the same manifest-relative containment policy as other
local inputs. External rule IDs must not start with `SHIP-`; use an
organization namespace such as `ORG-*`. Findings emitted by policy packs support
the same suppressions, severity overrides, baselines, Markdown, JSON, and SARIF
output as built-in findings.

Minimal pack:

```yaml
name: Org Release Policy
version: "1.0"
rules:
  - id: ORG-HIGH-RISK-OWNER-MISSING
    title: High-risk production tool has no org owner
    category: org_policy
    severity: high
    confidence: high
    recommendation: Assign an owning team before production release.
    match:
      risk_tags: [financial_action]
      source_types: [openapi]
      environment_targets: [production_like, production]
      missing_owner: true
```

Supported match fields are `risk_tags`, `source_types`,
`environment_targets`, `missing_owner`, `missing_auth_scopes`,
`missing_approval_policy`, `missing_confirmation_policy`,
`missing_idempotency_policy`, and `parameters`. Parameter predicates support
`name`, `names`, `types`, `missing_maximum`, and `required`.

## Severity Overrides

Teams can re-rank built-in and policy-pack checks without forking the scanner:

```yaml
checks:
  severity_overrides:
    SHIP-DOC-INJECTION-RISK: low
    SHIP-AUTH-MISSING-SCOPE: critical
```

The legacy top-level `check_severity_overrides` alias was removed in v0.4. Move
those entries under `checks.severity_overrides`.

## Risk Overrides

Use `risk_overrides` to add high-confidence manual tags, set owners, or remove known-wrong heuristic tags.

```yaml
risk_overrides:
  tools:
    refund_status_lookup:
      tags: ["read_only"]
      remove_tags: ["financial_action"]
      reason: "This endpoint only reads refund status."
```

## CI Failure Policy

`ci.mode: advisory` exits `0` by default. `ci.mode: strict` exits `20` on unsuppressed `critical` findings by default. Configuration, input parsing, and internal scanner errors use `2`, `3`, and `4`.

Override the failing severities with `ci.fail_on`:

```yaml
ci:
  mode: strict
  fail_on:
    - critical
    - high
```

The CLI equivalent is:

```bash
agents-shipgate scan --config shipgate.yaml --fail-on critical,high
```

## Finding Fingerprints

Each finding includes a stable `fingerprint` computed from:

- `check_id`
- `tool_name`
- sorted `evidence`

This is the baseline/diff key for future workflows. The human-readable `id` is derived from the same fingerprint and may receive a numeric suffix only if identical findings collide in one report.

## Strict Field Validation

Manifest schema models reject unknown keys. This is intentional: a typo such as `declared_purpoze` should fail fast instead of silently weakening the release review.
