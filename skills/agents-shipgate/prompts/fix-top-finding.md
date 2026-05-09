# Prompt · Fix the top Agents Shipgate finding

You are working in a repo with `shipgate.yaml` already in place. Run a scan and fix the highest-severity unsuppressed finding.

## Your task

1. **Run a scan and locate the top finding.**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```
   Read `agents-shipgate-reports/report.json`. For v0.12+ reports the easy path is `agent_summary.first_recommended_action.why` — for most `blocked`/`review_required` verdicts it names the top finding's `check_id` and `tool_name` directly. Two exceptions to expect:

   - **Evidence-coverage-driven `review_required`** (low-confidence/static evidence; no specific finding to fix). The action's `why` describes the evidence situation and recommends gathering MCP/OpenAPI inputs or eval traces — there is no `check_id` to parse out. If you see "low-confidence evidence" or "static-only" in the why-text, follow that guidance instead of looking for a top finding.
   - **`auto_appliable_patches > 0`**. The action proposes `apply-patches`; the why-text names the apply-patches command, not a specific finding. Walk `findings[]` for the actual top entry.

   Fall back to picking the entry with the highest severity (`critical > high > medium > low > info`) and `"suppressed": false` whenever the action doesn't name a finding directly.

2. **Look up the check definition.**
   ```bash
   agents-shipgate explain <CHECK_ID> --json
   ```
   This returns the `CheckMetadata` with `description`, `rationale`, `fires_when`, `evidence_fields`, `recommendation`.

3. **Diagnose the fix.** There are exactly four legitimate responses to a finding. v0.12+ reports project the routing via `agent_action`:

   | Response | When | `agent_action` (v0.12+) |
   |---|---|---|
   | **Add the missing policy / scope / annotation** to `shipgate.yaml` | The check is correct; the manifest just hadn't declared the safeguard yet | `propose_patch_for_review` (a `set_pointer`/`append_pointer` patch is attached) or `escalate_to_human` (no patch — you write the entry by hand) |
   | **Override the heuristic** via `risk_overrides.tools.{tool}.{tags,remove_tags}` | The risk classification is wrong (e.g. a GET endpoint that picked up the `destructive` tag because of a misleading operationId) | `escalate_to_human` |
   | **Suppress the finding** via `checks.ignore` with a `reason` | The check is correct but you've decided to accept the risk explicitly (e.g. "tool deprecated 2026-Q2") | `escalate_to_human` (the future `suppress_with_reason` value is reserved for checks that pre-classify themselves as suppressible) |
   | **Fix the underlying tool definition** | The tool spec itself is wrong (missing description, broad scope, free-form action field) | `escalate_to_human` |

4. **Apply the fix.** Edit either `shipgate.yaml` or the tool source file. Do not delete tools wholesale to silence findings.

5. **Re-scan and confirm the count went down.**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```
   The previously-failing fingerprint should be gone from `report.json`.

6. **Report back**:
   - What was the original finding (check ID, tool, severity)
   - Which of the four response types you used
   - The diff to `shipgate.yaml` (or other file) you applied
   - The new finding count

## Common fixes by check ID

| Check | Typical fix |
|---|---|
| `SHIP-POLICY-APPROVAL-MISSING` | Add the tool to `policies.require_approval_for_tools` with a reason |
| `SHIP-POLICY-CONFIRMATION-MISSING` | Add the tool to `policies.require_confirmation_for_tools` |
| `SHIP-SIDEFX-IDEMPOTENCY-MISSING` | Add an `idempotency_key` parameter, set `idempotentHint: true` annotation, or list under `policies.require_idempotency_for_tools` |
| `SHIP-AUTH-MISSING-SCOPE` | Declare the scope on the tool (in OpenAPI security or MCP metadata) and in `permissions.scopes` |
| `SHIP-AUTH-MANIFEST-BROAD-SCOPE` | Replace `*` / `admin` with the specific operation scope(s) |
| `SHIP-DOC-MISSING-DESCRIPTION` | Add a 20+ char description to the tool definition |
| `SHIP-SCHEMA-BROAD-FREE-TEXT` | Constrain the parameter with an enum, structured schema, or narrower fields |
| `SHIP-SCHEMA-MISSING-BOUNDS` | Add `maximum` to the numeric parameter |
| `SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE` | Declare the tools through MCP/OpenAPI for higher-confidence inventory; or move target to staging |

## What NOT to do

- Do not blanket-suppress an entire check. Suppressions are per-tool unless the check is genuinely irrelevant for this repo.
- Do not write `reason: "false positive"` without explanation. Reviewers should be able to read the reason and understand the decision in 60 seconds.
- Do not edit `agents-shipgate-reports/`. It's regenerated each run.

## Verification

- The previously-failing finding's fingerprint is no longer present in `report.json`
- The fix is committed in a single, focused diff (manifest change + reason)
- If you used `checks.ignore`, the `reason` is concrete (a date, a ticket link, or "tool deprecated; see roadmap")
