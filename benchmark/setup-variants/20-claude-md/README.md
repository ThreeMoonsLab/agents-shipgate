# Variant 20 — `CLAUDE.md`

Layer the canonical CLAUDE.md snippet onto the archetype repo before running the prompt. Claude Code reads `CLAUDE.md` at session start; this variant tests whether that path is enough to drive Shipgate adoption.

## Setup

```bash
cd benchmark/repos/<archetype>/
cp ../../setup-variants/20-claude-md/CLAUDE.md.template CLAUDE.md
git add CLAUDE.md
git commit -m "Add CLAUDE.md Shipgate snippet (benchmark variant)"
```

If the archetype already has a `CLAUDE.md`, append rather than overwrite.

## What this measures

`20-claude-md` and `10-agents-md` carry similar information; the difference is which file the agent prefers. Claude Code privileges `CLAUDE.md`; Codex/Cursor weight `AGENTS.md` more. Running both variants on the same archetype across all three agents tells us whether to prioritize one snippet or both.

A common failure mode: `CLAUDE.md` exists but is dense (thousands of words). The Shipgate section gets lost. Keep the snippet short.
