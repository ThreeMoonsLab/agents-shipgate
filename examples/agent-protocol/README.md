# Agent Protocol Examples

These examples preserve the deprecated `shipgate check` /
`shipgate.codex_boundary_result/v2` compatibility projection. New consumers
should request `agent-boundary-json` and parse
`shipgate.agent_boundary_result/v2`.

Run a fixture diff:

```bash
shipgate check --agent codex --workspace . --diff diffs/block-stop.diff --format codex-boundary-json
```

Expected outputs are under `expected/`:

- `block-stop.json` — policy weakening routes to a human stop.
- `repair-before.json` — an MCP write tool auto-approval is agent-repairable.
- `repair-after.json` — the narrowed approval config allows completion.
- `policy-bypass.json` — editing Shipgate policy to weaken a rule blocks.
- `missing-install.json` — the instruction-level fallback when the binary is missing.
- `stale-install.json` — the instruction-level fallback when the binary is present but older than contract v14.

The MCP server exposes the neutral v1 shape through the read-only
`shipgate.check` tool when a caller supplies `diff_text`.
