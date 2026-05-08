# Variant 10 — `AGENTS.md` snippet

Layer the canonical AGENTS.md trigger snippet onto the archetype repo before running the prompt.

## Setup

```bash
cd benchmark/repos/<archetype>/
cp ../../setup-variants/10-agents-md/AGENTS.md.template AGENTS.md
# If the archetype already has an AGENTS.md, append the snippet rather
# than overwriting:
#   cat ../../setup-variants/10-agents-md/AGENTS.md.template >> AGENTS.md
git add AGENTS.md
git commit -m "Add AGENTS.md release-readiness snippet (benchmark variant)"
```

The template's content is the canonical "Agent Release Readiness" section from [`docs/target-repo-agent-snippets.md`](../../../docs/target-repo-agent-snippets.md#agentsmd) — keep it in sync with that doc when the trigger surface changes.

## What this measures

If `00-no-hints` scores low and `10-agents-md` scores high, the in-repo prose is doing the discovery work. That's the cheapest, most repeatable win.

If both score the same, agents aren't reading `AGENTS.md` (or the snippet's prose isn't compelling). Investigate placement (top of file? mid-file?) and prose (does it mention the trigger globs explicitly?).
