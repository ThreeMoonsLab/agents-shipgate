# Use Agents Shipgate With Codex

Codex uses the shared agent-native protocol:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
```

Parse stdout as `shipgate.agent_boundary_result/v2`. Switch only on
`control.state`; follow `control.next_action`, `control.allowed_next_commands`,
and `control.human_review`. Treat `decision` as diagnostic context only.

If the binary is missing, surface the schema-valid install fixture with
`control.state="agent_action_required"`, `control.next_action.kind="install"`,
and command `pipx install agents-shipgate`.
After installation, rerun `shipgate check`; do not invent a natural-language
decision.

Codex-specific discovery surfaces:

- repo-level `AGENTS.md`
- repo-scoped skill `.agents/skills/agents-shipgate/`
- installable Codex plugin `agents-shipgate`

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
