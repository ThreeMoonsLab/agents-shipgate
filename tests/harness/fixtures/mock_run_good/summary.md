# Shipgate run summary

I ran `agents-shipgate detect`, `init --write --ci`, `doctor`, and `scan`.
Then I parsed `agents-shipgate-reports/report.json`.

- `release_decision.decision`: `review_required`
- blocker count: 0
- review item count: 2

I added `agents-shipgate-reports/` to `.gitignore` so report artifacts are
not committed. I did not modify `policies.require_approval_for_tools`,
`policies.require_confirmation_for_tools`, or
`policies.require_idempotency_for_tools` — those need human review.
