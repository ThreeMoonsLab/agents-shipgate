# Use Agents Shipgate With Cursor

Cursor uses the shared agent-native protocol:

```bash
shipgate check --agent cursor --workspace . --format agent-json
```

Parse stdout as `agent_result_v1`. Switch only on `decision`,
`completion_allowed`, `must_stop`, `first_next_action`, `repair`, and
`human_review`.

If the binary is missing, surface the schema-valid install fixture with
`first_next_action.kind="install"` and command `pipx install agents-shipgate`.
After installation, rerun `shipgate check`; do not invent a natural-language
decision.

Cursor-specific discovery surfaces:

- `.cursor/rules/agents-shipgate.mdc`
- path globs for agent tools, policies, prompts, Codex plugin metadata, and CI
  release gates

For committed PR verification, use the CI substrate after the local check:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json
```

Read `agents-shipgate-reports/verifier.json` first, then `report.json`.
`agent-result.json` is a supporting/provisional compact projection for Action
and automation consumers.

See [protocol.md](protocol.md) for the state machine, repair loop, policy
discovery convention, and MCP read-only boundary.
