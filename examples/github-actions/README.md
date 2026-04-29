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
- uses: ThreeMoonsLab/agents-shipgate@v0.5.0
  with:
    shipgate_version: "0.5.0"
```

When `shipgate_version` is empty the action installs the CLI from the action source — convenient on `@main`, less reproducible.

## Action outputs

Useful for downstream steps:

```yaml
- id: shipgate
  uses: ThreeMoonsLab/agents-shipgate@v0.5.0

- if: steps.shipgate.outputs.critical_count != '0'
  run: echo "Action this!"
```

Available outputs: `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `report_json`, `report_markdown`, `exit_code`.
