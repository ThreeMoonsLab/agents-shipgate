# Use Agents Shipgate with Codex

OpenAI Codex supports both `AGENTS.md` (read natively at the repo root) and Codex Skills (versioned `SKILL.md` bundles scanned from `.agents/skills/` in every directory from the working directory up to the repo root, plus `$HOME/.agents/skills/` for user-scoped skills; invoked explicitly with `/skills` or by typing `$<skill-name>`, or implicitly when Codex decides the task matches). **This repo does not ship a Codex skill bundle yet** — the parallel to the Claude Code [`skills/agents-shipgate/`](../../skills/agents-shipgate/) bundle has not been authored. The minimal on-ramp that works today is therefore "drop the canonical Shipgate snippet into your repo's `AGENTS.md`" plus paste-style prompt invocation. See "What's next" below for the Codex skill path.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `AGENTS.md` snippet | Tells Codex when and how to run Shipgate. Copy the `## Agent Release Readiness` block into your repo's `AGENTS.md`. | [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) §`AGENTS.md` |
| Reusable prompts | Codex reads pasted Markdown directly. Copy the body of any [`prompts/*.md`](../../prompts/) recipe into the chat. | [`prompts/README.md`](../../prompts/README.md) |
| Codex skill | Not shipped here yet. Would live at `.agents/skills/agents-shipgate/SKILL.md` (repo-scoped) or `$HOME/.agents/skills/agents-shipgate/SKILL.md` (user-scoped) and mirror the Claude Code skill structure. | — |

---

## Install Agents Shipgate

From the root of your agent project:

```bash
pipx install agents-shipgate
agents-shipgate self-check --json
```

See [`AGENTS.md`](../../AGENTS.md) §Install for fallbacks (`pip`, `uv`, `python -m`).

---

## Drop in the Codex on-ramp

Open [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) and copy the `## Agent Release Readiness` block (the first fenced block under §`AGENTS.md`) into your repo's `AGENTS.md`. The snippet:

- Lists the trigger conditions (when to run Shipgate on a PR).
- Names the four-call canonical flow (`detect`, `init`, `scan`, `apply-patches`).
- Tells Codex to parse `agents-shipgate-reports/report.json` and use `release_decision.decision` as the release signal.
- Explicitly forbids auto-asserting approval, confirmation, idempotency, broad-scope, or prohibited-action policy decisions — see [`agent-autofix-boundary.md`](../agent-autofix-boundary.md) for the runtime trace evidence category as well.
- Reminds the agent to add `agents-shipgate-reports/` to `.gitignore`.

The snippet is the minimal on-ramp that works today and does not require authoring a Codex skill. If a Codex skill bundle is added later (see "What's next"), it should reuse the same trigger conditions and the same `release_decision.decision` reading order; the AGENTS.md snippet remains the lowest-friction surface for any repo that has not opted into the skill.

---

## Verify

Open Codex in the project. Two checks:

1. In a fresh chat, ask "add release-readiness checks for this agent" without saying the word "shipgate." Codex should read `AGENTS.md`, find the §Agent Release Readiness block, and run `agents-shipgate detect --workspace . --json`.
2. Confirm Codex reads `agents-shipgate-reports/report.json` rather than scraping the markdown summary, and that it leads with `release_decision.decision` when reporting back.

If both happen, you are done. The first run installs `agents-shipgate` (if not already), generates `shipgate.yaml`, and produces `agents-shipgate-reports/report.json`.

---

## Run prompts

For tasks beyond the bootstrap flow — fixing the top finding, triaging false positives, stabilizing strict mode, upgrading the version — open the relevant file in [`prompts/`](../../prompts/) and paste the body into Codex:

| Prompt | When to use |
|---|---|
| [`add-shipgate-to-repo.md`](../../prompts/add-shipgate-to-repo.md) | Bootstrap a repo that doesn't have Shipgate yet |
| [`fix-top-finding.md`](../../prompts/fix-top-finding.md) | Iterate on a single highest-severity finding |
| [`recommend-fixes.md`](../../prompts/recommend-fixes.md) | Walk all active findings and surface targeted fix recommendations |
| [`stabilize-strict-mode.md`](../../prompts/stabilize-strict-mode.md) | Tune → baseline → promote workflow for going from advisory to strict CI |
| [`triage-false-positive.md`](../../prompts/triage-false-positive.md) | Override vs. suppress decision |
| [`upgrade-shipgate-version.md`](../../prompts/upgrade-shipgate-version.md) | Bump `agents-shipgate` version safely |

See [`prompts/README.md`](../../prompts/README.md) for the full convention.

---

## Behavioral boundary and report-reading

Codex must follow the same boundary as any other agent driving Shipgate:

- **What it may do mechanically** — install, detect, init, doctor, scan, summarize, add advisory CI, apply high-confidence mechanical patches (`apply-patches --confidence high --apply`), add `agents-shipgate-reports/` to `.gitignore`.
- **What it must not assert without human review** — approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime trace evidence.

Both are spelled out in [`agent-autofix-boundary.md`](../agent-autofix-boundary.md). For the right order to read `report.json`, see [`report-reading-for-agents.md`](../report-reading-for-agents.md) — read `release_decision.decision` first.

For the stable CLI / JSON contract, see [`STABILITY.md`](../../STABILITY.md).

---

## What's next

A Codex skill bundle is the natural future surface — it would mirror what [`skills/agents-shipgate/`](../../skills/agents-shipgate/) does for Claude Code: bundle the recipes from [`prompts/`](../../prompts/) and the advisory CI workflow from [`examples/github-actions/01-advisory-pr-comment.yml`](../../examples/github-actions/01-advisory-pr-comment.yml) into a self-contained `SKILL.md` bundle a downstream repo can drop in.

If you want to assemble one locally before this repo ships an official version, the building blocks are:

- **`SKILL.md` manifest** — front matter (name, description, when to use) plus instructions. Codex loads the full file only when it decides to use the skill, so the front matter is the discovery surface.
- **`scripts/`, `references/`, `assets/`** — optional sibling directories per the Codex skill layout.
- **Install location** — `.agents/skills/agents-shipgate/` for a repo-scoped skill (Codex scans `.agents/skills/` in every directory from the working directory up to the repo root), `$HOME/.agents/skills/agents-shipgate/` for user-scoped. Invoke explicitly with `/skills` or `$agents-shipgate`, or let Codex pick implicitly.
- **Body content** — the canonical 4-call flow plus the seven "must not assert" categories from [`agent-autofix-boundary.md`](../agent-autofix-boundary.md), and the `release_decision.decision`-first reading order from [`report-reading-for-agents.md`](../report-reading-for-agents.md).

Until a vetted Codex skill ships in this repo, prefer the `AGENTS.md` snippet — it works today, requires no Codex-side install, and is the surface every Codex install reads natively.

For Claude Code, see [`use-with-claude-code.md`](use-with-claude-code.md). For Cursor, see [`use-with-cursor.md`](use-with-cursor.md).
