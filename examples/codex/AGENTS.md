# Codex Local Boundary Check

Before finishing a Codex-local change that touches `.codex/`, AGENTS files,
skills, plugins, MCP config, hooks, permission profiles, or the Shipgate CI
workflow, run:

```bash
git diff --no-ext-diff --unified=0 HEAD > /tmp/codex.diff
shipgate check --agent codex --diff /tmp/codex.diff --format agent-boundary-json
```

Read stdout as `shipgate.agent_boundary_result/v2` JSON only. Switch on
`control.state`; `decision` is diagnostic context:

- `complete`: continue and report the diagnostic decision.
- `agent_action_required`: perform only `control.next_action`, then rerun.
- `review_publishable`: a human must approve the merge. Do not merge or claim
  completion; you may still commit, push, and update the pull request so that
  review can happen.
- `human_review_required`: stop and surface `control.next_action.why` to a human.

Do not weaken `shipgate.yaml`, the Shipgate workflow, AGENTS.md, skills, hooks,
policies, baselines, waivers, or suppressions to make the local check pass.
