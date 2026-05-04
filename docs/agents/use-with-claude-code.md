# Use Agents Shipgate with Claude Code

Two pieces of agent-facing surface ship with this repo. Drop them into your own agent project so Claude Code can install, run, and explain Shipgate without you typing the steps.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `/shipgate` slash command | Bootstrap flow: install → `init --write` → fill placeholders → `scan` → report top findings | [`.claude/commands/shipgate.md`](../../.claude/commands/shipgate.md) |
| `agents-shipgate` skill | Auto-discovered when the user mentions release readiness, scanning an agent, fixing a finding, adding Shipgate to CI, or `shipgate.yaml`. Routes to bundled recipes. | [`skills/agents-shipgate/SKILL.md`](../../skills/agents-shipgate/SKILL.md) |

The skill is named `agents-shipgate`, not `shipgate`, on purpose: Claude Code lets a skill with the same name as a command preempt it, which would silently bypass the `/shipgate` slash command. Keeping the names distinct lets users invoke the slash command explicitly **and** lets the skill auto-trigger on relevant phrases.

The skill bundles all six [`prompts/`](../../prompts/) recipes plus the advisory CI YAML in its own directory, so a user project does not depend on the upstream `main` branch at runtime. When you change anything in [`prompts/`](../../prompts/) or [`examples/github-actions/01-advisory-pr-comment.yml`](../../examples/github-actions/01-advisory-pr-comment.yml), sync the bundled copy under `skills/agents-shipgate/`.

## Install in your agent project

From the root of the project where you want `/shipgate` and the skill available:

```bash
# Slash command
mkdir -p .claude/commands
curl -fsSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/.claude/commands/shipgate.md \
  -o .claude/commands/shipgate.md

# Skill (bundled recipes — recursive)
mkdir -p .claude/skills/agents-shipgate
for f in SKILL.md \
         prompts/add-shipgate-to-repo.md \
         prompts/fix-top-finding.md \
         prompts/recommend-fixes.md \
         prompts/triage-false-positive.md \
         prompts/stabilize-strict-mode.md \
         prompts/upgrade-shipgate-version.md \
         ci-recipes/advisory-pr-comment.yml; do
  mkdir -p ".claude/skills/agents-shipgate/$(dirname "$f")"
  curl -fsSL "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/skills/agents-shipgate/$f" \
    -o ".claude/skills/agents-shipgate/$f"
done
```

Or, if you have this repo cloned, copy them over:

```bash
cp /path/to/agents-shipgate/.claude/commands/shipgate.md .claude/commands/shipgate.md
cp -r /path/to/agents-shipgate/skills/agents-shipgate .claude/skills/agents-shipgate
```

## Verify

Open Claude Code in the project. Two checks:

1. Type `/shipgate` and confirm the command shows up. It should run the bootstrap flow (slash command, NOT the skill).
2. In a fresh chat, ask "add release-readiness checks for this agent" without saying the word "shipgate" — the `agents-shipgate` skill should auto-trigger.

If `/shipgate` runs the bootstrap end-to-end, you are done. The first run installs `agents-shipgate` via `pipx`, generates `shipgate.yaml`, and produces `agents-shipgate-reports/report.json`.

## What the skill knows about

The `agents-shipgate` skill routes to bundled recipes (relative paths inside the skill directory):

- Bootstrap a repo → `prompts/add-shipgate-to-repo.md`
- First-time CI (advisory PR comment) → `ci-recipes/advisory-pr-comment.yml`
- Fix the top finding → `prompts/fix-top-finding.md`
- Recommend fixes across all findings → `prompts/recommend-fixes.md`
- Triage a false positive → `prompts/triage-false-positive.md`
- Promote advisory CI to strict → `prompts/stabilize-strict-mode.md`
- Upgrade the version → `prompts/upgrade-shipgate-version.md`

For the stable CLI / JSON contract the skill relies on, see [`STABILITY.md`](../../STABILITY.md).

## Codex / Cursor / Aider

The skill format is Claude Code-specific. For other coding agents, paste the body of the relevant `prompts/*.md` file directly. See [`prompts/README.md`](../../prompts/README.md).
