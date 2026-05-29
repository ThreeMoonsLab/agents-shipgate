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

## Permissions

Most examples need:

```yaml
permissions:
  contents: read
  pull-requests: write       # for pr_comment
  security-events: write     # for SARIF upload
```

Configure per-job, never repo-wide.

## Pinning versions

For reproducible CI, pin both the action and the underlying CLI:

```yaml
- uses: ThreeMoonsLab/agents-shipgate@v0.10.0
  with:
    shipgate_version: "0.10.0"
```

When `shipgate_version` is empty the action installs the CLI from the action source — convenient on `@main`, less reproducible.

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
  uses: ThreeMoonsLab/agents-shipgate@v0.10.0

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
`release_decision.decision`, then shows a top capability-change table,
trust-root warnings, required next steps, and artifact links. For one minor
release cycle, existing adopters can set `pr_comment_style: findings` to keep
the v1 findings-oriented comment while updating downstream automation.

For PR review diffs, set `diff_base: target`. The action delegates to
`agents-shipgate verify`, which never fetches. Use `fetch-depth: 0` on
`actions/checkout` or fetch the base ref in an earlier step; otherwise verify
records `base_status` and runs a head-only gate. `base_ref` and `head_ref` may
be set explicitly for clearer PR wiring. When `head_ref` is set, verify scans
an isolated archive of that ref; otherwise it scans the checked-out workspace.
Existing `diff_base` / `diff_from` workflows keep working.

Rollout note for the verifier-cycle minor: the Action defaults are
`verify_mode: verify` and `pr_comment_style: capability-review`. New outputs
are additive and old outputs remain stable; keep using `decision` as the
preferred gating output. The additive verifier outputs are:
`should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
`trust_root_touched`, `policy_weakened`, `capability_changes_added`,
`capability_changes_modified`, and `capability_changes_removed`.
