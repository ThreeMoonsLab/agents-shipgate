"""Render the inner content for the AGENTS.md managed block.

Content lifted from ``docs/target-repo-agent-snippets.md`` (the `## AGENTS.md`
section) plus a closing ``### CI`` mini-section with the shared advisory pointer.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.renderers._shared import (
    CI_POINTER_PARAGRAPH,
    CURRENT_CONTROL_PARAGRAPH,
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
shipgate check --agent codex --workspace . --format agent-boundary-json
shipgate check --agent claude-code --workspace . --format agent-boundary-json
shipgate check --agent cursor --workspace . --format agent-boundary-json
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
operational control signal. Do not infer control from prose.

Before finishing an agent-related diff, run `shipgate check`. If
`control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action and command authorized by `control.next_action`, then rerun the command.
If `control.state=human_review_required`, stop and surface the JSON result to a
human. Conversation-level acknowledgement never clears this state; only a new
verifier artifact can do so.

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

{CURRENT_CONTROL_PARAGRAPH}

For coding-agent host grants, run `shipgate audit --host` and read the emitted
host-grants inventory before changing MCP servers, permission rules, hooks, or
workflow scopes.

Auto-apply only high-confidence safe patches. Do not auto-assert action effect,
action authority, approval, confirmation, idempotency, broad-scope, or
prohibited-action policy decisions; surface those as human review items.

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
