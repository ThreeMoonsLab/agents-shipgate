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

  shipgate check --agent cursor --workspace . --format codex-boundary-json

Read the check stdout JSON only. It is
`shipgate.codex_boundary_result/v2`; switch on `control.state`, then follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`. Treat `decision` as diagnostic context, not as the
operational control signal. Do not infer control from prose.

If `control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action and command authorized by `control.next_action`, then rerun the command.
If `control.state=human_review_required`, stop and surface the JSON result to a
human. Conversation-level acknowledgement never clears this state; only a new
verifier artifact can do so.

For local verification, run:

  agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Read `agents-shipgate-reports/agent-handoff.json` first for
`gate.merge_verdict`, `gate.can_merge_without_human`, and `control`; then read
`agents-shipgate-reports/verifier.json` for detailed control context,
`agents-shipgate-reports/verify-run.json` for reproducibility metadata, and
`agents-shipgate-reports/report.json.release_decision.decision` for the
release gate.
Legacy `agent-result.json` surfaces, where present, are supporting/provisional
projections and not the CI gate.

For coding-agent host grants, run:

  shipgate audit --host --json --out agents-shipgate-reports/host-grants.json

Read the host-grants inventory before changing MCP servers, permission rules,
hooks, or workflow scopes.

Apply only high-confidence safe patches. Do not invent action effect, action
authority, approval, confirmation, or idempotency evidence.

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
