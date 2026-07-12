# Prompt · Verify an agent-related diff

You are reviewing or finishing a change to a tool-using AI agent. Use Agents
Shipgate as the deterministic verifier for the diff before you report that the
work is complete.

## Your task

1. **Set agent mode for structured recovery hints.**
   ```bash
   export AGENTS_SHIPGATE_AGENT_MODE=1
   ```

2. **Use verify preview only when relevance or setup is unclear.**
   ```bash
   agents-shipgate verify --preview --json
   ```
   Preview is a lightweight verify entry point: no manifest required, no scan,
   exit 0. It tells you whether to configure Shipgate, skip, or run the full
   verifier. If the repo already has `shipgate.yaml`, proceed to full verify.

3. **Treat protected-surface edits as verifier-owned review.**
   Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules,
   policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex
   plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, do not
   self-approve the trust-root change. Run full verify before reporting
   completion and route human review when the verifier requires it.

4. **Run the verifier.**
   For local uncommitted work, omit `--head` and omit `--base` so the
   checked-out working tree is scanned, including uncommitted edits:
   ```bash
   agents-shipgate verify --workspace . --config shipgate.yaml \
     --ci-mode advisory --format json
   ```
   For committed PR or CI verification, pass the head ref explicitly:
   ```bash
   agents-shipgate verify --workspace . --config shipgate.yaml \
     --base origin/main --head HEAD --ci-mode advisory --format json
   ```
   `verify` never fetches. If you pass `--base` and that ref is missing,
   `verify` exits 2 with an unknown merge verdict instead of producing a
   head-only pass. Fetch the base ref for committed PR/CI verification, or
   omit `--base` for local working-tree verification.

5. **Read JSON, not Markdown.**
   - `agents-shipgate-reports/verifier.json` is the PR/control artifact.
   - Switch on `control.state`, then read `merge_verdict` and `applicability`, and
     inspect `control.next_action.actor` and `fix_task.safe_to_attempt`.
   - `agents-shipgate-reports/report.json` is the release-gate artifact.
   - `release_decision.decision` is the only gate signal.
   - `capability_review.top_changes[]` and `verifier_summary` are
     supporting/provisional composition summaries; verdict-like values mirror
     `release_decision.decision` and never gate independently.

6. **Do not bypass the verifier.** Do not suppress findings, lower severity,
   expand baselines or waivers, remove Shipgate CI, or soften agent
   instructions to make the run pass. Those trust-root edits are protected by
   `SHIP-VERIFY-*` findings and require human review.

7. **Report back with:**
   - `merge_verdict` and `headline` from `verifier.json`
   - `capability_review.top_changes[]`
   - `control.next_action.actor` and `fix_task.safe_to_attempt`
   - `release_decision.decision` and `release_decision.reason`
   - blocker count and review-item count
   - `verifier_summary.protected_surface_touched`
   - `verifier_summary.policy_weakened`
   - top `verifier_summary.top_reason_codes[]`
   - whether `verifier.json.base_status` was `succeeded`, `cache_hit`, or a
     degraded status
   - the next safe action from `agent_summary.first_recommended_action`

## What NOT to do

- Do not claim the diff is verified until `agents-shipgate verify` has run or
  `agents-shipgate verify --preview --json` has returned a clear skip verdict.
- Do not claim completion unless `control.state` is `complete`.
  Conversation-level acknowledgement cannot clear a human-review route; only
  a new verifier artifact can change control state.
- Do not use `summary.status` for gating; it is legacy and baseline-blind.
- Do not invent action effect, action authority, approval, confirmation,
  idempotency, prohibited-action, broad-scope, human acknowledgement, or
  runtime trace evidence.
- Do not commit `agents-shipgate-reports/`.

## Verification

- `agents-shipgate-reports/report.json` exists and parses.
- `agents-shipgate-reports/verifier.json` exists and parses.
- `verifier.json.merge_verdict` is surfaced to the user.
- `control.state` and `applicability` are considered before generic findings.
- `capability_review.top_changes[]` is treated as supporting/provisional review
  context.
- `report.json.release_decision.decision` is surfaced to the user.
- If `verifier_summary.protected_surface_touched` or `policy_weakened` is true,
  the response names the human-review requirement.
