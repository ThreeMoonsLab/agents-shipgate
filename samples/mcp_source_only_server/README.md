# Source-only MCP Tool Server

A minimal MCP server whose tool surface exists **only in its TypeScript
registration sites**. There is no `tools/list` export to read, which is the
normal state of a vendor MCP server: the official MongoDB and Grafana servers
both publish dozens of tools and commit no export at all.

Contrast with [`mcp_only_server`](../mcp_only_server/), which is the same kind
of server with its surface committed as [`mcp/tools.json`](../mcp_only_server/mcp/tools.json).
Where both exist, the export wins — it is the server's own published contract,
it carries the input schemas, and it is read at `high` confidence against this
route's `medium`.

## What it pins

`detect` reports `is_agent_project: true` and suggests
`{"type": "mcp_server_source", "path": "src"}` — and **both** detectors do:
the installed CLI and the zero-install `tools/shipgate-detect.py`, which is the
documented first command run against a repository that has not adopted
Shipgate. Until #485 the script had no such reader, so it answered "Stop" on
exactly the repositories the CLI had just learned to read; this fixture is what
puts that route inside the parity sweep in
[`tests/test_zero_install_detector.py`](../../tests/test_zero_install_detector.py).

Two tools are named, one registration is not:

- `support.search_kb` — registered at the call site in
  [`src/server.ts`](src/server.ts), named by a string literal.
- `support.drop_ticket_archive` — a tool class in
  [`src/tools/dropTicketArchive.ts`](src/tools/dropTicketArchive.ts) with a
  `static toolName` field, plus the sibling `description` and `operationType`
  literals from the same class body.
- The `server.registerTool(DropTicketArchiveTool.toolName, …)` call in that
  same file passes a reference, not a literal. It is reported as
  **unenumerated** rather than dropped, which is why the evidence line says
  `1 registration(s) name themselves at runtime and are not enumerated`. A
  count without that sentence would be an over-claim.

The doc comment in `dropTicketArchive.ts` contains a registration too. It is
invisible to both readers: comments and string bodies are masked before
anything is matched, so a documented example can never enter the catalog.
