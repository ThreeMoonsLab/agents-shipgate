# GitHub Actions examples

Copy-paste-ready workflows. Each one is a complete file — drop it into `.github/workflows/` in a repo that has `shipgate.yaml` at the root.

| File | When to use |
|---|---|
| [`01-advisory-pr-comment.yml`](01-advisory-pr-comment.yml) | First time you're adding the gate. Comments on PRs but never blocks. **Recommended starting point.** |
| [`02-strict-on-critical.yml`](02-strict-on-critical.yml) | After your team has tuned suppressions and is ready to fail PRs on new criticals. |
| [`03-strict-with-baseline.yml`](03-strict-with-baseline.yml) | When you have existing findings and want to fail only on net-new ones. |
| [`04-multi-config-workspace.yml`](04-multi-config-workspace.yml) | Monorepo with several agents (each with its own `shipgate.yaml`). |
| [`05-sarif-to-code-scanning.yml`](05-sarif-to-code-scanning.yml) | Surface findings in GitHub's Security tab and as PR annotations. |
| [`06-on-tool-source-changes.yml`](06-on-tool-source-changes.yml) | Run only when the tool surface or manifest actually changed. |
| [`07-block-on-blocked-verdict.yml`](07-block-on-blocked-verdict.yml) | Intermediate verifier policy: allow human-review PRs, but fail blocked verdicts. |
| [`08-require-mergeable.yml`](08-require-mergeable.yml) | Strict verifier policy: fail unless no human authority gap remains. |
| [`09-risk-labels-and-reviewers.yml`](09-risk-labels-and-reviewers.yml) | Label PRs by risk signal (`agent-capability-change`, `trust-root-touched`, `shipgate-blocked`) and request boundary owners as reviewers. |
| [`10-check-run-annotations.yml`](10-check-run-annotations.yml) | Native Check Run with merge-relevant line annotations; branch protection can require the "Agents Shipgate" check directly. Needs `checks: write`. |
| [`11-fail-on-insufficient-evidence.yml`](11-fail-on-insufficient-evidence.yml) | Evidence policy: fail when static evidence is too weak to gate confidently. |
| [`12-host-grant-drift.yml`](12-host-grant-drift.yml) | Scheduled drift gate: fail when current coding-agent host grants (MCP servers, permission rules, hooks, workflow scopes) no longer match the acknowledged `.agents-shipgate/host-grants.json` baseline. Catches authority changes that land outside PR review. |
| [`13-org-governance.yml`](13-org-governance.yml) | Scheduled organization governance gate: exception hygiene, policy-pack pinning, and host-grant drift. Does not create a second release verdict. |

## Permissions

Most examples need:

```yaml
permissions:
  contents: read
  pull-requests: write       # for pr_comment
  security-events: write     # for SARIF upload
  checks: write              # for check_run
```

Configure per-job, never repo-wide.

## Pinning versions

For reproducible CI, pin both the action and the underlying CLI:

```yaml
- uses: ThreeMoonsLab/agents-shipgate@v0.13.0
  with:
    shipgate_version: "0.13.0"
```

When `shipgate_version` is empty the action installs the CLI from the action source — convenient for local action development, less reproducible for CI.

## Action outputs

**Prefer for new release gates (v0.8+):**

| Output | Purpose |
|---|---|
| `decision` | `blocked` / `review_required` / `insufficient_evidence` / `passed`. Baseline-aware; this is the gating signal. `insufficient_evidence` (added v0.14) fires when evidence coverage is degraded past threshold. |
| `blocker_count` | Number of items in `release_decision.blockers`. |
| `review_item_count` | Number of items in `release_decision.review_items`. |
| `ci_would_fail` | `true`/`false`. Whether the active fail policy would fail CI. |

```yaml
- id: shipgate
  uses: ThreeMoonsLab/agents-shipgate@v0.13.0

- if: steps.shipgate.outputs.decision == 'blocked'
  run: echo "Release blocked by Agents Shipgate"
```

**Diagnostic (informational by itself):** `diff_enabled` — `true`/`false`.
Whether the action performed a base-branch tool-surface comparison
(`diff_base: target` or `diff_from: <ref>` was set and the scan succeeded).

Action Surface Diff outputs:

| Output | Purpose |
|---|---|
| `action_diff_enabled` | `true`/`false`. Whether `action_surface_diff` was enabled. |
| `actions_added` | Count of newly added actions. |
| `actions_modified` | Count of modified actions. |
| `actions_removed` | Count of removed actions. |

Release decisions still come from `decision` / `ci_would_fail`; action policy
findings can feed those fields through `findings[].blocks_release`.

**Legacy (kept for v0.7 callers, baseline-blind):** `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `baseline_matched_count`, `baseline_resolved_count`, `report_json`, `report_markdown`, `report_sarif`, `exit_code`. New gates should use `decision` and `ci_would_fail` instead — `summary.status` flips to `release_blockers_detected` even on baseline-matched-only criticals, while `decision` correctly classifies them as `review_required`.

Verifier artifacts: `verifier_json` points at `verifier.json`, and
`pr_comment_markdown` points at the Markdown body the action posts to PRs.
The default PR comment style is `capability-review`: it leads with
two sections: a human summary with `merge_verdict`, capability delta, next
actor, and artifact links; then a fenced JSON agent instruction block with
`first_next_action`, `fix_task`, and `agent_controller`. The underlying release
gate remains `report.json.release_decision.decision`. For one minor release
cycle, existing adopters can set `pr_comment_style: findings` to keep the v1
findings-oriented comment while updating downstream automation.

The Action also emits GitHub Actions job annotations by default for
source-backed blockers and review items. Disable with `check_annotations:
'false'`, or tune the cap with `check_annotation_limit`.

When `check_run: 'true'` is enabled, the Check Run uses the same PR projection
as job annotations. `check_run_policy: advisory` preserves the default
mergeable/success, blocked/failure, human-routed/neutral behavior.
`check_run_policy: blocked-fails` keeps human-routed verdicts neutral but fails
`blocked` and `unknown` so setup failures do not look successful. For direct
branch protection, use `check_run_policy: require-mergeable`; only
`can_merge_without_human == true` succeeds. `check_run_policy` is newer than
v0.13.0; until the next release is tagged, the Check Run policy example targets
`main` and omits `shipgate_version` so the action installs from that ref.

`verify` writes static capability artifacts to the workflow artifact when
available: `capabilities.lock.json`, `base.capabilities.lock.json`, and
`capability-lock-diff.json`. These are review artifacts only; they do not
create a second gate.

For PR review diffs, set `diff_base: target`. The action delegates to
`agents-shipgate verify`, which never fetches. Use `fetch-depth: 0` on
`actions/checkout` or fetch the base ref in an earlier step; otherwise verify
records `merge_verdict: "unknown"`, skips the head-only scan, and exits 2.
`base_ref` and `head_ref` may be set explicitly for clearer PR wiring. When
`head_ref` is set, verify scans an isolated archive of that ref; otherwise it
scans the checked-out workspace.
Existing `diff_base` / `diff_from` workflows keep working.

Rollout note for the verifier-cycle minor: the Action defaults are
`verify_mode: verify` and `pr_comment_style: capability-review`. New outputs
are additive and old outputs remain stable; keep using `decision` /
`ci_would_fail` as CI gating outputs, and use `merge_verdict` /
`can_merge_without_human` for PR-controller routing. The additive verifier
outputs are:
`should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
`merge_verdict`, `can_merge_without_human`, `trust_root_touched`,
`policy_weakened`, `capability_changes_added`,
`capability_changes_modified`, and `capability_changes_removed`.
The verifier flags mirror `verifier_summary`; the capability counts mirror
`capability_change` (`modified` is `broadened + narrowed`).

## Verifier Rollout Policies

Use one of these policies after the advisory comment is understood. These
policies consume verifier projections for workflow routing; the source gate is
still `report.json.release_decision.decision`.

```yaml
- name: Fail blocked capability changes
  if: steps.shipgate.outputs.merge_verdict == 'blocked'
  run: exit 1
```

This blocks obvious release blockers while still allowing
`human_review_required` PRs to proceed after the team performs the review.

```yaml
- name: Require mergeable verifier verdict
  if: steps.shipgate.outputs.can_merge_without_human != 'true'
  run: exit 1
```

This is the strict authority mode: only PRs with no blocker, no insufficient
evidence, and no human-review requirement can merge automatically.

```yaml
- name: Fail insufficient static evidence
  if: steps.shipgate.outputs.merge_verdict == 'insufficient_evidence'
  run: exit 1
```

This blocks only evidence-degraded PRs while leaving `blocked` and
`human_review_required` to separate policies.
