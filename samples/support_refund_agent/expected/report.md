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

Review items (15):
- HIGH SHIP-INVENTORY-WILDCARD-TOOLS — Wildcard tool exposure declared
- HIGH SHIP-SCHEMA-MISSING-BOUNDS — stripe.create\_refund.amount has no maximum bound
- HIGH SHIP-SCHEMA-BROAD-FREE-TEXT — zendesk.update\_ticket accepts broad free-form action input
- HIGH SHIP-SCHEMA-BROAD-FREE-TEXT — gmail.send\_customer\_email accepts broad free-form action input
- HIGH SHIP-AUTH-MANIFEST-BROAD-SCOPE — Manifest declares broad permission scopes
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — shopify.cancel\_order requires scopes not declared in the manifest
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — support.search\_kb requires scopes not declared in the manifest
- HIGH SHIP-AUTH-MISSING-SCOPE — refund\_status\_lookup lacks declared auth scopes
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — gmail.send\_customer\_email requires scopes not declared in the manifest
- HIGH SHIP-SCOPE-PROHIBITED-TOOL-PRESENT — stripe.create\_refund appears to overlap with a prohibited action
- HIGH SHIP-SCOPE-PROHIBITED-TOOL-PRESENT — gmail.send\_customer\_email appears to overlap with a prohibited action
- HIGH SHIP-POLICY-CONFIRMATION-MISSING — stripe.create\_refund lacks a declared confirmation policy
- HIGH SHIP-POLICY-CONFIRMATION-MISSING — gmail.send\_customer\_email lacks a declared confirmation policy
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — shopify.cancel\_order is high-risk but has no owner
- MEDIUM SHIP-MANIFEST-UNUSED-SCOPE — Manifest declares unused permission scope zendesk:tickets:read

Evidence coverage: static (1 source warning(s); 3 semantic evidence gap(s); 1 semantic review concern(s); 6/8 actions pass-eligible; human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 3
- High: 16
- Medium: 1
- Low: 0
- Suppressed: 0
- Status: Release blockers detected (legacy; see Release Decision above)

## Top Findings

1. stripe.create\_refund has financial write capability without required controls
   Evidence: action\_id=agent:support-refund-agent/refund-assistant:action\_v2\_6f2d18a55f2189a165089034897d52ada547bb19afe20036cb7ec3119c2d95ca; missing=\['approval.required', 'safeguards.audit\_log', 'safeguards.idempotency'\]
   Recommendation: Declare approval.required, safeguards.audit\_log, and safeguards.idempotency for this financial write action.

2. stripe.create\_refund lacks a declared approval policy
   Evidence: risk\_tags=\['external\_write', 'financial\_action', 'write'\]; policy\_match=None
   Recommendation: Declare an approval policy for stripe.create\_refund or remove this tool from the release.

3. stripe.create\_refund lacks idempotency evidence
   Evidence: risk\_tags=\['external\_write', 'financial\_action', 'write'\]; retry\_policy\_known=True
   Recommendation: Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.

4. gmail.send\_customer\_email has external communication capability without required controls
   Evidence: action\_id=agent:support-refund-agent/refund-assistant:action\_v2\_49bfadd2eb7605a1065c24081f890481ee2f37e9718152ae533c8e43ddaab17a; missing=\['safeguards.audit\_log', 'confirmation.required'\]
   Recommendation: Declare confirmation policy and safeguards.audit\_log for this external communication action.

5. stripe.create\_refund has external communication capability without required controls
   Evidence: action\_id=agent:support-refund-agent/refund-assistant:action\_v2\_6f2d18a55f2189a165089034897d52ada547bb19afe20036cb7ec3119c2d95ca; missing=\['safeguards.audit\_log', 'confirmation.required'\]
   Recommendation: Declare confirmation policy and safeguards.audit\_log for this external communication action.

## Finding Provenance

Reviewer triage signal only. Provenance kind does not change severity, release decision, fingerprints, baselines, or CI exit codes.

| Provenance kind | Active findings |
| --- | ---: |
| `static_declaration` | 15 |
| `ast_extraction` | 0 |
| `keyword_heuristic` | 5 |
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
- shopify.cancel\_order: capability=destructive, risk=destructive, write, control=missing
- stripe.create\_refund: capability=financial\_action, risk=external\_write, financial\_action, write, control=missing
- support.search\_kb: capability=read\_only, risk=read\_only, control=missing
- 2 more in report.json

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
- HIGH control\_missing \[gmail.send\_customer\_email\]: gmail.send\_customer\_email accepts broad free-form action input. (at .agents-shipgate/mcp-tools.json)
  Requires: Action-like tool inputs must constrain high-blast-radius fields.
  Release implication: Release reviewers cannot bound the operation payload safely.
- HIGH control\_missing \[shopify.cancel\_order\]: shopify.cancel\_order is high-risk but has no owner. (at specs/support-tools.openapi.yaml:116)
  Requires: Manifest metadata must match the active release surface.
  Release implication: Release review metadata is incomplete or stale.
- 15 more in report.json

Release implication:

- Decision: blocked
- 5 release-relevant finding\(s\) map to active release blockers; resolve required controls or remove the capability.

Next validation:

- Retry behavior for risky write: Retries use idempotency evidence or the side effect is not retried.
- Approval gate for high-risk action: The run records human approval before the tool call and denies calls without approval.
- Tool schema boundary check: The tool accepts bounded structured inputs and returns structured outputs where needed.
- High-risk tool validation case: A declared test or review scenario covers the high-risk tool path.
- Explicit tool inventory review: The release exposes a static allowlist instead of wildcard or unbounded tools.
- 3 more in report.json

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

## Tool Surface Summary

- Total tools: 8
- High-risk tools: 3
- Wildcard tools: 1
- Missing descriptions: 0
- Sources: mcp=4, openapi=4

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

### Inventory

- HIGH: SHIP-INVENTORY-WILDCARD-TOOLS [wildcard\_mcp\_tools.\*] - Wildcard tool exposure declared

### Manifest

- HIGH: SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING [shopify.cancel\_order] - shopify.cancel\_order is high-risk but has no owner
- MEDIUM: SHIP-MANIFEST-UNUSED-SCOPE - Manifest declares unused permission scope zendesk:tickets:read

### Policy

- CRITICAL: SHIP-POLICY-APPROVAL-MISSING [stripe.create\_refund] - stripe.create\_refund lacks a declared approval policy
- HIGH: SHIP-POLICY-CONFIRMATION-MISSING [gmail.send\_customer\_email] - gmail.send\_customer\_email lacks a declared confirmation policy
- HIGH: SHIP-POLICY-CONFIRMATION-MISSING [stripe.create\_refund] - stripe.create\_refund lacks a declared confirmation policy

### Schema

- HIGH: SHIP-SCHEMA-BROAD-FREE-TEXT [gmail.send\_customer\_email] - gmail.send\_customer\_email accepts broad free-form action input
- HIGH: SHIP-SCHEMA-BROAD-FREE-TEXT [zendesk.update\_ticket] - zendesk.update\_ticket accepts broad free-form action input
- HIGH: SHIP-SCHEMA-MISSING-BOUNDS [stripe.create\_refund] - stripe.create\_refund.amount has no maximum bound

### Scope

- HIGH: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT [gmail.send\_customer\_email] - gmail.send\_customer\_email appears to overlap with a prohibited action
- HIGH: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT [stripe.create\_refund] - stripe.create\_refund appears to overlap with a prohibited action

### Side Effects

- CRITICAL: SHIP-SIDEFX-IDEMPOTENCY-MISSING [stripe.create\_refund] - stripe.create\_refund lacks idempotency evidence

## Appendix: Normalized Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| shopify.cancel\_order | openapi | destructive, write | destructive=high, write=high | shopify:orders:write | \- |
| send\_email\_preview | mcp | read\_only | read\_only=high | \- | \- |
| support.search\_kb | mcp | read\_only | read\_only=high | support:kb:read | support-platform |
| refund\_status\_lookup | openapi | read\_only | read\_only=high | \- | \- |
| stripe.create\_refund | openapi | external\_write, financial\_action, write | external\_write=high, financial\_action=high, write=high | stripe:refunds:write | payments-platform |
| zendesk.update\_ticket | openapi | write | write=high | zendesk:tickets:write | \- |
| gmail.send\_customer\_email | mcp | customer\_communication, external\_write | customer\_communication=high, external\_write=high | gmail:send | support-platform |
| wildcard\_mcp\_tools.\* | mcp | \- | \- | \- | \- |


## Disclaimer

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
