# Target-Repo Agent Snippets

Use these snippets in repos that contain tool-using AI agents. They make
Agents Shipgate visible to coding agents working in that target repo, not only
to agents reading the Agents Shipgate source repo.

> The CLI plants these snippets for you. Run
> `agents-shipgate init --write --agent-instructions=all` (or pass a subset
> like `--agent-instructions=agents-md,cursor`) to emit them into managed
> `<!-- agents-shipgate:start -->` blocks. Idempotent — safe to rerun. The raw
> content below is the canonical reference and the source the renderers in
> `src/agents_shipgate/cli/discovery/agent_instructions/renderers/` lift from.

## When To Run

Run Agents Shipgate when a repo or PR changes:

- agent tools or tool decorators
- MCP exports
- OpenAPI specs
- prompts that constrain tool use
- permissions, scopes, approval policies, or confirmation policies
- `.github/workflows/agents-shipgate.yml` or another release gate
- `shipgate.yaml`

Skip it for pure docs, tests, formatting, or refactors with no tool or policy
impact unless Shipgate is already configured or the user explicitly asks.

## `AGENTS.md`

````md
## Agent Release Readiness

This repo uses Agents Shipgate as the static release-readiness preflight for
AI agent tool surfaces.

Run Shipgate when this PR changes agent tools, MCP exports, OpenAPI specs,
prompts that constrain tool use, permissions, approval policies,
confirmation policies, CI release gates, or `shipgate.yaml`.

Skip Shipgate for pure docs, tests, formatting, or refactors with no tool or
policy impact unless the user explicitly asks.

Commands:

```bash
agents-shipgate detect --workspace . --json
agents-shipgate contract --json
agents-shipgate init --workspace . --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches \
  --from agents-shipgate-reports/report.json \
  --confidence high --apply
```

Always parse `agents-shipgate-reports/report.json`, not Markdown. Use
`release_decision.decision` as the release signal.

Auto-apply only high-confidence safe patches. Do not auto-assert approval,
confirmation, idempotency, broad-scope, or prohibited-action policy decisions;
surface those as human review items.

Before committing, ensure `.gitignore` includes:

```gitignore
agents-shipgate-reports/
```
````

## `CLAUDE.md`

````md
## Agents Shipgate

For agent tool-surface or release-policy changes, run:

```bash
agents-shipgate detect --workspace . --json
agents-shipgate contract --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
```

Read `agents-shipgate-reports/report.json` and summarize:

- `release_decision.decision`
- blocker count
- review item count
- top critical/high findings
- safe patches applied
- findings requiring human review

Use `apply-patches --confidence high --apply` only for high-confidence safe
patches. Approval, confirmation, idempotency, broad-scope, and prohibited-action
changes require human review.
````

## `.cursor/rules/agents-shipgate.mdc`

```md
---
description: Run Agents Shipgate for AI agent tool-surface release readiness.
globs:
  - "shipgate.yaml"
  - "**/*openapi*.yaml"
  - "**/*openapi*.yml"
  - "**/*openapi*.json"
  - "**/*swagger*.yaml"
  - "**/*swagger*.yml"
  - "**/*swagger*.json"
  - "**/*mcp*.json"
  - "**/*tools*.json"
  - ".codex-plugin/**"
  - "**/.codex-plugin/**"
  - ".agents/plugins/**"
  - "**/.agents/plugins/**"
  - "**/.app.json"
  - "**/.mcp.json"
  - "**/SKILL.md"
  - "n8n/*.json"
  - "workflows/*.json"
  - "**/*workflow*.json"
  - ".agents-shipgate/*.json"
  - "prompts/**"
  - "policies/**"
  - ".github/workflows/agents-shipgate.yml"
  - ".github/workflows/agents-shipgate.yaml"
alwaysApply: false
---

When a change affects agent tools, MCP exports, OpenAPI specs, prompts,
permissions, approval policies, or release gates, run Agents Shipgate.
Default to advisory scans while adopting the gate.

Use `agents-shipgate-reports/report.json` as the source of truth. Prefer
`release_decision.decision` over legacy severity/status summaries.

Apply only high-confidence safe patches. Do not invent approval, confirmation,
or idempotency evidence.

For one-fetch counts and a deterministic next step, read
`report.json.agent_summary` (v0.12+): verdict, blocker_count,
review_item_count, auto_appliable_patches, needs_human_review,
first_recommended_action.

For per-finding routing read `findings[].agent_action` (v0.12+):
auto_apply, propose_patch_for_review, escalate_to_human,
suppress_with_reason, informational. Do not synthesize an action from
the underlying flags when the enum is present.

To translate a single finding into user-facing prose, run:

  agents-shipgate explain-finding <FINGERPRINT> \
      --from agents-shipgate-reports/report.json --json

The payload includes the full Finding shape plus `metadata` (catalog
CheckMetadata) and `explanation` (a deterministic 3–5 sentence prose
summary). See `prompts/explain-finding-to-user.md` for the
translation rubric.

References:

- AGENTS.md — agent-facing instructions
- docs/agent-contract-current.md — current schema versions and field list
- docs/agent-action-guide.md — per-category recipe for what to DO with a finding
- docs/upstream-integrations.md — per-framework drop-in (60-second adoption)
- docs/triggers.json — machine-readable mirror of the trigger table
```

## `.github/pull_request_template.md`

````md
## Agent Tool-Surface Release Readiness

- [ ] If this PR changes agent tools, MCP/OpenAPI specs, prompts, permissions,
      approval policy, confirmation policy, CI release gates, or
      `shipgate.yaml`, I ran:

      ```bash
      agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
      ```

- [ ] I reviewed `agents-shipgate-reports/report.json` and used
      `release_decision.decision` as the release signal.
- [ ] I did not auto-assert approval, confirmation, idempotency, broad-scope,
      or prohibited-action policy decisions.
````

## Advisory GitHub Action

```yaml
name: Agents Shipgate

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  agents-shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.10.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          diff_base: target
          pr_comment: "true"
```

Advisory mode reports findings without blocking merge. Move to strict mode only
after the team has triaged current findings and saved a baseline.
