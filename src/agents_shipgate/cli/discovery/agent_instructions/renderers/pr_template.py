"""Render the inner content for the PR template managed block.

Content lifted from ``docs/target-repo-agent-snippets.md`` (the
``## .github/pull_request_template.md`` section). The conditional "If this PR
changes…" wording avoids docs-only false positives.
"""

from __future__ import annotations


def render_block() -> str:
    """Return the inner content (between markers) for the PR template."""
    return """## Agent Tool-Surface Release Readiness

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

<sub>Managed by agents-shipgate; rerun `agents-shipgate init --write --agent-instructions=pr-template` to refresh.</sub>
"""
