# MCP Server Mode

`agents-shipgate mcp-serve` exposes the agent-native `shipgate.check` tool as
a local MCP server for agents that cannot or should not shell out to the CLI.

## Install and run

```bash
pip install "agents-shipgate[mcp]"
agents-shipgate mcp-serve
```

Claude Code registration (`.mcp.json`):

```json
{
  "mcpServers": {
    "agents-shipgate": {
      "command": "agents-shipgate",
      "args": ["mcp-serve"]
    }
  }
}
```

## Tool

| Tool | Input | Output |
|---|---|---|
| `shipgate.check` | `{agent, workspace, diff_text, config?, policy?}` | exact `agent_result_v1` |

Compatibility note: v0.12.0 briefly exposed preview-oriented tool names
(`shipgate_preview`, `shipgate_verify`, and `shipgate_explain_finding`).
Those names were not part of `STABILITY.md`; v0.13.0 intentionally narrows the
optional MCP surface to the single `shipgate.check` adapter so it matches the
agent-native CLI protocol.

The MCP tool is the same protocol surface documented in
[`agents/protocol.md`](agents/protocol.md). It accepts caller-provided unified
diff text and returns `allow`, `warn`, `block`, or `require_review` plus the
structured next action, repair boundary, human-review boundary, policy
provenance, and audit id.

## Trust model

The MCP server is a read-only static adapter. It does not shell out to git, run
`verify`, run `scan`, apply patches, write artifacts, call tools, execute an
agent, or access the network. It exposes no privileged runtime gate and no
mutating tools.
