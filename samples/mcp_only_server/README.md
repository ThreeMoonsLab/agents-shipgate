# MCP-only Tool Server

A minimal MCP server exposing three support tools — two read-only lookups and
one external-write tool. There is no Python framework; the tool surface lives
entirely in [`mcp/tools.json`](mcp/tools.json).

Used as an archetype in the adoption-harness benchmark (`mcp-only`).

## Tools

- `support.search_kb` — read-only KB search.
- `support.lookup_ticket` — read-only ticket fetch.
- `gmail.send_customer_email` — external write; should require approval and
  confirmation in a real production deployment.
