# Use Agents Shipgate With Claude Code

Claude Code uses the shared agent-native protocol:

```bash
shipgate check --agent claude-code --workspace . --format agent-boundary-json
```

Parse stdout as `shipgate.agent_boundary_result/v1`. Switch only on
`control.state`; follow `control.next_action`, `control.allowed_next_commands`,
and `control.human_review`. Treat `decision` as diagnostic context only.
`--agent claude-code` labels the caller; it does not limit the check to Claude
files or hide changes to another host's boundary.

If the binary is missing, surface the schema-valid install fixture with
`control.state="agent_action_required"`, `control.next_action.kind="install"`,
and command `pipx install agents-shipgate`.
After installation, rerun `shipgate check`; do not invent a natural-language
decision.

Claude Code-specific discovery surfaces:

- `.claude/settings.json` and `.claude/settings.local.json` permission modes,
  rules, sandbox/network settings, additional paths, plugins, and hooks
- project MCP declarations in `.mcp.json`
- repo-level `CLAUDE.md`
- repo-scoped skill `.claude/skills/agents-shipgate/`
- slash command `/agents-shipgate` when the skill is installed

Use `shipgate audit --host --scope local-static` only when you intentionally
want supported user and file-based managed settings included. Shipgate never
executes `policyHelper` and does not observe remote/session state.

For committed PR verification, use the CI substrate after the local check:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json
```

Read `agents-shipgate-reports/agent-handoff.json` first, then
`verifier.json`, then `verify-run.json`, then `report.json` for reviewer
evidence. Legacy `agent-result.json` surfaces are supporting/provisional
compatibility projections for older automation consumers.

See [protocol.md](protocol.md) for the state machine, repair loop, policy
discovery convention, and MCP read-only boundary.
