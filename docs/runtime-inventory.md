# Runtime Inventory Design Note

Runtime inventory remains design-only in the current release. Agents Shipgate
does not ship a runtime inventory command, does not run agents, and does not
connect to MCP servers by default.

The intended future shape is an explicit command outside default CI, for
example:

```bash
agents-shipgate inventory export --framework google_adk --out tool-inventory.json
```

Any future implementation must be trust-gated, visibly separate from `scan`,
and documented as executing framework/runtime code or connecting to configured
tool providers when that is required. Static `scan` behavior must remain
local-only and no-execution by default.

## Boundary Requirements

Runtime inventory must remain opt-in even after it ships:

- `agents-shipgate scan` and `agents-shipgate verify` must never call runtime
  inventory implicitly.
- GitHub Action defaults must never connect to MCP servers, execute agent code,
  or fetch tool inventories from a network service.
- Runtime inventory output must be written to a local artifact that users then
  declare explicitly in `shipgate.yaml`.
- Any command that executes framework code or connects to a provider must say so
  in `--help`, docs, and PR examples.

This boundary keeps Tool-Use Readiness deterministic at PR time while leaving a
future path for teams that deliberately want runtime-assisted inventory.
