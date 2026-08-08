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
shipgate check --agent claude-code --workspace . --format agent-boundary-json
agents-shipgate verify --workspace . --config shipgate.yaml \\
  --ci-mode advisory --format json
agents-shipgate verify --workspace . --config shipgate.yaml \\
  --base origin/main --head HEAD --ci-mode advisory --format json
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

For local agent control, read the `shipgate check` stdout JSON only. It is
`shipgate.agent_boundary_result/v1`; switch on `control.state`, then follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`. Treat `decision` as diagnostic context, not as the
operational control signal.

Before finishing an agent-related diff, run `shipgate check`. If
`control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action and command authorized by `control.next_action`, then rerun the command.
If `control.state=review_publishable`, a human must approve the merge — surface
the JSON result and note that you may still commit, push, and update the pull
request so that review can happen. If `control.state=human_review_required`,
stop and surface the JSON result to a human. `control.permissions` states the
authority exactly: updating a pull request is not merging it, and
`permissions.merge` / `permissions.report_complete` are false on every state
except `complete`. Conversation-level acknowledgement never clears these
states; only a new verifier artifact can do so.

For committed PR/CI verification, run `agents-shipgate verify --base
origin/main --head HEAD --json` after making the base ref available; it never
fetches. Validate `agents-shipgate-reports/verification-receipt.json` first,
then read `agents-shipgate-reports/agent-handoff.json` for
`gate.merge_verdict`, `gate.can_merge_without_human`, and `control`; then read
`agents-shipgate-reports/verifier.json` for detailed control context,
`agents-shipgate-reports/verify-run.json` for reproducibility metadata, and
`agents-shipgate-reports/report.json.release_decision.decision` for the
release gate.
Legacy `agent-result.json` surfaces, where present, are supporting/provisional
projections and not the CI gate.

For coding-agent host grants, run `shipgate audit --host` and read the emitted
host-grants inventory before changing MCP servers, permission rules, hooks, or
workflow scopes.

Use `apply-patches --confidence high --apply` only for high-confidence safe
patches. Action effect, action authority, approval, confirmation, idempotency,
broad-scope, and prohibited-action changes require human review.

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
