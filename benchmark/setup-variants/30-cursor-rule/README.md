# Variant 30 — Cursor rule pack

Layer the canonical Cursor rule onto the archetype repo. Cursor activates rules based on glob matches, so this variant fires only when a tool-surface file is touched in the agent's session.

## Setup

```bash
cd benchmark/repos/<archetype>/
mkdir -p .cursor/rules
cp ../../setup-variants/30-cursor-rule/agents-shipgate.mdc.template .cursor/rules/agents-shipgate.mdc
git add .cursor/rules/agents-shipgate.mdc
git commit -m "Add Cursor rule for Shipgate adoption (benchmark variant)"
```

## What this measures

Cursor's glob-triggered rule system is fundamentally different from Claude Code / Codex's "read at session start" model. This variant scores whether glob-targeted rules are an effective discovery channel for tool-surface PRs.

Expect this variant to score well on prompts `01`–`03` (which touch tool surfaces) and to score the same as `00-no-hints` on prompt `04` (docs-only — the rule's globs don't fire).
