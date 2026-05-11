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
| [`misplaced_field/`](misplaced_field/) | `declared_purpose` at the wrong nesting level (root vs. under `agent`). Fails validation as an extra root-level input. |
| [`dynamic_toolset_factory/`](dynamic_toolset_factory/) | OpenAI Agents SDK source where tools are built by a runtime factory wrapper. Manifest validates; scan produces `SHIP-INVENTORY-NOT-ENUMERABLE` high. Documents the canonical recovery paths from [`agent-recipes.md`](../../docs/agent-recipes.md#recipe-2--add-shipgate-to-a-repo-that-already-has-tool-surfaces). |

These fixtures are excluded from `agents-shipgate fixture list` (the directory name starts with `_`). They are not part of the supported public surface.
