# Reusable prompts

Prebuilt prompts for AI coding agents (Claude Code, Codex, Cursor, Aider) working with Agents Shipgate.

| Prompt | When to use |
|---|---|
| [`add-shipgate-to-repo.md`](add-shipgate-to-repo.md) | Bootstrap Agents Shipgate in a repo that doesn't have it yet |
| [`fix-top-finding.md`](fix-top-finding.md) | Iterate on a single highest-severity finding |
| [`stabilize-strict-mode.md`](stabilize-strict-mode.md) | Tune → baseline → promote workflow for going from advisory to strict CI |
| [`triage-false-positive.md`](triage-false-positive.md) | Decide whether to override the heuristic, suppress the finding, or fix the underlying issue |
| [`upgrade-shipgate-version.md`](upgrade-shipgate-version.md) | Bump agents-shipgate version safely (regenerate baseline if needed) |

## How to use these prompts

### Claude Code

The repository ships a `/shipgate` slash command (`.claude/commands/shipgate.md`) that runs the bootstrap flow. For other tasks, paste the prompt content directly:

```
> /shipgate
```

or

```
> [paste contents of prompts/fix-top-finding.md]
```

### Codex / Cursor / Aider

Open the prompt file, copy the markdown body (everything below the front-matter), and paste into the agent.

## Conventions

Each prompt is self-contained: install commands are explicit, exit codes are referenced, and the next-action JSON shape (under `AGENTS_SHIPGATE_AGENT_MODE=1`) is documented where relevant. An agent should be able to read one prompt and complete the task without asking clarifying questions.

If a prompt becomes stale, please open a PR — the prompts directory is part of the public surface.
