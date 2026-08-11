# GitLab CI examples

Copy one of these files into `.gitlab-ci.yml` or include the job in an existing
pipeline. Each job installs `agents-shipgate`, writes reports under
`agents-shipgate-reports/`, and keeps those reports as artifacts.

| File | When to use |
| --- | --- |
| `01-advisory.yml` | First rollout. Reports findings but does not block merge requests. |
| `02-strict-with-baseline.yml` | Fail only on new critical/high findings after saving a baseline. |
| `03-sarif-or-artifact.yml` | Generate SARIF and retain all reports as artifacts. |
| `04-multi-config-workspace.yml` | Monorepo with multiple `shipgate.yaml` files. |

> **Retired:** the `on-tool-source-changes` recipe was removed. A change-prefilter cannot gate Shipgate safely. `TRIGGER-EXISTING-MANIFEST-PRESENT` is `force_run`, so an adopted repo (one with `shipgate.yaml`) is contracted to run on **every** PR — the prefilter was not saving the scan it claimed to save. Worse, every prefilter language here matches paths case-sensitively while the trigger catalog does not, so an allowlist silently drops governance edits such as `services/foo/Policies/refund.yaml` — with no job, no check, and no signal. Run the advisory recipe on every PR and let the in-job trigger evaluator decide.

GitLab SARIF report ingestion is tier/version dependent. These examples always
retain `agents-shipgate-reports/` as path artifacts; enable
`artifacts:reports:sarif` only where your GitLab instance supports it.
