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

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review.

For agent tool-surface or release-policy changes, run:

```bash
shipgate check --agent claude-code --workspace . --format agent-json
agents-shipgate verify --preview --json
agents-shipgate preflight --json
agents-shipgate verify --workspace . --config shipgate.yaml \\
  --ci-mode advisory --format json
```

For local agent control, read the `shipgate check` stdout JSON only. It is
`agent_result_v1`; switch on `decision`, then follow `first_next_action`,
`repair`, and `human_review`.

Before finishing an agent-related diff, run `shipgate check`. If
`decision=allow` or `warn`, continue and summarize. If `first_next_action.kind`
is `repair` and `repair.safe_to_attempt=true`, make only the listed mechanical
repair and rerun the command. If `human_review.required=true` or
`must_stop=true`, stop and surface the JSON result to a human.

Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules,
policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex
plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, run
`agents-shipgate preflight --json` or `agents-shipgate preflight
--changed-files changed.txt --json`. If `requires_human_review` is `true` or
`first_next_action.actor` is `human`, stop and route the change to a human.

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Read `agents-shipgate-reports/agent-result.json` first, then
`agents-shipgate-reports/verifier.json` for `merge_verdict` and
`agents-shipgate-reports/report.json.release_decision.decision` for the release
gate.

Use `apply-patches --confidence high --apply` only for high-confidence safe
patches. Approval, confirmation, idempotency, broad-scope, and prohibited-action
changes require human review.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits
release-visible.

Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on
stderr.

### CI

{CI_POINTER_PARAGRAPH}

<sub>Managed by agents-shipgate; rerun `agents-shipgate init --write --agent-instructions=claude-md` to refresh.</sub>
"""
