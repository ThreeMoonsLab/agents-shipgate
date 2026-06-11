# Use Agents Shipgate With Codex

Codex uses the shared agent-native protocol:

```bash
shipgate check --agent codex --workspace . --format agent-json
```

Parse stdout as `agent_result_v1`. Switch only on `decision`,
`completion_allowed`, `must_stop`, `first_next_action`, `repair`, and
`human_review`.

If the binary is missing, surface the schema-valid install fixture with
`first_next_action.kind="install"` and command `pipx install agents-shipgate`.
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

Read `agents-shipgate-reports/agent-result.json` first, then
`verifier.json`, then `report.json` for reviewer evidence.

See [protocol.md](protocol.md) for the state machine, repair loop, policy
discovery convention, and MCP read-only boundary.
