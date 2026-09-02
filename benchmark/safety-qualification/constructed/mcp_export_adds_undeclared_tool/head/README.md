# Support tools server

An MCP server exposing the support assistant's tools. The published tool list
is checked in at [`mcp/tools.json`](mcp/tools.json) and is what the deployed
server returns from `tools/list`.

## Tools

- `support.search_kb` — knowledge-base search.
- `support.lookup_ticket` — fetch a Zendesk ticket.
- `gmail.send_customer_email` — send one of the approved status templates; the
  assistant asks the customer to confirm before it is sent.
- `zendesk.add_ticket_comment` — add a comment to a ticket.
