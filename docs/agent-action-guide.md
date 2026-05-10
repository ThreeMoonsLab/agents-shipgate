# Agent Action Guide

Per-category recipe for what a coding agent should DO when it sees a Shipgate finding. Companion to [`autofix-policy.md`](autofix-policy.md) (which describes the four-class autofix model) and [`agent-recipes.md`](agent-recipes.md) (the canonical 4-call flow). This doc is the lookup table for "I have a finding with `check_id: SHIP-X-Y`; what's the right move?"

> **Audience.** AI coding agents reading a specific finding from `agents-shipgate-reports/report.json`. Drives the routing inside [`prompts/recommend-fixes.md`](../prompts/recommend-fixes.md) and [`prompts/fix-top-finding.md`](../prompts/fix-top-finding.md).

## How to use this doc

1. Open the finding in `report.json`. Read its `check_id`, `category`, and `agent_action`.
2. Find the category row in the table below.
3. Apply the recipe **only after** confirming `agent_action` matches the recipe's expected action. If it doesn't, the per-finding fields override this doc — use [`explain-finding`](../README.md#for-coding-agents) to get the canonical projection.
4. Surface the recommended fix to the user. Use [`prompts/explain-finding-to-user.md`](../prompts/explain-finding-to-user.md) when you need to translate it into prose.

The recipes assume the user owns the manifest and the tool sources. For checks against third-party tools (e.g. an MCP server you didn't write), the recipe usually shifts from "fix the spec" to "narrow the surface or add a wrapper."

## Quick reference

| Category | Typical `agent_action` | One-line recipe | Last-resort suppression |
|---|---|---|---|
| `manifest` | `auto_apply` | Run `apply-patches --confidence high --apply`; the patch is a stale-entry removal | Don't suppress — fix the stale entry instead |
| `inventory` | `escalate_to_human` | Replace wildcard tools with an explicit allowlist; declare MCP/OpenAPI sources for high-confidence inventory | Acceptable if tool surface is intentionally broad and audited externally |
| `documentation` | `escalate_to_human` | Add a 20+ char description to the tool definition (in OpenAPI / MCP / SDK source) | Acceptable for tools slated for imminent removal — record the deletion ticket in the `reason` |
| `schema` | `escalate_to_human` | Tighten the parameter schema (add `maximum`, replace free-form strings with enums, structure free-form output) | Almost never — broad schemas are the actual risk |
| `auth` | `propose_patch_for_review` for scope-coverage; `escalate_to_human` for broad-scope | Append the missing scope to `permissions.scopes` (medium-confidence patch attached) OR replace `*`/`admin` with operation-specific scopes | Suppress only if a wrapper enforces narrower scope at runtime |
| `scope` | `escalate_to_human` | Either remove the offending tool from this release or update `agent.declared_purpose` / `prohibited_actions` to match | Don't suppress — the contradiction will surface again |
| `policy` | `escalate_to_human` | Add the tool to `policies.require_approval_for_tools` / `require_confirmation_for_tools` / `require_idempotency_for_tools` with a reviewer-visible reason | Acceptable for purely-internal tools with explicit ADR linking the suppression |
| `side_effects` | `escalate_to_human` | Constrain blast-radius parameters (max amount, target allowlist) or split into separate tools per scope | Don't suppress — the risk is the action surface, not the check |
| `evidence` | `escalate_to_human` | Add the missing local HITL evidence file (approval trace / override log / promotion criteria); fix `validation.required_evidence` flags if intentionally lower posture | Acceptable when the deployment legitimately doesn't have HITL evidence — explicitly document the review posture |
| `security` | `escalate_to_human` | Rewrite the offending text — secrets out of descriptions, neutralize prompt-like directives | Almost never — these are real findings |
| `api` (OpenAI) | `escalate_to_human` | Tighten response schemas, add decision/refusal/error fields, align prompt scope with enabled tools | Suppress only when the artifact is being deprecated |
| `adk` / `langchain` / `crewai` | `escalate_to_human` | Provide explicit MCP/OpenAPI inputs OR declare a local tool inventory (`tool_inventories[]`); add eval files for production targets | Suppress when the framework's static surface is intentionally limited and runtime evidence is provided separately |

## Per-category prescriptions

### `manifest` — auto-applicable in most cases

Stale-suppression / stale-policy / stale-risk-override findings emit a high-confidence `remove_pointer` patch when the match is unique. The recipe is:

```bash
agents-shipgate apply-patches --from agents-shipgate-reports/report.json \
    --confidence high --apply
```

**Caveat**: when multiple suppressions match the same stale check, the generator falls back to `ManualPatch` and `agent_action` becomes `escalate_to_human` — the agent should surface which entries were ambiguous and ask the user to pick.

### `inventory` — narrow the surface

`SHIP-INVENTORY-WILDCARD-TOOLS` and friends fire when the agent exposes "all tools" via a wildcard. There's no patch — fix the source:

- **MCP-only repos**: replace the wildcard with an explicit `tools` array in the export.
- **ADK / LangChain / CrewAI**: declare `tool_inventories[]` with a static JSON listing the resolved tools.
- **OpenAPI**: ensure all paths are documented; the wildcard usually means a path glob that's broader than reality.

`SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE` fires when the production target depends on best-effort SDK inference. Either move the target to staging while the inventory matures, or provide an MCP/OpenAPI source.

### `documentation` — fix the source

Most documentation findings are pure source-side fixes. Add a description (20+ chars), remove instruction-override-like language, remove secrets. **Do not** add the description to `shipgate.yaml` — descriptions live with the tool itself.

### `schema` — constrain the parameter

`SHIP-SCHEMA-MISSING-BOUNDS` on a numeric field: add `maximum` (and optionally `minimum`). `SHIP-SCHEMA-BROAD-FREE-TEXT`: replace `type: string` with an `enum`, or split the field into structured sub-fields. `SHIP-SCHEMA-FREEFORM-OUTPUT`: add a `response_format` (OpenAI) or structured output schema.

### `auth` — scope coverage gets a medium-confidence patch

`SHIP-AUTH-SCOPE-COVERAGE-MISSING` emits an `append_pointer` patch at `medium` confidence. The recipe is:

1. Show the user the patch's `value` (the proposed scope) and `pointer` (`/permissions/scopes/-`).
2. Ask whether the proposed scope is the right one — scope strings encode policy choices and shouldn't be auto-appended.
3. After confirmation, apply with `--confidence medium`:
   ```bash
   agents-shipgate apply-patches --from agents-shipgate-reports/report.json \
       --confidence medium --apply
   ```

`SHIP-AUTH-MANIFEST-BROAD-SCOPE` (`*` / `admin` declared at manifest level) is `escalate_to_human` — the agent must NOT auto-pick a narrower scope. Surface the broad scope to the user with a list of alternatives and let them choose.

### `scope` — purpose vs. capability mismatch

`SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE` and `SHIP-SCOPE-PROHIBITED-TOOL-PRESENT` are contradiction findings. There's no automatic resolution; the user has to decide whether the tool is in scope (then update `agent.declared_purpose` / drop the entry from `prohibited_actions`) or out of scope (then remove the tool from the release surface). Suppression is wrong because the contradiction will keep surfacing on every scan.

### `policy` — declare approval / confirmation / idempotency

The most common "release_decision: blocked" cause. Recipe:

```yaml
# shipgate.yaml
policies:
  require_approval_for_tools:
    - stripe.create_refund
  require_confirmation_for_tools:
    - gmail.send_customer_email
  require_idempotency_for_tools:
    - stripe.create_refund
```

**Trace findings (`SHIP-API-TRACE-APPROVAL-MISSING`, `SHIP-API-TRACE-CONFIRMATION-MISSING`) are class-four "never auto-fix"** per [`autofix-policy.md`](autofix-policy.md). Do NOT edit the trace recording to flip `approved: true`/`confirmed: true` — that patches the evidence, not the runtime gate. The fix is to implement the runtime gate and let it produce a real trace.

### `side_effects` — narrow blast radius

Add bounds to risky parameters (max refund amount, max email recipients), or split the tool into per-scope variants (`refund_small` / `refund_admin`). The check fires precisely when the surface is too broad to reason about; the recipe is to make the surface narrower, not to suppress.

### `evidence` — add or document the HITL files

`SHIP-EVIDENCE-APPROVAL-TRACE-MISSING` and friends fire when `validation.required_evidence` declares a posture but the local artifact files are missing or empty. Recipe:

1. If the posture is correct: add the missing approval trace / override log / promotion criteria file at the path declared in the manifest.
2. If the posture is wrong: lower the `validation.target_review_posture` and clear the `required_evidence` flags that don't apply.
3. Either way, **don't fabricate evidence**. The `ManualPatch.instructions` for these checks include explicit anti-pattern language to that effect.

### `security` — fix the offending text

`SHIP-DOC-SECRET-IN-DESCRIPTION` (description carries an API key shape): rotate the secret, then remove from the description. `SHIP-DOC-INJECTION-RISK`: rewrite the text to be neutral metadata without instruction-override directives.

### `api` (OpenAI Messages API artifacts)

The OpenAI API category covers checks against `tools/openai-tools.json`, `prompts/`, `policies/openai-*.yaml`, and `tests/openai-cases.json`. Most are about response-format strictness and prompt-tool alignment. Recipe per check is in [`docs/checks.md`](checks.md).

### `adk` / `langchain` / `crewai` — provide static evidence

Framework-specific findings usually fire because the agent has dynamic toolsets / factory wrappers / runtime-loaded tools that the static AST extractor can't see. Recipe:

- **ADK dynamic toolsets**: provide a static MCP export, an OpenAPI spec, OR a local tool inventory file (`google_adk.tool_inventories[]`). Eval coverage findings need eval files declared in `google_adk.eval_sets`.
- **LangChain / CrewAI**: declare `langchain.tool_inventories[]` / `crewai.tool_inventories[]` pointing at JSON files listing the resolved tools.

## What NOT to do

- Do **not** treat `agent_action: auto_apply` as universal. Even auto-applicable findings should preview the diff before mutation in this guide's recipes — surface the patch to the user before `--apply`.
- Do **not** suppress to silence — every `checks.ignore` entry must have a `reason` that names the audit trail (ADR link, ticket, deprecation date). Empty or vague reasons fail manifest validation.
- Do **not** add `risk_overrides` as a default response. Overrides change the underlying risk classification; suppressions accept a specific finding. Use [`triage-false-positive.md`](../prompts/triage-false-positive.md) to decide between them.
- Do **not** edit trace files (approval_trace, override_log) to silence trace findings. The trace is evidence; flip the runtime gate, not the recording.
- Do **not** apply `--confidence medium` patches without showing the user the proposed `value` first. Scope strings, prompts, and policy entries encode decisions that shouldn't be auto-picked.

## See also

- [`autofix-policy.md`](autofix-policy.md) — the four-class autofix model and the catalog/Finding contract.
- [`agent-recipes.md`](agent-recipes.md) — the canonical 4-call flow.
- [`agent-contract-current.md`](agent-contract-current.md) — current schema versions and the `agent_action` enum.
- [`upstream-integrations.md`](upstream-integrations.md) — per-framework drop-in instructions.
- [`prompts/recommend-fixes.md`](../prompts/recommend-fixes.md) — coordinated remediation pass across all active findings.
- [`prompts/fix-top-finding.md`](../prompts/fix-top-finding.md) — single-finding deep dive.
- [`prompts/explain-finding-to-user.md`](../prompts/explain-finding-to-user.md) — translate a finding into user-facing prose.
- [`prompts/triage-false-positive.md`](../prompts/triage-false-positive.md) — override vs. suppress decision tree.
