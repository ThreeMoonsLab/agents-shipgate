# CircleCI examples

Copy one of these files to `.circleci/config.yml` or merge the job into an
existing CircleCI config. Each job installs `agents-shipgate`, writes reports
under `agents-shipgate-reports/`, and stores the directory as CircleCI
artifacts.

| File | When to use |
| --- | --- |
| `01-advisory.yml` | First rollout. Reports findings but does not block. |
| `02-strict-with-baseline.yml` | Fail only on new critical/high findings after saving a baseline. |
| `03-sarif-artifact-retention.yml` | Generate Markdown, JSON, and SARIF reports as artifacts. |
| `04-multi-config-workspace.yml` | Monorepo with multiple `shipgate.yaml` files. |
| `05-on-tool-source-changes.yml` | Skip the scan when the manifest/tool surface did not change. |
