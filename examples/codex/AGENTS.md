# Codex Local Boundary Check

Before finishing a Codex-local change that touches `.codex/`, AGENTS files,
skills, plugins, MCP config, hooks, permission profiles, or the Shipgate CI
workflow, run:

```bash
git diff --no-ext-diff --unified=0 HEAD > /tmp/codex.diff
shipgate check --agent codex --diff /tmp/codex.diff --format agent-json
```

Read stdout as JSON only. Use `decision` as the local next-action signal:

- `allow`: continue normally.
- `warn`: continue and mention the warning.
- `require_review`: stop and surface `first_next_action.why` to a human.
- `block`: stop; do not claim the change is complete.

Do not weaken `shipgate.yaml`, the Shipgate workflow, AGENTS.md, skills, hooks,
policies, baselines, waivers, or suppressions to make the local check pass.

