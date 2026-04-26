# Integration Recipes

## GitHub Actions

The public Marketplace wrapper is planned for the first tagged release. The
action installs from its tagged source by default; set `shipgate_version` when
you want the action to install a pinned PyPI package version.

```yaml
name: Agents Shipgate

on:
  pull_request:

permissions:
  contents: read

jobs:
  agents-shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - id: agents-shipgate
        uses: ThreeMoonsLab/agents-shipgate@v0.3.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
```

To post PR comments, set:

```yaml
permissions:
  contents: read
  pull-requests: write

with:
  pr_comment: "true"
```

Action outputs:

| Output | Meaning |
| --- | --- |
| `status` | Report summary status, such as `release_blockers_detected`. |
| `critical_count` | Unsuppressed critical finding count. |
| `high_count` | Unsuppressed high finding count. |
| `medium_count` | Unsuppressed medium finding count. |
| `baseline_new_count` | New finding count when `baseline` is set. |
| `baseline_matched_count` | Baseline-matched finding count when `baseline` is set. |
| `baseline_resolved_count` | Resolved baseline finding count when `baseline` is set. |
| `adk_agent_count` | Statically detected Google ADK agent count. |
| `adk_dynamic_toolset_count` | Google ADK dynamic or unresolved toolset count. |
| `report_json` | Path to `report.json`. |
| `report_markdown` | Path to `report.md`. |
| `report_sarif` | Path to `report.sarif`. |
| `exit_code` | Agents Shipgate CLI exit code. |

The action writes Markdown, JSON, and SARIF reports. Upload `report.sarif` to
GitHub code scanning from your workflow if you want SARIF annotations.

For source-only testing in this repository:

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
- uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405
  with:
    python-version: "3.12"
- run: python -m pip install -e ".[dev]"
- run: agents-shipgate scan --config shipgate.yaml --ci-mode advisory --format markdown,json,sarif
```

## Local Diagnostics

```bash
agents-shipgate init --workspace . --write
agents-shipgate doctor --config shipgate.yaml
AGENTS_SHIPGATE_LOG_FORMAT=json agents-shipgate scan --config shipgate.yaml --verbose
```

## GitLab CI

```yaml
agents-shipgate:
  image: python:3.12
  script:
    - python -m pip install agents-shipgate
    - agents-shipgate scan --config shipgate.yaml --ci-mode advisory
  artifacts:
    paths:
      - agents-shipgate-reports/
```

## CircleCI

```yaml
jobs:
  agents-shipgate:
    docker:
      - image: cimg/python:3.12
    steps:
      - checkout
      - run: python -m pip install agents-shipgate
      - run: agents-shipgate scan --config shipgate.yaml --ci-mode advisory
```

## Jenkins

```groovy
stage('Agents Shipgate') {
  steps {
    sh 'python -m pip install agents-shipgate'
    sh 'agents-shipgate scan --config shipgate.yaml --ci-mode advisory'
    archiveArtifacts artifacts: 'agents-shipgate-reports/**', allowEmptyArchive: true
  }
}
```
