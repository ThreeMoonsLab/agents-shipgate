"""Render the full ``.cursor/rules/agents-shipgate.mdc`` file.

We own the whole file. Content lifted from ``docs/target-repo-agent-snippets.md``
(the ``## .cursor/rules/agents-shipgate.mdc`` section).

Idempotency: the file is overwritten only if its current SHA-256 matches a hash
the package has shipped previously (this list grows when ``BLOCK_VERSION`` bumps).
A user-edited file the CLI has never produced is left alone with status
``skipped_user_modified``.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.renderers._shared import (
    CI_POINTER_PARAGRAPH,
)


def render_file() -> str:
    """Return the full file body for ``.cursor/rules/agents-shipgate.mdc``."""
    return f"""---
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
  - "**/*.py"
alwaysApply: false
---

When a change affects agent tools, MCP exports, OpenAPI specs, prompts,
permissions, approval policies, or release gates, run Agents Shipgate.

Use `agents-shipgate-reports/report.json` as the source of truth. Prefer
`release_decision.decision` over legacy severity/status summaries.

Apply only high-confidence safe patches. Do not invent approval, confirmation,
or idempotency evidence.

## CI

{CI_POINTER_PARAGRAPH}
"""


# SHA-256 hashes of every prior render of this file. When BLOCK_VERSION bumps
# and the rendered content changes, the previous current-render hash moves into
# this tuple so the next CLI run can safely overwrite v(N-1) files. Leave the
# tuple empty when there is no prior shipped version (v=1 is the initial).
PRIOR_RENDER_SHA256: tuple[str, ...] = ()
