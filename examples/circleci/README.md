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

> **Retired:** the `on-tool-source-changes` recipe was removed. A change-prefilter cannot gate Shipgate safely. `TRIGGER-EXISTING-MANIFEST-PRESENT` is `force_run`, so an adopted repo (one with `shipgate.yaml`) is contracted to run on **every** PR — the prefilter was not saving the scan it claimed to save. Worse, every prefilter language here matches paths case-sensitively while the trigger catalog does not, so an allowlist silently drops governance edits such as `services/foo/Policies/refund.yaml` — with no job, no check, and no signal. Run the advisory recipe on every PR and let the in-job trigger evaluator decide.
