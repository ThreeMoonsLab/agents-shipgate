# Anti-pattern · misplaced field

This manifest puts `declared_purpose` at the root level instead of nested under `agent`. The validator rejects extra root-level keys explicitly.

## Expected behavior

```bash
$ agents-shipgate scan -c shipgate.yaml
Config error: Invalid shipgate.yaml:
- declared_purpose: Extra inputs are not permitted
```

Exit code: `2` (manifest config error).

## Why this is an anti-pattern

The root manifest schema only allows a fixed set of keys (`version`, `project`, `agent`, `environment`, `tool_sources`, etc.). Putting an `agent`-level field at the root looks plausible — many YAML schemas are permissive — but Pydantic's `extra="forbid"` mode means any unrecognized key fails fast as an extra input.

The fix: nest the field under its owning section.

```yaml
agent:
  name: my-agent
  declared_purpose:
    - what the agent should do
```

The full schema lives at [`docs/manifest-v0.1.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/manifest-v0.1.json) (machine-validatable) and [`docs/manifest-v0.1.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/manifest-v0.1.md) (prose). Editor support: every manifest produced by `agents-shipgate init` includes a `# yaml-language-server: $schema=…` comment so VS Code / Cursor flag misplaced fields inline.
