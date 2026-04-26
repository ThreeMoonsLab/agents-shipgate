# Troubleshooting

## `Config file not found: shipgate.yaml`

Create a starter manifest:

```bash
agents-shipgate init --workspace . --write
```

Then inspect the sources before running checks:

```bash
agents-shipgate doctor --config shipgate.yaml
```

## `doctor` Shows Zero Tools

Common causes:

- `tool_sources[].path` points at the wrong file.
- The MCP export does not contain a `tools` array.
- The OpenAPI document has no `paths` object.
- The source is marked `optional: true` and failed to parse.

Run with verbose logs:

```bash
AGENTS_SHIPGATE_LOG_FORMAT=json agents-shipgate doctor --config shipgate.yaml --verbose
```

## The SDK Extractor Finds Nothing

The OpenAI Agents SDK extractor is AST-only. It recognizes direct `function_tool` decorators and simple import aliases such as:

```python
from agents import function_tool as ft

@ft
def lookup_customer(customer_id: str) -> str:
    ...
```

It intentionally does not execute imports, factories, dynamic wrappers, `Tool.from_fn()` calls, or dynamic tool lists. Declare those tools through MCP/OpenAPI inputs or manifest metadata.

## A Finding Is Intentional

Suppress it with a reason:

```yaml
checks:
  ignore:
    - check_id: SHIP-SCHEMA-BROAD-FREE-TEXT
      tool: support.search_kb
      reason: "Search query intentionally accepts free text."
```

Suppressed findings remain in JSON with `suppressed: true` and are excluded from active severity counts.

## A Risk Tag Is Wrong

Use `risk_overrides` to add or remove tags:

```yaml
risk_overrides:
  tools:
    refund_status_lookup:
      tags: ["read_only"]
      remove_tags: ["financial_action"]
      reason: "This endpoint only reads refund status."
```
