# Use Agents Shipgate with Codex

OpenAI Codex (the CLI agent) does not have an equivalent of Claude Code's `/shipgate` slash command or `agents-shipgate` skill — there is no per-agent install bundle to drop in. Codex's discoverability surface is `AGENTS.md` at the repo root: it reads that file natively when working on a project. The integration path is therefore "drop the canonical Shipgate snippet into your repo's `AGENTS.md`" plus paste-style prompt invocation.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `AGENTS.md` snippet | Tells Codex when and how to run Shipgate. Copy the `## Agent Release Readiness` block into your repo's `AGENTS.md`. | [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) §`AGENTS.md` |
| Reusable prompts | Codex reads pasted Markdown directly. Copy the body of any [`prompts/*.md`](../../prompts/) recipe into the chat. | [`prompts/README.md`](../../prompts/README.md) |

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

The snippet is the only discoverable surface Codex needs. There is no skill, no slash command, no auto-attach rule.

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

## What's missing

Codex has no native slash-command or auto-discovered skill mechanism (as of this writing). The on-ramp is the `AGENTS.md` snippet plus paste-style prompt invocation — there is nothing else to install on the Codex side. If a future Codex release ships per-agent extensions, this doc will be updated.

For Claude Code, see [`use-with-claude-code.md`](use-with-claude-code.md). For Cursor, see [`use-with-cursor.md`](use-with-cursor.md).
