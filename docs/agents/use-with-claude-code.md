# Use Agents Shipgate with Claude Code

Two pieces of agent-facing surface ship with this repo. Drop them into your own agent project so Claude Code can install, run, and explain Shipgate without you typing the steps.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `/shipgate` slash command | Bootstrap flow: install → `init --write` → fill placeholders → `scan` → report top findings | [`.claude/commands/shipgate.md`](../../.claude/commands/shipgate.md) |
| `shipgate` skill | Auto-discovered when the user mentions release readiness, scanning an agent, fixing a finding, or `shipgate.yaml`. Routes to the right [`prompts/`](../../prompts/) recipe. | [`skills/shipgate/SKILL.md`](../../skills/shipgate/SKILL.md) |

Both wrap the canonical [`prompts/`](../../prompts/) and [`AGENTS.md`](../../AGENTS.md). They do not duplicate content — when you upgrade agents-shipgate, the prompts are the source of truth.

## Install in your agent project

From the root of the project where you want `/shipgate` and the skill available:

```bash
# Slash command
mkdir -p .claude/commands
curl -fsSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/.claude/commands/shipgate.md \
  -o .claude/commands/shipgate.md

# Skill
mkdir -p .claude/skills/shipgate
curl -fsSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/skills/shipgate/SKILL.md \
  -o .claude/skills/shipgate/SKILL.md
```

Or, if you have this repo cloned, copy them over:

```bash
cp -r /path/to/agents-shipgate/.claude/commands/shipgate.md .claude/commands/shipgate.md
cp -r /path/to/agents-shipgate/skills/shipgate .claude/skills/shipgate
```

## Verify

Open Claude Code in the project. Two checks:

1. Type `/shipgate` and confirm the command shows up.
2. Ask Claude Code "add release-readiness checks for this agent" — the `shipgate` skill should auto-trigger and walk through the bootstrap.

If `/shipgate` runs the bootstrap end-to-end, you are done. The first run installs `agents-shipgate` via `pipx`, generates `shipgate.yaml`, and produces `agents-shipgate-reports/report.json`.

## What the skill knows about

The `shipgate` skill routes to [these recipes](../../prompts/) by task:

- Bootstrap a repo → [`add-shipgate-to-repo.md`](../../prompts/add-shipgate-to-repo.md)
- Fix the top finding → [`fix-top-finding.md`](../../prompts/fix-top-finding.md)
- Triage a false positive → [`triage-false-positive.md`](../../prompts/triage-false-positive.md)
- Promote to strict CI → [`stabilize-strict-mode.md`](../../prompts/stabilize-strict-mode.md)
- Upgrade the version → [`upgrade-shipgate-version.md`](../../prompts/upgrade-shipgate-version.md)

For the stable CLI / JSON contract the skill relies on, see [`STABILITY.md`](../../STABILITY.md).

## Codex / Cursor / Aider

The skill format is Claude Code-specific. For other coding agents, paste the body of the relevant `prompts/*.md` file directly. See [`prompts/README.md`](../../prompts/README.md).
