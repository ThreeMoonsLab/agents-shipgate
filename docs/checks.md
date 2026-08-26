# Check Catalog

Agents Shipgate checks are deterministic static checks. They do not certify safety, run agents, call tools, call LLMs, or verify runtime routing.

## Severity Contract

- `critical`: strict CI exits `20` unless the finding is explicitly suppressed
  with a reason (suppression-immune checks remain active).
- `high`: requires human review but does not fail CI by default.
- `medium`: review during release hardening.
- `low` and `info`: informational.

Among Findings, only unsuppressed `critical` findings block strict mode by
default. Independently, report v0.29 semantic `insufficient_evidence` also
exits `20` in strict mode; semantic gaps are not Findings and cannot be
suppressed. Suppressed Findings remain in JSON with `suppressed: true` and are
excluded from active severity counts.

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
| `SHIP-ACTION-UNDECLARED` | high | A loaded tool lacks explicit action-surface metadata when explicit actions are required. |
| `SHIP-ACTION-POLICY-VIOLATION` | high | A user-declared action-surface policy, or a selected control pack's obligation, is not satisfied. |
| `SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING` | critical | A newly added financial write action lacks approval, audit, or idempotency controls. |
| `SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING` | critical | A newly added destructive action lacks approval or rollback controls. |
| `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING` | high | A newly added external communication action lacks audit evidence. |
| `SHIP-ACTION-WILDCARD-SCOPE` | critical | An action declares or expands into a wildcard/admin-like scope. |
| `SHIP-ACTION-EFFECT-ESCALATED` | critical | An action effect escalated compared with the base surface. |
| `SHIP-ACTION-EFFECT-DOWNGRADE-DECLARED` | high | An action declaration weakens the effect inferred from the loaded tool surface. |
| `SHIP-ACTION-CONTROL-DOWNGRADE` | high | An action declaration weakens an inherited approval or safeguard control. |
| `SHIP-ACTION-APPROVAL-REMOVED` | critical | An existing action approval policy was removed. |
| `SHIP-ACTION-SAFEGUARD-REMOVED` | high | An existing action safeguard was removed. |
| `SHIP-EVIDENCE-APPROVAL-TRACE-MISSING` | high | Local HITL approval trace evidence is missing or incomplete for an approval-required tool. |
| `SHIP-EVIDENCE-OVERRIDE-REASON-MISSING` | high | Local HITL override reason evidence is missing or incomplete. |
| `SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING` | high | Local high-risk auto-approval exclusion evidence is missing or incomplete. |
| `SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING` | high | Local HITL promotion criteria evidence is missing or incomplete. |
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
| `SHIP-CODEX-PLUGIN-METADATA-MISSING` | medium | A Codex plugin package has incomplete or ambiguous identity metadata. |
| `SHIP-CODEX-PLUGIN-COMPONENT-PATH-MISSING` | high | A declared Codex plugin component path is missing or outside the package/workspace. |
| `SHIP-CODEX-PLUGIN-MARKETPLACE-POLICY-MISSING` | medium | A Codex plugin marketplace entry lacks installation/authentication policy metadata. |
| `SHIP-CODEX-PLUGIN-MCP-SERVER-NOT-ENUMERABLE` | high | A Codex plugin MCP server is declared without a local enumerable tool inventory. |
| `SHIP-CODEX-PLUGIN-APP-SURFACE-NOT-ENUMERABLE` | medium | A Codex plugin connector app surface is not statically enumerable from local metadata. |
| `SHIP-CODEX-PLUGIN-SKILL-METADATA-MISSING` | medium | A Codex plugin skill lacks unique name/description frontmatter. |
| `SHIP-CODEX-BOUNDARY-CONFIG-PARSE-FAILED` | medium | Codex project configuration could not be parsed. |
| `SHIP-CODEX-BOUNDARY-UNKNOWN-PERMISSION-KEY` | medium | Codex permissions contain an unknown high-risk key. |
| `SHIP-CODEX-BOUNDARY-NETWORK-WILDCARD` | high | Codex network permissions allow a wildcard domain. |
| `SHIP-CODEX-BOUNDARY-NETWORK-EXPANDED` | high | Codex network access expanded. |
| `SHIP-CODEX-BOUNDARY-DANGER-FULL-ACCESS` | critical | Codex full-access sandbox is selected. |
| `SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-WRITE` | critical | Codex auto-approves a write or destructive MCP/app tool. |
| `SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-UNKNOWN` | high | Codex auto-approves an MCP server whose tool surface is not statically enumerable. |
| `SHIP-CODEX-BOUNDARY-APP-AUTO-APPROVE` | high | Codex app connector tool approval changed to approve. |
| `SHIP-CODEX-BOUNDARY-AGENTS-SHIPGATE-REQUIREMENT-REMOVED` | medium | AGENTS.md removed a Shipgate requirement. |
| `SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED` | critical | Shipgate GitHub Action no longer invokes the gate. |
| `SHIP-CODEX-BOUNDARY-POLICY-WEAKENED` | critical | Codex boundary policy was weakened. |
| `SHIP-CODEX-BOUNDARY-HOOK-COMMAND-CHANGED` | high | A Codex executable hook changed. |
| `SHIP-CODEX-BOUNDARY-SKILL-COMMAND-CHANGED` | medium | A Codex skill gained command-bearing instructions. |
| `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED` | medium | A protected coding-agent surface lacks a safe static classification. |
| `SHIP-AGENT-BOUNDARY-EXPERIMENTAL-SURFACE-CHANGED` | high | An experimental coding-agent boundary surface changed. |
| `SHIP-AGENT-BOUNDARY-STATIC-REQUIREMENTS-CHANGED` | high | Static host requirements changed. |
| `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` | medium | Boundary input could not be evaluated completely. |
| `SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED` | medium | A coding-agent host configuration file could not be parsed. |
| `SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED` | high | A new MCP server was declared for the coding-agent host. |
| `SHIP-HOST-BOUNDARY-MCP-SERVER-CHANGED` | high | An existing MCP server declaration changed its command, URL, args, or env keys. |
| `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW` | critical | A Claude Code allow rule grants a wildcard tool surface. |
| `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` | high | The Claude Code permission allowlist expanded. |
| `SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED` | high | A Claude Code permission deny rule was removed. |
| `SHIP-HOST-BOUNDARY-HOOK-CHANGED` | high | Claude Code hooks changed. |
| `SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL` | critical | A GitHub workflow grants write-all permissions. |
| `SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED` | high | GitHub workflow permissions expanded. |
| `SHIP-HOST-BOUNDARY-PULL-REQUEST-TARGET-ADDED` | critical | A GitHub workflow gained a pull_request_target trigger. |
| `LINT-SPEC-002` | high | A skill has invalid YAML frontmatter. |
| `LINT-SPEC-003` | high | A skill is missing required `name` frontmatter. |
| `LINT-SPEC-004` | high | A skill is missing required `description` frontmatter. |
| `LINT-DESC-001` | high | A skill description is too vague to route reliably. |
| `LINT-DESC-003` | medium | A skill description is overbroad and may false-trigger. |
| `LINT-BODY-001` | medium | A skill body lacks a step-by-step procedure. |
| `LINT-BODY-003` | medium | A skill body lacks an output contract. |
| `LINT-BODY-004` | medium | A skill body lacks verification criteria. |
| `LINT-SCRIPT-001` | medium | A skill script lacks documented `--help` usage. |
| `LINT-SCRIPT-004` | medium | A stateful skill script lacks dry-run support. |
| `SEC-PI-001` | critical | A skill artifact contains instruction override language. |
| `SEC-PI-003` | high | A skill artifact tells the agent to hide behavior. |
| `SEC-SECRET-001` | critical | A skill artifact contains a hardcoded secret-like value. |
| `SEC-SECRET-003` | high | A skill artifact instructs credential or secret-file access. |
| `SEC-SCRIPT-001` | critical | Remote content is piped to a shell or interpreter. |
| `SEC-SCRIPT-002` | critical | A destructive or stateful command lacks guardrails. |
| `SEC-REMOTE-001` | medium | A skill fetches remote instruction content. |
| `SEC-REMOTE-002` | critical | Remote content is fetched and executed. |
| `SEC-TOOL-001` | high | A skill pre-approves shell or bash without justification. |
| `SEC-FLOW-004` | medium | A skill lacks data and instruction separation guidance. |
| `SEC-PROV-001` | high | A third-party skill lacks provenance metadata. |
| `SEC-MISMATCH-001` | high | A skill declares read-only behavior but bundled scripts mutate state. |
| `SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE` | high | An n8n tool surface uses runtime, unresolved, wildcard, or uninventoried custom exposure. |
| `SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED` | high/medium | An n8n MCP Client Tool exposes `All` or `All Except` tools without an explicit inventory. |
| `SHIP-N8N-AI-TOOL-METADATA-MISSING` | medium | An n8n AI-exposed tool lacks static description or parameter metadata. |
| `SHIP-N8N-CREDENTIAL-EVIDENCE-MISSING` | high | Production-like n8n workflows reference credentials without declared credential stubs. |
| `SHIP-N8N-EVAL-COVERAGE-MISSING` | medium | Production-like n8n workflows are present without declared eval files. |
| `SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER` | high | n8n workflow JSON contains a secret-like value; evidence is redacted. |
| `SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE` | high | A Conductor OSS workflow uses a dynamic or unresolved tool-capability surface. |
| `SHIP-MANIFEST-STALE-SUPPRESSION` | medium | A suppression references a missing check ID or missing tool. |
| `SHIP-MANIFEST-STALE-POLICY` | medium | An approval, confirmation, or idempotency policy references a missing tool. |
| `SHIP-MANIFEST-STALE-RISK-OVERRIDE` | medium | A risk override references a missing tool. |
| `SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING` | high | A high-risk production or production-like tool lacks owner metadata. |
| `SHIP-MANIFEST-UNUSED-SCOPE` | medium/high | `permissions.scopes` contains a scope unused by any loaded tool; broad unused scopes are high. |
| `SHIP-VERIFY-TRUST-ROOT-TOUCHED` | medium | A PR changed a release trust-root file; emitted only when a verification context (changed files) is supplied. |
| `SHIP-VERIFY-POLICY-WEAKENED` | high | Base-vs-head effective policy weakened (CI mode downgraded, fail-on loosened, a severity override lowered across a tier, or the control pack moved to one that requires less). |
| `SHIP-VERIFY-POLICY-BASE-ABSENT` | medium | A policy or manifest trust root changed with no base policy snapshot to compare against (first adoption, or no base report); routed to human review without a weakening claim. |
| `SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` | high | The PR broadens what the gate forgives — a new suppression, a widened waiver scope, or a larger accepted-debt baseline — versus the base. |
| `SHIP-VERIFY-CI-GATE-REMOVED` | critical | The PR deletes the Shipgate CI workflow from an opted-in repo, which would stop the release gate from running. |
| `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED` | medium | The PR edits agent-instruction trust roots and weakening cannot be statically disproven; routed to human review. |
| `SHIP-VERIFY-TRIGGER-CATALOG-DRIFT` | medium | The PR changes the trigger catalog that decides when Shipgate runs; routed to human review to rule out gate evasion. |
| `SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED` | critical | The PR removes or broadens a dynamically-loaded toolkit's least-privilege configuration bound (e.g. a `stripe_agent_toolkit` allowlist), silently expanding the toolkit surface; blocks rather than degrading to insufficient_evidence. |
| `SHIP-CAP-CONFIG-BINDING-REMOVED` | high | The PR removes the config binding from a dynamic toolkit factory present on both sides of the diff, so the toolkit may fall back to its everything-enabled defaults; routed to human review instead of silent insufficient_evidence parity. |
| `SHIP-CAP-CONFIG-BINDING-CHANGED` | medium | The PR changes the config file that binds a dynamic toolkit's authority; static analysis cannot diff the effective tool list, so the config delta is routed to review. |

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

### SHIP-SCOPE-TOOLKIT-UNBOUNDED

A recognized agent-toolkit constructor (e.g. `stripe_agent_toolkit`) is mounted with no `configuration` allowlist, so the full toolkit surface — which static extraction cannot enumerate — is granted. Routes to human review instead of passing silently. Pass an explicit `configuration` allowlist (resource:verb actions) to bound the mounted tools.

### SHIP-POLICY-APPROVAL-MISSING

A high-risk tool lacks a declared approval policy. Add an approval policy or remove the tool from the release.

### SHIP-POLICY-CONFIRMATION-MISSING

A destructive, external-write, or customer-communication tool lacks a confirmation policy. Add confirmation policy or remove the tool.

### SHIP-ACTION-UNDECLARED

`action_surface.require_explicit_actions` is true, but a loaded tool has no
matching `action_surface.actions[]` declaration. Add action metadata for the
tool or disable the explicit-action requirement.

### SHIP-ACTION-POLICY-VIOLATION

A user-declared `action_surface.policies[]` rule matched an action, and one or
more required dot-path values were absent or different. Satisfy the policy
requirements or narrow/remove the action.

This id also carries two built-in rules, distinguished by
`evidence.policy_id`. `builtin-high-impact-approval` is the approval a
production operation or code execution requires. `control-pack:<pack>:<effect>`
is an obligation the manifest's selected control pack
(`policies.control_pack`) states about an effect with no control check of its
own — a plain write, a privileged read, or identity access. Both are mandatory
current-surface controls: a `checks.ignore` entry records the exception but
does not waive the blocker.

### SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING

A newly added action is classified as `financial_write` and is missing
`approval.required`, `safeguards.audit_log`, or `safeguards.idempotency`.
Declare the required controls before releasing the action.

### SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING

A newly added destructive action is missing `approval.required` or
`safeguards.rollback`. Declare the approval and rollback controls, or remove
the destructive action from the release surface.

### SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING

A newly added external communication action lacks `safeguards.audit_log`.
Declare audit evidence so reviewers can trace outbound side effects.

### SHIP-ACTION-WILDCARD-SCOPE

An added action declares a broad scope, or a modified action expands into a
broad scope such as wildcard/admin access. Replace it with operation-specific
scopes.

### SHIP-ACTION-EFFECT-ESCALATED

An action changed to a higher-risk effect, such as read to write or write to
destructive. Add reviewer approval for the escalation or reduce the effect.

### SHIP-ACTION-EFFECT-DOWNGRADE-DECLARED

An `action_surface.actions[]` declaration sets a lower-risk effect than
Shipgate inferred from the loaded tool metadata. Align the declared effect
with the inferred operation or remove the weaker declaration.

### SHIP-ACTION-CONTROL-DOWNGRADE

An `action_surface.actions[]` declaration sets an inherited approval or
safeguard control from `true` to `false`. Keep the inherited control enabled
or remove the weakening declaration.

### SHIP-ACTION-APPROVAL-REMOVED

The base action required approval, but the current action no longer does.
Restore `approval.required` or document a reviewed override.

### SHIP-ACTION-SAFEGUARD-REMOVED

An existing action lost a safeguard such as audit logging, idempotency,
rollback, or dry-run support. Restore the safeguard or document a reviewed
override.

### SHIP-EVIDENCE-APPROVAL-TRACE-MISSING

`validation.required_evidence.approval_trace_required` is true, but local
validation evidence does not show `approved: true` for an approval-required
tool. Add local approval trace evidence produced by runtime middleware or
change the declared review posture. Agents Shipgate reads this evidence; it
does not produce or certify it. Missing local evidence does not prove the
runtime approval control is absent.

### SHIP-EVIDENCE-OVERRIDE-REASON-MISSING

`validation.required_evidence.override_reason_required` is true, but override
logs are absent, empty, or include normalized `override`, `bypass`, or
`auto_approve` events without a non-empty `reason`. Record reviewer-visible
reasons in the local override log. Missing local evidence does not prove the
runtime override control is absent.

### SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING

`validation.required_evidence.high_risk_auto_approval_exclusion_required` is
true, and a high-risk tool with declared approval policy is not listed under
`high_risk_auto_approval_exclusions`. This is separate from
`SHIP-POLICY-APPROVAL-MISSING`: it only fires after approval policy is already
declared, because it checks the local evidence that the tool is excluded from
auto-approval review posture. Missing local evidence does not prove the
runtime exclusion control is absent.

### SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING

`validation.target_review_posture` is `limited_auto_approval`, but local
promotion criteria evidence is missing or the canonical required-evidence
flags are not true in the manifest and criteria file. Finding evidence includes
`reason: file_missing` or `reason: flags_missing` so reviewers can distinguish
an absent local source from incomplete criteria. Missing local evidence does
not prove runtime controls are absent.

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

### SHIP-CODEX-PLUGIN-METADATA-MISSING

A Codex plugin package has incomplete or ambiguous identity metadata. Fill
`name`, `version`, and `description`; keep the plugin name aligned with the
package root; and avoid duplicate plugin names across scanned package roots.

### SHIP-CODEX-PLUGIN-COMPONENT-PATH-MISSING

A Codex plugin component path for skills, MCP servers, apps, or hooks could not
be loaded. Paths must resolve inside both the plugin package and the manifest
directory.

### SHIP-CODEX-PLUGIN-MARKETPLACE-POLICY-MISSING

A marketplace entry lacks `policy.installation`, `policy.authentication`, or
`category`. Add those fields so coding agents can see installation and
authentication posture before adoption.

### SHIP-CODEX-PLUGIN-MCP-SERVER-NOT-ENUMERABLE

A plugin declares an MCP server in `.mcp.json`, but Agents Shipgate does not
execute MCP commands to discover tools. Provide a local MCP tools inventory via
`codex_plugins.mcp_tool_inventories`.

### SHIP-CODEX-PLUGIN-APP-SURFACE-NOT-ENUMERABLE

A plugin declares a connector app in `.app.json`. Connector-backed capabilities
are externally mediated and are review items unless a local inventory or policy
artifact documents the effective surface.

### SHIP-CODEX-PLUGIN-SKILL-METADATA-MISSING

A `skills/**/SKILL.md` file is missing parseable `name` or `description`
frontmatter, or duplicates another skill name in the same plugin. Give every
skill a unique routing name and clear description.

### SHIP-CODEX-BOUNDARY-CONFIG-PARSE-FAILED

A changed repo-local `.codex/config.toml` or `.codex/hooks.json` cannot be
parsed. Fix the malformed config or have a human review the boundary change.

### SHIP-CODEX-BOUNDARY-UNKNOWN-PERMISSION-KEY

A changed `.codex/config.toml` contains an unknown key below a permissions
profile or permissions network table. Review the key before trusting the local
Codex boundary.

### SHIP-CODEX-BOUNDARY-NETWORK-WILDCARD

A changed Codex permission profile allows a wildcard domain. Replace wildcard
network access with explicit domains or get human approval.

### SHIP-CODEX-BOUNDARY-NETWORK-EXPANDED

Codex workspace-write network access or full network mode was enabled. Have a
human approve the expanded local execution boundary.

### SHIP-CODEX-BOUNDARY-DANGER-FULL-ACCESS

Codex `sandbox_mode` or `default_permissions` selects `danger-full-access`.
Use a narrower permission profile or get explicit human approval.

### SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-WRITE

A changed MCP or app tool approval mode is `approve` for a write or
destructive-looking tool. Do not auto-approve write or destructive tools.

### SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-UNKNOWN

Codex auto-approves an MCP server whose tool surface is not statically
enumerable. Enumerate MCP tools or keep the approval mode at `prompt`.

### SHIP-CODEX-BOUNDARY-APP-AUTO-APPROVE

A Codex app connector tool approval changed to `approve`. Review connector
approval changes before local automation.

### SHIP-CODEX-BOUNDARY-AGENTS-SHIPGATE-REQUIREMENT-REMOVED

`AGENTS.md` or `AGENTS.override.md` removed Shipgate command or requirement
text without adding a replacement. A human should confirm the agent
instructions were not weakened.

### SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED

The Shipgate GitHub Actions workflow was deleted or no longer contains a
Shipgate invocation. Restore the workflow or get human approval to remove it.

### SHIP-CODEX-BOUNDARY-POLICY-WEAKENED

The Codex boundary policy was deleted or downgraded. Restore the stricter
policy or get human approval before weakening the local boundary gate.

### SHIP-CODEX-BOUNDARY-HOOK-COMMAND-CHANGED

A changed Codex hooks source contains a command hook. Review executable hooks
before relying on them.

### SHIP-CODEX-BOUNDARY-SKILL-COMMAND-CHANGED

A changed `.agents/skills/**/SKILL.md` adds command-like text. Review
command-bearing skill changes before local automation.

### SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED

A recognized host, instruction, policy, state, or workflow surface changed
without a specialized safe classification. The change routes to human review at
PR time because absence of a risk finding is not evidence of non-broadening
behavior. In the local `shipgate check` loop this medium row is graded: unless
the path is a gate-governing trust root (manifest, policy, CI gate, or
`.agents-shipgate/` state), the coding agent may finish its turn with the
obligation carried in `pending_review[]` rather than stopping.

### SHIP-AGENT-BOUNDARY-EXPERIMENTAL-SURFACE-CHANGED

An adapter marked experimental observed a boundary change. Experimental
coverage cannot authorize coding-agent completion without human review.

### SHIP-AGENT-BOUNDARY-STATIC-REQUIREMENTS-CHANGED

The repository's `.codex/requirements.toml` changed. This is a reviewer-owned
host trust root for approvals, sandboxing, and network constraints.

### SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE

A relevant diff or file was truncated, malformed, unsafe to read, traversing,
binary, oversized, or otherwise unresolved. Supply coherent input and rerun.

### SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED

A changed `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`,
`.claude/settings(.local).json`, or `.github/workflows` file cannot be parsed,
or its content cannot be resolved from the diff. Fix the malformed host config
or have a human review the change.

### SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED

A changed MCP server declaration file adds a server key that did not exist
before. Have a human review the new MCP server before the agent can use it.

### SHIP-HOST-BOUNDARY-MCP-SERVER-CHANGED

An existing MCP server changed its `command`, `args`, `url`, `serverUrl`, or
`env` keys. Review the change — env var values never appear in evidence, key
names only.

### SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW

A changed `.claude/settings.json` or `.claude/settings.local.json` adds an
allow rule that is `*`, a bare tool name, or a wildcard-shaped rule such as
`Bash(*)`. Do not allow wildcard tool permissions; scope the rule to specific
commands.

### SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED

A changed Claude Code settings file adds a non-wildcard `permissions.allow`
entry. Have a human approve the new permission allow rule.

### SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED

A changed Claude Code settings file drops an entry from `permissions.deny`.
Have a human confirm the removed deny rule is no longer needed.

### SHIP-HOST-BOUNDARY-HOOK-CHANGED

A changed Claude Code settings file adds, modifies, or removes hook handlers.
Review executable hook changes before the agent relies on them.

### SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL

A changed `.github/workflows` file sets `permissions: write-all` at the top
level or for a job. Replace write-all with the minimal explicit permission
scopes.

### SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED

A changed `.github/workflows` file grants an explicit write scope that the
base did not grant, at the top level or per job. Have a human approve the
expanded workflow write permission.

### SHIP-HOST-BOUNDARY-PULL-REQUEST-TARGET-ADDED

A changed `.github/workflows` file adds `pull_request_target` to its triggers.
Review it carefully — `pull_request_target` runs with secrets on fork PRs.

### LINT-BODY-001

A `SKILL.md` body lacks an ordered Procedure, Steps, or Workflow section. Add
step-by-step instructions an agent can follow.

### LINT-BODY-003

A `SKILL.md` body does not define the expected output artifact, response shape,
or completion criteria. Add an Output section.

### LINT-BODY-004

A `SKILL.md` body lacks verification or acceptance criteria. Add checks the
agent can run or cite before reporting completion.

### LINT-DESC-001

A skill description is too short or generic to route reliably. Rewrite it with
concrete trigger conditions and domain-specific nouns.

### LINT-DESC-003

A skill description is broad enough to false-trigger. Narrow it to the specific
workflow, file type, or domain where the skill applies.

### LINT-SCRIPT-001

A bundled script lacks documented `--help` usage. Document safe invocation in
`SKILL.md` and make usage discoverable without interaction.

### LINT-SCRIPT-004

A bundled script appears stateful or destructive but lacks dry-run support. Add
`--dry-run` or document a non-mutating preview path.

### LINT-SPEC-002

`SKILL.md` frontmatter is not parseable YAML mapping data. Fix the frontmatter
before relying on skill discovery.

### LINT-SPEC-003

`SKILL.md` is missing required `name` frontmatter. Add a stable routing name.

### LINT-SPEC-004

`SKILL.md` is missing required `description` frontmatter. Add a clear trigger
description that tells agents when to load the skill.

### SEC-FLOW-004

A skill combines untrusted content with outbound or secret-access behavior but
lacks instruction/data separation guidance. State that untrusted content is data
and must not supply agent instructions.

### SEC-MISMATCH-001

A skill declares read-only or review-only behavior but bundled scripts mutate
files or external state. Update the declared purpose or remove the mutation.

### SEC-PI-001

A skill or related artifact tells agents to ignore or override higher-priority
instructions. Remove the instruction-override language.

### SEC-PI-003

A skill or related artifact tells agents to hide behavior from users, reviewers,
or logs. Remove concealment instructions.

### SEC-PROV-001

A skill marked as third-party lacks `metadata.shipgate.source` provenance. Add
source, source reference, owner, and review metadata.

### SEC-REMOTE-001

A skill fetches mutable remote prompt, instruction, or skill content at runtime.
Avoid remote instruction fetch or document source trust and pinning.

### SEC-REMOTE-002

A skill sources or executes content fetched from a remote URL. Remove the
runtime remote-code execution or pin, verify, and sandbox the content.

### SEC-SCRIPT-001

A skill artifact pipes remote content to a shell or interpreter. Vendor the
script or verify pinned content instead of executing mutable remote bytes.

### SEC-SCRIPT-002

A skill artifact contains destructive or stateful command patterns without
dry-run, confirmation, or path validation. Add guardrails or remove the command.

### SEC-SECRET-001

A skill artifact contains a hardcoded secret-like value. Remove it and rotate the
exposed credential; reports redact the value by kind and location.

### SEC-SECRET-003

A skill artifact instructs agents to read broad credential files or secret-bearing
environment variables. Replace with scoped placeholder guidance.

### SEC-TOOL-001

A skill pre-approves shell or bash-like tools without reviewed-script or sandbox
justification. Remove preapproval or document the review boundary.

### SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE

An n8n workflow uses a runtime expression in a tool name, an unresolved
Call-Workflow target, wildcard MCP Server/Client exposure, or an uninventoried
community/custom tool node. Provide a local n8n/MCP inventory or replace the
dynamic exposure with a static allowlist. This is high severity in every
environment because static release evidence cannot prove the actual tool
inventory.

### SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED

An n8n MCP Client Tool exposes `All` or `All Except` tools without a local
inventory. Select explicit MCP tools or provide a local MCP inventory for
release review. The severity is environment-sensitive because the selector is
easy to narrow before production, while production-like use increases blast
radius.

### SHIP-N8N-AI-TOOL-METADATA-MISSING

An n8n AI-exposed tool lacks a static description or parameter metadata. Add
tool descriptions, `$fromAI()` metadata, workflow input schemas, or explicit
inventory metadata.

### SHIP-N8N-CREDENTIAL-EVIDENCE-MISSING

Production-like n8n workflows reference credentials but no local credential
stubs are declared. Declare source-control credential stubs so reviewers can
see credential types without seeing secret values.

### SHIP-N8N-EVAL-COVERAGE-MISSING

n8n workflows target `production_like` or `production` without declared eval
files. Add eval artifacts that cover expected responses and tool-use
trajectories.

### SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER

An n8n workflow parameter, node note, `pinData` entry, or `staticData` entry
contains a secret-like value. Evidence includes only the source reference,
stable pointer, and secret kind; it never includes the matched secret value or
a verifier hash for that value.

### SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE

A Conductor OSS workflow uses a dynamic or missing MCP method/server, dynamic
LLM tool advertisement, runtime-generated task, or unresolved sub-workflow.
Make the target static and locally reviewable before release. The adapter does
not connect to Conductor, MCP servers, or model providers to fill the gap.

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

### SHIP-BASELINE-INTEGRITY-MISMATCH

Baseline file integrity check failed. Emitted when the baseline JSON has been
edited outside `agents-shipgate baseline save` (hash mismatch against the
audit log), when the audit log is missing or empty for a non-empty baseline,
when the audit log is malformed, when an entry's `provenance.run_id` is not
present in the audit log, or when an entry pre-dates the v0.5 provenance
contract. In
`baseline.integrity_mode: strict` the finding carries `blocks_release=true`
and `agents-shipgate baseline verify --strict` exits with code 6.
Re-run `agents-shipgate baseline save` to refresh the baseline alongside its
audit row; investigate the diff before accepting.

### SHIP-BASELINE-ENTRY-EXPIRED

A baseline entry's reviewer-set `provenance.expires` date is past today.
Renewable consent is a deliberate choice: accepted technical debt should
need re-review on a schedule, not a silent extension. Re-review the entry
and either remove it, fix the underlying finding, or extend
`provenance.expires` with a new `reason`.

### SHIP-BASELINE-ENTRY-STALE

A baseline entry no longer corresponds to an active finding or check ID.
Two sub-kinds, both `low` severity:

- `deprecated_check_id` — entry references an alias in `LEGACY_CHECK_ID_ALIASES`.
  Update the entry to the canonical replacement check IDs (re-running
  `baseline save` does not rewrite check IDs).
- `resolved_not_pruned` — entry matched no active scan finding. Re-run
  `agents-shipgate baseline save` to drop the entry from the baseline.

### SHIP-VERIFY-TRUST-ROOT-TOUCHED

A PR changed a file that defines the release gate's trust spine — the
manifest (`shipgate.yaml`), `.agents-shipgate/` state (baselines,
waivers), `policies/`, `prompts/`, the Shipgate CI gate
(`.github/workflows/agents-shipgate.yml`), agent instructions
(`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.cursor/rules/`,
`.agents/skills/`, `.codex/`), Codex plugin packages (`.codex-plugin/`),
or tool-surface declarations (`.app.json`, `.mcp.json`, `SKILL.md`).

This is Tier A trust-root protection: pure path/glob classification of
the changed files. It is the cheap half of the reward-hacking guard — a
coding agent told to make CI pass can weaken the gate instead of fixing
the readiness issue, so touching a trust root must require human review.
The finding fires only when a verification context (changed files) is
supplied (`agents-shipgate scan --changed-files ...` or, later, `verify`);
a plain `scan` emits nothing. It is one ordinary `Finding` at `medium`
severity routed through `release_decision` — never a second verdict.

### SHIP-VERIFY-POLICY-WEAKENED

Tier B trust-root protection: instead of classifying *which* files
changed, it compares the normalized effective-policy snapshot of the base
report (supplied via `--diff-from`) against the head manifest and fires
when the gate moved toward *less* review or *less* blocking — CI mode
downgraded (e.g. `strict` → `advisory`), the fail-on severity set lost a
tier, a check's severity override dropped across a tier boundary, or
`policies.control_pack` moved to a pack that requires *less* of some action
effect (`kind: control_pack_weakened`). The comparison is semantic, not a text
diff, so it is robust to reformatting.

The first three kinds answer "does the same finding still block?"; the control
pack answers "does the same action still produce the finding?", which is the
other way a gate gets weaker. A pack move is one changed line, so it is one
finding: `evidence.removed_controls` carries `{effect, controls}` for every
effect that lost something, and the sentence names a bounded prefix plus how
many it is not naming.

The claim is base-relative, so this check fires only when a base snapshot
exists to compare against. The no-base fail-safe is its own reason code,
`SHIP-VERIFY-POLICY-BASE-ABSENT`. Category `verify` (suppression-immune,
floor `high`); never a second verdict.

### SHIP-VERIFY-POLICY-BASE-ABSENT

The fail-safe for "there is no base policy to compare against". It fires in
verify mode when the PR touched a policy/manifest trust root
(`**/shipgate.yaml`, `**/policies/**`, `**/.agents-shipgate/**`, or the
configured manifest under any name) and no base effective-policy snapshot
was available — either because the base carries no Shipgate manifest at all
(a first adoption) or because no base report was obtainable (no
`--diff-from`, a pre-v0.22 base, or a base scan that produced none).

It makes no weakening claim in either direction, which is the point: on a
first adoption the base had no gate, so a weakening definitionally could not
have happened, and reporting one told every new adopter that their first PR
had loosened something. It is equally not a pass — a reward-hacker must not
be able to dodge review by breaking the base scan — so the change is routed
to a human at `medium` under this reason code instead.

Two evidence kinds separate the two conditions:

- `manifest_introduced` — git proves the base carries no manifest under any
  name and the diff removes none, so this PR adopts the gate. This is the
  one case that reports `verifier_summary.policy_weakened: false`.
- `base_snapshot_unavailable` — the comparison simply could not be made. The
  direction is unprovable, so `policy_weakened` stays raised; a
  rename-and-loosen diff cannot clear the alarm by hiding the base.

Category `verify` (suppression-immune, floor `medium`). Like the rest of the
family it requires a declared `human_ack` for the `policy` surface and is
routed through the one decision engine — never a second verdict.

### SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED

Tier B: detects a PR that broadens what the gate forgives — a new entry in
`checks.ignore`, a widened waiver scope (e.g. one tool widened to `*`), or
a larger accepted-debt baseline — by a base-vs-head superset comparison of
the effective-policy snapshot. Suppressing or baselining a finding instead
of fixing it is a classic reward hack; this makes the expansion
release-visible. Requires a base snapshot (touching the files alone is
already covered by `SHIP-VERIFY-TRUST-ROOT-TOUCHED`). Category `verify`,
floor `high`.

### SHIP-VERIFY-CI-GATE-REMOVED

Tier B: fires when, in verify mode, a Shipgate CI workflow path
(`.github/workflows/agents-shipgate.yml`/`.yaml`) appears in the changed
files **and** that file no longer exists on disk — i.e. the PR deleted the
gate. Detectable without a base snapshot. Emitted at `critical` (floor
`high`): removing CI enforcement from an opted-in repo is the strongest
weakening signal in the family.

### SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED

Tier B: agent-instruction files (`AGENTS.md`, `CLAUDE.md`, `.claude/`,
`.cursor/rules/`, `.agents/skills/`, `.codex/`, `SKILL.md`) tell coding
agents how to behave around the gate. Shipgate is static and makes no NLP
judgement, so it cannot prove semantic weakening from text — per Principle
3 ("prompts are not controls"), any verify-mode change to these trust
roots is routed to human review at `medium`. Deterministic on changed-file
membership; the human confirms no gate-protecting instruction was removed.

### SHIP-VERIFY-TRIGGER-CATALOG-DRIFT

Tier B: the trigger catalog (`docs/triggers.json` or an
`.agents-shipgate` trigger config) decides *when* Shipgate runs. Editing
it can carve out paths so the gate stops firing — a gate-evasion one level
up from suppressing findings. Fires on changed-file membership in verify
mode at `medium`; the human confirms the change does not create a path
that evades the release gate.

### SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED

Tier B: some agent toolkits load their tools through a runtime factory
(`*toolkit.get_tools()`) the static extractor cannot enumerate, so the only
statically-provable least-privilege signal is the *constructor's* permission
allowlist (`configuration={"actions": {...}}`). This check diffs that bound
base-vs-head and fires `critical` when the head removes it (mounting the full
toolkit surface — e.g. refund / cancel / dispute on a customer-support agent)
or adds permissions the base allowlist did not. Without it the opaque factory
would degrade the verdict to `insufficient_evidence` instead of `blocked`. A
coding agent cannot self-approve the broadening; a human must re-apply a
least-privilege configuration or explicitly approve the expanded surface. A
narrowing (bound added or tightened) emits nothing.

### SHIP-CAP-CONFIG-BINDING-REMOVED

Config-bound capability detection: a dynamic toolkit factory whose
authority-bearing constructor argument is *bound from a config read*
(`json`/`yaml`/`toml` load, `os.environ`, pydantic settings) hides its
effective tool surface from static enumeration on both sides of a verify
diff. When the same factory site appears on both sides and the head removes
the binding entirely, the toolkit may fall back to its everything-enabled
defaults — authority expands while looking like cleanup, and the generic
capability diff sees nothing. Fires `high` in verify mode and routes to
human review instead of silent `insufficient_evidence` parity. Fail-safe by
design: a head binding that is merely *unreadable* (`unknown`) never fires
this check — it degrades to a source warning; a head that moves to a
literal allowlist is the safe direction and emits nothing.

### SHIP-CAP-CONFIG-BINDING-CHANGED

Companion review item: the same factory site is config-bound on both sides
of the diff and the PR changes the bound config file itself (e.g. adding
`"refund"` to an actions list). Static analysis cannot diff the effective
tool list, so the config delta is routed to review at `medium` with the
inventory remedy. Fires only when the binding's literal config path matches
a changed file — env- or settings-bound factories never guess a match.

### SHIP-MCP-ENV-SECRET-PASSTHROUGH

Fires when a statically parsed MCP server passes through secret-like
environment variables, such as token, password, API key, or credential
names. Secret pass-through changes the credential boundary available to the
server, so Shipgate routes the change to human review without exposing raw
secret values in evidence.

### SHIP-MCP-AUTO-APPROVE-SIDE-EFFECT

Fires when an MCP tool classified as write, destructive, external,
financial, or production is configured with approval mode `approve`. This is
an explicit release blocker because auto-approved side-effecting MCP tools
can let an agent act outside the review boundary.

### SHIP-MCP-UNKNOWN-TOOL-SCHEMA

Fires for new or changed MCP capabilities whose static metadata cannot prove
the callable surface and side effect, including wildcard server exposure.
Provide an explicit local MCP inventory/schema or keep the server behind
human review. Unknown side effects route to review by default; they become
blockers only when paired with a separate blocking condition such as
auto-approved side-effecting access.

### SHIP-MCP-PERMISSION-EXPANDED

Fires when the MCP capability diff broadens scope, effect, risk tags,
controls, or accepted input schema. Narrowing and pure least-privilege
downgrades do not block by default.

### SHIP-MCP-READONLY-SERVER-ADDED

Fires as a low-severity warning when a new local documentation MCP server is
classified as read-only. It keeps benign expansion visible in review
artifacts while remaining non-blocking by default.

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

The JSON catalog includes `mvp_tier` for display and triage:

| `mvp_tier` | Meaning |
|---|---|
| `core` | Core Tool-Use Readiness MVP signal. |
| `adapter` | Framework or provider-specific readiness signal. |
| `evidence` | Validation, trace, or HITL evidence signal. |
| `lifecycle` | Baseline, diff, or action-surface evolution signal. |
| `hygiene` | Useful quality or maintenance signal, not core MVP positioning. |

`mvp_tier` never changes check execution, severity, fingerprints, baselines,
`release_decision`, or CI exit behavior.

Third-party packages can register checks through the `agents_shipgate.checks` Python entry-point group. Plugins are disabled by default because loading them imports third-party Python modules. Set `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` to opt in, or pass `--no-plugins` to force them off for a scan or catalog command. Reports include `loaded_plugins` provenance for every third-party check entry point Shipgate discovered — including ones that failed validation. A plugin check should expose a callable with the same `ScanContext -> list[Finding]` shape as built-ins and attach `AGENTS_SHIPGATE_METADATA` as either a `CheckMetadata` instance or a compatible dictionary. Adapter artifacts are available through `context.framework_artifacts` or `context.artifact("openai_api", OpenAIApiArtifacts)`. Legacy `context.*_artifacts` read-only properties remain available for v0.11 plugin compatibility, raise `TypeError` on artifact type mismatch, and are scheduled for removal in v0.12.

**Plugin validation (v0.17+; six gates v0.18+).** Shipgate runs six load-time gates against every entry point — load, signature, metadata, dynamic-default-not-supported (v0.18+), ID-collision, and floor-consistency — before letting it produce findings. Metadata may use either `id` or `check_id` as the identifier key (the alias is symmetric with `Finding.check_id`); both names map to `CheckMetadata.id`. The `dynamic_default_not_supported` gate (v0.18+) rejects plugins declaring `AGENTS_SHIPGATE_METADATA.dynamic_default=True`: plugins have no path to wire into `core/dynamic_defaults.py:dynamic_check_defaults`, so a swing check would never receive a manifest-effective default and would be silently bypassable. This gate runs **before** `_coerce_metadata` so a plugin declaring `dynamic_default=True` without `floor_severity` lands here under a precise status rather than being mis-classified as `bad_floor`. Plugins that fail validation surface in `loaded_plugins[]` with a non-`valid` `validation_status` and human-readable `validation_errors`, and they do not run. At runtime, findings whose `check_id` does not match the declared plugin metadata are dropped and recorded under `loaded_plugins[].runtime_errors` — a plugin cannot smuggle findings under another check ID. Default behavior is lenient (record failures, continue scanning). Pass `--strict-plugins` to exit non-zero (code 4) when any plugin has a non-`valid` status or non-empty `runtime_errors`. See [STABILITY.md § Trust-model invariants](../STABILITY.md#trust-model-invariants) and [STABILITY.md § Severity-override floor](../STABILITY.md#severity-override-floor) (for the dynamic-default contract) for the full contracts.

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

When `tool_sources[].path` points at a directory, the extractor scans immediate
`*.py` files in sorted order; it does not recurse into nested packages. The
static extractor does not execute user code and intentionally does not detect
dynamic wrappers, factory-created tools, `Tool.from_fn()` style objects, runtime
imports, or dynamic tool lists. Declare those tools through MCP/OpenAPI inputs or
manifest metadata.

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

## n8n Static Extraction

n8n extraction reads only local workflow JSON exports/source-control files and
optional local stubs or evidence artifacts declared under `n8n:`. It does not
call a live n8n instance, run `n8n`, execute workflows, decrypt credentials,
connect to MCP endpoints, execute code nodes, or fetch network resources.

The adapter enumerates AI Agent tool sub-nodes, MCP Client Tool selections,
MCP Server Trigger exposed tools, Call n8n Workflow Tool entrypoints, Custom
Code Tool nodes, HTTP Request Tool nodes, and explicit inventories when those
surfaces are statically visible. Workflow triggers such as Webhook and Chat
Trigger are recorded as ingress evidence, not as tools.

Inactive workflows (`active: false`) are recorded as workflow evidence but are
not normalized as live tool or ingress surfaces; their workflow JSON is still
scanned for secret-like values. Workflow tags, error-workflow settings, and
node execution controls such as retry/continue-on-fail are preserved as
review metadata when present.

Credential names, workflow/node names, code bodies, request bodies, headers,
pinned data, static data, node notes, variable values, execution payloads, and
detected secrets are redacted or omitted from reports. Credential types and
credential IDs may be preserved as local release evidence.

## Conductor OSS Static Extraction

Conductor OSS extraction reads only local `schemaVersion: 2` workflow JSON
declared through `tool_sources[].type: conductor`. The MCP-core v1 adapter
normalizes literal `CALL_MCP_TOOL.method` call sites, records
`LIST_MCP_TOOLS`, `LLM_CHAT_COMPLETE`, `HUMAN`, and sub-workflow facts, and
recursively traverses switch/decision, loop, and fork containers.

The adapter never starts Conductor, executes expressions or inline code,
imports workers, connects to MCP/model endpoints, or treats runtime discovery
as a local inventory. Dynamic MCP bindings and unresolved sub-workflows produce
the Conductor finding above. HTTP/custom-worker/A2A/provider-native execution
is recorded as an unsupported capability and source warning in v1, so partial
coverage cannot silently produce `passed`. A `HUMAN` task is structural pause
evidence only; it does not prove reviewer identity or approval.
