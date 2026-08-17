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

This repository's launcher, `./shipgate`, uses the first row rather than adding
a fifth: it sets `AGENTS_SHIPGATE_CLI` to its own absolute path when the
variable is unset, so every emitted command names it and runs as printed. It has
to, and not only for tidiness — its `argv[0]` is named `shipgate`, so without
the announcement the policy would read it as a console script and emit
`agents-shipgate …`, a command a clean checkout has no way to run. An operator
who set the variable themselves still wins. Because the variable now has a
writer, `agents_shipgate.invocation.render_cli_override` is the inverse of how
it is parsed — host rules, not `join_argv`'s POSIX ones, so a checkout path
containing a space survives the round trip on Windows too.

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

**Structured argv, where it is carried.** `executable` and `args` need no
shell at all: `subprocess.run([*executable, *args])` runs exactly what the
string describes. They are *computed* from `command` and ignored on input, and
they are recomputed on every read, so no mutation can leave them describing a
command the action no longer holds.

They are carried on **`next_actions[]`** — agent-mode error lines and the
`detect` / `doctor` / `init` / `scan` / `verify` / `check` / `preflight` JSON
payloads. They are **not** carried on the operational control contracts:
`control.next_action`, `allowed_next_commands`, verifier repairs, and
`fix_task.verification_command` publish the string only. Extending the argv
pair into those contracts changes the `AgentControl` union, which raises
`minimum_control_contract_version` and forces a down-projection for the frozen
`shipgate.codex_boundary_result/v2` schema; it is tracked in
[#369](https://github.com/ThreeMoonsLab/agents-shipgate/issues/369) rather
than folded in here.

**Recovering argv from any command string.** Because every command Shipgate
emits is rendered with POSIX quoting on every platform — one renderer, no
exceptions — `shlex.split(command)` reproduces the exact argv on every surface,
including the control contracts, and on Windows:

```python
subprocess.run(shlex.split(control["next_action"]["command"]))
```

This is a guarantee, not an observation: it is pinned by
`tests/test_invocation_policy.py::test_every_control_surface_command_recovers_exact_argv`.
Use it wherever `executable`/`args` are absent, in preference to `shell=True`.

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

## Which Shipgate answered: the `environment` block

Every `doctor --json` payload carries an `environment` block, and so does every
`doctor` agent-mode error line — including the discovery failure, where `--json`
prints no payload at all and the error line is the only thing a caller receives.

It exists because the three versions in play are never stated together: the
distribution installed in the running interpreter, the package that actually got
imported, and the source tree the caller believes they are working on. When
those diverge the symptom is never "your versions diverge" — it is a subcommand
that looks missing, a fix that appears not to take, or a console script that
dies before a line of Shipgate runs.

| Field | Contents |
| --- | --- |
| `interpreter` | `executable`, `version`, `minimum_supported`, `supported`. |
| `launcher` | `source` (the invocation policy's own `console_script` / `module` / `override` / `fallback`), `executable[]`, and `console_scripts[]`: each `agents-shipgate` / `shipgate` wrapper found on `PATH`, with `path`, the `interpreter` its shebang names, `interpreter_exists`, and `runs_this_interpreter`. Only the first hit per name — the rest are shadowed and not a choice the caller has. |
| `import_source` | `package_path`, `root`, and `kind`: `source_checkout` (an editable install or a launcher run — either way the tree being edited is what ran), `installed` (a build no edit reaches), or `unknown`. |
| `installed_version`, `imported_version` | What `pip` records for this interpreter, and what was imported. A `null` installed version is the normal state of a clean checkout. |
| `source_tree` | The enclosing Agents Shipgate checkout, if one was found: `root`, `version` (from its `pyproject.toml`), `launcher`, `contains_import`. All `null` for an ordinary installed run outside a checkout. |
| `mismatches[]` | `code`, `severity`, `detail`, and a runnable `command` where one exists. Empty is normal. |

| `mismatches[].code` | Severity | Means |
| --- | --- | --- |
| `interpreter_unsupported` | error | The running Python is older than `requires-python`. |
| `import_outside_source_tree` | error | You are standing in a checkout that is not what ran. This is the stale-shadow case, and the emitted `command` is the same command through that checkout's own launcher. |
| `source_tree_version_differs` | warning | The checkout's `pyproject.toml` and its package disagree; one was edited without the other. |
| `installed_version_differs` | warning | Two *installed* copies shadow each other, so which one answers depends on path order. Not raised when a source checkout out-votes an install: that is intended, and an editable install's metadata lags every version bump by design. |
| `console_script_interpreter_missing` | error | A wrapper on `PATH` names an interpreter that no longer exists, so it fails before Shipgate starts. |
| `console_script_runs_other_interpreter` | warning | A bare `agents-shipgate` would execute a different installation than this command did. |

Nothing here runs an interpreter or executes a console script to find out.
That is the trust-model invariant (`tests/test_adapter_static_only.py` bans
`subprocess` and the `os.exec*` family under `src/`), and it is also the only
thing that could work: the environments worth diagnosing are the ones where
running something is what fails. A stale wrapper is identified from its shebang.

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

## Projection onto the control envelope

Contract v24 adds a `control` field to `detect --json`, `init --json`, and each
`doctor --json` payload, carrying the same `shipgate.agent_control/v1` envelope
that `verify`, `check`, and `agent control` emit
([`docs/agent-control-schema.v1.json`](agent-control-schema.v1.json)). It is a
projection of these diagnostics, not a second analysis: the rank-1 control
action is a diagnostic's own rank-1 `NextAction`, retyped.

The mapping, in `agents_shipgate.cli.setup_control` — one module, so the four
commands cannot drift apart:

| Selected route | `control_state` | `next_actor` |
| --- | --- | --- |
| A `block`-severity diagnostic with a `command` or `edit` action | `agent_action_required` | `coding_agent` |
| An unresolved **human-owned** manifest placeholder | `human_review_required` | `human` |
| A diagnostic whose rank-1 action is `review` or `stop`, or whose id is human-owned | `human_review_required` | `human` |
| No diagnostic and no outstanding human obligation | `agent_action_required` on the next stage | `coding_agent` |

Precedence is that table's order. A blocking diagnostic outranks the placeholder
obligation deliberately: a manifest the loader rejects has to be repaired before
anyone can usefully review what it declares, and the obligation is not lost —
it is derived from the manifest on every run, never remembered.

`NextAction.kind` maps to the control action type: `command` becomes a
`CodingAgentCommandAction` whose `kind` comes from
`setup_control.SETUP_ACTION_KINDS` (one entry per diagnostic id, pinned by
test), `edit` becomes the contract-v24 `SetupEditAction` — declared on the
envelope, not in the shared control union, and rejected on any non-setup
operation — and
`review`/`stop` become human routes.

**Human-owned placeholders.** A placeholder is human-owned when any segment of
its reported field path names a declaration a person makes. Two sets, both in
`agents_shipgate.cli.discovery.placeholders`:

- whole manifest blocks — `agent_bindings`, `tool_identity`, `action_surface`,
  `permissions`, `policies`, `checks`, `baseline`, `human_ack`,
  `risk_overrides`, `organization` — each mapped to the `do_not_auto_assert`
  entry it carries, in `HUMAN_OWNED_MANIFEST_BLOCKS`;
- leaf fields wherever they appear — `declared_purpose`, `prohibited_actions`,
  `owner`, `reason`, `expires`, `approval`, `approval_required`, `authority`,
  `effect`, `safeguards`, `confirmation`, `idempotency`.

These are reviewed closed-world claims about deployed wiring, or the record of a
person having decided something. A value a coding agent supplied is not a guess
to be corrected later — it is a declaration nobody made, and Shipgate treats it
as evidence. Every other placeholder (a tool-source path, a project name) is
ordinary repository reading and stays coding-agent work.

Matching is on every segment, not the leaf, because `collect_placeholders` names
a list item by its own text: a `CHANGE_ME` under `declared_purpose: [...]` is
reported as `agent.declared_purpose.CHANGE_ME`.

**One route reaches every field.** `next_action`, `next_actions[0]`, and
`control.next_action` are all projections of the one selected route, so a
consumer reading the legacy string and a consumer reading `control` cannot be
sent to different work. A human route publishes exactly one action and no
command; the ranked alternatives appear only behind a coding-agent route.

The `control` field never carries authority: setup reads no diff, so all six
`permissions` are `false`, `review_publishable` is unreachable, no artifact or
`current_control_id` is bound, and `control_state: "complete"` is unreachable
for these operations. Every one of those is enforced in the published JSON
Schema as well as in Pydantic.
