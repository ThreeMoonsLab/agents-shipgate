# Agents Shipgate Report

Project: simple-openai-api-agent
Agent: api-refund-assistant
Target: production\_like

## Release Decision

Decision: review_required
Reason: 20 findings require human review before shipping.

Blockers (0): none

Review items (20):
- HIGH SHIP-SCHEMA-MISSING-BOUNDS — create\_refund.amount has no maximum bound
- HIGH SHIP-SCHEMA-BROAD-FREE-TEXT — send\_customer\_email accepts broad free-form action input
- HIGH SHIP-AUTH-MISSING-SCOPE — create\_refund lacks declared auth scopes
- HIGH SHIP-AUTH-MISSING-SCOPE — send\_customer\_email lacks declared auth scopes
- HIGH SHIP-SCOPE-PROHIBITED-TOOL-PRESENT — create\_refund appears to overlap with a prohibited action
- HIGH SHIP-SCOPE-PROHIBITED-TOOL-PRESENT — send\_customer\_email appears to overlap with a prohibited action
- HIGH SHIP-SIDEFX-IDEMPOTENCY-MISSING — create\_refund lacks idempotency evidence
- HIGH SHIP-SIDEFX-IDEMPOTENCY-MISSING — send\_customer\_email lacks idempotency evidence
- HIGH SHIP-API-FUNCTION-SCHEMA-STRICTNESS — create\_refund function schema is not strict enough
- HIGH SHIP-API-FUNCTION-SCHEMA-STRICTNESS — send\_customer\_email function schema is not strict enough
- MEDIUM SHIP-API-STRUCTURED-OUTPUT-READINESS — Response format schemas/refund\_decision.schema.json is under-specified
- HIGH SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH — Prompt says read-only or advise-only while write/high-risk tools are enabled
- MEDIUM SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH — Prompt lacks approval/confirmation language for high-risk tools
- MEDIUM SHIP-API-TIMEOUT-MISSING — OpenAI API flow lacks timeout metadata
- MEDIUM SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING — create\_refund lacks success/failure output modeling
- HIGH SHIP-API-RETRY-WITHOUT-IDEMPOTENCY — create\_refund may be retried without idempotency evidence
- HIGH SHIP-API-RETRY-WITHOUT-IDEMPOTENCY — send\_customer\_email may be retried without idempotency evidence
- MEDIUM SHIP-API-TRACE-APPROVAL-MISSING — Trace sample shows create\_refund without approval
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — create\_refund is high-risk but has no owner
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — send\_customer\_email is high-risk but has no owner

Evidence coverage: static (human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 0
- High: 15
- Medium: 5
- Low: 0
- Suppressed: 0
- Status: Warnings detected (legacy; see Release Decision above)

## Top Findings

1. create\_refund function schema is not strict enough
   Evidence: issues=\['missing\_strict\_true', 'additional\_properties\_not\_false', 'properties\_missing\_from\_required:amount,reason', 'risky\_field\_unbounded:amount'\]; risk\_tags=\['financial\_action', 'write'\]
   Recommendation: Make create\_refund a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.

2. send\_customer\_email function schema is not strict enough
   Evidence: issues=\['broad\_free\_text:message'\]; risk\_tags=\['customer\_communication', 'external\_write', 'write'\]
   Recommendation: Make send\_customer\_email a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.

3. Prompt says read-only or advise-only while write/high-risk tools are enabled
   Evidence: tools=\['create\_refund', 'send\_customer\_email'\]
   Recommendation: Align prompt scope with enabled tools or remove write/high-risk tools.

4. create\_refund may be retried without idempotency evidence
   Evidence: retry\_policy=\{'max\_attempts': 2\}; risk\_tags=\['financial\_action', 'write'\]
   Recommendation: Add idempotency evidence for create\_refund or avoid retrying this side effect.

5. send\_customer\_email may be retried without idempotency evidence
   Evidence: retry\_policy=\{'max\_attempts': 2\}; risk\_tags=\['customer\_communication', 'external\_write', 'write'\]
   Recommendation: Add idempotency evidence for send\_customer\_email or avoid retrying this side effect.

## Capability <-> Intent Diff

Agent intent:

- prohibited\_action: issue refund without approval (tags: financial\_action)
- prohibited\_action: send customer email without confirmation (tags: external\_write, customer\_communication)
- instruction\_preview: You are a support refund assistant. You should only advise the support representative and prepare a draft response. Do not take action on the customer's account. (tags: financial\_action)

Actual capabilities:

- create\_refund: capability=financial\_action, risk=financial\_action, write, control=missing
- send\_customer\_email: capability=external\_write, risk=customer\_communication, external\_write, write, control=missing

Policy/control gaps:

- HIGH control\_missing \[create\_refund\]: create\_refund.amount has no maximum bound. (at tools/openai-tools.json)
  Requires: Risky numeric parameters must declare a maximum or equivalent limit.
  Release implication: Release reviewers cannot verify blast-radius limits.
- HIGH control\_missing \[create\_refund\]: create\_refund function schema is not strict enough. (at tools/openai-tools.json)
  Requires: API function schemas must be strict enough for reliable tool calls.
  Release implication: The model may send ambiguous or overbroad tool arguments.
- HIGH control\_missing \[create\_refund\]: create\_refund is high-risk but has no owner. (at tools/openai-tools.json)
  Requires: Manifest metadata must match the active release surface.
  Release implication: Release review metadata is incomplete or stale.
- HIGH control\_missing \[create\_refund\]: create\_refund lacks idempotency evidence. (at tools/openai-tools.json)
  Requires: Risky write tools need idempotency evidence before retryable release.
  Release implication: Retries could duplicate financial, destructive, or external effects.
- HIGH control\_missing \[send\_customer\_email\]: send\_customer\_email lacks idempotency evidence. (at tools/openai-tools.json)
  Requires: Risky write tools need idempotency evidence before retryable release.
  Release implication: Retries could duplicate financial, destructive, or external effects.
- 18 more in report.json

Release implication:

- Decision: review\_required
- 20 release-relevant finding\(s\) require release review before shipping.

Next validation:

- Tool schema boundary check: The tool accepts bounded structured inputs and returns structured outputs where needed.
- High-risk tool validation case: A declared test or review scenario covers the high-risk tool path.
- Retry behavior for risky write: Retries use idempotency evidence or the side effect is not retried.
- Prompt and tool-surface alignment: The agent instructions match the enabled write and high-risk capabilities.
- Prohibited-action guard: The prohibited action is blocked, removed, or covered by the stated control.
- 2 more in report.json

## Recommended Next Actions

- Make create\_refund a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.
- Make send\_customer\_email a strict function schema: object parameters, additionalProperties=false, complete required list, and bounded risky fields.
- Align prompt scope with enabled tools or remove write/high-risk tools.
- Add idempotency evidence for create\_refund or avoid retrying this side effect.
- Add idempotency evidence for send\_customer\_email or avoid retrying this side effect.
- Declare auth scopes for create\_refund in OpenAPI, MCP metadata, or the manifest before release review.
- Declare auth scopes for send\_customer\_email in OpenAPI, MCP metadata, or the manifest before release review.
- Declare an owner for each high-risk production tool in risk\_overrides.tools.

## Tool Surface Summary

- Total tools: 2
- High-risk tools: 2
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: openai_api=2

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

### Api

- HIGH: SHIP-API-FUNCTION-SCHEMA-STRICTNESS [create\_refund] - create\_refund function schema is not strict enough
- HIGH: SHIP-API-FUNCTION-SCHEMA-STRICTNESS [send\_customer\_email] - send\_customer\_email function schema is not strict enough
- HIGH: SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH - Prompt says read-only or advise-only while write/high-risk tools are enabled
- HIGH: SHIP-API-RETRY-WITHOUT-IDEMPOTENCY [create\_refund] - create\_refund may be retried without idempotency evidence
- HIGH: SHIP-API-RETRY-WITHOUT-IDEMPOTENCY [send\_customer\_email] - send\_customer\_email may be retried without idempotency evidence
- MEDIUM: SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH - Prompt lacks approval/confirmation language for high-risk tools
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

### Schema

- HIGH: SHIP-SCHEMA-BROAD-FREE-TEXT [send\_customer\_email] - send\_customer\_email accepts broad free-form action input
- HIGH: SHIP-SCHEMA-MISSING-BOUNDS [create\_refund] - create\_refund.amount has no maximum bound

### Scope

- HIGH: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT [create\_refund] - create\_refund appears to overlap with a prohibited action
- HIGH: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT [send\_customer\_email] - send\_customer\_email appears to overlap with a prohibited action

### Side Effects

- HIGH: SHIP-SIDEFX-IDEMPOTENCY-MISSING [create\_refund] - create\_refund lacks idempotency evidence
- HIGH: SHIP-SIDEFX-IDEMPOTENCY-MISSING [send\_customer\_email] - send\_customer\_email lacks idempotency evidence

## Appendix: Normalized Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| create\_refund | openai\_api | financial\_action, write | financial\_action=high, write=medium | \- | \- |
| send\_customer\_email | openai\_api | customer\_communication, external\_write, write | customer\_communication=high, external\_write=high, write=medium | \- | \- |


## Disclaimer

Agents Shipgate is an advisory release-readiness scanner. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
