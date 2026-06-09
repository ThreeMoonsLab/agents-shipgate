# Golden PR — what a coding agent's output looks like

A worked example of the **artifact** a coding agent produces after running Agents Shipgate end-to-end. Sibling examples ([`mcp-only-tool-server`](mcp-only-tool-server/), [`openai-agents-sdk-refund-agent`](openai-agents-sdk-refund-agent/), [`openapi-support-agent`](openapi-support-agent/)) document the *recipe* for adopting Shipgate; this doc documents the *output* — the diff, the PR comment, and the structured summary an agent should generate.

> **Audience.** Coding agents (Claude Code, Codex, Cursor) running Shipgate inside a host repo. Use this as the template for the message you post back to the user once the verify-first flow finishes.

## Setup (the agent's actions before the PR)

The agent has just completed a verify-first run on a target repo:

```bash
agents-shipgate verify --preview --json
agents-shipgate init --workspace . --write --ci --agent-instructions=all
agents-shipgate verify --workspace . --config shipgate.yaml \
    --ci-mode advisory --format json
```

`verify` produced `agents-shipgate-reports/verifier.json` with:

- `merge_verdict: "blocked"`
- `capability_review.top_changes[]` names `stripe.create_refund`
- `first_next_action.actor: "human"`
- `fix_task.safe_to_attempt: false`

The head scan also produced `agents-shipgate-reports/report.json` with:

- `release_decision.decision: "blocked"`
- 2 blockers on `stripe.create_refund` (missing approval policy, missing idempotency)
- 14 high / 2 medium release review items; 15 need human review
- 1 auto-applicable patch (a stale suppression removed by `apply-patches`)

The agent now has to summarize this for the user.

## The expected artifact (PR comment / chat reply)

```markdown
## Agents Shipgate: blocked

**Merge verdict**: `blocked`
**Release decision**: `blocked`

This PR changes what `support-agent` can do: it adds the money-moving action
`stripe.create_refund`. Shipgate cannot clear this for merge because approval
and idempotency evidence are missing.

| Impact | Change | Subject | Why |
|---|---|---|---|
| blocks release | action added | `stripe.create_refund` | money-moving refund action lacks approval and idempotency evidence |

**Required before merge**:
1. Human owner: confirm or add approval-policy evidence for `stripe.create_refund`.
2. Human owner: confirm or add idempotency evidence for refund retries.
3. Re-run `agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json`.

**Top blockers**:
1. `stripe.create_refund` — `SHIP-POLICY-APPROVAL-MISSING` (critical).
   The refund tool ships without a declared approval policy. High-risk
   actions need an explicit human approval gate before promotion. I
   can draft `policies.require_approval_for_tools: [stripe.create_refund]`
   for you; want me to add it with a placeholder reason you can edit?
2. `stripe.create_refund` — `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (critical).
   The tool has no `idempotency_key` parameter and no policy entry.
   Without idempotency, retried refunds duplicate. The fix is either
   adding a parameter to the tool spec or declaring
   `policies.require_idempotency_for_tools: [stripe.create_refund]`
   with an external idempotency-key generator. Same draft offer.

**What I already applied** (high-confidence, no manual review needed):
- Removed 1 stale suppression entry that referenced a check ID that
  no longer exists in the catalog. Diff shown below.

**What needs your review next** (15 non-blocker review findings): I can
walk these one-by-one if you want, or you can read
`agents-shipgate-reports/report.md`. The full machine-readable list
is at `agents-shipgate-reports/report.json`; the top-level
`agent_summary` block carries the headline/action counts.

**Reports**:
- Verifier JSON: `agents-shipgate-reports/verifier.json`
- Markdown: `agents-shipgate-reports/report.md`
- JSON: `agents-shipgate-reports/report.json` (schema v0.12)
- Release Evidence Packet: `agents-shipgate-reports/packet.{md,json,html}`

**CI**: `.github/workflows/agents-shipgate.yml` already wires
`agents-shipgate@v0.12.0` in advisory mode; this PR will get a
sticky-marker comment from the Action on every push.

<details>
<summary>Diff applied by <code>apply-patches --confidence high --apply</code></summary>

```diff
--- shipgate.yaml
+++ shipgate.yaml
@@ -42,7 +42,3 @@
 checks:
   ignore:
     - check_id: SHIP-RETIRED-CHECK-ID
       tool: legacy_search
       reason: removed in v0.10
-    - check_id: SHIP-MANIFEST-STALE-SUPPRESSION
-      tool: ghost_tool
-      reason: tool removed
```

The removal is safe: the suppressed check ID either doesn't exist in
the loaded catalog (orphaned) or matches no loaded tool (ghost). Either
way, leaving the entry in place misleads future review.
</details>

<details>
<summary>Top human-review items (full list in <code>report.json</code>)</summary>

| # | Severity | Check | Tool | Action |
|---|---|---|---|---|
| 1 | high | `SHIP-AUTH-SCOPE-COVERAGE-MISSING` | `stripe.create_refund` | propose_patch_for_review (medium-confidence patch attached; I can show the diff) |
| 2 | high | `SHIP-DOC-MISSING-DESCRIPTION` | `wildcard_mcp_tools.*` | escalate_to_human (no patch; description belongs with the tool spec) |
| 3 | high | `SHIP-INVENTORY-WILDCARD-TOOLS` | `wildcard_mcp_tools.*` | escalate_to_human (replace wildcard with explicit allowlist) |
| ... | ... | ... | ... | ... |
</details>
```

## The structured summary (for downstream automation)

The agent should also be ready to surface the structured form when asked.
In a real run, copy these fields directly from `agent_summary` in
`report.json`; the object below shows the expected shape and internally
consistent counts:

```json
{
  "merge_verdict": "blocked",
  "first_next_action": {
    "actor": "human",
    "kind": "review",
    "why": "Approval and idempotency evidence cannot be invented by a coding agent."
  },
  "fix_task": {
    "actor": "human",
    "safe_to_attempt": false
  },
  "release_decision": "blocked",
  "headline": "2 active finding(s) block release; 16 review item(s) accepted as debt.",
  "blocker_count": 2,
  "review_item_count": 16,
  "auto_appliable_patches": 1,
  "needs_human_review": 17,
  "first_recommended_action": {
    "kind": "command",
    "command": "agents-shipgate apply-patches --from /abs/path/agents-shipgate-reports/report.json --confidence high --apply",
    "why": "1 finding(s) carry high-confidence patches safe to apply without human review."
  }
}
```

Here, `needs_human_review` includes the 2 blockers plus the 15 review
items whose `agent_action` requires human input. It is intentionally not
the same number as `review_item_count`, which mirrors
`release_decision.review_items`.

## What the agent did NOT do (and shouldn't have)

- ❌ Apply medium-confidence patches without showing the diff and getting confirmation. Scope strings encode policy decisions; the user has to pick.
- ❌ Add a `checks.ignore` entry to silence the approval-policy blocker. Suppression isn't a fix; the `triage-false-positive.md` workflow names the (rare) case for it.
- ❌ Edit the trace recording (`approval_trace.jsonl`) to flip `approved: true`. Trace findings are class-four "never auto-fix" — patching the trace patches the evidence, not the runtime gate.
- ❌ Fabricate a recommendation that wasn't grounded in `recommendation`, `evidence`, `patches[].instructions`, or `docs_url`.
- ❌ Dump the raw JSON in the PR comment. The structured summary above is the agent-to-agent form; the PR comment translates each finding into prose for the human reviewer.

## What to copy from this template

- **Lead with the merge verdict.** `mergeable` / `human_review_required` / `insufficient_evidence` / `blocked` / `unknown`, with the capability change on the same screen.
- **Show capability changes.** Pull the highest-signal rows from `verifier.json.capability_review.top_changes[]` before listing generic findings.
- **Name who acts next.** Use `first_next_action.actor` and `fix_task.safe_to_attempt`; a human-routed task is not safe for a coding agent to self-resolve.
- **Top blockers** named by `check_id` and `tool_name`, with a one-sentence "why it matters" pulled from `metadata.rationale` (use `agents-shipgate explain-finding <FINGERPRINT> --json`).
- **Apply / review split**. What you applied automatically, what needs human review. Always show the auto-applied diff.
- **Reports paths**. The agent shouldn't hide where the reports landed; the user may want to read them.
- **CI status**. Mention whether `init --ci` wrote a workflow.

## What to vary per scan

- **Merge verdict and capability changes** come from `verifier.json`.
- **Summary counts** in the headline come from `agent_summary.{blocker_count, review_item_count, auto_appliable_patches, needs_human_review}`.
- **Top blockers** come from `release_decision.blockers[]`. For each, run `agents-shipgate explain-finding <FINGERPRINT> --json` to get the metadata + evidence + templated explanation; quote the explanation or rewrite for tone.
- **Diff blocks** come from the `apply-patches --apply --json` output's `files` object — keyed by file path, with each entry exposing `status`, `patches`, `diff`, `error`. Iterate `Object.entries(out.files)` (or `out["files"].items()` in Python) and render each `diff` with standard `+`/`-` markers.
- **Review-item table** comes from walking `findings[]` filtered by `release_decision.review_items[].fingerprint`.

## When the merge verdict is different

- **`human_review_required`**: replace the headline with "human review required; N review item(s)". Still split by `agent_action`. Still cite the auto-applied diff if there was one.
- **`mergeable`**: a one-liner is fine ("Agents Shipgate is mergeable; advisory CI is wired."). Mention the verifier/report paths so the user can verify.
- **`unknown`**: do not call the PR mergeable. Surface `base_status`, `head_status`, and the first next action from `verifier.json`.
- **Evidence-only `human_review_required`**: the headline IS the `release_decision.reason`. Surface it verbatim with a follow-up question about whether to gather more evidence (MCP/OpenAPI inputs, eval traces).

## See also

- [`prompts/add-shipgate-to-repo.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/add-shipgate-to-repo.md) — the recipe that produces this artifact.
- [`prompts/recommend-fixes.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/recommend-fixes.md) — coordinated remediation pass.
- [`prompts/explain-finding-to-user.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/explain-finding-to-user.md) — translate one finding into user-facing prose.
- [`docs/agent-action-guide.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-action-guide.md) — per-category recipes.
- [`mcp-only-tool-server/`](mcp-only-tool-server/), [`openai-agents-sdk-refund-agent/`](openai-agents-sdk-refund-agent/), [`openapi-support-agent/`](openapi-support-agent/) — sibling golden PRs with full manifests.
