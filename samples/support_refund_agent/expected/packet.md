# Release Evidence Packet

- Project: support-refund-agent
- Agent: refund-assistant
- Environment: production\_like
- Run id: agents\_shipgate\_451f9ef602218f07
- Generated at: 2026-01-01T00:00:00\+00:00
- Packet schema: 0\.13

This packet is a reviewer-shaped synthesis of a static Agents Shipgate scan. See §10 for what the packet does *not* prove.

## §1 Release decision — BLOCKED

- Decision: `blocked`
- Reason: 5 active findings block release.
- Blockers: 5
- Review items: 12

### CI gate behavior (informational)

- ci_mode: `advisory`, would_fail_ci: `false`, exit code: `0`
- Note: CI behavior is metadata about the run gate, not the verdict. The verdict above derives from `release_decision.decision`.

### Static semantic coverage

- Pass-eligible actions: 0/7
- Evidence gaps: 7
- Known authority review concerns: 1
- Reasons: conflicting\_binding\_evidence=7, unscoped\_authority=1

### Blockers

- `SHIP-POLICY-APPROVAL-MISSING` (critical): stripe.create\_refund lacks a declared approval policy — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:82`
- `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (critical): stripe.create\_refund lacks idempotency evidence — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:87`
- `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING` (high): gmail.send\_customer\_email has external communication capability without required controls — `.agents-shipgate/mcp-tools.json\#/tools/1`
- `SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING` (high): stripe.create\_refund has external communication capability without required controls — `specs/support-tools.openapi.yaml:97`
- `SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING` (critical): stripe.create\_refund has financial write capability without required controls — `specs/support-tools.openapi.yaml:97`

### Review items

- `SHIP-SCHEMA-FREEFORM-OUTPUT` (medium): send\_email\_preview returns free-form text output — `agents/refund\_agent.py:5`
- `SHIP-AUTH-MANIFEST-BROAD-SCOPE` (high): Manifest declares broad permission scopes — `shipgate.yaml:91`
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): shopify.cancel\_order requires scopes not declared in the manifest — `specs/support-tools.openapi.yaml:116`
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): support.search\_kb requires scopes not declared in the manifest — `.agents-shipgate/mcp-tools.json\#/tools/0`
- `SHIP-AUTH-MISSING-SCOPE` (high): refund\_status\_lookup lacks declared auth scopes — `specs/support-tools.openapi.yaml:72`
- `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (high): gmail.send\_customer\_email requires scopes not declared in the manifest — `.agents-shipgate/mcp-tools.json\#/tools/1`
- `SHIP-POLICY-CONFIRMATION-MISSING` (high): stripe.create\_refund lacks a declared confirmation policy — `specs/support-tools.openapi.yaml:97` — `shipgate.yaml:85`
- `SHIP-POLICY-CONFIRMATION-MISSING` (high): gmail.send\_customer\_email lacks a declared confirmation policy — `.agents-shipgate/mcp-tools.json\#/tools/1` — `shipgate.yaml:85`
- `SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING` (high): shopify.cancel\_order is high-risk but has no owner — `specs/support-tools.openapi.yaml:116`
- `SHIP-MANIFEST-UNUSED-SCOPE` (medium): Manifest declares unused permission scope zendesk:tickets:read — `shipgate.yaml`
- `SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE` (medium): send\_email\_preview overrides inferred external\_communication evidence with a reviewed read declaration — `inventories/sdk-tools.json\#/tools/0`
- `SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE` (medium): support.search\_kb overrides inferred financial\_write evidence with a reviewed read declaration — `.agents-shipgate/mcp-tools.json\#/tools/0`

## §1A Evidence matrix — compact review summary

- Evidence Matrix Light is derived from public report.json only. Release decisions, CI exit behavior, and baseline semantics remain owned by release\_decision. Domain rows intentionally overlap; a single finding can appear in multiple rows when it is relevant to each review lens.

| Domain | Evidence present | Evidence source | Confidence | Missing controls | Blocking findings | Review items |
|---|---|---|---|---|---|---|
| Inventory | covered | tool\_inventory; tool\_surface; \+1 more | medium | — | — | — |
| Schema | partial | tool\_surface\_facts.tools\[\].hashes; findings\[\] | medium | SHIP-SCHEMA-FREEFORM-OUTPUT on send\_email\_preview: send\_email\_preview returns free-form text output | — | SHIP-SCHEMA-FREEFORM-OUTPUT \(medium\) |
| Auth | partial | tool\_surface\_facts.scopes; tool\_inventory\[\].auth\_scopes; \+1 more | mixed | SHIP-AUTH-MANIFEST-BROAD-SCOPE: Manifest declares broad permission scopes; SHIP-AUTH-SCOPE-COVERAGE-MISSING on shopify.cancel\_order: shopify.cancel\_order requires scopes not declared in the manifest; \+4 more | — | SHIP-AUTH-MANIFEST-BROAD-SCOPE \(high\); SHIP-AUTH-SCOPE-COVERAGE-MISSING \(high\); \+4 more |
| Approval | partial | tool\_surface\_facts.controls\[kind=approval\_policy\]; findings\[\] | high | SHIP-POLICY-APPROVAL-MISSING on stripe.create\_refund: stripe.create\_refund lacks a declared approval policy; SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING on stripe.create\_refund: stripe.create\_refund has financial write capability without required controls | SHIP-POLICY-APPROVAL-MISSING \(critical\); SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING \(critical\) | — |
| Confirmation | partial | tool\_surface\_facts.controls\[kind=confirmation\_policy\]; findings\[\] | high | SHIP-POLICY-CONFIRMATION-MISSING on stripe.create\_refund: stripe.create\_refund lacks a declared confirmation policy; SHIP-POLICY-CONFIRMATION-MISSING on gmail.send\_customer\_email: gmail.send\_customer\_email lacks a declared confirmation policy | — | SHIP-POLICY-CONFIRMATION-MISSING \(high\); SHIP-POLICY-CONFIRMATION-MISSING \(high\) |
| Idempotency | partial | tool\_surface\_facts.controls\[kind=idempotency\_evidence\]; action\_surface\_facts.actions\[\].safeguards.idempotency; \+1 more | high | SHIP-SIDEFX-IDEMPOTENCY-MISSING on stripe.create\_refund: stripe.create\_refund lacks idempotency evidence; SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING on stripe.create\_refund: stripe.create\_refund has financial write capability without required controls | SHIP-SIDEFX-IDEMPOTENCY-MISSING \(critical\); SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING \(critical\) | — |
| Side effects | partial | tool\_inventory\[\].risk\_tags; action\_surface\_facts.actions\[\].effect; \+1 more | high | SHIP-POLICY-APPROVAL-MISSING on stripe.create\_refund: stripe.create\_refund lacks a declared approval policy; SHIP-POLICY-CONFIRMATION-MISSING on stripe.create\_refund: stripe.create\_refund lacks a declared confirmation policy; \+5 more | SHIP-POLICY-APPROVAL-MISSING \(critical\); SHIP-SIDEFX-IDEMPOTENCY-MISSING \(critical\); \+3 more | SHIP-POLICY-CONFIRMATION-MISSING \(high\); SHIP-POLICY-CONFIRMATION-MISSING \(high\) |
| Memory isolation | not\_declared | — | unknown | — | — | — |
| Human-in-the-loop evidence | not\_declared | — | unknown | — | — | — |
| Prompt/scope alignment | covered | declared\_intentions; misalignments; \+1 more | medium | — | — | — |
| Retry/timeout | not\_declared | — | unknown | — | — | — |
| Baseline debt | informational | — | unknown | — | — | — |
| Action-surface policy | partial | action\_surface\_facts.actions; findings\[\].blocks\_release; \+1 more | high | SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE on send\_email\_preview: send\_email\_preview overrides inferred external\_communication evidence with a reviewed read declaration; SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE on support.search\_kb: support.search\_kb overrides inferred financial\_write evidence with a reviewed read declaration; \+3 more | SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(high\); SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING \(high\); \+1 more | SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE \(medium\); SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE \(medium\) |

## §2 Capability ↔ Intent diff — covered

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
- zendesk.update\_ticket

## §3 High-risk tool surface — partial

- Total tools: 7 · High-risk: 5

| Tool | Source | Risk tags | Approval | Idempotency |
|---|---|---|---|---|
| `gmail.send\_customer\_email` | mcp | customer\_communication, external\_write | no | no |
| `send\_email\_preview` | mcp | read\_only | no | no |
| `shopify.cancel\_order` | openapi | destructive, write | yes | yes |
| `stripe.create\_refund` | openapi | external\_write, financial\_action, write | no | no |
| `support.search\_kb` | mcp | read\_only | no | no |

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
| `stripe.create\_refund` | no | — | fp\_973ea0ef2110ca9a |

### Gap findings

- `SHIP-POLICY-APPROVAL-MISSING` (critical): stripe.create\_refund lacks a declared approval policy

## §5 Idempotency / retry risk — partial

- Retry policy: not declared

| Tool | Declared | Source | Gap finding(s) |
|---|---|---|---|
| `shopify.cancel\_order` | yes | policies | — |
| `stripe.create\_refund` | no | — | fp\_2cf0d6c77d9c3eee |

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

- **Manual review for SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE** — Confirm the recorded override for send\_email\_preview: Renders a draft for the agent to show; gmail.send\_customer\_email delivers.
  - Related finding(s): fp\_92bb0d5fa615e120, fp\_b46a7d3061029897
- **Manual review for SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING** — Declare confirmation policy and safeguards.audit\_log for this external communication action.
  - Related finding(s): fp\_1c94d2d2693dccdf, fp\_e042ce7813b97a2d
- **Manual review for SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING** — Declare approval.required, safeguards.audit\_log, and safeguards.idempotency for this financial write action.
  - Related finding(s): fp\_dfa27ad5b52d8fd6
- **Manual review for SHIP-AUTH-MANIFEST-BROAD-SCOPE** — Replace broad manifest permission scopes with the narrowest scopes needed for this release.
  - Related finding(s): fp\_df4a990cca9f936b
- **Manual review for SHIP-AUTH-MISSING-SCOPE** — Declare operation-specific auth scopes for refund\_status\_lookup, or explicitly declare anonymous authority when the operation requires no credentials.
  - Related finding(s): fp\_519cb82f038efd10
- **Manual review for SHIP-AUTH-SCOPE-COVERAGE-MISSING** — Add the required scopes for shopify.cancel\_order to permissions.scopes or narrow the tool's declared auth requirements.
  - Related finding(s): fp\_095f3a5337124f6e, fp\_1fd01b4ed2e41d51, fp\_24d610b0d4324190
- **Manual review for SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING** — Declare an owner for each high-risk production tool in risk\_overrides.tools.
  - Related finding(s): fp\_674fa79ae9993422
- **Manual review for SHIP-MANIFEST-UNUSED-SCOPE** — Remove unused manifest scopes or add tool metadata showing why they are required.
  - Related finding(s): fp\_609d62f4dc434961
- **Manual review for SHIP-POLICY-APPROVAL-MISSING** — Declare an approval policy for stripe.create\_refund or remove this tool from the release.
  - Related finding(s): fp\_973ea0ef2110ca9a
- **Manual review for SHIP-POLICY-CONFIRMATION-MISSING** — Declare a user confirmation policy for stripe.create\_refund or remove this action from the release.
  - Related finding(s): fp\_c762eebfadaf39d9, fp\_fae2921fd2d0cbd5
- **Manual review for SHIP-SCHEMA-FREEFORM-OUTPUT** — Prefer a structured output schema for send\_email\_preview, especially when output is later passed back into model context.
  - Related finding(s): fp\_70c544942ba7c6ab
- **Manual review for SHIP-SIDEFX-IDEMPOTENCY-MISSING** — Add an idempotency key, idempotent annotation, or declared idempotency policy for stripe.create\_refund.
  - Related finding(s): fp\_2cf0d6c77d9c3eee
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
- Low-confidence tool extractions: none
- Suppressed findings in effect: none
- Memory isolation is not modeled by the v0.1 manifest schema; no static evidence is available.
