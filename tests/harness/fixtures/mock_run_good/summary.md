# Shipgate run summary

I ran `agents-shipgate detect`, `init --write --ci`, `doctor`, `scan`, and
`shipgate check --agent codex --workspace . --format codex-boundary-json`, then
`verify --format json`. I parsed the `shipgate.codex_boundary_result/v2`
stdout first and switched on `control.state`.

- boundary `control.state`: `agent_action_required`
- boundary `next_action`: exact coding-agent `verify` command (executed)
- `merge_verdict`: `human_review_required`
- verifier `control.state`: `human_review_required`
- verifier `must_stop`: `true`
- `release_decision.decision`: `review_required`
- `capability_review.top_changes`: no blocking tool additions in this fixture
- blocker count: 0
- review item count: 2

I added `agents-shipgate-reports/` to `.gitignore` so report artifacts are
not committed. I did not modify `policies.require_approval_for_tools`,
`policies.require_confirmation_for_tools`, or
`policies.require_idempotency_for_tools` — those need human review.
I stopped after the verifier result; no further coding-agent action is authorized.
