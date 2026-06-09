# MCP Server Mode

`agents-shipgate mcp-serve` exposes the verifier as a local MCP server so
agents without shell access (PR bots, IDE-embedded agents, restricted tool
environments) can ask "may this diff merge?" in-loop.

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

## Tools

| Tool | Mirrors | Read first |
|---|---|---|
| `shipgate_preview` | `verify --preview --json` | whether Shipgate applies to the repo/diff |
| `shipgate_verify` | `verify --format json` | `merge_verdict`, `can_merge_without_human`, `first_next_action`, `fix_task` |
| `shipgate_explain_finding` | `explain-finding --json` | one finding's evidence + remediation + autofix boundary |

Errors return a structured payload (`merge_verdict: "unknown"`, `error`,
`message`, `next_action`) instead of raising — the same recovery shape as
agent-mode CLI errors.

## Trust model

Identical to the CLI: **stdio transport only, no network**, static-by-
default, and every verdict field is a deterministic projection of
`report.json.release_decision.decision` — the server adds no second gate.
Artifacts (`verifier.json`, `report.json`, `pr-comment.md`) are written to
the workspace's reports directory exactly as the CLI writes them, so the
human review trail is unchanged.

The server intentionally exposes **no mutating tools**: no `apply-patches`,
no `init --write`, no baseline writes. Repair stays in the agent's own
editing tools where the human can see it; the server only answers
questions.
