# 25-codex-skill

Installs the repo-scoped Codex skill at `.agents/skills/agents-shipgate/`.
Used by Codex adoption-harness cells to measure whether the skill helps Codex
discover and run Agents Shipgate without the prompt naming Shipgate.

`overlay.yaml` uses the package `codex-skill` renderer directly, so this variant
does not keep its own copy of the skill files.
