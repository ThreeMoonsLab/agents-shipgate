"""Render the full ``.cursor/rules/agents-shipgate.mdc`` file.

We own the whole file. Content lifted from ``docs/target-repo-agent-snippets.md``
(the ``## .cursor/rules/agents-shipgate.mdc`` section).

Idempotency: the file is overwritten only if its current SHA-256 matches a hash
the package has shipped previously (this list grows when ``BLOCK_VERSION`` bumps).
A user-edited file the CLI has never produced is left alone with status
``skipped_user_modified``.
"""

from __future__ import annotations


def render_file() -> str:
    """Return the full file body for ``.cursor/rules/agents-shipgate.mdc``."""
    return """---
description: Run Agents Shipgate as the deterministic merge gate for AI-generated agent capability changes.
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

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review.

When a change affects agent tools, MCP exports, OpenAPI specs, prompts,
permissions, approval policies, or release gates, run Agents Shipgate.
Default to advisory verification while adopting the gate.

For local agent control, run:

  agents-shipgate preflight --workspace . --plan - --json
  shipgate check --agent cursor --workspace . --format agent-json

Read the stdout JSON only. It is `agent_result_v1`; switch on `decision`,
`completion_allowed`, and `must_stop`, then follow `first_next_action`,
`human_review`, `repair`, and `policy`. Do not infer a decision from prose.

If `decision=allow` or `warn`, continue and summarize. If
`first_next_action.kind` is `repair` and `repair.safe_to_attempt=true`, make
only the listed mechanical repair and rerun the command. If
`human_review.required=true` or `must_stop=true`, stop and surface the JSON
result to a human.

Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules,
policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex
plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, run
`agents-shipgate preflight --workspace . --plan - --json` with a
`PreflightPlanV1` object. Legacy shorthands such as
`agents-shipgate preflight --changed-files changed.txt --json` remain available.
If `requires_human_review` is `true` or
`first_next_action.actor` is `human`, stop and route the change to a human.

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Read `agents-shipgate-reports/agent-result.json` first, then
`agents-shipgate-reports/verifier.json` for `merge_verdict` and
`agents-shipgate-reports/report.json.release_decision.decision` for the release
gate.

Apply only high-confidence safe patches. Do not invent approval, confirmation,
or idempotency evidence.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits
release-visible.

For one-fetch counts and a deterministic next step, read
`report.json.agent_summary` (v0.12+): verdict, blocker_count,
review_item_count, auto_appliable_patches, needs_human_review,
first_recommended_action.

For per-finding routing read `findings[].agent_action` (v0.12+):
auto_apply, propose_patch_for_review, escalate_to_human,
suppress_with_reason, informational. Do not synthesize an action from
the underlying flags when the enum is present.

For reviewer triage by source reliability, run
`agents-shipgate findings --from agents-shipgate-reports/report.json
--provenance-kind keyword_heuristic,regex_heuristic --json`. The
underlying `findings[].provenance_kind` field is a filter signal only,
not a gate input.

To translate a single finding into user-facing prose, run:

  agents-shipgate explain-finding <FINGERPRINT> \\
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
"""


# SHA-256 hashes of every prior render of this file. When BLOCK_VERSION bumps
# and the rendered content changes, the previous current-render hash moves into
# this tuple so the next CLI run can safely overwrite v(N-1) files. Leave the
# tuple empty when there is no prior shipped version (v=1 is the initial).
PRIOR_RENDER_SHA256: tuple[str, ...] = ()
