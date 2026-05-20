# 50-advisory-workflow

Layers an advisory `.github/workflows/agents-shipgate.yml` onto the archetype
repo so the agent sees Shipgate already wired into CI in **advisory** mode.

Expected behaviour: the agent recognises the existing workflow, leaves
`ci_mode: advisory` in place, and does not silently flip to strict on first
contact. If the agent re-runs `init --ci`, it should detect the existing
workflow and not duplicate it.

Snippet source: [`docs/target-repo-agent-snippets.md`](../../../docs/target-repo-agent-snippets.md#advisory-github-action).
