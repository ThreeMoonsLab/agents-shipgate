# Golden PR Examples

These examples show the full advisory loop a human reviewer or coding agent
should imitate:

1. Identify a risky tool surface.
2. Run Agents Shipgate.
3. Read `agents-shipgate-reports/report.json`.
4. Use `release_decision.decision`.
5. Separate safe patches from human release decisions.
6. Post a recommended coding-agent PR summary.

Examples:

- [`openai-agents-sdk-refund-agent`](openai-agents-sdk-refund-agent/) - SDK
  Python tools plus OpenAPI support tools.
- [`mcp-only-tool-server`](mcp-only-tool-server/) - MCP inventory/export only.
- [`openapi-support-agent`](openapi-support-agent/) - OpenAPI support/refund
  API surface.

For the **artifact** a coding agent produces after running the flow (the
PR comment / chat reply, not the recipe to run Shipgate), see
[`golden-pr-from-coding-agent.md`](golden-pr-from-coding-agent.md).

These are documentation examples, not new framework adapters.
