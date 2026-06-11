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
    return f"""## Agents Shipgate — merge gate

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes. This repo uses it to gate what its AI agent can do.

- After changing MCP servers, tool definitions, OpenAPI specs that describe
  agent tools, agent prompts, permission scopes, approval/confirmation
  policies, agent CI workflows, or `shipgate.yaml` — and before creating a PR
  for any such change — run: `agents-shipgate verify --json`
  (unsure Shipgate applies? `agents-shipgate verify --preview --json`)
- Act on the stdout JSON: `merge_verdict`, `can_merge_without_human`, and the
  repair instructions. Full artifacts land in `agents-shipgate-reports/`;
  `report.json.release_decision.decision` is the release gate.
- Zero-flag runs auto-detect the default branch as base (v0.12+) and scan the
  working tree. For committed PR/CI refs, pass `--base`/`--head` explicitly
  after making the base ref available (`verify` never fetches).
- Do not claim completion while `merge_verdict` is `blocked`,
  `insufficient_evidence`, or `human_review_required` unless the user
  explicitly accepts human review.
- Never suppress findings, lower severities, expand baselines or waivers,
  remove Shipgate CI, or weaken agent instructions to make verify pass;
  `SHIP-VERIFY-*` checks make those trust-root edits release-visible.
- Pure docs/test/format changes with no tool or policy impact: skip.

### CI

{CI_POINTER_PARAGRAPH}

<sub>Managed by agents-shipgate; rerun `agents-shipgate init --write --agent-instructions=claude-md` to refresh.</sub>
"""
