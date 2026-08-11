# Reading Agents Shipgate Reports

For verify runs, run `agents-shipgate agent control --workspace .` first. It
names which run is current, checks it against the repository as it stands right
now, and a non-zero exit means none is — you hold no authority. On a CLI whose
`contract --json` reports `agent_control_schema_version` it prints one
`shipgate.agent_control/v1` object, which is the whole routing answer; when it
is enough, stop there, and add `--format pointer` for the raw
`agents-shipgate-reports/current-control.json` artifact instead. On an older
CLI it prints that raw pointer directly.

For detail beyond routing, validate the
`agents-shipgate-reports/verification-receipt.json` the run binds. Then read
`agents-shipgate-reports/agent-handoff.json`. After that,
read `agents-shipgate-reports/verifier.json` for detailed control context
and `agents-shipgate-reports/report.json` for findings. Do not scrape Markdown.

## Order

0. `agents-shipgate agent control --workspace .`: what is current, and what you may do. When `agents-shipgate contract --json` reports `agent_control_schema_version`, this prints the compact `shipgate.agent_control/v1` object — read `control_state`, `permissions`, `next_actor`, `next_action`, `current_control_id` — and `--format pointer` returns the raw `current-control.json` with `lifecycle_state` and nested `control.state`. On a CLI that predates the envelope there is no `--format` and the raw pointer is what you get, so read `current_control_id` / `lifecycle_state` / `control.state` there instead. Either way `permissions.merge` (or, on the raw pointer, `control.permissions.merge`) is the only field that answers "may I merge"; `execution` only says the tool ran, and `exit_code` is the CI gate signal, which is 0 in advisory mode even on `blocked`. Re-read this before enforcing a cached `must_stop`, before commit/push/PR update, before merge, and before declaring the task complete; if `current_control_id` changed, discard cached control state and start again from the new identity.
1. `agent-handoff.json.control.state`: `complete`, `agent_action_required`, `review_publishable`, or `human_review_required`. `review_publishable` means a human must approve the merge and you may still commit, push, and update the pull request so that review can happen; `control.permissions` says exactly which actions are authorized, and updating a PR is never merging it.
2. `agent-handoff.json.capability_review.top_changes[]`: the highest-signal tool/action or trust-root changes.
3. `agent-handoff.json.next_action` / `control.next_action` / `fix_task`: who acts next and whether a coding agent may safely attempt the fix.
4. `report.json.release_decision.decision`: `blocked`, `review_required`, `insufficient_evidence`, or `passed`; this is the release gate.
5. `verifier.json.capability_review.top_changes[]`: supporting/provisional highest-signal tool/action or trust-root changes.
6. `release_decision.blockers[]` and `release_decision.review_items[]`.
7. `findings[]`: detailed evidence, source, severity, and remediation.

## Verifier Summary

When `report_schema_version` is `0.22` or newer, read
`verifier_summary` after `release_decision`:

- `verdict`: exact mirror of `release_decision.decision`.
- `protected_surface_touched`: true when verify-mode `SHIP-VERIFY-*`
  findings show a trust-root edit.
- `policy_weakened`: true when the normalized policy surface moved toward
  less review, less blocking, or less evidence.
- `human_ack_required` / `human_ack_satisfied`: declared human-acknowledgement
  state; a coding agent must not synthesize acknowledgement.
- `top_reason_codes[]`: ranked reason-code counts for concise summaries.

This block is a supporting/provisional deterministic projection. It cannot
introduce a blocker that is not already present in `findings[]` and
`release_decision`.

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

- action effect declarations
- action authority declarations
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
Merge verdict: <agent-handoff.json.gate.merge_verdict>
Decision: <release_decision.decision>
Capability changes: <top verifier capability_review.top_changes entries>
Blockers: <count>
Review items: <count>
Safe patches applied: <count or none>
Needs human review: <short list>
Top findings:
1. <check/tool/risk/next action>
```

If `privacy_audit` is present, mention that default report redaction ran. If `insufficient_evidence` appears, treat it as review-required unless the user has stricter release policy.
