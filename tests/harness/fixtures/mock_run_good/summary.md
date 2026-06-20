# Shipgate run summary

I ran `agents-shipgate detect`, `init --write --ci`, `doctor`, `scan`, and
`shipgate check --agent codex --workspace . --format codex-boundary-json`, then
`verify --format json`. I parsed the `shipgate.codex_boundary_result/v1` stdout first and
switched on `decision`.

- `shipgate.codex_boundary_result/v1.decision`: `require_review`
- `must_stop`: `false`
- `first_next_action`: route to human review before claiming merge approval
- `merge_verdict`: `human_review_required`
- `release_decision.decision`: `review_required`
- `capability_review.top_changes`: no blocking tool additions in this fixture
- blocker count: 0
- review item count: 2

I added `agents-shipgate-reports/` to `.gitignore` so report artifacts are
not committed. I did not modify `policies.require_approval_for_tools`,
`policies.require_confirmation_for_tools`, or
`policies.require_idempotency_for_tools` — those need human review.
