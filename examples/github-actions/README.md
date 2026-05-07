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
| `decision` | `blocked` / `review_required` / `passed`. Baseline-aware; this is the gating signal. |
| `blocker_count` | Number of items in `release_decision.blockers`. |
| `review_item_count` | Number of items in `release_decision.review_items`. |
| `ci_would_fail` | `true`/`false`. Whether the active fail policy would fail CI. |

```yaml
- id: shipgate
  uses: ThreeMoonsLab/agents-shipgate@v0.10.0

- if: steps.shipgate.outputs.decision == 'blocked'
  run: echo "Release blocked by Agents Shipgate"
```

**Diagnostic (informational, not a release gate):** `diff_enabled` — `true`/`false`. Whether the action performed a base-branch comparison (`diff_base: target` or `diff_from: <ref>` was set and the scan succeeded).

**Legacy (kept for v0.7 callers, baseline-blind):** `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `baseline_matched_count`, `baseline_resolved_count`, `report_json`, `report_markdown`, `report_sarif`, `exit_code`. New gates should use `decision` and `ci_would_fail` instead — `summary.status` flips to `release_blockers_detected` even on baseline-matched-only criticals, while `decision` correctly classifies them as `review_required`.

For PR review diffs, set `diff_base: target`. The action performs a best-effort base-branch scan with the PR-side installed package; use `fetch-depth: 0` on `actions/checkout` if your workflow needs reliable target-branch comparison.
