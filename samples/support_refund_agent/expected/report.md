# Agents Shipgate Report

Project: support-refund-agent
Agent: refund-assistant
Target: production\_like

## Release Decision

Decision: blocked
Reason: 2 active findings block release.

Blockers (2):
- CRITICAL SHIP-POLICY-APPROVAL-MISSING — stripe.create\_refund lacks a declared approval policy
- CRITICAL SHIP-SIDEFX-IDEMPOTENCY-MISSING — stripe.create\_refund lacks idempotency evidence

Review items (16):
- HIGH SHIP-INVENTORY-WILDCARD-TOOLS — Wildcard tool exposure declared
- HIGH SHIP-SCHEMA-MISSING-BOUNDS — stripe.create\_refund.amount has no maximum bound
- HIGH SHIP-SCHEMA-BROAD-FREE-TEXT — zendesk.update\_ticket accepts broad free-form action input
- HIGH SHIP-SCHEMA-BROAD-FREE-TEXT — gmail.send\_customer\_email accepts broad free-form action input
- MEDIUM SHIP-SCHEMA-FREEFORM-OUTPUT — send\_email\_preview returns free-form text output
- HIGH SHIP-AUTH-MANIFEST-BROAD-SCOPE — Manifest declares broad permission scopes
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — shopify.cancel\_order requires scopes not declared in the manifest
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — support.search\_kb requires scopes not declared in the manifest
- HIGH SHIP-AUTH-SCOPE-COVERAGE-MISSING — gmail.send\_customer\_email requires scopes not declared in the manifest
- HIGH SHIP-SCOPE-PROHIBITED-TOOL-PRESENT — stripe.create\_refund appears to overlap with a prohibited action
- HIGH SHIP-SCOPE-PROHIBITED-TOOL-PRESENT — gmail.send\_customer\_email appears to overlap with a prohibited action
- HIGH SHIP-POLICY-CONFIRMATION-MISSING — stripe.create\_refund lacks a declared confirmation policy
- HIGH SHIP-POLICY-CONFIRMATION-MISSING — gmail.send\_customer\_email lacks a declared confirmation policy
- HIGH SHIP-SIDEFX-IDEMPOTENCY-MISSING — gmail.send\_customer\_email lacks idempotency evidence
- HIGH SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING — shopify.cancel\_order is high-risk but has no owner
- MEDIUM SHIP-MANIFEST-UNUSED-SCOPE — Manifest declares unused permission scope zendesk:tickets:read

Evidence coverage: mixed (1 low-confidence tool(s); 1 source warning(s); human review recommended)

Baseline delta: not enabled

Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)

## Summary

- Critical: 2
- High: 14
- Medium: 2
- Low: 0
- Suppressed: 0
- Status: Release blockers detected (legacy; see Release Decision above)

## Top Findings

1. stripe.create\_refund lacks a declared approval policy
   Evidence: risk\_tags=\['external\_write', 'financial\_action', 'write'\]; policy\_match=None
   Recommendation: Declare an approval policy for stripe.create\_refund or remove this tool from the release.

2. stripe.create\_refund lacks idempotency evidence
   Evidence: risk\_tags=\['external\_write', 'financial\_action', 'write'\]; retry\_policy\_known=True
   Recommendation: Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.

3. Manifest declares broad permission scopes
   Evidence: scopes=\['stripe:\*'\]
   Recommendation: Replace broad manifest permission scopes with the narrowest scopes needed for this release.

4. shopify.cancel\_order requires scopes not declared in the manifest
   Evidence: tool\_scopes=\['shopify:orders:write'\]; manifest\_scopes=\['zendesk:tickets:read', 'zendesk:tickets:write', 'stripe:\*'\]; missing\_scopes=\['shopify:orders:write'\]
   Recommendation: Add the required scopes for shopify.cancel\_order to permissions.scopes or narrow the tool's declared auth requirements.

5. support.search\_kb requires scopes not declared in the manifest
   Evidence: tool\_scopes=\['support:kb:read'\]; manifest\_scopes=\['zendesk:tickets:read', 'zendesk:tickets:write', 'stripe:\*'\]; missing\_scopes=\['support:kb:read'\]
   Recommendation: Add the required scopes for support.search\_kb to permissions.scopes or narrow the tool's declared auth requirements.

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
- send\_email\_preview: capability=read\_only, risk=read\_only, control=partial
- shopify.cancel\_order: capability=destructive, risk=destructive, write, control=missing
- stripe.create\_refund: capability=financial\_action, risk=external\_write, financial\_action, write, control=missing
- support.search\_kb: capability=read\_only, risk=read\_only, control=missing
- 2 more in report.json

Policy/control gaps:

- CRITICAL control\_missing \[stripe.create\_refund\]: stripe.create\_refund lacks idempotency evidence.
  Requires: Risky write tools need idempotency evidence before retryable release.
  Release implication: Retries could duplicate financial, destructive, or external effects.
- CRITICAL policy\_gap \[stripe.create\_refund\]: stripe.create\_refund lacks a declared approval policy.
  Requires: High-risk tools must have a declared approval policy.
  Release implication: Release is blocked until approval is declared or the tool is removed.
- HIGH control\_missing \[gmail.send\_customer\_email\]: gmail.send\_customer\_email accepts broad free-form action input.
  Requires: Action-like tool inputs must constrain high-blast-radius fields.
  Release implication: Release reviewers cannot bound the operation payload safely.
- HIGH control\_missing \[gmail.send\_customer\_email\]: gmail.send\_customer\_email lacks idempotency evidence.
  Requires: Risky write tools need idempotency evidence before retryable release.
  Release implication: Retries could duplicate financial, destructive, or external effects.
- HIGH control\_missing \[shopify.cancel\_order\]: shopify.cancel\_order is high-risk but has no owner.
  Requires: Manifest metadata must match the active release surface.
  Release implication: Release review metadata is incomplete or stale.
- 13 more in report.json

Release implication:

- Decision: blocked
- 2 release-relevant finding\(s\) map to active release blockers; resolve required controls or remove the capability.

Next validation:

- Retry behavior for risky write: Retries use idempotency evidence or the side effect is not retried.
- Approval gate for high-risk action: The run records human approval before the tool call and denies calls without approval.
- Tool schema boundary check: The tool accepts bounded structured inputs and returns structured outputs where needed.
- High-risk tool validation case: A declared test or review scenario covers the high-risk tool path.
- Explicit tool inventory review: The release exposes a static allowlist instead of wildcard or unbounded tools.
- 3 more in report.json

## Recommended Next Actions

- Declare an approval policy for stripe.create\_refund or remove this tool from the release.
- Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.
- Replace broad manifest permission scopes with the narrowest scopes needed for this release.
- Add the required scopes for shopify.cancel\_order to permissions.scopes or narrow the tool's declared auth requirements.
- Add the required scopes for support.search\_kb to permissions.scopes or narrow the tool's declared auth requirements.
- Add the required scopes for gmail.send\_customer\_email to permissions.scopes or narrow the tool's declared auth requirements.
- Replace wildcard tool exposure with an explicit tool allowlist before release review.
- Declare an owner for each high-risk production tool in risk\_overrides.tools.

## Source Warnings

- MCP source declares wildcard tool exposure

## Tool Surface Summary

- Total tools: 8
- High-risk tools: 3
- Wildcard tools: 1
- Missing descriptions: 0
- Sources: mcp=3, openapi=4, sdk_function=1

## Tool Surface Diff

- Status: disabled - No --diff-from report or v0.3 baseline snapshot was provided.
- Base: none

## Findings By Category

### Auth

- HIGH: SHIP-AUTH-MANIFEST-BROAD-SCOPE - Manifest declares broad permission scopes
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
- MEDIUM: SHIP-SCHEMA-FREEFORM-OUTPUT [send\_email\_preview] - send\_email\_preview returns free-form text output

### Scope

- HIGH: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT [gmail.send\_customer\_email] - gmail.send\_customer\_email appears to overlap with a prohibited action
- HIGH: SHIP-SCOPE-PROHIBITED-TOOL-PRESENT [stripe.create\_refund] - stripe.create\_refund appears to overlap with a prohibited action

### Side Effects

- CRITICAL: SHIP-SIDEFX-IDEMPOTENCY-MISSING [stripe.create\_refund] - stripe.create\_refund lacks idempotency evidence
- HIGH: SHIP-SIDEFX-IDEMPOTENCY-MISSING [gmail.send\_customer\_email] - gmail.send\_customer\_email lacks idempotency evidence

## Appendix: Normalized Tool Inventory

| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |
| --- | --- | --- | --- | --- | --- |
| gmail.send\_customer\_email | mcp | customer\_communication, external\_write | customer\_communication=medium, external\_write=medium | gmail:send | support-platform |
| refund\_status\_lookup | openapi | read\_only | read\_only=high | \- | \- |
| send\_email\_preview | sdk\_function | read\_only | read\_only=high | \- | \- |
| shopify.cancel\_order | openapi | destructive, write | destructive=high, write=high | shopify:orders:write | \- |
| stripe.create\_refund | openapi | external\_write, financial\_action, write | external\_write=high, financial\_action=high, write=high | stripe:refunds:write | payments-platform |
| support.search\_kb | mcp | read\_only | read\_only=high | support:kb:read | support-platform |
| wildcard\_mcp\_tools.\* | mcp | \- | \- | \- | \- |
| zendesk.update\_ticket | openapi | write | write=high | zendesk:tickets:write | \- |


## Disclaimer

Agents Shipgate is an advisory release-readiness scanner. It does not certify agent safety or compliance. Findings are based on static configuration, declared policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool routing, and output interpretation are not verified.
