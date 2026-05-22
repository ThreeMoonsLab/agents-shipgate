"""Render the repo-scoped Codex skill bundle.

The canonical checked-in copy lives under ``.agents/skills/agents-shipgate``.
This renderer deliberately keeps a hard-coded copy so the installed wheel can
generate the skill without relying on repository files being present.
Snapshot tests keep the two copies in sync.
"""

from __future__ import annotations


def render_files() -> dict[str, str]:
    """Return relative file path -> UTF-8 text for the Codex skill bundle."""
    return {
        ".agents/skills/agents-shipgate/SKILL.md": _SKILL_MD,
        ".agents/skills/agents-shipgate/references/recipes.md": _RECIPES_MD,
        ".agents/skills/agents-shipgate/references/report-reading.md": _REPORT_READING_MD,
        ".agents/skills/agents-shipgate/assets/advisory-pr-comment.yml": _ADVISORY_CI_YML,
        ".agents/skills/agents-shipgate/agents/openai.yaml": _OPENAI_YAML,
    }


def render_bundle_text() -> str:
    """Return a human-readable dry-run rendering of the full bundle."""
    chunks: list[str] = []
    for path, text in render_files().items():
        chunks.append(f"--- {path} ---\n{text.rstrip()}\n")
    return "\n".join(chunks)


PRIOR_RENDER_SHA256: dict[str, tuple[str, ...]] = {}


_SKILL_MD = """---
name: agents-shipgate
description: Use when the user wants to add or run Agents Shipgate as a local-first, static Tool-Use Readiness release gate for an AI agent's tool surface; review or prepare a tool-using agent for release; scan MCP, OpenAPI, OpenAI Agents SDK, Anthropic, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API, Codex plugin, or n8n tool artifacts; add advisory CI; or interpret, fix, triage, suppress, or explain a Shipgate finding.
---

# Agents Shipgate

Agents Shipgate is a local-first, static Tool-Use Readiness release gate for AI agent tool surfaces. It reads `shipgate.yaml` plus local tool sources and writes deterministic reports as Markdown, JSON, SARIF, and Release Evidence Packets.

Use this skill when a task touches agent tools, MCP exports, OpenAPI specs, prompts that constrain tool use, permissions/scopes, approval or confirmation policies, `shipgate.yaml`, Shipgate CI, or `agents-shipgate-reports/report.json`.

Do not use it for general linting, runtime monitoring, evals, model-output quality, or runtime guardrail enforcement. Shipgate is static-only: no agent execution, no tool calls, no LLM calls, no MCP server connections, and no telemetry by default.

## Workflow

1. For relevance decisions, bootstrap, scanning, CI setup, finding fixes, false-positive triage, strict-mode promotion, or version upgrades, read `references/recipes.md`.
2. For reading `report.json`, summarizing release decisions, or deciding what may be auto-applied, read `references/report-reading.md`.
3. Set `AGENTS_SHIPGATE_AGENT_MODE=1` before running Shipgate commands so errors include structured `next_action` JSON.
4. Default first-time CI to advisory mode. Do not enable release-blocking CI or save a baseline until a human has reviewed current findings.
5. Always parse `agents-shipgate-reports/report.json`, not Markdown. Use `release_decision.decision` as the release signal.
6. Auto-apply only high-confidence safe patches. Do not auto-assert approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
7. Ensure `.gitignore` covers `agents-shipgate-reports/` before committing.

## Fast Paths

- First adoption: run `agents-shipgate detect --workspace . --json`, then follow `references/recipes.md`.
- Existing manifest: run `agents-shipgate scan -c shipgate.yaml --suggest-patches --format json`.
- First GitHub CI: copy `assets/advisory-pr-comment.yml` to `.github/workflows/agents-shipgate.yml`.
- Explain one finding: run `agents-shipgate explain-finding <fingerprint> --from agents-shipgate-reports/report.json --json`.

## Boundaries

- Do not claim a finding is fixed without re-running `agents-shipgate scan` and reporting the new decision/counts.
- Do not silently suppress findings. Suppressions require a non-empty `reason`.
- Do not commit generated reports.
- Do not edit the upstream `agents-shipgate` check implementation unless the user is working in the Agents Shipgate repo itself.
"""


_RECIPES_MD = """# Agents Shipgate Recipes

Use these recipes after the `agents-shipgate` skill triggers.

## Decide Relevance

Run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate detect --workspace . --json
```

Proceed when any of these are true:

- `is_agent_project: true`
- `suggested_sources` is non-empty
- `codex_plugin_candidates` is non-empty
- `shipgate.yaml` already exists
- the user explicitly asked for a Shipgate scan or Tool-Use Readiness gate

Stop only when all signals are absent and the user did not explicitly request Shipgate.

## Bootstrap A Repo

Run:

```bash
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate detect --workspace . --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate contract --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate init --workspace . --write --ci --json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate apply-patches \\
  --from agents-shipgate-reports/report.json \\
  --confidence high --apply
```

If `init` reports placeholders, replace `CHANGE_ME` values from repo context before scanning. If `shipgate.yaml` already exists, edit it rather than overwriting it.

## First-Time CI

Use advisory mode only. Copy `assets/advisory-pr-comment.yml` to `.github/workflows/agents-shipgate.yml`.

Do not switch to release-blocking behavior in the same task. Strict promotion requires human review, suppressions with reasons, and optionally a saved baseline.

## Fix Top Finding

1. Read `agents-shipgate-reports/report.json`.
2. Pick the first blocker, then highest-severity review item.
3. If `findings[].agent_action == "auto_apply"` and a high-confidence patch exists, apply it with `apply-patches --confidence high --apply`.
4. For policy/evidence gaps, propose the exact human decision needed. Do not fabricate approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
5. Re-run scan and report the new `release_decision.decision`, blocker count, and review item count.

## Recommend Fixes

Group active findings by action:

- `auto_apply`: safe mechanical patches.
- `propose_patch_for_review`: show patch, leave final decision to user.
- `escalate_to_human`: policy/evidence decision.
- `suppress_with_reason`: only when the user confirms the finding is intentionally accepted.
- `informational`: summarize, no gate action.

## Explain A Finding

Run:

```bash
agents-shipgate explain-finding <fingerprint> \\
  --from agents-shipgate-reports/report.json --json
```

Use the returned deterministic `explanation` for PR comments or chat replies. Keep it to 3-5 sentences and include the tool name, release risk, and next action.

## Triage False Positives

Prefer fixing the manifest or policy evidence over suppression. Suppress only with a specific reason:

```yaml
checks:
  ignore:
    - check_id: SHIP-CHECK-ID
      tool: tool.name
      reason: specific accepted-risk rationale
```

## Promote Advisory To Strict

Only after humans review advisory output:

```bash
agents-shipgate baseline save -c shipgate.yaml --out .agents-shipgate/baseline.json
agents-shipgate scan -c shipgate.yaml \\
  --baseline .agents-shipgate/baseline.json \\
  --ci-mode strict --fail-on critical,high
```

The promoted gate should fail only on new findings above the selected threshold.

## Upgrade Shipgate

Update the GitHub Action tag and `shipgate_version` together. Re-run:

```bash
agents-shipgate contract --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
```

If schema or decision fields changed, use `docs/agent-contract-current.md` from the installed version or upstream repo.
"""


_REPORT_READING_MD = """# Reading Agents Shipgate Reports

Always read `agents-shipgate-reports/report.json`. Do not scrape Markdown.

## Order

1. `release_decision.decision`: `blocked`, `review_required`, `insufficient_evidence`, or `passed`.
2. `release_decision.blockers[]`: items blocking release.
3. `release_decision.review_items[]`: accepted debt or human-review items.
4. `agent_summary`: one-fetch summary with `headline`, counts, safe patches, human-review needs, and `first_recommended_action`.
5. `findings[]`: detailed evidence, source, severity, and remediation.

## Per-Finding Action

Prefer `findings[].agent_action` when present:

- `auto_apply`: safe to apply only when a high-confidence patch exists.
- `propose_patch_for_review`: show patch and ask for review.
- `escalate_to_human`: policy/evidence decision.
- `suppress_with_reason`: suppress only after explicit user confirmation.
- `informational`: summarize only.

Do not synthesize an action from lower-level fields when `agent_action` exists.

## Manual-Review Boundary

Never auto-assert these categories:

- approval policy
- confirmation policy
- idempotency evidence
- broad-scope permission decisions
- prohibited-action policy decisions
- runtime trace evidence

For those, summarize the risk and the exact decision a human needs to make.

## Summary Template

Report back with:

```text
Decision: <release_decision.decision>
Blockers: <count>
Review items: <count>
Safe patches applied: <count or none>
Needs human review: <short list>
Top findings:
1. <check/tool/risk/next action>
```

If `privacy_audit` is present, mention that default report redaction ran. If `insufficient_evidence` appears, treat it as review-required unless the user has stricter release policy.
"""


_ADVISORY_CI_YML = """# Advisory PR comment.
# Recommended starting point: runs the scanner on every PR, posts a summary
# comment, uploads the report as an artifact, and never fails the job.
name: Agents Shipgate (advisory)

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  shipgate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.10.0
        with:
          ci_mode: advisory
          diff_base: target
          pr_comment: 'true'
          shipgate_version: '0.10.0'
"""


_OPENAI_YAML = """interface:
  display_name: "Agents Shipgate"
  short_description: "Run Tool-Use Readiness release gates"
  default_prompt: "Use $agents-shipgate to add a Tool-Use Readiness release gate to this agent repo."

policy:
  allow_implicit_invocation: true
"""
