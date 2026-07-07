# Use Agents Shipgate With Claude Code

Claude Code uses the shared agent-native protocol:

```bash
shipgate check --agent claude-code --workspace . --format codex-boundary-json
```

Parse stdout as `shipgate.codex_boundary_result/v1`. Switch only on `decision`,
`completion_allowed`, `must_stop`, `first_next_action`, `human_review`,
`repair`, `policy`, and `verify_required`.

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

Read `agents-shipgate-reports/agent-handoff.json` first, then
`verifier.json`, then `verify-run.json`, then `report.json` for reviewer
evidence. Legacy `agent-result.json` surfaces are supporting/provisional
compatibility projections for older automation consumers.

See [protocol.md](protocol.md) for the state machine, repair loop, policy
discovery convention, and MCP read-only boundary.
