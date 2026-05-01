# Check Catalog

Agents Shipgate checks are deterministic static checks. They do not certify safety, run agents, call tools, call LLMs, or verify runtime routing.

## Severity Contract

- `critical`: strict CI exits `20` unless the finding is explicitly suppressed with a reason.
- `high`: requires human review but does not fail CI by default.
- `medium`: review during release hardening.
- `low` and `info`: informational.

Only unsuppressed `critical` findings block strict mode. Suppressed findings remain in JSON with `suppressed: true` and are excluded from active severity counts.

## Evidence Coverage

- `static`: all enumerated tools came from high-confidence static sources.
- `mixed`: at least one enumerated tool came from lower-confidence enrichment, such as SDK AST extraction.

Suppressions do not change evidence coverage.

## Baselines

v0.2 adds local baseline gating. `agents-shipgate baseline save` writes active,
unsuppressed findings to `.agents-shipgate/baseline.json`. A later
`agents-shipgate scan --baseline .agents-shipgate/baseline.json --ci-mode strict`
marks findings as `matched` or `new` and fails only on new findings that match
the active fail policy. Resolved baseline findings are counted in the report
baseline summary and do not fail CI.

## Checks

| Check ID | Severity | Meaning |
| --- | --- | --- |
| `SHIP-INVENTORY-NOT-ENUMERABLE` | high | No tool surface could be enumerated from the manifest inputs. |
| `SHIP-INVENTORY-WILDCARD-TOOLS` | high | A source exposes wildcard/all tools instead of an explicit allowlist. |
| `SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE` | medium | The normalized tool count exceeds the MVP review threshold. |
| `SHIP-DOC-MISSING-DESCRIPTION` | medium | A tool has no description or a description too short for reliable review. |
| `SHIP-DOC-INJECTION-RISK` | medium/high | A tool description contains instruction-override style language. High only when multiple patterns match on a write/high-risk tool. |
| `SHIP-DOC-SECRET-IN-DESCRIPTION` | medium/high | A tool description contains a secret-like token or credential value. High only when multiple patterns match on a write/high-risk tool. |
| `SHIP-SCHEMA-BROAD-FREE-TEXT` | high | A write/action-like tool accepts broad `action`, `body`, `command`, `updates`, or similar free-form input. |
| `SHIP-SCHEMA-MISSING-BOUNDS` | high | A risky numeric parameter such as `amount`, `count`, or `quantity` lacks a maximum. |
| `SHIP-SCHEMA-FREEFORM-OUTPUT` | medium | A tool returns free-form string output that may later be placed in model context. |
| `SHIP-AUTH-MISSING-SCOPE` | high | A write-like tool has no declared auth scope metadata. |
| `SHIP-AUTH-MANIFEST-BROAD-SCOPE` | high | The manifest declares broad scopes such as `*`, `admin`, or `service:*`. |
| `SHIP-AUTH-TOOL-BROAD-SCOPE` | high | A tool declares broad scopes such as `*`, `admin`, or `service:*`. |
| `SHIP-AUTH-SCOPE-COVERAGE-MISSING` | high | A tool requires scopes that are not covered by `permissions.scopes`. |
| `SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE` | high | A write-capable tool contradicts a read-only declared purpose. |
| `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` | high | A tool appears to overlap with a manifest `prohibited_actions` entry. |
| `SHIP-POLICY-APPROVAL-MISSING` | critical | A high-risk tool lacks a manifest approval policy. |
| `SHIP-POLICY-CONFIRMATION-MISSING` | high | A destructive, external-write, or customer-communication tool lacks a confirmation policy. |
| `SHIP-SIDEFX-IDEMPOTENCY-MISSING` | critical/high | A risky write tool lacks idempotency evidence. Critical only when retry behavior is known. |
| `SHIP-API-FUNCTION-SCHEMA-STRICTNESS` | high/medium | An OpenAI API function schema is missing strictness, required fields, or bounded risky fields. |
| `SHIP-API-STRUCTURED-OUTPUT-READINESS` | high/medium | An OpenAI API response format is missing or too broad for downstream decisions. |
| `SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH` | high/medium | Prompt language contradicts the enabled OpenAI API tool surface or lacks approval/confirmation instructions. |
| `SHIP-API-RETRY-POLICY-MISSING` | medium | High-risk OpenAI API tools are enabled without retry policy metadata. |
| `SHIP-API-TIMEOUT-MISSING` | medium | High-risk OpenAI API tools are enabled without timeout metadata. |
| `SHIP-API-TEST-CASES-MISSING` | medium | High-risk OpenAI API tools are enabled without declared test cases. |
| `SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING` | medium | A high-risk OpenAI API tool lacks success/failure output modeling. |
| `SHIP-API-RETRY-WITHOUT-IDEMPOTENCY` | high | A risky OpenAI API write tool may be retried without idempotency evidence. |
| `SHIP-API-TRACE-APPROVAL-MISSING` | medium | A trace sample shows a policy-controlled tool call without approval. |
| `SHIP-API-TRACE-CONFIRMATION-MISSING` | medium | A trace sample shows a policy-controlled tool call without confirmation. |
| `SHIP-API-OPERATIONAL-READINESS` | medium | Deprecated v0.3 compatibility alias for the v0.4 atomic OpenAI API operational readiness checks. |
| `SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE` | high | A Google ADK toolset cannot be statically enumerated and no explicit inventory is declared. |
| `SHIP-ADK-MCP-TOOLSET-UNFILTERED` | high/medium | A Google ADK `McpToolset` has no static `tool_filter`. |
| `SHIP-ADK-FUNCTION-TOOL-METADATA-MISSING` | medium | A Google ADK function/config tool lacks static description or parameter metadata. |
| `SHIP-ADK-LONGRUNNING-CONTRACT-MISSING` | high | A Google ADK long-running tool lacks operation-id and status/progress contract evidence. |
| `SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING` | high | High-risk Google ADK tools lack callback/plugin or policy guardrail evidence. |
| `SHIP-ADK-EVAL-COVERAGE-MISSING` | medium | Production-like Google ADK inputs are present without declared eval files. |
| `SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE` | high | A LangChain/LangGraph tool surface cannot be statically enumerated and no explicit inventory is declared. |
| `SHIP-LANGCHAIN-FUNCTION-TOOL-METADATA-MISSING` | medium | A LangChain/LangGraph function tool lacks static description or parameter metadata. |
| `SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE` | high | A CrewAI tool surface cannot be statically enumerated and no explicit inventory is declared. |
| `SHIP-CREWAI-FUNCTION-TOOL-METADATA-MISSING` | medium | A CrewAI function/class tool lacks static description or parameter metadata. |
| `SHIP-MANIFEST-STALE-SUPPRESSION` | medium | A suppression references a missing check ID or missing tool. |
| `SHIP-MANIFEST-STALE-POLICY` | medium | An approval, confirmation, or idempotency policy references a missing tool. |
| `SHIP-MANIFEST-STALE-RISK-OVERRIDE` | medium | A risk override references a missing tool. |
| `SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING` | high | A high-risk production or production-like tool lacks owner metadata. |
| `SHIP-MANIFEST-UNUSED-SCOPE` | medium/high | `permissions.scopes` contains a scope unused by any loaded tool; broad unused scopes are high. |

## Check Details

### SHIP-INVENTORY-NOT-ENUMERABLE

The scanner could not enumerate any tools from required manifest inputs. Add a local MCP JSON or OpenAPI source before relying on the report.

### SHIP-INVENTORY-WILDCARD-TOOLS

A source exposes wildcard or all-tools access. Replace it with an explicit allowlist so review can reason about the actual release surface.

### SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE

The normalized tool count exceeds the MVP review threshold. Split or reduce the surface when the report becomes too broad to review.

### SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE

A production target depends on lower-confidence extraction, such as SDK AST enrichment. Declare the tools through manifest, MCP, or OpenAPI inputs.

### SHIP-DOC-MISSING-DESCRIPTION

A tool has no description or a description too short for reliable review. Add a concise capability description.

### SHIP-DOC-INJECTION-RISK

A tool description contains instruction-override-like language. Rewrite it as neutral metadata.
Purely heuristic matches default to `medium`; multiple matches on write/high-risk tools are `high`.

### SHIP-DOC-SECRET-IN-DESCRIPTION

A tool description contains a secret-like token or credential value. Remove it and rotate the exposed secret.
Purely heuristic matches default to `medium`; multiple matches on write/high-risk tools are `high`.

### SHIP-SCHEMA-BROAD-FREE-TEXT

A write/action-like tool accepts broad free-form input. Constrain the field with structured schema or enums.

### SHIP-SCHEMA-MISSING-BOUNDS

A risky numeric parameter lacks a maximum. Add a maximum or equivalent policy limit.

### SHIP-SCHEMA-FREEFORM-OUTPUT

A tool returns free-form string output that may later be placed in model context. Prefer structured output for model-consumed tool results.

### SHIP-AUTH-MISSING-SCOPE

A write or sensitive-data tool has no auth scope metadata. Declare scopes in OpenAPI, MCP, or manifest metadata.

### SHIP-AUTH-MANIFEST-BROAD-SCOPE

The manifest declares broad permission scopes such as wildcard or admin scopes. Replace them with operation-specific scopes.

### SHIP-AUTH-TOOL-BROAD-SCOPE

A tool declares broad auth scopes. Use narrower tool scopes where possible.

### SHIP-AUTH-SCOPE-COVERAGE-MISSING

A tool requires scopes that are not covered by `permissions.scopes`. Reconcile the manifest with the tool requirements.

### SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE

A write-capable tool contradicts a read-only declared purpose. Remove the tool or update the declared release scope.

### SHIP-SCOPE-PROHIBITED-TOOL-PRESENT

A tool appears to overlap with a manifest `prohibited_actions` entry. Remove or narrow the tool, or revise policy/scope text.

### SHIP-POLICY-APPROVAL-MISSING

A high-risk tool lacks a declared approval policy. Add an approval policy or remove the tool from the release.

### SHIP-POLICY-CONFIRMATION-MISSING

A destructive, external-write, or customer-communication tool lacks a confirmation policy. Add confirmation policy or remove the tool.

### SHIP-SIDEFX-IDEMPOTENCY-MISSING

A risky write tool lacks idempotency evidence. Add an idempotency key, idempotent annotation, or declared idempotency policy.

### SHIP-API-FUNCTION-SCHEMA-STRICTNESS

An OpenAI API function schema is not strict enough for reliable tool calls. The check flags missing `strict: true`, missing object parameters, `additionalProperties` not set to `false`, properties omitted from `required`, broad free-text action fields, and risky numeric fields without bounds or enums.

### SHIP-API-STRUCTURED-OUTPUT-READINESS

An OpenAI API response format is missing or under-specified. The check flags missing response schemas for high-risk API tools, broad response objects, decision/status fields without enums, missing `refusal` / `needs_review` / `error` modeling, and missing `downstream_critical_fields`.

### SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH

Prompt files contradict the enabled API tool surface. The check flags prompts that say "advise only" or "read-only" while write/high-risk tools are enabled, and high-risk tools whose prompts do not mention approval and confirmation expectations.

### OpenAI API Operational Readiness Checks

v0.4 splits the former `SHIP-API-OPERATIONAL-READINESS` bundle into atomic
check IDs so suppressions, severity overrides, SARIF rules, and baselines can
target one missing contract at a time. The split checks use `model_config`,
`policy_rules`, simple test cases, and trace samples to flag missing retry
policy, missing timeouts, missing test cases, non-idempotent high-risk tools
with retry evidence, missing success/failure tool-output modeling, and trace
samples that show required approval or confirmation missing.

The old bundled check ID remains as a deprecated compatibility alias through at
least one minor release. v0.4 does not emit new findings with
`SHIP-API-OPERATIONAL-READINESS`, but existing suppressions, severity overrides,
baseline entries, `explain`, `list-checks`, and stale-suppression validation
continue to recognize it. New configs should use the specific v0.4 ID that
represents the condition.

### SHIP-API-OPERATIONAL-READINESS

Deprecated compatibility alias for the v0.3 OpenAI API operational readiness
bundle. Migrate suppressions, severity overrides, and baselines to the specific
v0.4 `SHIP-API-*` readiness checks when you touch the config.

### SHIP-API-RETRY-POLICY-MISSING

A high-risk OpenAI API tool flow runs without declared retry policy metadata.
Reviewers cannot reason about duplicate side effects when retry behavior is
unspecified. Declare `retry_policy` in `openai_api.policy_rules` or
`openai_api.model_config`.

### SHIP-API-TIMEOUT-MISSING

A high-risk OpenAI API tool flow runs without declared timeout metadata.
Without an explicit timeout, failure behavior and tool-call continuation
become ambiguous. Declare a tool-call timeout in policy rules or model
config.

### SHIP-API-TEST-CASES-MISSING

High-risk OpenAI API tools exist with no declared test cases. Tool-call flows
that approve refunds, send mail, or modify state should ship with simple test
cases as release evidence. Add cases under `openai_api.test_cases`.

### SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING

A high-risk OpenAI API tool lacks declared success/failure output modeling.
Reviewers depend on `success_fields` and `failure_fields` to reason about
downstream failure handling. Declare them in policy rules.

### SHIP-API-RETRY-WITHOUT-IDEMPOTENCY

A retry policy is declared and a risky write tool lacks idempotency evidence.
Retries against non-idempotent writes can duplicate financial, destructive, or
external side effects. Either add idempotency evidence or remove the retry
policy for this tool.

### SHIP-API-TRACE-APPROVAL-MISSING

A trace sample shows a policy-controlled tool call with `approved: false` for
a tool that has approval policy evidence elsewhere in the manifest. Implement
the runtime approval gate; **do not edit the trace recording** to flip
`approved` — that patches the evidence, not the agent's behavior.

### SHIP-API-TRACE-CONFIRMATION-MISSING

A trace sample shows a policy-controlled tool call with `confirmed: false`
for a tool that has confirmation policy evidence. Implement the runtime
confirmation gate; **do not edit the trace recording** to flip `confirmed`
— same anti-pattern as the approval-missing finding above.

### SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE

A Google ADK `OpenAPIToolset`, `McpToolset`, or dynamic tools expression could
not be enumerated statically. Provide explicit local OpenAPI, MCP, or ADK tool
inventory inputs before relying on the release report.

### SHIP-ADK-MCP-TOOLSET-UNFILTERED

An ADK `McpToolset` has no static `tool_filter`. Add a narrow filter and an
explicit inventory file so reviewers can see the intended runtime surface.

### SHIP-ADK-FUNCTION-TOOL-METADATA-MISSING

An ADK function or Agent Config tool reference lacks description or parameter
metadata. Add docstrings, type annotations, or explicit local inventory
metadata.

### SHIP-ADK-LONGRUNNING-CONTRACT-MISSING

An ADK `LongRunningFunctionTool` lacks static evidence for operation id and
status/progress fields. Google-style `name` plus `done`, `state`, `phase`,
`metadata`, or `result` fields count as contract evidence; tools may also carry
`annotations.long_running_contract: true` in explicit inventory metadata.
Document the handoff and completion contract before promotion.

### SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING

High-risk ADK tools are present without static callback/plugin or manifest
policy evidence. ADK callbacks and plugins count only as static evidence of
intent; they are not proof that runtime enforcement works.

### SHIP-ADK-EVAL-COVERAGE-MISSING

Google ADK inputs target `production_like` or `production` without declared eval
files. Add eval artifacts that cover expected responses and tool-use
trajectories.

### SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE

A LangChain/LangGraph tool list, binding, or graph node could not be enumerated
statically. Provide an explicit local inventory when tools are produced by
factories, comprehensions, loop-built lists, unresolved imports, or other
runtime-only code. This ID uses `TOOL-SURFACE` instead of ADK's `TOOLSET`
because LangChain exposes ad hoc tool lists and model/graph bindings rather
than a consistent toolset abstraction.

### SHIP-LANGCHAIN-FUNCTION-TOOL-METADATA-MISSING

A LangChain/LangGraph `@tool` function or `StructuredTool.from_function(...)`
surface lacks a static description or parameter metadata. Add docstrings,
function annotations, or same-file Pydantic `args_schema` metadata.

### SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE

A CrewAI agent or crew tool surface could not be enumerated statically. Provide
an explicit local inventory when tools are produced by factories,
comprehensions, loop-built lists, unresolved imports, or other runtime-only
code. This ID uses `TOOL-SURFACE` instead of ADK's `TOOLSET` because CrewAI
agents bind ad hoc tool lists rather than a consistent toolset abstraction.

### SHIP-CREWAI-FUNCTION-TOOL-METADATA-MISSING

A CrewAI `@tool` function or `BaseTool` subclass lacks a static description or
parameter metadata. Add descriptions, `_run` annotations, or same-file Pydantic
`args_schema` metadata.

### SHIP-MANIFEST-STALE-SUPPRESSION

A suppression references an unknown check ID or a tool that is not loaded in the
current scan. Remove stale suppressions so reviewers can trust the suppression
list as current release intent.

### SHIP-MANIFEST-STALE-POLICY

A policy entry references a tool that is not loaded. Remove or update stale
approval, confirmation, or idempotency policies so release policy matches the
actual tool surface.

### SHIP-MANIFEST-STALE-RISK-OVERRIDE

`risk_overrides.tools` references a tool that is not loaded. Remove stale
overrides or update them to the current tool names.

### SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING

A high-risk tool in `production_like` or `production` has no owner metadata.
Declare an owner in the tool source or `risk_overrides.tools` so reviewers know
who is accountable for remediation.

### SHIP-MANIFEST-UNUSED-SCOPE

`permissions.scopes` includes a scope not required by any loaded tool. Remove
unused scopes or add tool metadata showing why the permission is needed. Broad
unused write/admin scopes are `high`; other unused scopes are `medium`.

## Risk Tags

Risk tags are hints, not findings by themselves. Checks consume tags with confidence thresholds.

Common tags:

- `read_only`
- `write`
- `destructive`
- `external_write`
- `financial_action`
- `customer_communication`
- `sensitive_data_access`
- `infrastructure_change`
- `code_execution`

Manual `risk_overrides` in `shipgate.yaml` are treated as high-confidence evidence. Use `remove_tags` to subtract heuristic tags that are known to be wrong for a specific tool.

## Listing Checks

Use the CLI to inspect the built-in catalog:

```bash
agents-shipgate list-checks
agents-shipgate list-checks --json
agents-shipgate explain SHIP-POLICY-APPROVAL-MISSING
```

Third-party packages can register checks through the `agents_shipgate.checks` Python entry-point group. Plugins are disabled by default because loading them imports third-party Python modules. Set `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` to opt in, or pass `--no-plugins` to force them off for a scan or catalog command. Reports include `loaded_plugins` provenance for every third-party check entry point that ran. A plugin check should expose a callable with the same `ScanContext -> list[Finding]` shape as built-ins and may attach `AGENTS_SHIPGATE_METADATA` as either a `CheckMetadata` instance or a compatible dictionary.

## Declarative Policy Packs

v0.4 adds local YAML policy packs for organization-specific release rules.
Policy packs are static data and are safe to enable by default when declared in
`checks.policy_packs` or passed with `scan --policy-pack`. External rule IDs
must use a non-`SHIP-*` namespace such as `ORG-*`; `SHIP-*` is reserved for
built-in checks. Pack findings behave like built-ins for suppressions, severity
overrides, baselines, Markdown, JSON, and SARIF. Python plugins remain a
separate opt-in extension mechanism.

## OpenAI Agents SDK Static Extraction

SDK extraction is optional enrichment. Agents Shipgate detects Python functions decorated directly with `@function_tool`, `@function_tool(...)`, `@agents.function_tool`, `@openai_agents.function_tool`, or simple import aliases such as `from agents import function_tool as ft`, for example:

```python
@function_tool
def search_customer(customer_id: str) -> str:
    ...
```

The static extractor does not execute user code and intentionally does not detect dynamic wrappers, factory-created tools, `Tool.from_fn()` style objects, runtime imports, or dynamic tool lists. Declare those tools through MCP/OpenAPI inputs or manifest metadata.

## Google ADK Static Extraction

Google ADK extraction is optional static enrichment. Agents Shipgate detects
Python `Agent` / `LlmAgent` definitions, literal function tools,
`FunctionTool`, `LongRunningFunctionTool`, `OpenAPIToolset`, `McpToolset`,
callbacks, plugins, sub-agents, and Agent Config YAML references where those
values are statically knowable.

The ADK extractor does not import user modules, run `adk`, connect to MCP
servers, fetch OpenAPI specs over the network, call tools, or call models.
Dynamic ADK toolsets produce source warnings and one ADK finding per unresolved
toolset unless explicit local MCP/OpenAPI/tool inventory inputs are provided.

## LangChain And CrewAI Static Extraction

LangChain/LangGraph and CrewAI extraction are optional static enrichment.
Agents Shipgate detects supported Python tool definitions, wrappers, agent
bindings, and local inventory files where those values are statically knowable.
CrewAI `BaseTool` class metadata may use literal strings or Pydantic-style
`Field(default="...")` assignments for `name` and `description`.

The extractors do not import user modules, import framework packages, run
agents, run graphs, run crews, connect to MCP servers, fetch specs over the
network, call tools, call models, or execute framework subprocesses. Dynamic
tool surfaces produce source warnings and framework findings unless explicit
local tool inventory inputs are provided. CrewAI prebuilt `crewai_tools.*Tool()`
references are emitted as low-confidence stubs and warnings; they do not by
themselves produce the dynamic-tools finding.
