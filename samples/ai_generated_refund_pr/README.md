# AI-generated refund PR verifier demo

Reproduces the homepage verifier story: the base support agent can only search
the knowledge base, then the head commit adds `stripe.create_refund` with a
broad Stripe scope and no approval or idempotency evidence.

The head commit changes only `tools.json`. `shipgate.yaml` is the same on both
sides, and it already declares the new tool: `agent_bindings` binds
`stripe.create_refund` to the agent, and `action_surface` declares it
`destructive`, tagged `financial_action`, with the `stripe:*` scope, while
`permissions.scopes` grants only `support:kb:read` and no approval policy
exists. That reviewed declaration stays dormant until the export adds the
tool. The binding is what lets the verifier name blockers: a manifest that does
not bind the tool gets `insufficient_evidence` for the same diff, routing a
human to `shipgate.yaml#agent_bindings` instead.

Run it with:

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

The fixture builds a temporary base/head git history and runs
`agents-shipgate verify --base origin/main --head HEAD --json`, writing
`verifier.json`, `report.json`, and `pr-comment.md`.
