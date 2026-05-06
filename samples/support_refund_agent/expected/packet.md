# Release Evidence Packet

- Project: support-refund-agent
- Agent: refund-assistant
- Environment: production_like
- Run id: agents_shipgate_3716da0eb0ec2fad
- Generated at: 2026-01-01T00:00:00+00:00
- Packet schema: 0.1

This packet is a reviewer-shaped synthesis of a static Agents Shipgate scan. See §10 for what the packet does *not* prove.

## §1 Release decision — BLOCKED

- Decision: `blocked`
- Reason: 2 active findings block release.
- Blockers: 2
- Review items: 16

### CI gate behavior (informational)

- ci_mode: `advisory`, would_fail_ci: `false`, exit code: `0`
- Note: CI behavior is metadata about the run gate, not the verdict. The verdict above derives from `release_decision.decision`.

### Blockers

- `SHIP-POLICY-APPROVAL-MISSING` (critical): stripe.create_refund lacks a declared approval policy
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (critical): stripe.create_refund lacks idempotency evidence

### Review items

- `SHIP-INVENTORY-WILDCARD-TOOLS` (high): Wildcard tool exposure declared
- `SHIP-SCHEMA-MISSING-BOUNDS` (high): stripe.create_refund.amount has no maximum bound
- `SHIP-SCHEMA-BROAD-FREE-TEXT` (high): zendesk.update_ticket accepts broad free-form action input
- `SHIP-SCHEMA-BROAD-FREE-TEXT` (high): gmail.send_customer_email accepts broad free-form action input
- `SHIP-SCHEMA-FREEFORM-OUTPUT` (medium): send_email_preview returns free-form text output
- `SHIP-AUTH-MANIFEST-BROAD-SCOPE` (high): Manifest declares broad permission scopes
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): shopify.cancel_order requires scopes not declared in the manifest
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): support.search_kb requires scopes not declared in the manifest
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): gmail.send_customer_email requires scopes not declared in the manifest
- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` (high): stripe.create_refund appears to overlap with a prohibited action
- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` (high): gmail.send_customer_email appears to overlap with a prohibited action
- `SHIP-POLICY-CONFIRMATION-MISSING` (high): stripe.create_refund lacks a declared confirmation policy
- `SHIP-POLICY-CONFIRMATION-MISSING` (high): gmail.send_customer_email lacks a declared confirmation policy
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (high): gmail.send_customer_email lacks idempotency evidence
- `SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING` (high): shopify.cancel_order is high-risk but has no owner
- `SHIP-MANIFEST-UNUSED-SCOPE` (medium): Manifest declares unused permission scope zendesk:tickets:read

## §2 Capability ↔ Intent diff — missing

### Declared

- Purpose: answer refund policy questions
- Purpose: prepare refund requests for human review
- Purpose: update support ticket notes
- Prohibited: issue refund without approval
- Prohibited: cancel order without explicit confirmation
- Prohibited: send external email without preview

### Observed tools

- gmail.send_customer_email
- refund_status_lookup
- send_email_preview
- shopify.cancel_order
- stripe.create_refund
- support.search_kb
- wildcard_mcp_tools.*
- zendesk.update_ticket

### Divergences

- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` on `gmail.send_customer_email, stripe.create_refund`: stripe.create_refund appears to overlap with a prohibited action
- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` on `gmail.send_customer_email, stripe.create_refund`: gmail.send_customer_email appears to overlap with a prohibited action

## §3 High-risk tool surface — partial

- Total tools: 8 · High-risk: 3

| Tool | Source | Risk tags | Approval | Idempotency |
|---|---|---|---|---|
| `gmail.send_customer_email` | mcp | customer_communication, external_write | no | no |
| `shopify.cancel_order` | openapi | destructive, write | yes | yes |
| `stripe.create_refund` | openapi | external_write, financial_action, write | no | no |

## §4 Approval policy coverage — partial

| Tool | Declared | Source | Gap finding(s) |
|---|---|---|---|
| `shopify.cancel_order` | yes | policies | — |
| `stripe.create_refund` | no | — | fp_f092940f62fbb012 |

### Gap findings

- `SHIP-POLICY-APPROVAL-MISSING` (critical): stripe.create_refund lacks a declared approval policy

## §5 Idempotency / retry risk — partial

- Retry policy: not declared

| Tool | Declared | Source | Gap finding(s) |
|---|---|---|---|
| `gmail.send_customer_email` | no | — | fp_0f8aaa912d589cf0 |
| `shopify.cancel_order` | yes | policies | — |
| `stripe.create_refund` | no | — | fp_dac8011e14c53777 |

### Gap findings

- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (critical): stripe.create_refund lacks idempotency evidence
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (high): gmail.send_customer_email lacks idempotency evidence

## §6 Scope coverage — missing

### Declared scopes

- `zendesk:tickets:read`
- `zendesk:tickets:write`
- `stripe:*`

| Scope | Declared | Used by tools |
|---|---|---|
| `gmail:send` | no | `gmail.send_customer_email` |
| `shopify:orders:write` | no | `shopify.cancel_order` |
| `stripe:*` | yes | — |
| `stripe:refunds:write` | yes | `stripe.create_refund` |
| `support:kb:read` | no | `support.search_kb` |
| `zendesk:tickets:read` | yes | — |
| `zendesk:tickets:write` | yes | `zendesk.update_ticket` |

### Unused declared scopes

- `zendesk:tickets:read`

### Used by tools but not declared

- `gmail:send`
- `shopify:orders:write`
- `support:kb:read`

### Gap findings

- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): shopify.cancel_order requires scopes not declared in the manifest
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): support.search_kb requires scopes not declared in the manifest
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): gmail.send_customer_email requires scopes not declared in the manifest
- `SHIP-MANIFEST-UNUSED-SCOPE` (medium): Manifest declares unused permission scope zendesk:tickets:read

## §7 Memory isolation — not declared

- Manifest does not declare a memory isolation policy. The current manifest schema (v0.1) has no agent.memory field. See §10 for the residual review item.

## §8 Human-in-the-loop evidence — covered

- Configured: yes
- Human review recommended: yes

### Approval-required tools

- `shopify.cancel_order`

### Confirmation-required tools

- `shopify.cancel_order`

## §9 Required dynamic scenarios — partial

- **Manual review for SHIP-AUTH-MANIFEST-BROAD-SCOPE** — Replace broad manifest permission scopes with the narrowest scopes needed for this release.
  - Related finding(s): fp_d27325cbdbbf5483
- **Manual review for SHIP-AUTH-SCOPE-COVERAGE-MISSING** — Add the required scopes for shopify.cancel_order to permissions.scopes or narrow the tool's declared auth requirements.
  - Related finding(s): fp_1f6cfd6b7daa9b7c, fp_83852fbd6b440524, fp_d8e6d1865dae97cc
- **Manual review for SHIP-INVENTORY-WILDCARD-TOOLS** — Replace wildcard tool exposure with an explicit tool allowlist before release review.
  - Related finding(s): fp_fc02d8ecd30f2578
- **Manual review for SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING** — Declare an owner for each high-risk production tool in risk_overrides.tools.
  - Related finding(s): fp_fd2577850cef1f87
- **Manual review for SHIP-MANIFEST-UNUSED-SCOPE** — Remove unused manifest scopes or add tool metadata showing why they are required.
  - Related finding(s): fp_39b9ae878f343d1b
- **Manual review for SHIP-POLICY-APPROVAL-MISSING** — Declare an approval policy for stripe.create_refund or remove this tool from the release.
  - Related finding(s): fp_f092940f62fbb012
- **Manual review for SHIP-POLICY-CONFIRMATION-MISSING** — Declare a user confirmation policy for stripe.create_refund or remove this action from the release.
  - Related finding(s): fp_8e08a4fe6b0917f6, fp_a62ca2fd9a68a1d1
- **Manual review for SHIP-SCHEMA-BROAD-FREE-TEXT** — Constrain zendesk.update_ticket.updates with an enum, structured schema, or narrower field-specific parameters.
  - Related finding(s): fp_acd63b899d49aa1c, fp_ff2f028953d1c220
- **Manual review for SHIP-SCHEMA-FREEFORM-OUTPUT** — Prefer a structured output schema for send_email_preview, especially when output is later passed back into model context.
  - Related finding(s): fp_85f8513ad72cd9ea
- **Manual review for SHIP-SCHEMA-MISSING-BOUNDS** — Add a maximum bound to stripe.create_refund.amount or document an equivalent limit in the tool policy.
  - Related finding(s): fp_ab60b01cb53cfcbe
- **Manual review for SHIP-SCOPE-PROHIBITED-TOOL-PRESENT** — Remove stripe.create_refund, narrow its policy, or revise prohibited_actions so the manifest and tool surface do not contradict each other.
  - Related finding(s): fp_12985c36a06026de, fp_e090c62e390e70ab
- **Manual review for SHIP-SIDEFX-IDEMPOTENCY-MISSING** — Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create_refund.
  - Related finding(s): fp_0f8aaa912d589cf0, fp_dac8011e14c53777
- **Re-run scan after resolving source warnings** — Source loaders emitted warnings; some tool surfaces may have been parsed with reduced confidence.
- **Verify low-confidence tool extractions** — One or more tools were extracted with low confidence; confirm against the upstream source before release.

## §10 What this packet did NOT prove

Agents Shipgate is a static release-readiness scanner. The packet below is derived from a scan; it does not, by itself, prove the following properties:

- **Prompt robustness.** Whether the agent's prompt holds up under jailbreaks, persona drift, indirect prompt injection, or adversarial inputs.
- **Runtime behavior.** Whether the agent actually invokes only the declared tools, respects approval gates at runtime, or follows policy under load. Static config is not runtime evidence.
- **Model correctness.** Whether the underlying model produces correct outputs, calls the right tools, or stays within the declared scope. The packet does not benchmark the model.
- **Adversarial resistance.** Whether the agent withstands red-team or penetration testing. The packet does not run scenarios; it organizes evidence.

### Per-run residuals

- Source warnings:
  - MCP source declares wildcard tool exposure
- Low-confidence tool extractions: none
- Suppressed findings in effect: none
- Memory isolation is not modeled by the v0.1 manifest schema; no static evidence is available.
