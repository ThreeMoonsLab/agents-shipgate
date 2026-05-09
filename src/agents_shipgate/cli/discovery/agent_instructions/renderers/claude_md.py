"""Render the inner content for the CLAUDE.md managed block.

Content lifted from ``docs/target-repo-agent-snippets.md`` (the `## CLAUDE.md`
section). Self-contained — no cross-link to AGENTS.md so generating only this
target does not produce a dangling reference.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.renderers._shared import (
    CI_POINTER_PARAGRAPH,
)


def render_block() -> str:
    """Return the inner content (between markers) for CLAUDE.md."""
    return f"""## Agents Shipgate

For agent tool-surface or release-policy changes, run:

```bash
agents-shipgate detect --workspace . --json
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

Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on
stderr.

### CI

{CI_POINTER_PARAGRAPH}

<sub>Managed by agents-shipgate; rerun `agents-shipgate init --write --agent-instructions=claude-md` to refresh.</sub>
"""
