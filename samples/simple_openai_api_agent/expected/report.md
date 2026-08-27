# Agents Shipgate Report

Project: simple-openai-api-agent
Agent: api-refund-assistant
Target: production\_like

## Release Decision

Decision: blocked
Reason: 2 active findings block release.

Blockers (2):
- HIGH SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING — send\_customer\_email has external communication capability without required controls
- CRITICAL SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING — create\_refund has financial write capability without required controls

Review items (13):
- HIGH SHIP-AUTH-MISSING-SCOPE — send\_customer\_email lacks declared auth scopes
- HIGH SHIP-AUTH-MISSING-SCOPE — create\_refund lacks declared auth scopes
- HIGH SHIP-SIDEFX-IDEMPOTENCY-MISSING — create\_refund lacks idempotency evidence
- HIGH SHIP-API-FUNCTION-SCHEMA-STRICTNESS — send\_customer\_email function schema is not strict enough
- HIGH SHIP-API-FUNCTION-SCHEMA-STRICTNESS — create\_refund function schema is not strict enough
- MEDIUM SHIP-API-STRUCTURED-OUTPUT-READINESS — Response format schemas/refund\_decision.schema.json is under-specified
- MEDIUM SHIP-API-TIMEOUT-MISSING — OpenAI API flow lacks timeout metadata
- HIGH SHIP-API-RETRY-WITHOUT-IDEMPOTENCY — send\_customer\_email may be retried without idempotency evidence
- MEDIUM SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING — create\_refund lacks success/failure output modeling
- HIGH SHIP-API-RETRY-WITHOUT-IDEMPOTENCY — create\_refund may be retried without idempotency evidence
- MEDIUM SHIP-API-TRACE-APPROVAL-MISSING — Trace sample shows create\_refund without approval
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — send\_customer\_email is high-risk but has no owner
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — create\_refund is high-risk but has no owner

Evidence coverage: static (2/2 catalog tools reachable; 2 semantic review concern(s); 0/2 actions pass-eligible; human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 1
- High: 10
- Medium: 4
- Low: 0
- Suppressed: 0
- Status: Release blockers detected (legacy; see Release Decision above)

## Top Findings

15 findings across 3 subjects, most urgent first.

- create\_refund \[openai\_api\] \(at tools/openai-tools.json\#/tools/0\) — BLOCKS RELEASE \(1 critical, 5 high, 1 medium\)
  - critical SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING \(blocks release\) — missing: approval.required, safeguards.audit\_log, safeguards.idempotency
  - high SHIP-API-FUNCTION-SCHEMA-STRICTNESS — create\_refund function schema is not strict enough
    - Make create\_refund a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.
  - high SHIP-API-RETRY-WITHOUT-IDEMPOTENCY — create\_refund may be retried without idempotency evidence
    - Add idempotency evidence for create\_refund or avoid retrying this side effect.
  - high SHIP-AUTH-MISSING-SCOPE — create\_refund lacks declared auth scopes
    - Declare operation-specific auth scopes for create\_refund, or explicitly declare anonymous authority when the operation requires no credentials.
  - high SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — create\_refund is high-risk but has no owner
    - Declare an owner for each high-risk production tool in risk\_overrides.tools.
  - … and 2 more findings for this subject
- send\_customer\_email \[openai\_api\] \(at tools/openai-tools.json\#/tools/1\) — BLOCKS RELEASE \(5 high\)
  - high SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(blocks release\) — missing: safeguards.audit\_log, confirmation.required
  - high SHIP-API-FUNCTION-SCHEMA-STRICTNESS — send\_customer\_email function schema is not strict enough
    - Make send\_customer\_email a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.
  - high SHIP-API-RETRY-WITHOUT-IDEMPOTENCY — send\_customer\_email may be retried without idempotency evidence
    - Add idempotency evidence for send\_customer\_email or avoid retrying this side effect.
  - high SHIP-AUTH-MISSING-SCOPE — send\_customer\_email lacks declared auth scopes
    - Declare operation-specific auth scopes for send\_customer\_email, or explicitly declare anonymous authority when the operation requires no credentials.
  - high SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — send\_customer\_email is high-risk but has no owner
    - Declare an owner for each high-risk production tool in risk\_overrides.tools.
- api-refund-assistant \(agent-wide\) \(at shipgate.yaml\) — review \(3 medium\)
  - medium SHIP-API-STRUCTURED-OUTPUT-READINESS — Response format schemas/refund\_decision.schema.json is under-specified
    - Tighten the structured output schema with enums, needs\_review/refusal/error modeling, and declared critical fields.
  - medium SHIP-API-TIMEOUT-MISSING — OpenAI API flow lacks timeout metadata
    - Declare tool-call timeout metadata for high-risk OpenAI API flows.
  - medium SHIP-API-TRACE-APPROVAL-MISSING — Trace sample shows create\_refund without approval
    - Require approval before calling create\_refund.

## Finding Provenance

Reviewer triage signal only. Provenance kind does not change severity, release decision, fingerprints, baselines, or CI exit codes.

| Provenance kind | Active findings |
| --- | ---: |
| `static_declaration` | 14 |
| `ast_extraction` | 0 |
| `keyword_heuristic` | 0 |
| `regex_heuristic` | 0 |
| `policy_pack` | 0 |
| `runtime_trace` | 1 |

Suppressed findings excluded: 0

## Capability <-> Intent Diff

Agent intent:

- prohibited\_action: issue refund without approval (tags: financial\_action)
- prohibited\_action: send customer email without confirmation (tags: external\_write, customer\_communication)
- instruction\_preview: You are a support refund assistant. You should only advise the support representative and prepare a draft response. Do not take action on the customer's account. (tags: financial\_action)

Actual capabilities:

- create\_refund: capability=financial\_action, risk=financial\_action, write, control=missing
- send\_customer\_email: capability=external\_write, risk=customer\_communication, external\_write, write, control=missing

Policy/control gaps:

- CRITICAL undetected\_gap \[create\_refund\]: create\_refund has financial write capability without required controls. (at tools/openai-tools.json)
  Requires: Static review requires deterministic evidence for release gaps.
  Release implication: Human review is required to interpret this finding.
- HIGH control\_missing \[create\_refund\]: create\_refund function schema is not strict enough. (at tools/openai-tools.json)
  Requires: API function schemas must be strict enough for reliable tool calls.
  Release implication: The model may send ambiguous or overbroad tool arguments.
- HIGH control\_missing \[create\_refund\]: create\_refund is high-risk but has no owner. (at tools/openai-tools.json)
  Requires: Manifest metadata must match the active release surface.
  Release implication: Release review metadata is incomplete or stale.
- HIGH control\_missing \[create\_refund\]: create\_refund lacks idempotency evidence. (at tools/openai-tools.json)
  Requires: Risky write tools need idempotency evidence before retryable release.
  Release implication: Retries could duplicate financial, destructive, or external effects.
- HIGH control\_missing \[send\_customer\_email\]: send\_customer\_email function schema is not strict enough. (at tools/openai-tools.json)
  Requires: API function schemas must be strict enough for reliable tool calls.
  Release implication: The model may send ambiguous or overbroad tool arguments.
- 11 more in report.json

Release implication:

- Decision: blocked
- 2 release-relevant finding\(s\) map to active release blockers; resolve required controls or remove the capability.

Next validation:

- Tool schema boundary check: The tool accepts bounded structured inputs and returns structured outputs where needed.
- High-risk tool validation case: A declared test or review scenario covers the high-risk tool path.
- Retry behavior for risky write: Retries use idempotency evidence or the side effect is not retried.
- Least-privilege scope review: Manifest and tool scopes match the narrow permissions needed for the release.
- Approval gate for high-risk action: The run records human approval before the tool call and denies calls without approval.

## Recommended Next Actions

- Declare approval.required, safeguards.audit\_log, and safeguards.idempotency for this financial write action.
- Declare confirmation policy and safeguards.audit\_log for this external communication action.
- Make send\_customer\_email a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.
- Make create\_refund a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.
- Add idempotency evidence for send\_customer\_email or avoid retrying this side effect.
- Add idempotency evidence for create\_refund or avoid retrying this side effect.
- Declare operation-specific auth scopes for send\_customer\_email, or explicitly declare anonymous authority when the operation requires no credentials.
- Declare operation-specific auth scopes for create\_refund, or explicitly declare anonymous authority when the operation requires no credentials.

## Control Pack

`default` — Shipgate default controls v1. Shipgate's built-in requirements: money, destruction, production operations, code execution, and outbound communication carry controls.

- external communication requires confirmation policy and safeguards.audit\_log — 1 action short
- financial write requires approval.required, safeguards.audit\_log, and safeguards.idempotency — 1 action short

## Tool Surface Summary

- Total tools: 2
- High-risk tools: 2
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: openai_api=2

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

- Sources: 1
- Trace rows: 1
- Matched rows: 1
- Unmatched rows: 0
- Warnings: 0

Matched trace rows:
- `ctrace\_8669c1b40747c28a` create\_refund (openai_api_trace, tool_name)

- Declared local trace artifacts are audit evidence only; no live trace collection or tool execution occurred.
- Trace normalization retains only allowlisted scalar fields and discards prompts, messages, arguments, outputs, and payload bodies.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## OpenAI API Surface Summary

- Prompt files: 1
- Tool files: 1
- Response formats: 1
- Model config present: True
- Test cases: 1
- Trace samples: 1
- Policy rule files: 1

## Findings By Category

### Action Surface

- CRITICAL: SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING [create\_refund] - create\_refund has financial write capability without required controls
- HIGH: SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING [send\_customer\_email] - send\_customer\_email has external communication capability without required controls

### Api

- HIGH: SHIP-API-FUNCTION-SCHEMA-STRICTNESS [create\_refund] - create\_refund function schema is not strict enough
- HIGH: SHIP-API-FUNCTION-SCHEMA-STRICTNESS [send\_customer\_email] - send\_customer\_email function schema is not strict enough
- HIGH: SHIP-API-RETRY-WITHOUT-IDEMPOTENCY [create\_refund] - create\_refund may be retried without idempotency evidence
- HIGH: SHIP-API-RETRY-WITHOUT-IDEMPOTENCY [send\_customer\_email] - send\_customer\_email may be retried without idempotency evidence
- MEDIUM: SHIP-API-STRUCTURED-OUTPUT-READINESS - Response format schemas/refund\_decision.schema.json is under-specified
- MEDIUM: SHIP-API-TIMEOUT-MISSING - OpenAI API flow lacks timeout metadata
- MEDIUM: SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING [create\_refund] - create\_refund lacks success/failure output modeling
- MEDIUM: SHIP-API-TRACE-APPROVAL-MISSING - Trace sample shows create\_refund without approval

### Auth

- HIGH: SHIP-AUTH-MISSING-SCOPE [create\_refund] - create\_refund lacks declared auth scopes
- HIGH: SHIP-AUTH-MISSING-SCOPE [send\_customer\_email] - send\_customer\_email lacks declared auth scopes

### Manifest

- HIGH: SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING [create\_refund] - create\_refund is high-risk but has no owner
- HIGH: SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING [send\_customer\_email] - send\_customer\_email is high-risk but has no owner

### Side Effects

- HIGH: SHIP-SIDEFX-IDEMPOTENCY-MISSING [create\_refund] - create\_refund lacks idempotency evidence

## Agent Binding Surface

Status: declared
Root agent: agent\_v1:3e1354866b28ba54f69c8e73
Entry points: root
Pass eligible: true
Catalog partition: 2 reachable, 0 possible, 0 unbound

## Appendix: Root-Reachable Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| send\_customer\_email | openai\_api | customer\_communication, external\_write, write | customer\_communication=high, external\_write=high, write=medium | \- | \- |
| create\_refund | openai\_api | financial\_action, write | financial\_action=high, write=medium | \- | \- |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
