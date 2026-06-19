# Integration Recipes

## GitHub Actions

The public Marketplace action installs from its tagged source by default; set
`shipgate_version` when you want the action to install a pinned PyPI package
version.

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
        with:
          fetch-depth: 0
      - id: agents-shipgate
        uses: ThreeMoonsLab/agents-shipgate@v1.0.0a1
        with:
          config: shipgate.yaml
          ci_mode: advisory
          diff_base: target
          shipgate_version: '1.0.0a1'
```

To post PR comments, set:

```yaml
permissions:
  contents: read
  pull-requests: write

with:
  pr_comment: "true"
```

To apply organization policy packs from CI, pass a comma- or newline-separated
list:

```yaml
with:
  policy_packs: policies/org-release.yaml,policies/security.yaml
```

To make the verifier merge verdict load-bearing in CI, configure
`fail_on_merge_verdicts`. The recommended agent-PR policy is either to
block only `blocked`, or to require `can_merge_without_human == true` in a
separate workflow step:

```yaml
with:
  fail_on_merge_verdicts: blocked
```

This is opt-in. When configured, the action fails closed if the installed
`agents-shipgate` package does not emit `verifier.json`, so pinned older
versions should be upgraded before enabling the input.

Action outputs:

| Output | Meaning |
| --- | --- |
| `decision` | Release decision (`blocked`, `review_required`, `insufficient_evidence`, or `passed`). v0.8+; `insufficient_evidence` added v0.14. **Use this as the CI gating signal.** Switch on the value with a `review_required` fallback for unknown future values. |
| `merge_verdict` | PR/controller projection of `decision` (`mergeable`, `human_review_required`, `insufficient_evidence`, `blocked`, or `unknown`). Used by `fail_on_merge_verdicts` when configured; this is a controller projection, not a second release gate. |
| `can_merge_without_human` | `true` only when the verifier projection says no human authority gap remains. Use for strict authority workflows after the advisory result is understood. |
| `agent_controller_must_stop` | `true` when `verifier.json.agent_controller.must_stop` tells a coding agent to stop. |
| `agent_controller_stop_reason` | Deterministic stop reason from `verifier.json.agent_controller.stop_reason`, when present. |
| `agent_controller_completion_allowed` | `true` when `verifier.json.agent_controller.completion_allowed` allows the agent to claim completion. |
| `blocker_count` | Number of blockers in `release_decision.blockers`. v0.8+. |
| `review_item_count` | Number of review items in `release_decision.review_items`. v0.8+. |
| `ci_would_fail` | `true`/`false` — whether the active fail policy would fail CI. v0.8+. |
| `status` | Legacy report summary status, such as `release_blockers_detected`. Baseline-blind; preserved for v0.7 compat. |
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
| `verifier_json` | Path to `verifier.json`. |
| `verify_run_json` | Path to `verify-run.json`, which validates against [`verify-run-schema.v1.json`](verify-run-schema.v1.json). |
| `run_id` | Stable verify-run input identity from `verify-run.json.run_id`. |
| `pr_comment_markdown` | Path to `pr-comment.md`. |
| `exit_code` | Agents Shipgate CLI exit code. Matches `release_decision.fail_policy.exit_code`. |

The action runs `agents-shipgate verify`, which writes Markdown, JSON, SARIF,
packet JSON, verifier JSON, verify-run JSON, and PR-comment Markdown
artifacts. It intentionally emits `packet.json` only for the packet;
`pr-comment.md` is the human PR surface. Read `verifier.json` first for `merge_verdict`,
`can_merge_without_human`, `agent_controller`, `first_next_action`, and
`capability_review.top_changes`; read `verify-run.json` for reproducibility
metadata; read `report.json.release_decision.decision` for the gate.
Verify never fetches; use `fetch-depth: 0` on checkout or fetch
the base ref before the action when `diff_base: target` is set. An explicit
`head_ref` is scanned from an isolated archive; without it, the checked-out
workspace is scanned. Upload `report.sarif` to GitHub code scanning from your
workflow if you want SARIF annotations. Policy-pack findings use stable policy
rule IDs as SARIF `ruleId` values when present, so the first upgrade run can
close/reopen existing GitHub code-scanning alerts whose identity was previously
the built-in Shipgate check ID.

For source-only testing in this repository:

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
  with:
    fetch-depth: 0
- uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405
  with:
    python-version: "3.12"
- run: python -m pip install -e ".[dev]"
- run: agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json
```

## Local Diagnostics

```bash
agents-shipgate init --workspace . --write
agents-shipgate doctor --config shipgate.yaml
AGENTS_SHIPGATE_LOG_FORMAT=json agents-shipgate scan --config shipgate.yaml --verbose
agents-shipgate scenario suggest \
  --from agents-shipgate-reports/report.json \
  --out agents-shipgate-reports/suggested-scenarios.yaml
```

The scenario YAML is derived from `report.json.suggested_scenarios[]` and
fans static findings out into concrete sandbox/adversarial validation steps.
Baseline-matched findings remain in this export because they are accepted
debt, not resolved risk.

## Claude Code hooks (local advisory)

After `agents-shipgate verify` and CI are working, install project-scoped
Claude Code hooks for faster local feedback:

```bash
agents-shipgate install-hooks --target claude-code --write
```

The installer writes `.claude/settings.json` and
`.claude/hooks/agents-shipgate.py`. The PostToolUse hook runs a cheap
`agents-shipgate trigger` check after `Edit|Write|MultiEdit` so Claude Code
gets immediate context when an edit touches an agent-related surface. It
evaluates the edited paths without the manifest-present force-run rule, so
irrelevant docs edits do not produce a nudge just because the repo is opted in.
The Stop hook runs full `agents-shipgate verify` only when the working tree or
current branch has a relevant change that has not already been checked.

These hooks are advisory local feedback. They may soft-block a Claude Code Stop
when the verifier itself returns findings, but local setup failures such as a
missing CLI or unavailable base ref are surfaced as context. They are not a
trust boundary and not a replacement for CI. CI should continue to run the
GitHub Action or an equivalent `agents-shipgate verify` command, and CI's
`report.json.release_decision.decision` remains authoritative.

## GitLab CI

First-class GitLab CI recipes live in [`../examples/gitlab-ci/`](../examples/gitlab-ci/):

- advisory rollout;
- strict mode with a baseline;
- SARIF-or-artifact retention;
- monorepo multi-config scans;
- tool-source-change triggers.

```yaml
agents-shipgate:
  stage: test
  image: python:3.12
  script:
    - python -m pip install --pre "agents-shipgate==1.0.0a1"
    - agents-shipgate scan --config shipgate.yaml --ci-mode advisory --format markdown,json,sarif
  artifacts:
    when: always
    expire_in: 1 week
    paths:
      - agents-shipgate-reports/
```

GitLab SARIF report ingestion is tier/version dependent. Always retain
`agents-shipgate-reports/` as path artifacts; enable `artifacts:reports:sarif`
only where your GitLab instance supports it.

## CircleCI

First-class CircleCI recipes live in [`../examples/circleci/`](../examples/circleci/):

- advisory rollout;
- strict mode with a baseline;
- SARIF artifact retention;
- monorepo multi-config scans;
- tool-source-change triggers.

```yaml
version: 2.1

jobs:
  agents-shipgate:
    docker:
      - image: cimg/python:3.12
    steps:
      - checkout
      - run: python -m pip install --pre "agents-shipgate==1.0.0a1"
      - run: agents-shipgate scan --config shipgate.yaml --ci-mode advisory --format markdown,json,sarif
      - store_artifacts:
          path: agents-shipgate-reports
          destination: agents-shipgate-reports
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

## MCP server (optional)

For coding agents without comfortable shell access (Cursor, restricted
harnesses), Agents Shipgate can serve read-only static tools over an MCP stdio
server. It is a thin wrapper over the same deterministic projections the CLI
uses: `shipgate.check`, `shipgate.preflight`, `shipgate.explain`, and
`shipgate.capabilities`. The release gate stays
`report.json.release_decision.decision`. Claude Code users should prefer the
CLI + hooks surface.

```bash
pip install 'agents-shipgate[mcp]'
```

```json
// .mcp.json
{
  "mcpServers": {
    "agents-shipgate": {
      "command": "agents-shipgate",
      "args": ["mcp-serve"]
    }
  }
}
```

Tools: `shipgate.check` (caller-provided diff to
`shipgate.codex_boundary_result/v1`),
`shipgate.preflight` (protected surfaces, required evidence, and policy/trust
root hashes), `shipgate.explain` (check id or `fp_...` fingerprint), and
`shipgate.capabilities` (capability lock export or diff). The server is
read-only: it does not run agents, call tools, write artifacts, connect to
external MCP servers, or broker general MCP permissions.

## Pre-commit hook (local)

Run Agents Shipgate locally on every commit that touches a tool-surface artifact. Two equivalent setups:

**Canonical** (let `pre-commit` manage the install):

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/ThreeMoonsLab/agents-shipgate
    rev: v1.0.0a1
    hooks:
      - id: agents-shipgate
```

**Local** (agents-shipgate already on PATH):

```yaml
repos:
  - repo: local
    hooks:
      - id: agents-shipgate
        name: Agents Shipgate merge-gate verify
        entry: agents-shipgate verify --config shipgate.yaml --ci-mode advisory --format text
        language: system
        pass_filenames: false
        files: |
          (?x)^(
            shipgate\.yaml|
            .*tools.*\.json|
            .*mcp.*\.json|
            (.*/)?\.codex/(config\.toml|hooks\.json)|
            .*\.codex-plugin/.*|
            .*\.agents/plugins/.*|
            .*\.app\.json|
            (.*/)?SKILL\.md|
            .*openapi.*\.(yaml|yml|json)|
            .*swagger.*\.(yaml|yml|json)|
            \.agents-shipgate/.*\.json|
            prompts/.*|
            policies/.*|
            \.github/workflows/agents-shipgate\.(yaml|yml)
          )$
```

The hook fires when a staged change touches a **path-based** trigger from [`docs/triggers.json`](triggers.json): `shipgate.yaml`, MCP/OpenAPI/Swagger exports, `**/*tools*.json` inventories, Codex repo config (`.codex/config.toml`, `.codex/hooks.json`), Codex plugin package files (`.codex-plugin/**`, `.agents/plugins/**`, `**/.app.json`, `**/.mcp.json`, `**/SKILL.md`), `prompts/**`, `policies/**`, and `.github/workflows/agents-shipgate.{yml,yaml}`. Diff-only triggers (`TRIGGER-FUNCTION-TOOL-DECORATOR`, `TRIGGER-FRAMEWORK-VERSION-BUMP`, and the diff-leg of `TRIGGER-SHIPGATE-CI-WORKFLOW`) are not covered by the regex pre-gate — pre-commit's `files:` regex is purely path-based. Once the hook fires, the `verify` entry runs the full trigger evaluator (including diff rules) and base auto-detection itself. Use the GitHub Action for coverage on commits whose paths don't match the regex at all, or `python -m agents_shipgate.triggers --git-diff HEAD` for diff-aware local checks. The canonical hook manifest pre-commit reads from the repo root is [`/.pre-commit-hooks.yaml`](../.pre-commit-hooks.yaml) — it exposes `agents-shipgate`, `agents-shipgate-strict`, and `agents-shipgate-validate`. See [`examples/pre-commit/`](../examples/pre-commit/) for the longer write-up on advisory vs. strict modes and which hook ID to pick.
