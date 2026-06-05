# AgentPR Governance Benchmark

This is the product-hardening governance benchmark for AI-generated agent PRs.
It is separate from the adoption benchmark: adoption asks whether coding agents
discover and run Agents Shipgate; governance asks whether the verifier prevents
unsafe merge, routes authority gaps to humans, and gives reviewers enough
evidence.

The initial case catalog is [`cases.yaml`](cases.yaml). Cases are intentionally
small and deterministic. They name the changed capability, the expected
`release_decision.decision`, the expected `merge_verdict`, required evidence,
and the safe next actor.

## Metrics

- unsafe merge prevention: unsafe cases must not produce `mergeable`.
- safe pass rate: benign controls should produce `mergeable` or clear
  not-applicable routing.
- authority routing: human-only gaps must route to `human`, not `coding_agent`.
- explanation usefulness: `capability_review.top_changes[]` or
  `release_decision.review_items[]` must point at the changed capability.
- remediation boundary: mechanical fixes may be agent-routable; approval,
  idempotency, broad-scope, waiver, baseline, and trust-root gaps may not.

## Adding A Case

Add a case only when it covers core product behavior or design-partner feedback
exposes a real false positive, missed capability, confusing next action, or
unsafe-pass risk. Do not add cases for academic breadth alone.

Every case must name the minimum artifact set needed to reproduce it:
`pr_diff`, `shipgate_manifest`, `tool_source`, `agent_trace`, `policy_pack`,
`verifier_json`, or `human_review_note`.
