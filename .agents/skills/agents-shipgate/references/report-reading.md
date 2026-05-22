# Reading Agents Shipgate Reports

Always read `agents-shipgate-reports/report.json`. Do not scrape Markdown.

## Order

1. `release_decision.decision`: `blocked`, `review_required`, `insufficient_evidence`, or `passed`.
2. `release_decision.blockers[]`: items blocking release.
3. `release_decision.review_items[]`: accepted debt or human-review items.
4. `agent_summary`: one-fetch summary with `headline`, counts, safe patches, human-review needs, and `first_recommended_action`.
5. `findings[]`: detailed evidence, source, severity, and remediation.

## Per-Finding Action

Prefer `findings[].agent_action` when present:

- `auto_apply`: safe to apply only when a high-confidence patch exists.
- `propose_patch_for_review`: show patch and ask for review.
- `escalate_to_human`: policy/evidence decision.
- `suppress_with_reason`: suppress only after explicit user confirmation.
- `informational`: summarize only.

Do not synthesize an action from lower-level fields when `agent_action` exists.

## Manual-Review Boundary

Never auto-assert these categories:

- approval policy
- confirmation policy
- idempotency evidence
- broad-scope permission decisions
- prohibited-action policy decisions
- runtime trace evidence

For those, summarize the risk and the exact decision a human needs to make.

## Summary Template

Report back with:

```text
Decision: <release_decision.decision>
Blockers: <count>
Review items: <count>
Safe patches applied: <count or none>
Needs human review: <short list>
Top findings:
1. <check/tool/risk/next action>
```

If `privacy_audit` is present, mention that default report redaction ran. If `insufficient_evidence` appears, treat it as review-required unless the user has stricter release policy.
