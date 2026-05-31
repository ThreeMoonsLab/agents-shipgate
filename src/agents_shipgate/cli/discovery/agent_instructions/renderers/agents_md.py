"""Render the inner content for the AGENTS.md managed block.

Content lifted from ``docs/target-repo-agent-snippets.md`` (the `## AGENTS.md`
section) plus a closing ``### CI`` mini-section with the shared advisory pointer.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.renderers._shared import (
    CI_POINTER_PARAGRAPH,
)


def render_block() -> str:
    """Return the inner content (between markers) for AGENTS.md."""
    return f"""## Tool-Use Readiness Release Gate

This repo uses Agents Shipgate as the deterministic merge gate for AI-generated
agent capability changes — a local-first, static Tool-Use Readiness review.

Run Shipgate when this PR changes agent tools, MCP exports, OpenAPI specs,
prompts that constrain tool use, permissions, approval policies,
confirmation policies, CI release gates, or `shipgate.yaml`.

Skip Shipgate for pure docs, tests, formatting, or refactors with no tool or
policy impact unless the user explicitly asks.

Commands:

```bash
agents-shipgate detect --workspace . --json
agents-shipgate init --workspace . --write --ci --json
agents-shipgate verify --workspace . --config shipgate.yaml \\
  --ci-mode advisory --format json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches \\
  --from agents-shipgate-reports/report.json \\
  --confidence high --apply
```

Always parse `agents-shipgate-reports/report.json`, not Markdown. Use
`release_decision.decision` as the release signal.

Before finishing an agent-related diff in a repo with `shipgate.yaml`, run
`agents-shipgate verify` or report the exact `agents-shipgate trigger` skip
verdict. Omit `--base` and `--head` for local pre-commit work so uncommitted
edits are scanned; add `--base origin/main --head HEAD` only for committed
PR/CI verification after making the base ref available.
`agents-shipgate-reports/verifier.json` explains trigger/base status; it is
not a second release verdict.

Auto-apply only high-confidence safe patches. Do not auto-assert approval,
confirmation, idempotency, broad-scope, or prohibited-action policy decisions;
surface those as human review items.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits
release-visible.

Before committing, ensure `.gitignore` includes:

```gitignore
agents-shipgate-reports/
```

### CI

{CI_POINTER_PARAGRAPH}

<sub>Managed by agents-shipgate; rerun `agents-shipgate init --write --agent-instructions=agents-md` to refresh. If your linter forbids raw HTML, exempt `<!-- agents-shipgate:* -->`.</sub>
"""
