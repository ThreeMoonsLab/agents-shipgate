# Release Evidence Packet

- Project: support-refund-agent
- Agent: refund-assistant
- Environment: production\_like
- Run id: agents\_shipgate\_eae2aebe6f2524ce
- Generated at: 2026-01-01T00:00:00\+00:00
- Packet schema: 0\.8

This packet is a reviewer-shaped synthesis of a static Agents Shipgate scan. See §10 for what the packet does *not* prove.

## §1 Release decision — BLOCKED

- Decision: `blocked`
- Reason: 5 active findings block release.
- Blockers: 5
- Review items: 15

### CI gate behavior (informational)

- ci_mode: `advisory`, would_fail_ci: `false`, exit code: `0`
- Note: CI behavior is metadata about the run gate, not the verdict. The verdict above derives from `release_decision.decision`.

### Static semantic coverage

- Pass-eligible actions: 6/8
- Evidence gaps: 3
- Known authority review concerns: 1
- Reasons: incomplete\_surface=1, missing\_authority\_evidence=1, missing\_effect\_evidence=1, unscoped\_authority=1

### Blockers

- `SHIP-POLICY-APPROVAL-MISSING` (critical): stripe.create\_refund lacks a declared approval policy — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:54`
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (critical): stripe.create\_refund lacks idempotency evidence — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:59`
- `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING` (high): gmail.send\_customer\_email has external communication capability without required controls — `.agents-shipgate/mcp-tools.json\#/tools/1`
- `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING` (high): stripe.create\_refund has external communication capability without required controls — `specs/support-tools.openapi.yaml:97`
- `SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING` (critical): stripe.create\_refund has financial write capability without required controls — `specs/support-tools.openapi.yaml:97`

### Review items

- `SHIP-INVENTORY-WILDCARD-TOOLS` (high): Wildcard tool exposure declared — `.agents-shipgate/wildcard-tools.json\#/wildcard`
- `SHIP-SCHEMA-MISSING-BOUNDS` (high): stripe.create\_refund.amount has no maximum bound — `specs/support-tools.openapi.yaml:97`
- `SHIP-SCHEMA-BROAD-FREE-TEXT` (high): zendesk.update\_ticket accepts broad free-form action input — `specs/support-tools.openapi.yaml:142`
- `SHIP-SCHEMA-BROAD-FREE-TEXT` (high): gmail.send\_customer\_email accepts broad free-form action input — `.agents-shipgate/mcp-tools.json\#/tools/1`
- `SHIP-AUTH-MANIFEST-BROAD-SCOPE` (high): Manifest declares broad permission scopes — `shipgate.yaml:63`
- `SHIP-AUTH-MISSING-SCOPE` (high): refund\_status\_lookup lacks declared auth scopes — `specs/support-tools.openapi.yaml:72`
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): shopify.cancel\_order requires scopes not declared in the manifest — `specs/support-tools.openapi.yaml:116`
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): support.search\_kb requires scopes not declared in the manifest — `.agents-shipgate/mcp-tools.json\#/tools/0`
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): gmail.send\_customer\_email requires scopes not declared in the manifest — `.agents-shipgate/mcp-tools.json\#/tools/1`
- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` (high): stripe.create\_refund appears to overlap with a prohibited action — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:22`
- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` (high): gmail.send\_customer\_email appears to overlap with a prohibited action — `.agents-shipgate/mcp-tools.json\#/tools/1` — `shipgate.yaml:24`
- `SHIP-POLICY-CONFIRMATION-MISSING` (high): stripe.create\_refund lacks a declared confirmation policy — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:57`
- `SHIP-POLICY-CONFIRMATION-MISSING` (high): gmail.send\_customer\_email lacks a declared confirmation policy — `.agents-shipgate/mcp-tools.json\#/tools/1` — `shipgate.yaml:57`
- `SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING` (high): shopify.cancel\_order is high-risk but has no owner — `specs/support-tools.openapi.yaml:116`
- `SHIP-MANIFEST-UNUSED-SCOPE` (medium): Manifest declares unused permission scope zendesk:tickets:read — `shipgate.yaml`

## §1A Evidence matrix — compact review summary

- Evidence Matrix Light is derived from public report.json only. Release decisions, CI exit behavior, and baseline semantics remain owned by release\_decision. Domain rows intentionally overlap; a single finding can appear in multiple rows when it is relevant to each review lens.

| Domain | Evidence present | Evidence source | Confidence | Missing controls | Blocking findings | Review items |
|---|---|---|---|---|---|---|
| Inventory | partial | tool\_inventory; tool\_surface; \+2 more | high | SHIP-INVENTORY-WILDCARD-TOOLS on wildcard\_mcp\_tools.\*: Wildcard tool exposure declared | — | SHIP-INVENTORY-WILDCARD-TOOLS \(high\) |
| Schema | partial | tool\_surface\_facts.tools\[\].hashes; findings\[\] | mixed | SHIP-SCHEMA-MISSING-BOUNDS on stripe.create\_refund: stripe.create\_refund.amount has no maximum bound; SHIP-SCHEMA-BROAD-FREE-TEXT on zendesk.update\_ticket: zendesk.update\_ticket accepts broad free-form action input; \+1 more | — | SHIP-SCHEMA-MISSING-BOUNDS \(high\); SHIP-SCHEMA-BROAD-FREE-TEXT \(high\); \+1 more |
| Auth | partial | tool\_surface\_facts.scopes; tool\_inventory\[\].auth\_scopes; \+1 more | mixed | SHIP-AUTH-MANIFEST-BROAD-SCOPE: Manifest declares broad permission scopes; SHIP-AUTH-MISSING-SCOPE on refund\_status\_lookup: refund\_status\_lookup lacks declared auth scopes; \+4 more | — | SHIP-AUTH-MANIFEST-BROAD-SCOPE \(high\); SHIP-AUTH-MISSING-SCOPE \(high\); \+4 more |
| Approval | partial | tool\_surface\_facts.controls\[kind=approval\_policy\]; findings\[\] | high | SHIP-POLICY-APPROVAL-MISSING on stripe.create\_refund: stripe.create\_refund lacks a declared approval policy; SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING on stripe.create\_refund: stripe.create\_refund has financial write capability without required controls | SHIP-POLICY-APPROVAL-MISSING \(critical\); SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING \(critical\) | — |
| Confirmation | partial | tool\_surface\_facts.controls\[kind=confirmation\_policy\]; findings\[\] | high | SHIP-POLICY-CONFIRMATION-MISSING on stripe.create\_refund: stripe.create\_refund lacks a declared confirmation policy; SHIP-POLICY-CONFIRMATION-MISSING on gmail.send\_customer\_email: gmail.send\_customer\_email lacks a declared confirmation policy | — | SHIP-POLICY-CONFIRMATION-MISSING \(high\); SHIP-POLICY-CONFIRMATION-MISSING \(high\) |
| Idempotency | partial | tool\_surface\_facts.controls\[kind=idempotency\_evidence\]; action\_surface\_facts.actions\[\].safeguards.idempotency; \+1 more | high | SHIP-SIDEFX-IDEMPOTENCY-MISSING on stripe.create\_refund: stripe.create\_refund lacks idempotency evidence; SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING on stripe.create\_refund: stripe.create\_refund has financial write capability without required controls | SHIP-SIDEFX-IDEMPOTENCY-MISSING \(critical\); SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING \(critical\) | — |
| Side effects | partial | tool\_inventory\[\].risk\_tags; action\_surface\_facts.actions\[\].effect; \+1 more | mixed | SHIP-SCHEMA-BROAD-FREE-TEXT on zendesk.update\_ticket: zendesk.update\_ticket accepts broad free-form action input; SHIP-SCHEMA-BROAD-FREE-TEXT on gmail.send\_customer\_email: gmail.send\_customer\_email accepts broad free-form action input; \+7 more | SHIP-POLICY-APPROVAL-MISSING \(critical\); SHIP-SIDEFX-IDEMPOTENCY-MISSING \(critical\); \+3 more | SHIP-SCHEMA-BROAD-FREE-TEXT \(high\); SHIP-SCHEMA-BROAD-FREE-TEXT \(high\); \+2 more |
| Memory isolation | not\_declared | — | unknown | — | — | — |
| Human-in-the-loop evidence | not\_declared | — | unknown | — | — | — |
| Prompt/scope alignment | partial | declared\_intentions; misalignments; \+2 more | medium | SHIP-SCOPE-PROHIBITED-TOOL-PRESENT on stripe.create\_refund: stripe.create\_refund appears to overlap with a prohibited action; SHIP-SCOPE-PROHIBITED-TOOL-PRESENT on gmail.send\_customer\_email: gmail.send\_customer\_email appears to overlap with a prohibited action | — | SHIP-SCOPE-PROHIBITED-TOOL-PRESENT \(high\); SHIP-SCOPE-PROHIBITED-TOOL-PRESENT \(high\) |
| Retry/timeout | not\_declared | — | unknown | — | — | — |
| Baseline debt | informational | — | unknown | — | — | — |
| Action-surface policy | partial | action\_surface\_facts.actions; findings\[\].blocks\_release; \+1 more | high | SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING on gmail.send\_customer\_email: gmail.send\_customer\_email has external communication capability without required controls; SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING on stripe.create\_refund: stripe.create\_refund has external communication capability without required controls; \+1 more | SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(high\); SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(high\); \+1 more | — |

## §2 Capability ↔ Intent diff — missing

### Declared

- Purpose: answer refund policy questions
- Purpose: prepare refund requests for human review
- Purpose: update support ticket notes
- Prohibited: issue refund without approval
- Prohibited: cancel order without explicit confirmation
- Prohibited: send external email without preview

### Observed tools

- gmail.send\_customer\_email
- refund\_status\_lookup
- send\_email\_preview
- shopify.cancel\_order
- stripe.create\_refund
- support.search\_kb
- wildcard\_mcp\_tools.\*
- zendesk.update\_ticket

### Divergences

- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` on `gmail.send\_customer\_email, stripe.create\_refund`: stripe.create\_refund appears to overlap with a prohibited action — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:22`
- `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` on `gmail.send\_customer\_email, stripe.create\_refund`: gmail.send\_customer\_email appears to overlap with a prohibited action — `.agents-shipgate/mcp-tools.json\#/tools/1` — `shipgate.yaml:24`

## §3 High-risk tool surface — partial

- Total tools: 8 · High-risk: 3

| Tool | Source | Risk tags | Approval | Idempotency |
|---|---|---|---|---|
| `gmail.send\_customer\_email` | mcp | customer\_communication, external\_write | no | no |
| `shopify.cancel\_order` | openapi | destructive, write | yes | yes |
| `stripe.create\_refund` | openapi | external\_write, financial\_action, write | no | no |

## §3A Tool-surface diff — not declared

- Status: disabled — No --diff-from report or v0.3 baseline snapshot was provided.
- Base: `none`

## §3B Action-surface diff — not declared

- Status: disabled — No action-surface comparison source was provided.
- Base: `none`

## §4 Approval policy coverage — partial

| Tool | Declared | Source | Gap finding(s) |
|---|---|---|---|
| `shopify.cancel\_order` | yes | policies | — |
| `stripe.create\_refund` | no | — | fp\_f092940f62fbb012 |

### Gap findings

- `SHIP-POLICY-APPROVAL-MISSING` (critical): stripe.create\_refund lacks a declared approval policy

## §5 Idempotency / retry risk — partial

- Retry policy: not declared

| Tool | Declared | Source | Gap finding(s) |
|---|---|---|---|
| `shopify.cancel\_order` | yes | policies | — |
| `stripe.create\_refund` | no | — | fp\_dac8011e14c53777 |

### Gap findings

- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (critical): stripe.create\_refund lacks idempotency evidence

## §6 Scope coverage — missing

### Declared scopes

- `zendesk:tickets:read`
- `zendesk:tickets:write`
- `stripe:\*`

| Scope | Declared | Used by tools |
|---|---|---|
| `gmail:send` | no | `gmail.send\_customer\_email` |
| `shopify:orders:write` | no | `shopify.cancel\_order` |
| `stripe:\*` | yes | — |
| `stripe:refunds:write` | yes | `stripe.create\_refund` |
| `support:kb:read` | no | `support.search\_kb` |
| `zendesk:tickets:read` | yes | — |
| `zendesk:tickets:write` | yes | `zendesk.update\_ticket` |

### Unused declared scopes

- `zendesk:tickets:read`

### Used by tools but not declared

- `gmail:send`
- `shopify:orders:write`
- `support:kb:read`

### Gap findings

- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): shopify.cancel\_order requires scopes not declared in the manifest
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): support.search\_kb requires scopes not declared in the manifest
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): gmail.send\_customer\_email requires scopes not declared in the manifest
- `SHIP-MANIFEST-UNUSED-SCOPE` (medium): Manifest declares unused permission scope zendesk:tickets:read

## §7 Memory isolation — not declared

- Manifest does not declare a memory isolation policy. The current manifest schema \(v0.1\) has no agent.memory field. See §10 for the residual review item.

## §8 Human-in-the-loop evidence — covered

- Configured: yes
- Human review recommended: yes
- Provenance mode: `fresh\_scan`
- Capability-linked trace rows: 0/0 matched (0 source(s))
- HITL evidence is local review evidence only. Missing local evidence does not prove a runtime control is absent, and present local evidence does not certify runtime enforcement.

### Approval-required tools

- `shopify.cancel\_order`

### Confirmation-required tools

- `shopify.cancel\_order`

## §9 Required dynamic scenarios — partial

- **Manual review for SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING** — Declare confirmation policy and safeguards.audit\_log for this external communication action.
  - Related finding(s): fp\_3c8cac2cb8af37ec, fp\_ad46dcc15d8c2969
- **Manual review for SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING** — Declare approval.required, safeguards.audit\_log, and safeguards.idempotency for this financial write action.
  - Related finding(s): fp\_3e9d5016edc50f97
- **Manual review for SHIP-AUTH-MANIFEST-BROAD-SCOPE** — Replace broad manifest permission scopes with the narrowest scopes needed for this release.
  - Related finding(s): fp\_d27325cbdbbf5483
- **Manual review for SHIP-AUTH-MISSING-SCOPE** — Declare operation-specific auth scopes for refund\_status\_lookup, or explicitly declare anonymous authority when the operation requires no credentials.
  - Related finding(s): fp\_8e1328bd648028d3
- **Manual review for SHIP-AUTH-SCOPE-COVERAGE-MISSING** — Add the required scopes for shopify.cancel\_order to permissions.scopes or narrow the tool's declared auth requirements.
  - Related finding(s): fp\_1f6cfd6b7daa9b7c, fp\_83852fbd6b440524, fp\_d8e6d1865dae97cc
- **Manual review for SHIP-INVENTORY-WILDCARD-TOOLS** — Replace wildcard tool exposure with an explicit tool allowlist before release review.
  - Related finding(s): fp\_fc02d8ecd30f2578
- **Manual review for SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING** — Declare an owner for each high-risk production tool in risk\_overrides.tools.
  - Related finding(s): fp\_fd2577850cef1f87
- **Manual review for SHIP-MANIFEST-UNUSED-SCOPE** — Remove unused manifest scopes or add tool metadata showing why they are required.
  - Related finding(s): fp\_39b9ae878f343d1b
- **Manual review for SHIP-POLICY-APPROVAL-MISSING** — Declare an approval policy for stripe.create\_refund or remove this tool from the release.
  - Related finding(s): fp\_f092940f62fbb012
- **Manual review for SHIP-POLICY-CONFIRMATION-MISSING** — Declare a user confirmation policy for stripe.create\_refund or remove this action from the release.
  - Related finding(s): fp\_8e08a4fe6b0917f6, fp\_a62ca2fd9a68a1d1
- **Manual review for SHIP-SCHEMA-BROAD-FREE-TEXT** — Constrain zendesk.update\_ticket.updates with an enum, structured schema, or narrower field-specific parameters.
  - Related finding(s): fp\_acd63b899d49aa1c, fp\_ff2f028953d1c220
- **Manual review for SHIP-SCHEMA-MISSING-BOUNDS** — Add a maximum bound to stripe.create\_refund.amount or document an equivalent limit in the tool policy.
  - Related finding(s): fp\_ab60b01cb53cfcbe
- **Manual review for SHIP-SCOPE-PROHIBITED-TOOL-PRESENT** — Remove stripe.create\_refund, narrow its policy, or revise prohibited\_actions so the manifest and tool surface do not contradict each other.
  - Related finding(s): fp\_12985c36a06026de, fp\_e090c62e390e70ab
- **Manual review for SHIP-SIDEFX-IDEMPOTENCY-MISSING** — Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.
  - Related finding(s): fp\_dac8011e14c53777
- **Re-run scan after resolving source warnings** — Source loaders emitted warnings; some tool surfaces may have been parsed with reduced confidence.

## §10 What this packet did NOT prove

Agents Shipgate is an advisory tool: the deterministic merge gate for AI-generated agent capability changes, run as a local-first, static Tool-Use Readiness review. The packet below is derived from a scan; it does not, by itself, prove the following properties:

- **Prompt robustness.** Whether the agent's prompt holds up under jailbreaks, persona drift, indirect prompt injection, or adversarial inputs.
- **Runtime behavior.** Whether the agent actually invokes only the declared tools, respects approval gates at runtime, or follows policy under load. Static config is not runtime evidence.
- **Model correctness.** Whether the underlying model produces correct outputs, calls the right tools, or stays within the declared scope. The packet does not benchmark the model.
- **Adversarial resistance.** Whether the agent withstands red-team or penetration testing. The packet does not run scenarios; it organizes evidence.

### Per-run residuals

- Source warnings:
  - MCP source declares wildcard tool exposure
  - Duplicate tool name 'send\_email\_preview'; kept mcp source 'reviewed\_sdk\_inventory' and merged metadata from sdk\_function source 'openai\_sdk\_static'.
- Low-confidence tool extractions: none
- Suppressed findings in effect: none
- Memory isolation is not modeled by the v0.1 manifest schema; no static evidence is available.
- 5 active finding\(s\) came from heuristic provenance \(keyword\_heuristic=5, regex\_heuristic=0\); review the finding evidence before acting.
