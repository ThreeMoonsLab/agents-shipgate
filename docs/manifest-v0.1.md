# `shipgate.yaml` v0.1

`shipgate.yaml` is mandatory and is the source of truth for v0.1.

A manifest is valid when it declares at least one of:

- `tool_sources` for MCP, OpenAPI, or optional OpenAI Agents SDK metadata.
- `openai_api` for simple OpenAI API apps that use prompt files, OpenAI tool schemas, and structured output schemas directly.

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

When two sources declare the same tool name, Agents Shipgate keeps the higher-fidelity source, merges non-schema metadata such as annotations, auth scopes, risk hints, and owner, and emits a source warning. Current precedence is OpenAI API artifacts, then OpenAPI, then MCP JSON, then SDK static extraction.

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

## Severity Overrides

Teams can re-rank built-in checks without forking the scanner:

```yaml
checks:
  severity_overrides:
    SHIP-DOC-INJECTION-RISK: low
    SHIP-AUTH-MISSING-SCOPE: critical
```

For compatibility with early design-partner manifests, top-level `check_severity_overrides` is also accepted and is applied after `checks.severity_overrides`. Prefer `checks.severity_overrides`; the top-level alias is legacy compatibility.

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
