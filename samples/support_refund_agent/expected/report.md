# Agents Shipgate Report

Project: support-refund-agent
Agent: refund-assistant
Target: production\_like

## Release Decision

Decision: blocked
Reason: 5 active findings block release.

Blockers (5):
- CRITICAL SHIP-POLICY-APPROVAL-MISSING — stripe.create\_refund lacks a declared approval policy
- CRITICAL SHIP-SIDEFX-IDEMPOTENCY-MISSING — stripe.create\_refund lacks idempotency evidence
- HIGH SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING — gmail.send\_customer\_email has external communication capability without required controls
- HIGH SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING — stripe.create\_refund has external communication capability without required controls
- CRITICAL SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING — stripe.create\_refund has financial write capability without required controls

Review items (10):
- MEDIUM SHIP-SCHEMA-FREEFORM-OUTPUT — send\_email\_preview returns free-form text output
- HIGH SHIP-AUTH-MANIFEST-BROAD-SCOPE — Manifest declares broad permission scopes
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — shopify.cancel\_order requires scopes not declared in the manifest
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — support.search\_kb requires scopes not declared in the manifest
- HIGH SHIP-AUTH-MISSING-SCOPE — refund\_status\_lookup lacks declared auth scopes
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — gmail.send\_customer\_email requires scopes not declared in the manifest
- HIGH SHIP-POLICY-CONFIRMATION-MISSING — stripe.create\_refund lacks a declared confirmation policy
- HIGH SHIP-POLICY-CONFIRMATION-MISSING — gmail.send\_customer\_email lacks a declared confirmation policy
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — shopify.cancel\_order is high-risk but has no owner
- MEDIUM SHIP-MANIFEST-UNUSED-SCOPE — Manifest declares unused permission scope zendesk:tickets:read

Evidence coverage: static (1 source warning(s); 1 binding evidence gap(s); 7/8 catalog tools reachable; 7 semantic evidence gap(s); 3 semantic review concern(s); 0/7 actions pass-eligible; human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 3
- High: 10
- Medium: 2
- Low: 0
- Suppressed: 0
- Status: Release blockers detected (legacy; see Release Decision above)

## Top Findings

15 findings across 7 subjects, most urgent first.

- stripe.create\_refund \[support\_openapi\] \(at specs/support-tools.openapi.yaml\#/paths/~1refunds/post\) — BLOCKS RELEASE \(3 critical, 2 high\)
  - critical SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING \(blocks release\) — missing: approval.required, safeguards.audit\_log, safeguards.idempotency
  - critical SHIP-POLICY-APPROVAL-MISSING \(blocks release\) — stripe.create\_refund lacks a declared approval policy
    - Declare an approval policy for stripe.create\_refund or remove this tool from the release.
  - critical SHIP-SIDEFX-IDEMPOTENCY-MISSING \(blocks release\) — stripe.create\_refund lacks idempotency evidence
    - Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.
  - high SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(blocks release\) — missing: safeguards.audit\_log, confirmation.required
  - high SHIP-POLICY-CONFIRMATION-MISSING — stripe.create\_refund lacks a declared confirmation policy
    - Declare a user confirmation policy for stripe.create\_refund or remove this action from the release.
- gmail.send\_customer\_email \[support\_mcp\_tools\] \(at .agents-shipgate/mcp-tools.json\#/tools/1\) — BLOCKS RELEASE \(3 high\)
  - high SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(blocks release\) — missing: safeguards.audit\_log, confirmation.required
  - high SHIP-AUTH-SCOPE-COVERAGE-MISSING — gmail.send\_customer\_email requires scopes not declared in the manifest
    - Add the required scopes for gmail.send\_customer\_email to permissions.scopes or narrow the tool's declared auth requirements.
  - high SHIP-POLICY-CONFIRMATION-MISSING — gmail.send\_customer\_email lacks a declared confirmation policy
    - Declare a user confirmation policy for gmail.send\_customer\_email or remove this action from the release.
- refund-assistant \(agent-wide\) — review \(1 high, 1 medium\)
  - high SHIP-AUTH-MANIFEST-BROAD-SCOPE — Manifest declares broad permission scopes \(at shipgate.yaml\#/permissions/scopes\)
    - Replace broad manifest permission scopes with the narrowest scopes needed for this release.
  - medium SHIP-MANIFEST-UNUSED-SCOPE — Manifest declares unused permission scope zendesk:tickets:read \(at shipgate.yaml\)
    - Remove unused manifest scopes or add tool metadata showing why they are required.
- shopify.cancel\_order \[support\_openapi\] \(at specs/support-tools.openapi.yaml\#/paths/~1orders~1\{order\_id\}~1cancel/post\) — review \(2 high\)
  - high SHIP-AUTH-SCOPE-COVERAGE-MISSING — shopify.cancel\_order requires scopes not declared in the manifest
    - Add the required scopes for shopify.cancel\_order to permissions.scopes or narrow the tool's declared auth requirements.
  - high SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — shopify.cancel\_order is high-risk but has no owner
    - Declare an owner for each high-risk production tool in risk\_overrides.tools.
- refund\_status\_lookup \[support\_openapi\] \(at specs/support-tools.openapi.yaml\#/paths/~1refunds~1\{payment\_id\}~1status/get\) — review \(1 high\)
  - high SHIP-AUTH-MISSING-SCOPE — refund\_status\_lookup lacks declared auth scopes
    - Declare operation-specific auth scopes for refund\_status\_lookup, or explicitly declare anonymous authority when the operation requires no credentials.
- support.search\_kb \[support\_mcp\_tools\] \(at .agents-shipgate/mcp-tools.json\#/tools/0\) — review \(1 high\)
  - high SHIP-AUTH-SCOPE-COVERAGE-MISSING — support.search\_kb requires scopes not declared in the manifest
    - Add the required scopes for support.search\_kb to permissions.scopes or narrow the tool's declared auth requirements.
- send\_email\_preview \[openai\_sdk\_static\] \(at agents/refund\_agent.py:5\) — review \(1 medium\)
  - medium SHIP-SCHEMA-FREEFORM-OUTPUT — send\_email\_preview returns free-form text output
    - Prefer a structured output schema for send\_email\_preview, especially when output is later passed back into model context.

## Finding Provenance

Reviewer triage signal only. Provenance kind does not change severity, release decision, fingerprints, baselines, or CI exit codes.

| Provenance kind | Active findings |
| --- | ---: |
| `static_declaration` | 15 |
| `ast_extraction` | 0 |
| `keyword_heuristic` | 0 |
| `regex_heuristic` | 0 |
| `policy_pack` | 0 |
| `runtime_trace` | 0 |

Suppressed findings excluded: 0

## Capability <-> Intent Diff

Agent intent:

- prohibited\_action: issue refund without approval (tags: financial\_action)
- prohibited\_action: send external email without preview (tags: external\_write, customer\_communication)
- prohibited\_action: cancel order without explicit confirmation (tags: destructive)
- declared\_purpose: prepare refund requests for human review (tags: financial\_action)
- declared\_purpose: update support ticket notes (tags: none)
- declared\_purpose: answer refund policy questions (tags: financial\_action)

Actual capabilities:

- gmail.send\_customer\_email: capability=external\_write, risk=customer\_communication, external\_write, control=missing
- refund\_status\_lookup: capability=read\_only, risk=read\_only, control=partial
- send\_email\_preview: capability=read\_only, risk=read\_only, control=partial
- shopify.cancel\_order: capability=destructive, risk=destructive, write, control=missing
- stripe.create\_refund: capability=financial\_action, risk=external\_write, financial\_action, write, control=missing
- 1 more in report.json

Policy/control gaps:

- CRITICAL control\_missing \[stripe.create\_refund\]: stripe.create\_refund lacks idempotency evidence. (at specs/support-tools.openapi.yaml:97)
  Requires: Risky write tools need idempotency evidence before retryable release.
  Release implication: Retries could duplicate financial, destructive, or external effects.
- CRITICAL policy\_gap \[stripe.create\_refund\]: stripe.create\_refund lacks a declared approval policy. (at specs/support-tools.openapi.yaml:97)
  Requires: High-risk tools must have a declared approval policy.
  Release implication: Release is blocked until approval is declared or the tool is removed.
- CRITICAL undetected\_gap \[stripe.create\_refund\]: stripe.create\_refund has financial write capability without required controls. (at specs/support-tools.openapi.yaml:97)
  Requires: Static review requires deterministic evidence for release gaps.
  Release implication: Human review is required to interpret this finding.
- HIGH control\_missing \[shopify.cancel\_order\]: shopify.cancel\_order is high-risk but has no owner. (at specs/support-tools.openapi.yaml:116)
  Requires: Manifest metadata must match the active release surface.
  Release implication: Release review metadata is incomplete or stale.
- HIGH policy\_gap \[gmail.send\_customer\_email\]: gmail.send\_customer\_email lacks a declared confirmation policy. (at .agents-shipgate/mcp-tools.json)
  Requires: Destructive, external, or customer actions require confirmation.
  Release implication: Release review must verify explicit user confirmation before shipping.
- 10 more in report.json

Release implication:

- Decision: blocked
- 5 release-relevant finding\(s\) map to active release blockers; resolve required controls or remove the capability.

Next validation:

- Retry behavior for risky write: Retries use idempotency evidence or the side effect is not retried.
- Approval gate for high-risk action: The run records human approval before the tool call and denies calls without approval.
- High-risk tool validation case: A declared test or review scenario covers the high-risk tool path.
- Confirmation gate for external or destructive action: The run records explicit confirmation before the side effect occurs.
- Least-privilege scope review: Manifest and tool scopes match the narrow permissions needed for the release.
- 1 more in report.json

## Recommended Next Actions

- Declare approval.required, safeguards.audit\_log, and safeguards.idempotency for this financial write action.
- Declare an approval policy for stripe.create\_refund or remove this tool from the release.
- Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.
- Declare confirmation policy and safeguards.audit\_log for this external communication action.
- Replace broad manifest permission scopes with the narrowest scopes needed for this release.
- Declare operation-specific auth scopes for refund\_status\_lookup, or explicitly declare anonymous authority when the operation requires no credentials.
- Add the required scopes for shopify.cancel\_order to permissions.scopes or narrow the tool's declared auth requirements.
- Add the required scopes for support.search\_kb to permissions.scopes or narrow the tool's declared auth requirements.

## Source Warnings

- MCP source declares wildcard tool exposure

## Control Pack

`default` — Shipgate default controls v1. Shipgate's built-in requirements: money, destruction, production operations, code execution, and outbound communication carry controls.

- external communication requires confirmation policy and safeguards.audit\_log — 2 actions short
- financial write requires approval.required, safeguards.audit\_log, and safeguards.idempotency — 1 action short

## Tool Surface Summary

- Total tools: 7
- High-risk tools: 5
- Wildcard tools: 0
- Missing descriptions: 0
- Sources: mcp=3, openapi=4

## Action Surface Diff

- Status: disabled - No action-surface comparison source was provided.
- Base: none

## Capability Runtime Evidence

No local runtime trace artifacts were declared for capability evidence.

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## Findings By Category

### Action Surface

- CRITICAL: SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING [stripe.create\_refund] - stripe.create\_refund has financial write capability without required controls
- HIGH: SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING [gmail.send\_customer\_email] - gmail.send\_customer\_email has external communication capability without required controls
- HIGH: SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING [stripe.create\_refund] - stripe.create\_refund has external communication capability without required controls

### Auth

- HIGH: SHIP-AUTH-MANIFEST-BROAD-SCOPE - Manifest declares broad permission scopes
- HIGH: SHIP-AUTH-MISSING-SCOPE [refund\_status\_lookup] - refund\_status\_lookup lacks declared auth scopes
- HIGH: SHIP-AUTH-SCOPE-COVERAGE-MISSING [gmail.send\_customer\_email] - gmail.send\_customer\_email requires scopes not declared in the manifest
- HIGH: SHIP-AUTH-SCOPE-COVERAGE-MISSING [shopify.cancel\_order] - shopify.cancel\_order requires scopes not declared in the manifest
- HIGH: SHIP-AUTH-SCOPE-COVERAGE-MISSING [support.search\_kb] - support.search\_kb requires scopes not declared in the manifest

### Manifest

- HIGH: SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING [shopify.cancel\_order] - shopify.cancel\_order is high-risk but has no owner
- MEDIUM: SHIP-MANIFEST-UNUSED-SCOPE - Manifest declares unused permission scope zendesk:tickets:read

### Policy

- CRITICAL: SHIP-POLICY-APPROVAL-MISSING [stripe.create\_refund] - stripe.create\_refund lacks a declared approval policy
- HIGH: SHIP-POLICY-CONFIRMATION-MISSING [gmail.send\_customer\_email] - gmail.send\_customer\_email lacks a declared confirmation policy
- HIGH: SHIP-POLICY-CONFIRMATION-MISSING [stripe.create\_refund] - stripe.create\_refund lacks a declared confirmation policy

### Schema

- MEDIUM: SHIP-SCHEMA-FREEFORM-OUTPUT [send\_email\_preview] - send\_email\_preview returns free-form text output

### Side Effects

- CRITICAL: SHIP-SIDEFX-IDEMPOTENCY-MISSING [stripe.create\_refund] - stripe.create\_refund lacks idempotency evidence

## Agent Binding Surface

Status: conflicting
Root agent: agent\_v1:7cb237a00d64b7400f4adc3b
Pass eligible: false
Catalog partition: 7 reachable, 0 possible, 1 unbound

Binding gaps:
- `conflicting\_binding\_evidence` — Closed-world declaration for 'root' does not match the complete structural tool set.

### Unbound Catalog Entries

- `wildcard\_mcp\_tools.\*` (mcp)

## Appendix: Root-Reachable Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| shopify.cancel\_order | openapi | destructive, write | destructive=high, write=high | shopify:orders:write | \- |
| send\_email\_preview | mcp | read\_only | read\_only=high | \- | \- |
| support.search\_kb | mcp | read\_only | read\_only=high | support:kb:read | support-platform |
| refund\_status\_lookup | openapi | read\_only | read\_only=high | \- | \- |
| stripe.create\_refund | openapi | external\_write, financial\_action, write | external\_write=high, financial\_action=high, write=high | stripe:refunds:write | payments-platform |
| zendesk.update\_ticket | openapi | write | write=high | zendesk:tickets:write | \- |
| gmail.send\_customer\_email | mcp | customer\_communication, external\_write | customer\_communication=high, external\_write=high | gmail:send | support-platform |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
