# MCP Server Mode

`agents-shipgate mcp-serve` exposes read-only static Shipgate tools as a local
MCP server for agents that cannot or should not shell out to the CLI.

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
| `shipgate.check` | `{agent, workspace, diff_text, config?, policy?}` | exact `shipgate.agent_boundary_result/v1` |
| `shipgate.preflight` | `{workspace?, config?, plan?, changed_files?, diff_text?, capability_request?, base_preflight?}` | exact `PreflightResultV3` |
| `shipgate.explain` | `{check_id}` or `{fingerprint, report_path}` | deterministic check/finding explanation JSON |
| `shipgate.capabilities` | `{config}` or `{base_lock, head_lock}` | capability lock or capability lock diff JSON |
| `shipgate.handoff` | `{verifier_path, report_path?, verify_run_path?}` | exact `shipgate.agent_handoff/v6` |

`shipgate.check` is the same protocol surface documented in
[`agents/protocol.md`](agents/protocol.md). `shipgate.preflight` is proactive
routing only: prefer passing a `PreflightPlanV1` object in `plan`. It can tell
an agent to stop before editing protected surfaces, route host/MCP permission
requests to a human, or gather evidence for a proposed high-risk capability,
but it is not a second release verdict. The release gate remains
`report.json.release_decision.decision`.

For `shipgate.preflight`, `plan` is mutually exclusive with the direct
`changed_files`, `diff_text`, `capability_request`, and `base_preflight`
inputs. Send one complete plan object or the applicable direct fields, never
both; a mixed request is rejected rather than silently preferring one source.

The `diff_text` accepted by `shipgate.check` is detached diagnostic input. It
is not bound to a checkout state that `verify` can reconstruct, so a result
that owes verification stops for human routing and authorizes no verify
command. Re-run `shipgate check` locally against the intended worktree or a
complete `base`/`head` ref range before following a verification route.

`shipgate.handoff` is a read-only projection over existing verifier artifacts.
It never runs `verify`, shells out to git, or writes `agent-handoff.json`; it
returns the same `shipgate.agent_handoff/v6` shape that `verify` writes for
agents that need a compact control/release-readiness object.

## Trust model

The MCP server is a read-only static adapter. It does not shell out to git, run
`verify`, run `scan`, apply patches, write artifacts, call tools, execute an
agent, connect to external MCP servers, or access the network. It exposes no
privileged runtime gate and no mutating tools.
