---
description: "Run the prominent Agents Shipgate flows: check, verify, or audit --host"
---

Arguments: `$ARGUMENTS`

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes.

If the arguments include `audit`, run the host-grant audit flow. If they include
`check`, run the local boundary check. Otherwise run the verifier flow. The
supporting adoption and scan commands remain available, but this slash command
should lead with only the prominent flows: `shipgate check`, `agents-shipgate verify`,
and `shipgate audit --host`.

The canonical, self-contained verifier instructions live in the bundled prompt
files. For verifier runs, read `prompts/verify-agent-diff.md`. Try these paths
in order; use the first that exists:

1. `${CLAUDE_PLUGIN_ROOT}/skills/agents-shipgate/prompts/<recipe>.md` — when
   this command runs from the installed `agents-shipgate` plugin, Claude Code
   expands `${CLAUDE_PLUGIN_ROOT}` to the plugin directory and the
   version-matched recipes are bundled there. (When the command is a committed
   project file instead, the variable is not expanded and this path simply
   won't exist — continue down the list.)
2. `.claude/skills/agents-shipgate/prompts/<recipe>.md` — bundled with the `agents-shipgate` skill if installed in this project.
3. `prompts/<recipe>.md` — present when this repo is a clone of `agents-shipgate` itself.
4. `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/<recipe>.md` — last-resort unpinned fetch; prefer any bundled copy above.

Prominent commands:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 shipgate check \
  --agent claude-code --workspace . --format agent-boundary-json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate verify \
  --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD \
  --ci-mode advisory --format json
AGENTS_SHIPGATE_AGENT_MODE=1 shipgate audit --host \
  --json --out agents-shipgate-reports/host-grants.json
```

For local uncommitted work, omit `--base`/`--head` so uncommitted edits are
scanned. For committed PR/CI refs, make the base ref available first because
`verify` never fetches.

Required behavior (do not skip):

1. Set `AGENTS_SHIPGATE_AGENT_MODE=1` for every CLI call so errors emit a `next_action` JSON line on stderr.
2. Run `agents-shipgate contract --json` when available and use it to verify the installed CLI's schema versions and gating signal.
3. For verifier runs, validate `agents-shipgate-reports/verification-receipt.json` first,
   then parse `agents-shipgate-reports/agent-handoff.json`,
   then `verifier.json`, `verify-run.json`, and
   `report.json.release_decision.decision` as the release gate.
4. For check runs, parse stdout as `shipgate.agent_boundary_result/v1` and
   switch on `control.state`; follow `control.next_action`,
   `control.allowed_next_commands`, and `control.human_review`. Treat
   `decision` as diagnostic context only.
5. For host audits, parse `agents-shipgate-reports/host-grants.json` when
   `--out` is used, or stdout when running JSON-only.
6. Do **not** bypass the verifier by suppressing findings, lowering severity,
   expanding baselines or waivers, removing Shipgate CI, or weakening agent
   instructions. Verify-mode `SHIP-VERIFY-*` checks route those trust-root edits
   to human review.
7. Add `agents-shipgate-reports/` to `.gitignore` if it is not already.

Report back: `release_decision.decision` and `reason`, `merge_verdict`,
`can_merge_without_human`, blocker / review-item counts, top 3 active findings
by severity, `verifier_summary` trust-root flags when present, capability
change highlights, and one suggested next step.

## Ongoing PRs

For an ongoing PR that changes agent tools, MCP exports, OpenAPI specs, prompts,
permissions, policies, CI gates, or `shipgate.yaml`, run the verifier:

```bash
agents-shipgate verify --base origin/main --head HEAD --json
```

Validate `agents-shipgate-reports/verification-receipt.json` first, then read
`agents-shipgate-reports/agent-handoff.json` and lead with
`gate.merge_verdict` (a deterministic projection of `release_decision.decision`,
which remains the gate in `report.json`), then the authoritative substrate
`agents-shipgate-reports/verifier.json` and supporting/provisional
`capability_review.top_changes[]`. Do not claim completion unless
`control.state` is `complete`; conversation-level acknowledgement cannot clear
a human-review route. Never weaken `shipgate.yaml`, Shipgate CI, `AGENTS.md`, policies, baselines, or
waivers to make Shipgate pass.
