# Prompt · Verify an agent-related diff

You are reviewing or finishing a change to a tool-using AI agent. Use Agents
Shipgate as the deterministic verifier for the diff before you report that the
work is complete.

## Your task

1. **Set agent mode for structured recovery hints.**
   ```bash
   export AGENTS_SHIPGATE_AGENT_MODE=1
   ```

2. **Decide whether the diff needs Shipgate.**
   For a committed PR diff:
   ```bash
   agents-shipgate trigger --workspace . --base origin/main --head HEAD --json
   ```
   For a local pre-commit working-tree diff, or when the base ref is
   unavailable locally, use the changed-files fallback:
   ```bash
   git diff --name-only HEAD > /tmp/shipgate-changed-files.txt
   git diff HEAD > /tmp/shipgate.diff
   agents-shipgate trigger --workspace . \
     --changed-files /tmp/shipgate-changed-files.txt \
     --diff /tmp/shipgate.diff --json
   ```

   Continue when `should_run` is `true` or `force_run` is `true`. If the
   repo already has `shipgate.yaml`, CI should verify every PR; for local
   pre-commit work, verify when the changed files are agent-related or when
   you need a full advisory check before handing off.

3. **Run the verifier.**
   For local pre-commit work, omit `--head` and omit `--base` unless the base
   ref exists locally, so the checked-out working tree is scanned, including
   uncommitted edits:
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
   head-only pass. Fetch the base ref or omit `--base` for local working-tree
   verification.

4. **Read JSON, not Markdown.**
   - `agents-shipgate-reports/report.json` is the release-gate artifact.
   - `release_decision.decision` is the only gate signal.
   - `verifier_summary` is a one-fetch composition for controller output; its
     `verdict` mirrors `release_decision.decision` and never gates
     independently.
   - `agents-shipgate-reports/verifier.json` explains trigger and base-scan
     status; do not treat it as a second verdict.

5. **Do not bypass the verifier.** Do not suppress findings, lower severity,
   expand baselines or waivers, remove Shipgate CI, or soften agent
   instructions to make the run pass. Those trust-root edits are protected by
   `SHIP-VERIFY-*` findings and require human review.

6. **Report back with:**
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
  `agents-shipgate trigger` has returned a clear skip verdict.
- Do not use `summary.status` for gating; it is legacy and baseline-blind.
- Do not invent approval, confirmation, idempotency, prohibited-action,
  broad-scope, human acknowledgement, or runtime trace evidence.
- Do not commit `agents-shipgate-reports/`.

## Verification

- `agents-shipgate-reports/report.json` exists and parses.
- `agents-shipgate-reports/verifier.json` exists and parses.
- `report.json.release_decision.decision` is surfaced to the user.
- If `verifier_summary.protected_surface_touched` or `policy_weakened` is true,
  the response names the human-review requirement.
