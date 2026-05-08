# Use Agents Shipgate with Cursor

Cursor's discoverability surface is the auto-attach project rule: a Markdown file under `.cursor/rules/*.mdc` with frontmatter that lists which globs cause it to attach to a chat. The canonical Shipgate rule already exists as a copy-paste snippet — drop it in and Cursor will load it whenever a chat touches `shipgate.yaml`, an OpenAPI/MCP spec, a tools JSON, or any `.py` file.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `.cursor/rules/agents-shipgate.mdc` | Auto-attaches to chats that touch agent-tool surfaces. Tells Cursor when and how to run Shipgate. | [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) §`.cursor/rules/agents-shipgate.mdc` |
| Reusable prompts | Cursor reads pasted Markdown directly in chat / composer (Cmd+L). Copy the body of any [`prompts/*.md`](../../prompts/) recipe. | [`prompts/README.md`](../../prompts/README.md) |

Cursor's rule mechanism is analogous to Claude Code's skill auto-trigger, not to a slash command. It fires when the chat context matches the rule's globs — there is no manual invocation step.

---

## Install Agents Shipgate

From the root of your agent project:

```bash
pipx install agents-shipgate
agents-shipgate self-check --json
```

See [`AGENTS.md`](../../AGENTS.md) §Install for fallbacks (`pip`, `uv`, `python -m`).

---

## Drop in the Cursor rule

Open [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) and copy the `.cursor/rules/agents-shipgate.mdc` snippet (the second-to-last fenced block, under §`.cursor/rules/agents-shipgate.mdc`) into your repo at exactly that path:

```bash
mkdir -p .cursor/rules
# paste the snippet into .cursor/rules/agents-shipgate.mdc
```

The snippet's frontmatter is the contract. Two fields drive discoverability:

- `globs:` — patterns that cause the rule to auto-attach to the chat. The default list covers `shipgate.yaml`, OpenAPI / Swagger files, MCP exports, tool JSONs, and `.py` files. Tune this list if your repo uses different paths.
- `alwaysApply: false` — the rule fires only on glob match, not on every chat. Keep this `false` so the rule does not bloat unrelated chats.

Do **not** edit the `description:` field unless you mean to change what Cursor's rule picker shows.

---

## Verify

Open Cursor in the project. Two checks:

1. Open `shipgate.yaml` (or any matching tool source — an MCP/OpenAPI spec, a tools JSON, a `.py` file in the agent) in the editor and start a chat. Confirm Cursor shows the `agents-shipgate` rule as auto-attached in the rule list.
2. In the same chat, with the matching file still in context (open in the editor or referenced via `@filename`), ask "add release-readiness checks for this agent" without saying the word "shipgate." Cursor should run `agents-shipgate detect --workspace . --json` per the rule and proceed to the canonical 4-call flow.

The rule's `alwaysApply: false` setting means it only fires when a matching file is in chat context. A chat with no matching file referenced will not auto-attach the rule — that is the intended behavior, not a bug.

If both checks pass, you are done.

---

## Run prompts

For tasks beyond the bootstrap flow, paste the prompt body into Cursor's chat or composer (Cmd+L on macOS):

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

Cursor must follow the same boundary as any other agent driving Shipgate:

- **What it may do mechanically** — install, detect, init, doctor, scan, summarize, add advisory CI, apply high-confidence mechanical patches (`apply-patches --confidence high --apply`), add `agents-shipgate-reports/` to `.gitignore`.
- **What it must not assert without human review** — approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime trace evidence.

Both are spelled out in [`agent-autofix-boundary.md`](../agent-autofix-boundary.md). For the right order to read `report.json`, see [`report-reading-for-agents.md`](../report-reading-for-agents.md) — read `release_decision.decision` first.

For the stable CLI / JSON contract, see [`STABILITY.md`](../../STABILITY.md).

---

## MDC frontmatter notes

If you tune the `.cursor/rules/agents-shipgate.mdc` snippet:

- Keep `description:` short and specific — Cursor's rule picker shows it.
- Add globs for any non-standard tool-source path your repo uses (e.g., `**/specs/*.json`).
- Leave `alwaysApply: false`. Setting it to `true` causes the rule to attach to every chat in the project, which is rarely what you want for a release-gate rule.

For Claude Code, see [`use-with-claude-code.md`](use-with-claude-code.md). For Codex, see [`use-with-codex.md`](use-with-codex.md).
