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
| `05-on-tool-source-changes.yml` | Run only when manifests or tool sources change. |

GitLab SARIF report ingestion is tier/version dependent. These examples always
retain `agents-shipgate-reports/` as path artifacts; enable
`artifacts:reports:sarif` only where your GitLab instance supports it.
