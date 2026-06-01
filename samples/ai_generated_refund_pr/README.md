# AI-generated refund PR verifier demo

Reproduces the homepage verifier story: the base support agent can only search
the knowledge base, then the head commit adds `stripe.create_refund` with a
broad Stripe scope and no approval or idempotency evidence.

Run it with:

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

The fixture builds a temporary base/head git history and runs
`agents-shipgate verify --base origin/main --head HEAD --json`, writing
`verifier.json`, `report.json`, and `pr-comment.md`.
