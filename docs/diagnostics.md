# Ranked Next-Action Diagnostics

`agents-shipgate detect`, `doctor`, and structured errors emit a
`diagnostics[]` and `next_actions[]` block alongside the existing
`next_action: str` field. A coding-agent caller can read the rank-1
action and route to the next command without consulting human-facing
docs.

Diagnostics describe conditions; the catalog itself does not pick exit
codes. A diagnostic with `severity: "block"` flags a blocking *condition*
and the caller (a CLI command) decides what to do. The current rules:

- `agents-shipgate scan` always exits non-zero (`ConfigError(2)`,
  `InputParseError(3)`, or the scan-policy `20`) on any condition that
  used to fail it. Diagnostics are extra context, not a replacement.
- `agents-shipgate doctor --json` is the agent contract: it exits **0**
  on `SHIP-DIAG-MISSING-SOURCE-FILE` so the agent can read
  `unresolved_sources[]` and route to a fix.
- `agents-shipgate doctor` (no `--json`) is the human contract: it
  exits **3** when any payload has `unresolved_sources`, so an
  interactive user still sees a loud failure. The diagnostic block
  prints in the human output regardless.

This is the only place a diagnostic affects an exit code, and the
divergence is bounded to `MISSING-SOURCE-FILE` on `doctor`. Other
diagnostics (`ZERO-TOOLS`, `CHANGE-ME-PLACEHOLDERS`, etc.) print but
do not change the exit code.

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

| Field      | Type                                      | Notes                                                         |
| ---------- | ----------------------------------------- | ------------------------------------------------------------- |
| kind       | `command \| edit \| review \| stop`       | Action category.                                              |
| command    | `string \| null`                          | Required when `kind="command"`. Always `null` when `"stop"`. |
| path       | `string \| null`                          | Required when `kind="edit"`. May be `shipgate.yaml:<line>`. |
| why        | `string`                                  | One-sentence rationale.                                       |
| expects    | `string \| null`                          | Optional: what the next run should output if the action worked. |
| executable | `string[]` (absent when N/A)              | Entry-point argv tokens (contract v23+). Present only when `kind="command"` and the command has a faithful argv form; **omitted**, never `null`. |
| args       | `string[]` (absent when N/A)              | The remaining argv tokens. Same presence rule as `executable`. |

### Invocation policy

Emitted commands name the entry point that started *this* process, so the
recovery loop stays runnable in the environment that produced it:

| How Shipgate was started              | What emitted commands say                       |
| ------------------------------------- | ----------------------------------------------- |
| `AGENTS_SHIPGATE_CLI` set             | That value, split with `shlex` — highest precedence. The same override the Claude Code hook installer honours. An explicit entry point is always spliced in, including `AGENTS_SHIPGATE_CLI=/private/venv/bin/agents-shipgate`: the program *name* matches a console script, but the operator named that wrapper, not whichever one `PATH` resolves. |
| `agents-shipgate …` / `shipgate …`    | The same console script the command was written with — unchanged. |
| `python -m agents_shipgate …`         | `<sys.executable> -m agents_shipgate …`. The interpreter is spelled by path, not as a bare `python`, because a bare name resolves through `PATH` and can land on a different interpreter. |
| Anything else                         | The canonical `agents-shipgate`. An unrecognised argv is not evidence of a better spelling. |

`command` never contains `__main__.py`.

**`command` is a POSIX-shell rendering, not a host-shell promise.** It is
quoted with POSIX rules on every platform, deliberately: one renderer and one
parser must agree, or a value changes in transit. (`subprocess.list2cmdline`
looks like the Windows answer but is MS C-runtime *argv* quoting — it leaves
`feature&whoami` unquoted for `cmd.exe`, and pairing it with a POSIX parse
turned `C:\repo` into `C:repo`: a runnable command against the wrong
workspace.) Uniform POSIX quoting round-trips Windows paths exactly, since a
single-quoted `'C:\repo'` keeps its backslashes. **Do not paste `command`
into `cmd.exe` or PowerShell** — single quotes are not quoting there. Use the
structured pair.

**Structured argv is the authoritative runnable form**, on every platform.
`executable` and `args` need no shell at all: `subprocess.run([*executable,
*args])` runs exactly what the string describes. They are *computed* from
`command`, never supplied — passing them is a validation error, and they are
recomputed on every read, so no mutation can leave them describing a command
the action no longer holds. The entry point comes from the resolved invocation
rather than from parsing the string back.

Both keys are **omitted**, not `null`, whenever the command has no faithful
argv form:

- a leading `NAME=VALUE` assignment, which is shell syntax rather than an argv
  token;
- any unquoted shell metacharacter — an operator (`&&`, `|`, `;`), redirection
  (`>`, and therefore a `<report.json>` placeholder), substitution (`$VAR`,
  backticks), or a glob. `shlex.split` returns `&&` as an ordinary token, so
  publishing its output would advertise a call that does something other than
  what the string says. Only **single** quotes make these inert: double quotes
  suppress word splitting but not substitution, so `"$HOME"` is withheld too;
- any action whose `kind` is not `command`.

The rendered string stays authoritative in all of those cases.

### What the policy does *not* touch

| Surface | Why it stays canonical |
| ------- | ---------------------- |
| `report.json`, `report.md`, `packet.*` | [`docs/architecture.md`](architecture.md) makes **same inputs → same report** a non-negotiable invariant. Process entry is not an input: the same scan through a wrapper and through `python -m` must produce the same bytes, or one semantic `run_id` acquires several artifact bodies and the packet hash stops meaning anything. Live routes carry the runnable spelling instead. |
| `primary_commands`, `.well-known/agents-shipgate.json` | Published vocabulary describing the installed CLI, not a route for one run — and its generator must never bake an interpreter path into a committed file. |
| Prose (`why`, `recommendation`, report Markdown) | Documentation of the canonical CLI rather than a field a caller executes. |

Everything else that *is* a command to run — `command` on next actions,
agent-mode error routes, preflight `related_command`, and the control and
repair commands the verifier and boundary publish — follows the policy. It is
applied at the emission boundary, not at each call site, so a route built as a
plain dict rather than as a `NextAction` cannot opt out.

The legacy `next_action: str` field on `detect`, `doctor`, and
agent-mode error JSON is the rank-1 action projected to a single string:

| Rank-1 kind | Legacy projection                  |
| ----------- | ---------------------------------- |
| command     | the `command` value verbatim       |
| edit        | `Edit <path>`, or a caller-written sentence naming the same file |
| review      | `Review: <why>`, or a caller-written sentence |
| stop        | `Stop: <why>`, or a caller-written sentence |

This keeps `next_action` string-typed even for negative-control
diagnostics where no command should run.

A rank-1 `command` is authoritative: the legacy field is that command
verbatim, spelled for the same invocation, so the two can never route a caller
to different programs. For the other kinds there is no program to disagree
about, and a command emitter may supply a more specific sentence than the
generic projection (`Remove <path> and re-run scan` rather than
`Edit <path>`); those are still retargeted, so prose cannot name a stale entry
point either.

## Catalog

| ID                                  | Severity | Fires when                                                                                                                                       |
| ----------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SHIP-DIAG-MISSING-MANIFEST`        | block    | The manifest file does not exist on disk. Rank-1 action: `agents-shipgate verify --workspace <dir> --preview --json`.                             |
| `SHIP-DIAG-INVALID-MANIFEST`        | block    | The manifest file exists but the loader rejected it (invalid YAML, schema validation failure, unsupported version). Rank-1 action: `edit <path>`. |
| `SHIP-DIAG-NO-AGENT-SURFACE`        | info     | `is_agent_project=false` AND `suggested_sources=[]` AND `codex_plugin_candidates=[]` AND no manifest. Catch-all negative control.               |
| `SHIP-DIAG-NON-AGENT-LIBRARY`       | info     | Python project (≥1 .py file + pyproject/requirements) with no agent framework, prompts, or tool surface.                                         |
| `SHIP-DIAG-PURE-PROMPT-EXPERIMENT`  | info     | Only `prompts/` is present; no Python framework, no tool sources.                                                                                |
| `SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY` | info   | `is_agent_project=false` BUT `suggested_sources` has MCP/OpenAPI entries. Artifact-only repos are valid Shipgate targets.                        |
| `SHIP-DIAG-CODEX-PLUGIN-PACKAGE-DETECTED` | info | `is_agent_project=false` BUT Codex plugin package or marketplace artifacts are present. Codex plugin repos are valid Shipgate targets.            |
| `SHIP-DIAG-ZERO-TOOLS`              | block    | Manifest exists but `doctor` reports `total_tools=0`.                                                                                            |
| `SHIP-DIAG-DYNAMIC-TOOLSETS-ONLY`   | warn     | `total_tools < 3` AND any of `dynamic_toolset_count` / `dynamic_tool_surface_count` ≥ 1 across ADK / LangChain / CrewAI surfaces.                 |
| `SHIP-DIAG-MISSING-SOURCE-FILE`     | block    | A required `tool_sources[].path` does not resolve under the manifest directory. (`doctor` no longer raises `InputParseError(3)` for this — see below.) |
| `SHIP-DIAG-CHANGE-ME-PLACEHOLDERS`  | warn     | Manifest text still contains `CHANGE_ME` markers.                                                                                                |
| `SHIP-DIAG-NO-PRODUCTION-PERMISSIONS` | warn   | `environment.target: production` AND no permissions / scopes / policies declared.                                                                 |
| `SHIP-DIAG-UNKNOWN-ADAPTER-SOURCE-TYPE` | block | Manifest references a `tool_sources[].type` that no registered adapter handles. Rank-1 action depends on plugin state: enable plugin discovery (`AGENTS_SHIPGATE_ENABLE_PLUGINS=1`) and install the third-party adapter package, or fix a typo. v0.20+. |

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

- `unresolved_sources: [{id, declared_path, line, reason}]` listing each
  unresolved entry. `reason` is `"missing"` (file does not exist) or
  `"outside_manifest_dir"` (file exists but resolves outside the
  manifest directory; loaders refuse it on containment grounds).
- a `SHIP-DIAG-MISSING-SOURCE-FILE` diagnostic whose rank-1 action is an
  `edit` pointing at `<manifest_path>:<line>` (the full path the user
  invoked `doctor` with, so workspace and nested-manifest runs stay
  unambiguous).

The non-JSON form (`agents-shipgate doctor` without `--json`) prints
the same `unresolved_sources` and diagnostic block in human-readable
form and **exits 3** to preserve the pre-feature loud failure for
interactive users.

`scan` is unchanged — it still raises `InputParseError(3)` on missing
or escaped required sources regardless of `--json`, because once an
agent moves past doctor, those are real scan failures.

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
