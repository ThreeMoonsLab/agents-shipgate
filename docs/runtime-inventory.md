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
