---
description: Bootstrap or verify agents-shipgate as the deterministic merge gate for AI-generated agent capability changes
---

Arguments: `$ARGUMENTS`

If the arguments include `verify`, run the ongoing-PR verifier flow. Otherwise
run the agents-shipgate bootstrap flow on the current repo: install the CLI,
add the deterministic merge gate for AI-generated agent capability changes (a
local-first, static Tool-Use Readiness review), generate `shipgate.yaml`, fill
in placeholders, run a scan, and surface the top findings from the JSON report.

The canonical, self-contained instructions live in the bundled prompt files.
For bootstrap, read `prompts/add-shipgate-to-repo.md`. For verifier runs, read
`prompts/verify-agent-diff.md`. Try these paths in order; use the first that
exists:

1. `.claude/skills/agents-shipgate/prompts/<recipe>.md` — bundled with the `agents-shipgate` skill if installed in this project.
2. `prompts/<recipe>.md` — present when this repo is a clone of `agents-shipgate` itself.
3. `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/<recipe>.md` — last-resort fetch.

Verifier command:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify \
  --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD \
  --ci-mode advisory --format json
```

For local uncommitted work, omit `--base`/`--head` so uncommitted edits are
scanned. For committed PR/CI refs, make the base ref available first because
`verify` never fetches.

Required behavior (do not skip):

1. Set `AGENTS_SHIPGATE_AGENT_MODE=1` for every CLI call so errors emit a `next_action` JSON line on stderr.
2. Run `agents-shipgate contract --json` when available and use it to verify the installed CLI's schema versions and gating signal.
3. Confirm with the user before running `agents-shipgate init --workspace . --write` (it writes `shipgate.yaml` to the workspace).
4. Parse `agents-shipgate-reports/report.json` directly — do not scrape the markdown. **For release gating, read `release_decision.decision` first** (`"blocked" | "review_required" | "insufficient_evidence" | "passed"`; baseline-aware, v0.8+; `insufficient_evidence` added v0.14) along with `release_decision.{reason, blockers, review_items, fail_policy.would_fail_ci}`. Other stable fields: `findings[].{check_id, severity, tool_name, recommendation}`. For reviewer triage by source reliability, run `agents-shipgate findings --from agents-shipgate-reports/report.json --provenance-kind keyword_heuristic,regex_heuristic --json`; `findings[].provenance_kind` is not a gate input. `summary.{critical_count, high_count, medium_count, status}` is legacy and baseline-blind — kept for v0.7 callers, do not lead with it. The Release Evidence Packet is at `agents-shipgate-reports/packet.{md,json,html}`. Full contract: [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md).
5. For verifier runs, parse `agents-shipgate-reports/verifier.json` first and lead with `merge_verdict`, `can_merge_without_human`, `first_next_action`, `fix_task`, and `capability_review.top_changes`; then read `agents-shipgate-reports/report.json.release_decision.decision` as the underlying release gate.
6. Do **not** bypass the verifier by suppressing findings, lowering severity, expanding baselines or waivers, removing Shipgate CI, or weakening agent instructions. Verify-mode `SHIP-VERIFY-*` checks route those trust-root edits to human review.
7. Add `agents-shipgate-reports/` to `.gitignore` if it is not already.
8. Do **not** run `agents-shipgate baseline save` in this flow — baselining is a separate decision.

Report back: `release_decision.decision` and `reason`, `merge_verdict`,
`can_merge_without_human`, blocker / review-item counts, top 3 active findings
by severity, `verifier_summary` trust-root flags when present, capability
change highlights, and one suggested next step.

## Ongoing PRs

The bootstrap flow above wires Shipgate into the repo. For an ongoing PR that
changes agent tools, MCP exports, OpenAPI specs, prompts, permissions, policies,
CI gates, or `shipgate.yaml`, run the verifier instead:

```bash
agents-shipgate verify --base origin/main --head HEAD --json
```

Read `agents-shipgate-reports/verifier.json` first and lead with `merge_verdict`
(a deterministic projection of `release_decision.decision`, which remains the
gate in `report.json`), then `capability_changes[]`. Do not claim completion when
`merge_verdict` is `blocked`, `insufficient_evidence`, or
`human_review_required` unless the user accepted the human-review requirement, and
never weaken `shipgate.yaml`, Shipgate CI, `AGENTS.md`, policies, baselines, or
waivers to make Shipgate pass.
