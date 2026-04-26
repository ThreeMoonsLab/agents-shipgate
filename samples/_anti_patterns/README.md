# Anti-pattern fixtures

Manifests that intentionally fail validation or produce specific findings. **Do not use these as templates.** They exist so:

1. **Agents can be told "look here for what NOT to do."**
2. The test suite can pin "this configuration produces this error" invariants.

Each subdirectory has a `README.md` explaining the deliberate problem and the expected error message or finding.

| Directory | Demonstrates |
|---|---|
| [`missing_purpose/`](missing_purpose/) | Manifest missing `agent.declared_purpose` (or `instructions_preview`). Fails validation. |
| [`path_traversal/`](path_traversal/) | `tool_sources[].path` escapes the manifest directory. Rejected with `InputParseError`. |
| [`empty_suppression_reason/`](empty_suppression_reason/) | `checks.ignore` entry with `reason: ""`. Fails validation. |
| [`misplaced_field/`](misplaced_field/) | `declared_purpose` at the wrong nesting level (root vs. under `agent`). Fails with a typo suggestion. |

These fixtures are excluded from `agents-shipgate fixture list` (the directory name starts with `_`). They are not part of the supported public surface.
