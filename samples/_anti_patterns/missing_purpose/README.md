# Anti-pattern · missing declared purpose

This manifest is missing `agent.declared_purpose`, `agent.instructions_preview`, **and** `openai_api.prompt_files`. The Agents Shipgate manifest validator requires at least one of these.

## Expected behavior

```bash
$ agents-shipgate scan -c shipgate.yaml
Config error: Invalid shipgate.yaml:
- <root>: Value error, agent.declared_purpose, agent.instructions_preview, or openai_api.prompt_files is required
```

Exit code: `2` (manifest config error).

## Why this is an anti-pattern

Without a declared purpose or prompt, Agents Shipgate cannot:

- Run the `SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE` check (which compares declared purpose against the tool surface)
- Render meaningful agent metadata in reports
- Detect contradictions between purpose and prohibited actions

The fix: add 1-2 sentences under `agent.declared_purpose`, OR provide a prompt under `instructions_preview`, OR declare prompt files under `openai_api.prompt_files`.
