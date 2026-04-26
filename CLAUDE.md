# Claude Code Instructions

The full agent-facing instructions for this repo live in [`AGENTS.md`](AGENTS.md). Everything there applies to Claude Code.

A few Claude-specific notes:

## Permissions

- `agents-shipgate scan`, `init`, `doctor`, `explain`, `list-checks`, `fixture`, `self-check` are **read-only** with respect to user code; safe to run without confirmation.
- `agents-shipgate init --write` writes `shipgate.yaml` in the workspace. Confirm before running on an unfamiliar repo.
- `agents-shipgate baseline save` writes one JSON file under `.agents-shipgate/`. Safe to run; reversible.

## Output handling

Prefer `--json` on every command and parse the result programmatically. Do not scrape stdout when a JSON form exists. The stable JSON shape is the contract.

For `scan`, parse `agents-shipgate-reports/report.json` directly — that's where the structured output lives. The stdout summary is for humans.

## Slash command

A `/shipgate` slash command is registered at [`.claude/commands/shipgate.md`](.claude/commands/shipgate.md). It runs the full bootstrap flow.

## Skills

When invoking the CLI from a skill, set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors include a structured `next_action` JSON line on stderr.
