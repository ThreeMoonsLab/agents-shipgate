# Ranked Next-Action Diagnostics

`agents-shipgate detect`, `doctor`, and structured errors emit a
`diagnostics[]` and `next_actions[]` block alongside the existing
`next_action: str` field. A coding-agent caller can read the rank-1
action and route to the next command without consulting human-facing
docs.

Diagnostics are advisory — they do not change exit codes. Exit codes
are still owned by `ConfigError(2)`, `InputParseError(3)`, and
the scan policy block (`20`). A diagnostic with `severity: "block"`
describes a blocking *condition*; the caller decides what to do.

## Schema

`Diagnostic` (one per detected condition):

```json
{
  "id": "SHIP-DIAG-...",
  "title": "Human-readable one-liner",
  "severity": "block | warn | info",
  "next_actions": [ NextAction, ... ]
}
```

`NextAction` (ranked recovery step; ordered list — array position is
the rank, no separate `rank` field):

| Field    | Type                                      | Notes                                                         |
| -------- | ----------------------------------------- | ------------------------------------------------------------- |
| kind     | `command \| edit \| review \| stop`       | Action category.                                              |
| command  | `string \| null`                          | Required when `kind="command"`. Always `null` when `"stop"`. |
| path     | `string \| null`                          | Required when `kind="edit"`. May be `shipgate.yaml:<line>`. |
| why      | `string`                                  | One-sentence rationale.                                       |
| expects  | `string \| null`                          | Optional: what the next run should output if the action worked. |

The legacy `next_action: str` field on `detect`, `doctor`, and
agent-mode error JSON is the rank-1 action projected to a single string:

| Rank-1 kind | Legacy projection                  |
| ----------- | ---------------------------------- |
| command     | the `command` value verbatim       |
| edit        | `Edit <path>`                      |
| review      | `Review: <why>`                    |
| stop        | `Stop: <why>`                      |

This keeps `next_action` string-typed even for negative-control
diagnostics where no command should run.

## Catalog

| ID                                  | Severity | Fires when                                                                                                                                       |
| ----------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SHIP-DIAG-MISSING-MANIFEST`        | block    | `shipgate.yaml` not found in the working directory.                                                                                              |
| `SHIP-DIAG-NO-AGENT-SURFACE`        | info     | `is_agent_project=false` AND `suggested_sources=[]` AND no manifest. Catch-all negative control.                                                |
| `SHIP-DIAG-NON-AGENT-LIBRARY`       | info     | Python project (≥1 .py file + pyproject/requirements) with no agent framework, prompts, or tool surface.                                         |
| `SHIP-DIAG-PURE-PROMPT-EXPERIMENT`  | info     | Only `prompts/` is present; no Python framework, no tool sources.                                                                                |
| `SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY` | info   | `is_agent_project=false` BUT `suggested_sources` has MCP/OpenAPI entries. Artifact-only repos are valid Shipgate targets.                        |
| `SHIP-DIAG-ZERO-TOOLS`              | block    | Manifest exists but `doctor` reports `total_tools=0`.                                                                                            |
| `SHIP-DIAG-DYNAMIC-TOOLSETS-ONLY`   | warn     | `total_tools < 3` AND any of `dynamic_toolset_count` / `dynamic_tool_surface_count` ≥ 1 across ADK / LangChain / CrewAI surfaces.                 |
| `SHIP-DIAG-MISSING-SOURCE-FILE`     | block    | A required `tool_sources[].path` does not resolve under the manifest directory. (`doctor` no longer raises `InputParseError(3)` for this — see below.) |
| `SHIP-DIAG-CHANGE-ME-PLACEHOLDERS`  | warn     | Manifest text still contains `CHANGE_ME` markers.                                                                                                |
| `SHIP-DIAG-NO-PRODUCTION-PERMISSIONS` | warn   | `environment.target: production` AND no permissions / scopes / policies declared.                                                                 |

## Negative-control precedence

When more than one negative-control predicate matches, only the most
specific diagnostic fires:

```
SHIP-DIAG-PURE-PROMPT-EXPERIMENT
    > SHIP-DIAG-NON-AGENT-LIBRARY
        > SHIP-DIAG-NO-AGENT-SURFACE
```

A workspace with both a `prompts/` directory and a `pyproject.toml`
emits only `SHIP-DIAG-PURE-PROMPT-EXPERIMENT`, not the broader
`SHIP-DIAG-NON-AGENT-LIBRARY`.

## Doctor behavior change

Before this feature, `agents-shipgate doctor` raised `InputParseError(3)`
when a required `tool_sources[].path` failed to load. That gave a coding
agent no routable next step.

Now `doctor --json` exits **0** with:

- `unresolved_sources: [{id, declared_path, line}]` listing each unresolved entry
- a `SHIP-DIAG-MISSING-SOURCE-FILE` diagnostic whose rank-1 action is an
  `edit` pointing at `shipgate.yaml:<line>`

`scan` is unchanged — it still raises `InputParseError(3)` on missing
required sources, because once an agent moves past doctor, those are real
scan failures.

## Where diagnostics surface

Diagnostics are emitted in three places:

1. `detect --json` — workspace classification + recovery hints.
2. Each `doctor --json` payload — per-manifest diagnostics.
3. `AGENTS_SHIPGATE_AGENT_MODE=1` stderr error JSON — alongside the
   existing `error` and `next_action` fields, errors now also carry
   `next_actions: list[NextAction]`.

Diagnostics are *not* added to `report.json` (the v0.9 schema is
unchanged). Per-finding remediation already has its own v0.7 fields
(`autofix_safe`, `requires_human_review`, `suggested_patch_kind`,
`docs_url`); diagnostics are pre-scan recovery hints, not post-scan
remediation.
