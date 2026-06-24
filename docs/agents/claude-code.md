# Use Agents Shipgate With Claude Code

Claude Code uses the shared agent-native protocol:

```bash
shipgate check --agent claude-code --workspace . --format agent-json
```

Parse stdout as `agent_result_v1`. Switch only on `decision`,
`completion_allowed`, `must_stop`, `first_next_action`, `repair`, and
`human_review`.

If the binary is missing, surface the schema-valid install fixture with
`first_next_action.kind="install"` and command `pipx install agents-shipgate`.
After installation, rerun `shipgate check`; do not invent a natural-language
decision.

Claude Code-specific discovery surfaces:

- repo-level `CLAUDE.md`
- repo-scoped skill `.claude/skills/agents-shipgate/`
- slash command `/agents-shipgate` when the skill is installed

For committed PR verification, use the CI substrate after the local check:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json
```

Read `agents-shipgate-reports/verifier.json` first, then `report.json`.
`agent-result.json` is a supporting/provisional compact projection for Action
and automation consumers.

See [protocol.md](protocol.md) for the state machine, repair loop, policy
discovery convention, and MCP read-only boundary.
