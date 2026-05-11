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
