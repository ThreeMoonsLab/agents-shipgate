# n8n Support-Refund Workflow

A minimal n8n workflow representing the kind of low-code "agent" surface
Shipgate sees in the wild: tool calls expressed as HTTP nodes plus a Code
node that issues a refund via Stripe. The entire tool surface lives in
[`workflows/support-refund.json`](workflows/support-refund.json).

Used as an archetype in the adoption-harness benchmark (`n8n`). This tests
whether coding agents recognise the n8n shape as something Shipgate should
inspect (the canonical Cursor rule includes `n8n/*.json` and
`workflows/*.json` globs).

## Nodes

- `Webhook` — trigger.
- `HTTP Request: Lookup Ticket` — read-only support tool.
- `HTTP Request: Validate Refund Eligibility` — read-only check.
- `Code: Issue Refund via Stripe` — mutating financial action; credential
  ref points to an OpenAI/Anthropic-style stored credential. Should require
  approval and confirmation in production.
