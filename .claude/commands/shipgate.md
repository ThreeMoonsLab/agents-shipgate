---
description: Bootstrap agents-shipgate (install, init, scan, report top findings)
---

Run the agents-shipgate bootstrap flow on the current repo: install the CLI, generate `shipgate.yaml`, fill in placeholders, run a scan, and surface the top findings from the JSON report.

The canonical, self-contained instructions live in `prompts/add-shipgate-to-repo.md`. Read that file first and follow it verbatim. Try these paths in order; use the first that exists:

1. `.claude/skills/agents-shipgate/prompts/add-shipgate-to-repo.md` — bundled with the `agents-shipgate` skill if installed in this project.
2. `prompts/add-shipgate-to-repo.md` — present when this repo is a clone of `agents-shipgate` itself.
3. `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/add-shipgate-to-repo.md` — last-resort fetch.

Required behavior (do not skip):

1. Set `AGENTS_SHIPGATE_AGENT_MODE=1` for every CLI call so errors emit a `next_action` JSON line on stderr.
2. Run `agents-shipgate contract --json` when available and use it to verify the installed CLI's schema versions and gating signal.
3. Confirm with the user before running `agents-shipgate init --workspace . --write` (it writes `shipgate.yaml` to the workspace).
4. Parse `agents-shipgate-reports/report.json` directly — do not scrape the markdown. **For release gating, read `release_decision.decision` first** (`"blocked" | "review_required" | "passed"`; baseline-aware, v0.8+) along with `release_decision.{reason, blockers, review_items, fail_policy.would_fail_ci}`. Other stable fields: `findings[].{check_id, severity, tool_name, recommendation}`. `summary.{critical_count, high_count, medium_count, status}` is legacy and baseline-blind — kept for v0.7 callers, do not lead with it. The Release Evidence Packet is at `agents-shipgate-reports/packet.{md,json,html}`. Full contract: [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md).
5. Add `agents-shipgate-reports/` to `.gitignore` if it is not already.
6. Do **not** run `agents-shipgate baseline save` in this flow — baselining is a separate decision.

Report back: `release_decision.decision` and `reason`, blocker / review-item counts, top 3 active findings by severity, the packet path (`agents-shipgate-reports/packet.md`), and one suggested next step.
