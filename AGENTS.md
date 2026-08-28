# Agents Shipgate · Agent Instructions

Authoritative instructions for AI coding agents (Claude Code, Codex, Cursor, Aider, Cline, Windsurf, Devin, or any other harness — see [`docs/agents/any-coding-agent.md`](docs/agents/any-coding-agent.md)) working **with** this repository or a project that uses Agents Shipgate.

> If you are a human, the README and the [wiki](https://github.com/ThreeMoonsLab/agents-shipgate/wiki) are the right places to start. This file is optimized for agent ingest: short, copy-pasteable, machine-friendly.

---

## What this project is

The deterministic merge gate for AI-generated agent capability changes. Reads `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Anthropic Messages API tool/prompt artifacts, Google ADK Python/config files, LangChain/LangGraph Python files, CrewAI Python files, OpenAI API artifacts, Codex repo config, Codex plugin packages and marketplaces, n8n workflow JSON/stubs, Conductor OSS workflow JSON) and produces deterministic findings. Local-first and static by default — no agent execution, tool calls, LLM calls, or network access.

- **Inputs:** MCP · OpenAPI · OpenAI Agents SDK · Anthropic Messages API · Google ADK · LangChain/LangGraph · CrewAI · OpenAI API · Codex config · Codex plugin · n8n · Conductor OSS workflow JSON
- **Outputs:** Markdown · JSON · SARIF
- **Trust:** Static-by-default. No agent execution, tool calls, LLM calls, or network access.
- **Marketing site:** [threemoonslab.com](https://threemoonslab.com/) — human-readable companion pages. **If you are an agent working inside this repo, use the in-tree [`.well-known/agents-shipgate.json`](.well-known/agents-shipgate.json) (current `main` contract, may be ahead of the site's released copy) for schema-version and gating-signal decisions.**

---

## Naming (canonical)

Use exactly one form depending on context. Mixing them in user-visible copy is an adoption cost.

| Form | When to use |
|---|---|
| **Agents Shipgate** | Display name. Prose, headings, marketing copy, social cards, slide titles, blog posts. |
| **`agents-shipgate`** | Package, CLI binary, repo, GitHub Action, PyPI distribution name, env-var prefix (`AGENTS_SHIPGATE_*`), import path (`agents_shipgate`). Always lowercase, kebab-case. |
| **`shipgate`** | Short alias for the CLI binary only. Acceptable in shell snippets where brevity helps; never as the project name. |

Do **not** use any of: `Agent Shipgate` (singular), `Agent Shipcheck`, `agents shipgate` (display lowercase), `Agents-Shipgate` (display kebab). When in doubt: prose → `Agents Shipgate`; code → `agents-shipgate`.

The canonical tagline is:

> The deterministic merge gate for AI-generated agent capability changes.

This single sentence is the source of truth for the GitHub repo description, [README.md](README.md), the [wiki Home page](https://github.com/ThreeMoonsLab/agents-shipgate/wiki/Home), and the [marketing site](https://threemoonslab.com/) `<meta name="description">`. Keep them in sync; the website's `.well-known` discovery file is pinned to the latest released tag and refreshes at each release.

Use **Tool-Use Readiness** in Title Case when naming the product/category or
the **Tool-Use Readiness Report** artifact. Use **tool-use readiness** in
sentence case when describing the general concept.

---

## Install (canonical)

```bash
pipx install agents-shipgate
```

Alternatives if `pipx` is unavailable:

```bash
python -m pip install agents-shipgate                   # global pip
uv tool install agents-shipgate                          # via uv
python -m agents_shipgate --help                         # run from a pip install without PATH
```

The CLI binary is `agents-shipgate`. A short alias `shipgate` is also installed.

---

## Run (canonical)

Handling a capability change right now? Start with **Local control** below —
run First-time setup only when the repo has no `shipgate.yaml` yet.

**First-time setup** — in a repo that contains an agent and its tools:

```bash
agents-shipgate init --workspace . --write
agents-shipgate scan -c shipgate.yaml
```

Reports land at `agents-shipgate-reports/report.{md,json}`.

**Local control for coding agents** — before reporting an agent-capability
change complete, run the local control loop and parse stdout JSON:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
shipgate check --agent claude-code --workspace . --format agent-boundary-json
shipgate check --agent cursor --workspace . --format agent-boundary-json
```

`--agent` identifies the caller; it never selects host coverage. Every
recognized changed Codex, Claude Code, Cursor, VS Code MCP, shared trust-root,
and GitHub workflow surface is evaluated on every run.

Read the single stdout object as `shipgate.agent_boundary_result/v1`. Switch on
`control.state`; inspect `input_coverage`, `host_coverage`, `affected_hosts`,
`policies`, `violations`, and `issues`; then follow `control.next_action`,
`control.allowed_next_commands`, and `control.human_review`. Treat `decision`
as diagnostic context, never as the operational control signal, and never
infer control from Markdown, PR comments, or prose. If
`control.state=complete`, summarize the result and finish. If
`control.state=agent_action_required`, perform only the exact coding-agent
action authorized by `control.next_action`, then rerun the command. If
`control.state=review_publishable`, a human must approve the merge — surface
the JSON result, and note that you may still commit, push, and update the pull
request so that review can happen. If `control.state=human_review_required`,
stop and surface the JSON result to a human. `control.permissions` states the
authority exactly: updating a PR is never merging it, and
`permissions.merge`/`permissions.report_complete` are false on every state but
`complete`. Conversation-level acknowledgement never changes control state;
only a new verifier artifact can clear it.

**Before editing a protected release surface** — ask the proactive static
planner first:

```bash
agents-shipgate preflight --workspace . --plan - --json
agents-shipgate preflight --changed-files changed.txt --json
agents-shipgate preflight --capability-request request.json --json
```

Switch on `control.state`. If it is `human_review_required`, stop and route the
change to a human. If it is `agent_action_required`, perform only the exact
coding-agent route in `control.next_action`. The plan form accepts `changed_files[]`,
`diff_text`, `capability_requests[]`, `host_permission_requests[]`, and
`context.{agent,task}`; prefer it whenever the agent can describe the planned
change as one JSON object. Protected surfaces include
`shipgate.yaml`, `.github/workflows/agents-shipgate.yml`,
`AGENTS.md`/`CLAUDE.md`/Cursor rules, policy packs, baselines, waivers,
suppressions, Codex hooks/config, Codex plugin manifests, `.mcp.json`,
`.app.json`, and `SKILL.md`. Preflight is a routing/projection surface only;
`release_decision.decision` remains the release gate.

**PR / reviewer evidence** — for committed PR/CI refs, run the deterministic
verifier on the diff. Make the base ref available first because `verify` never
fetches:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

For local uncommitted verifier work, omit `--base`/`--head` so the working tree
is scanned. Read `agents-shipgate-reports/current-control.json`
first — it names which run is current — then validate the
`verification-receipt.json` it binds, then read
`agents-shipgate-reports/agent-handoff.json` and lead
with `control.state`, then `gate.merge_verdict`
(`mergeable | human_review_required | insufficient_evidence | blocked |
unknown`), `gate.can_merge_without_human`, `next_action`,
`fix_task`, and `capability_review.top_changes[]`. Fall back to
`agents-shipgate-reports/verifier.json` only when the installed CLI contract is
older than v6. Then read
`agents-shipgate-reports/report.json.release_decision.decision`
(`blocked | review_required | insufficient_evidence | passed`), which remains
the release gate. Do not report completion unless `control.state` is
`complete`. A human-review route keeps merge and completion denied until a new
verifier artifact changes the control state; conversation-level acceptance is
not a gate
override.

Read the pointer with:

```bash
agents-shipgate agent control --workspace . --reports-dir agents-shipgate-reports
```

That returns `shipgate.agent_control/v1`, the compact control envelope: the
control state, the `permissions` vector, the next actor, the exact next action,
and the path and sha256 of every artifact `current-control.json` binds, in one
object. It is the
whole routing answer — an agent that switches on `permissions` and
`next_action` from it does not need the artifact walk above. Read
`execution` and `exit_code` as what they are: whether the tool ran, and whether
the CI gate failed. Neither is merge authority; `permissions.merge` is. Pass
`--format pointer` for the raw `current-control.json`, and use
`agents-shipgate verify --format control` to get the same envelope directly
from a run you just performed.

A zero exit means the printed answer was validated against every artifact it
binds, still describes the repository as it stands right now, and did not move
while it was read. Byte consistency is not generation consistency: one commit
is enough to make an intact artifact set describe a workspace that has moved,
so the read compares the pointer's HEAD, tree, and worktree overlay against the
live repository and refuses on drift. A non-zero exit means no control identity
is current here — you hold no authority, and a remembered result does
not substitute for one. Re-read it after any human or external-tool action,
after commit, rebase, checkout, pull, or any worktree change, after any
agents-shipgate command returns, before enforcing a cached `must_stop`, before
commit/push/PR update, before merge or release, and before declaring the task
complete. If `current_control_id` changed, discard every cached control state
and restart from the new identity. This runs in both directions: a cached stop
must not survive a newer complete run, and a cached completion must not survive
a changed workspace.

Do not bypass the verifier by suppressing findings, lowering severity,
expanding baselines or waivers, removing Shipgate CI, or weakening agent
instructions. Verify-mode `SHIP-VERIFY-*` checks make those trust-root edits
release-visible and route them to human review.
Never invent or auto-fill an action effect or action authority declaration —
including the shared `tool_sources[].authority` block, which is the same
authority claim made once for a whole source rather than once per action.
Never invent or auto-fill `agent_bindings` root, tool, or handoff declarations,
or the `tool_sources[].binding` block that makes the same closed-world claim
once for a whole published tool surface; they are reviewed claims about
deployed wiring.
Contract v14 publishes these boundaries as `action_effect`, `action_authority`,
and `agent_binding` in `do_not_auto_assert[]`; route binding and semantic next
actions to a human and rerun verification after the reviewed declaration is supplied.

There is exactly one exception, and it is narrow by construction (contract v26,
report v0.41). When `control.next_action.kind` is `confirm_declarations`, run
the command it names and nothing else. That command writes only the rows the
report itself tags `next_action.authorable_by: "coding_agent"` — rows whose
declaration the scan filled in completely from its own evidence, which today
means an effect it read directly. Everything else stays exactly as above: a row
tagged `"human"`, any authority or `agent_bindings` block, an `override`, and a
`declaration_drift` row asking a person to re-confirm an answer are never yours
to write, and you may never fill a blank the scan left or weaken a declaration
the manifest already carries. Do not reconstruct the edit by hand if the route
is absent — its absence is the answer.

`action_surface.actions[].basis` is the one field in that block that is not a
human assertion: it is a digest of the evidence the scan read for the action,
published in the row's own `declaration_template`, and it re-opens the question
as `declaration_drift` when that evidence moves. Copy it verbatim from the
report when carrying a reviewed declaration forward; it can never make an action
pass-eligible, and it does not make an effect you invented any more declared.
A `declaration_drift` row still routes to a human — it asks a person to re-read
the evidence, not a machine to restamp the digest.

To reproduce the verify-native blocked refund PR demo without writing YAML:

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

To verify your install on the older static scan fixture:

```bash
agents-shipgate fixture run support_refund_agent
```

---

## First-adoption helper flow (v0.6+)

For coding agents adopting Shipgate end-to-end in one turn:

```bash
agents-shipgate detect --json
agents-shipgate init --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches --from agents-shipgate-reports/report.json \
    --confidence high --apply
```

Or chain all four in one call:

```bash
agents-shipgate bootstrap --json
```

`bootstrap` runs `detect → init --write --ci → scan --suggest-patches → apply-patches --confidence high` against the current workspace, stopping on the first non-recoverable error and emitting a structured per-step summary. Use it for first-time adoption; for ongoing CI keep using the GitHub Action. Flags: `--workspace`, `--confidence`, `--no-ci`, `--no-apply`, `--json`.

- **`detect`** — read-only; classifies the workspace. `is_agent_project: false`
  is **not** on its own a reason to stop. It is false for every artifact-only
  and Codex-plugin-only workspace, which are adoptable, and it is unsafe to
  read at all when the parse was cut short. Stop only when the whole published
  stop condition holds: `is_agent_project: false` **and** `suggested_sources`
  empty **and** `codex_plugin_candidates` empty **and**
  `python_parse_truncated: false`. `python_parse_truncated: true` means the
  Python parse stopped at `max_python_files`, so the negative describes the
  files that were read rather than the repository — re-run with
  `--max-python-files <workspace_signals.python_file_total>`, which is a bound
  that cannot hit the cap again. `init --write` takes the same flag and refuses
  without it while the parse is truncated, rather than declaring an agent name
  and tool surface read from part of the tree. `agent_scope` says whether one manifest can
  describe this workspace at all: `"ambiguous"` means agents live in several self-contained
  projects (`agent_project_candidates[]` lists them, and the manifest belongs
  in one of them rather than at the workspace root); `"unknown"` means
  discovery was capped before it could tell, so raise `--max-python-files` or
  name the project directly. `agent_scope_truncated: true` says the candidate
  list itself is a lower bound — the parse stopped at its cap, so any project
  in the part of the tree that was not read is missing from it. Never read
  absence from a truncated list as an answer; raise the cap first. On
  `agent_scope: "ambiguous"` with a complete parse, `next_actions[]` ranks the
  decision first (`kind: "review"`, no command) and then carries one exact
  `init --workspace <candidate> --write --json` per candidate — the list
  `init --write` publishes when it refuses the same workspace, minus the setup
  flags, which `detect` was not asked for and does not invent: if you want
  `--ci` or `--agent-instructions`, add them, or take the command from `init`'s
  own refusal, which repeats what you asked for. A candidate that already
  carries a manifest gets `doctor --config <that manifest> --json` instead —
  `init --write` there refuses a file it will not overwrite — or, when you asked
  for setup it still owes, an `init` carrying those flags. Every candidate gets
  an entry, the workspace root as a `review` rather than a command; the ten-item
  cap is on the human summary only. Choosing the project is the only work left. A truncated parse outranks that, in `detect` and in `init`
  alike: rank 1 is then the higher-cap rerun and no candidate commands are
  offered, because the list they would be built from is a lower bound.
- **`init`** — auto-detects by default. `--ci` writes
  `.github/workflows/agents-shipgate.yml`; orthogonal to `--write`. Use
  `--minimal` for the pre-v0.6 CHANGE_ME-heavy template.
  `--agent-instructions=default` renders the recommended downstream kit
  (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/agents-shipgate.mdc`,
  `.claude/commands/shipgate.md`, and `.shipgate/agent-contract.json`).
  Use `--ci` to write advisory CI. `--agent-instructions=all` means every
  supported target. A comma-separated subset can name any target:
  `agents-md,claude-md,cursor,claude-command,local-contract,codex-skill,claude-code-skill,pr-template`.
  Combined with `--write`, managed-block hosts are idempotently updated and
  full-file / skill-bundle targets use safe-update checks. The `codex-skill` and
  `claude-code-skill` targets remain explicit opt-ins and write multi-file skill
  bundles under `.agents/skills/agents-shipgate/` and
  `.claude/skills/agents-shipgate/` respectively. Strict CI and baselines remain
  opt-in human decisions; generated CI stays advisory by default.
  `--write` **refuses** a workspace whose manifest scope is unresolved —
  `manifest_status: "refused_unresolved_scope"`, exit `2`, nothing written at
  all (no manifest, no workflow, no snippets, no `.gitignore` block) — rather
  than adopting the first `Agent(name=…)` literal it parsed for a manifest that
  would cover unrelated agents. That covers both `agent_scope` values that are
  not `"single"`. Re-run with `--workspace` pointed at one of
  `auto_detected.agent_project_candidates[].path` (the emitted
  `next_actions[]` commands carry the setup flags you passed);
  `--allow-unresolved-scope` overrides when one agent surface genuinely spans
  the workspace, and `--minimal` (which adopts no detected name or tool
  surface) is never scope-gated. With `--ci`, the workflow is written at the
  repository root with a repo-relative `config:` — GitHub loads workflows from
  nowhere else — so a scoped adoption still gets a gate that runs.
- **`scan --suggest-patches`** — attaches Patch objects to every active
  finding. `Finding.patches` is absent without the flag.
- **`apply-patches`** — file-grouped, dry-run by default. Containment-
  checked against `report.manifest_dir`. v0.6 default `--confidence high`
  applies only manifest stale-removals; scope-coverage appends require
  `--confidence medium`. Trace approval/confirmation findings are
  always `ManualPatch` — never auto-applied (flipping the trace patches
  the evidence, not the agent's runtime gate).

---

## Agent mode

Every command supports JSON output for programmatic consumption:

```bash
agents-shipgate detect --workspace . --json
agents-shipgate preflight --workspace . --plan - --json
agents-shipgate init --workspace . --write --json
agents-shipgate scan -c shipgate.yaml                    # already produces report.json
agents-shipgate apply-patches --from agents-shipgate-reports/report.json --json
agents-shipgate doctor --json
agents-shipgate contract --json
agents-shipgate explain SHIP-POLICY-APPROVAL-MISSING --json
agents-shipgate list-checks --json
agents-shipgate self-check --json
agents-shipgate fixture list --json
```

Errors carry a structured `next_action` (single string, back-compat) and `next_actions` (ranked list) when agent mode is active. Agent mode auto-enables inside a known coding-agent harness (Claude Code exports `CLAUDECODE=1`, Cursor `CURSOR_TRACE_ID`); set `AGENTS_SHIPGATE_AGENT_MODE=1` to force it on elsewhere, or `=0` to force it off:

```bash
$ AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c missing.yaml
Config error: Config file not found: missing.yaml
{"error": "config_error", "message": "...", "next_action": "agents-shipgate detect --workspace . --json", "next_actions": [{"kind": "command", "command": "agents-shipgate detect --workspace . --json", "why": "..."}, {"kind": "command", "command": "agents-shipgate init --workspace . --write", "why": "..."}]}
```

The full set of error kinds emitted in agent mode: `config_error`, `config_already_exists`, `input_parse_error`, `unknown_check_id`, `unknown_fingerprint`, `other_error`, `internal_error`, `malformed_patch`, `environment_error`. `unknown_fingerprint` is emitted by `explain-finding` when the fingerprint doesn't match any entry in the supplied report; the payload includes `suggestion` (a close-match fingerprint, when one exists) and `source_report`. `environment_error` is the one kind emitted before Agents Shipgate is running — the interpreter is unsupported, or it cannot import the package or its dependencies — so it carries the `environment` block described below instead of a `control` envelope.

The machine-readable catalog of error kinds — exit codes, typical causes, additional fields per kind, recovery hints — lives at [`docs/errors.json`](docs/errors.json). Pre-fetch it once and pattern-match the `error` field instead of re-deriving the recovery vocabulary from this prose.

`detect --json` and each `doctor --json` payload also carry `diagnostics: [...]` and `next_actions: [...]` fields. `next_action` (single string) remains the rank-1 action projected to a string; `next_actions` is the ranked list with `kind`, `command|path`, `why`, `expects`, and the structured `executable[]` / `args[]` pair. See [docs/diagnostics.md](docs/diagnostics.md) for the full catalog and schema.

### Which Shipgate answered: `environment`

Every `doctor --json` payload carries an `environment` block, and so does every `doctor` agent-mode error line — including the one where no manifest could be found and no payload is printed at all. Read it before concluding that a fix did not take or that a subcommand does not exist:

| Field | What it answers |
| ----- | --------------- |
| `interpreter` | `executable`, `version`, `minimum_supported`, `supported`. |
| `launcher` | `source` (`console_script` / `module` / `override` / `fallback`, the invocation policy above), `executable[]`, and `console_scripts[]` — each `agents-shipgate` / `shipgate` wrapper found on `PATH` with the interpreter it ultimately runs (the `exec` target when the wrapper is a `#!/bin/sh` trampoline, as `pip` writes for interpreter paths containing spaces), whether that interpreter still exists, and whether it is the running one. `null` when the wrapper names no interpreter — a compiled Windows wrapper, `#!/usr/bin/env python`, or an unrecognised handoff. |
| `import_source` | `package_path`, `root`, and `kind` (`source_checkout` / `installed` / `unknown`) — where the code that just ran came from. |
| `installed_version` / `imported_version` | What `pip` records for this interpreter, and what actually got imported. `null` installed version is normal on a source checkout. |
| `source_tree` | The enclosing Agents Shipgate checkout, if any: `root`, `version`, `launcher`, and `contains_import`. |
| `mismatches[]` | `code`, `severity` (`error` / `warning`), `detail`, and — when one exists — a runnable `command` spelled for this invocation. Empty is the normal state. |

`mismatches[]` codes: `interpreter_unsupported`, `import_outside_source_tree`, `source_tree_version_differs`, `installed_version_differs`, `console_script_interpreter_missing`, `console_script_runs_other_interpreter`. Nothing here executes an interpreter or a console script to find out — a stale wrapper is identified by reading it, because a wrapper that cannot start is exactly the one that cannot report on itself.

### One control vocabulary across the setup commands

`detect --json`, `init --json`, and each `doctor --json` payload carry a
`control` field holding the same `shipgate.agent_control/v1` envelope that
`verify --format control`, `check --format agent-control-json`, and
`agents-shipgate agent control` emit. Switch on `control.control_state` and
`control.permissions` for the whole adoption walk instead of learning a
different result shape per command; `control.next_action` is the one typed
rank-1 step, and `next_actions[]` beside it keeps the ranked alternatives.

When that step is a file edit, `control.next_action` is
`{"kind": "edit", "path": …, "expects": …, "command": null}` and
**`control.next_action.path` is the file to open** — exact, never normalized.
The kind is setup-only: `verify`, `check`, and `scan` never emit it, and both
schema layers reject it on those operations.

`scan` is **not** part of this: `agents-shipgate agent control` after a scan
reports `decision: null` with a `reason` saying the verdict is withheld. A scan
pointer binds no reconfirmable snapshot of the inputs it read, so no artifact in
that directory can show its verdict still describes the workspace. Run `verify`
for one that can.

Read `control.decision_source` before `control.decision`. Setup commands run
before a release decision exists, so they report `setup` and a verdict from
`setup_complete | setup_incomplete | setup_not_applicable`, never a release
verdict; `release_decision` means `report.json`'s
`release_decision.decision` and nothing else. The two cannot be confused: the
published schema requires a setup source to come from `detect`/`init`/`doctor`
and requires those operations to report no other source.

**Setup authorizes nothing.** Every field of `permissions` is `false` on every
setup envelope, no setup envelope binds an artifact or a `current_control_id`,
and `control_state: "complete"` is unreachable for these operations in the
schema. Running `init` successfully is not permission to commit, merge, or
report the task done — only a verifier run can grant that. When a manifest still
holds an unresolved `declared_purpose`, policy, or permission placeholder,
`control_state` is `human_review_required` and the action names the exact file,
line, and field: those are declarations a person makes, and an agent must never
supply them.

`next_action` may be `kind: "edit"` on these commands — a typed coding-agent
step with `path` and `expects` and no command. It appears only on setup output:
`verify`, `check`, and `agent control` cannot return it, and the published schema
rejects it on any other operation. `permissions.edit` is `false` beside it, which
is not a contradiction — a setup route authorizes only its own `next_action`.

`next_action` and `next_actions[0]` are **derived from the same selected route**
as `control.next_action`, so the compact envelope and the ranked list can never
send you to different work. Where the route is human-owned, that list holds
exactly one action and no command: an alternative would be a way around the
obligation. Agent-mode *error* lines still carry `next_action`/`next_actions`
rather than a `control` object.

Every emitted command names the entry point that started the running process, so it is runnable where it was produced: a console-script run emits `agents-shipgate …`, and a `python -m agents_shipgate` run emits `<sys.executable> -m agents_shipgate …`. Set `AGENTS_SHIPGATE_CLI` to name the entry point explicitly; it wins over detection. **On `next_actions[]`, run `[*executable, *args]` (contract v23+) rather than parsing `command`** — it needs no shell and is computed from `command`, so it cannot disagree with it; it is omitted, never `null`, when the command has no faithful argv form. The operational control contracts (`control.next_action`, `allowed_next_commands`, verifier repairs) carry the string only: recover argv there with `shlex.split(command)`, which is exact on every platform because every emitted command is POSIX-rendered. Never use `shell=True`, and do not paste `command` into `cmd.exe` or PowerShell. Durable artifacts (`report.json`, `packet.*`) stay canonical so that same inputs still produce the same report. See [docs/diagnostics.md](docs/diagnostics.md#invocation-policy).

### Doctor behavior change for unresolved tool_sources

When a required `tool_sources[].path` does not resolve under the manifest directory (file missing OR resolves outside the manifest dir):

- `agents-shipgate doctor --json` exits **0** with a `SHIP-DIAG-MISSING-SOURCE-FILE` diagnostic and an `unresolved_sources: [{id, declared_path, line, reason}]` field in the payload, so an agent can route to a fix without parsing the error message. `reason` is `"missing"` or `"outside_manifest_dir"`.
- `agents-shipgate doctor` (no `--json`) prints the same `unresolved_sources` + diagnostic block in human-readable form and **exits 3**, preserving the pre-feature loud failure for interactive users.
- `agents-shipgate scan` is unchanged — it still raises `InputParseError(3)` regardless of `--json`. Once you're past doctor, missing sources are real scan failures.

### Missing vs invalid manifests

`config_error` covers two distinct shapes — agent-mode emits a different rank-1 action for each:

- **Missing**: file does not exist → `SHIP-DIAG-MISSING-MANIFEST`, rank-1 is `agents-shipgate detect --workspace <dir> --json` (then `init --write`).
- **Invalid**: file exists but the loader rejected it (invalid YAML, schema validation, unsupported version) → `SHIP-DIAG-INVALID-MANIFEST`, rank-1 is `edit <path>` with the loader error in `why`. Do **not** re-run `init` — it refuses to overwrite an existing file.

---

## When to use this tool

Use it when the user is:

- Writing or modifying an AI agent that calls tools (MCP servers, OpenAPI APIs, `@function_tool`-decorated Python, Google ADK, LangChain/LangGraph, CrewAI, n8n, Conductor OSS)
- Publishing or modifying a Codex plugin package or marketplace entry that declares skills, apps, MCP servers, or hooks
- Adding a new tool, scope, or policy to an existing agent
- Preparing an agent for production promotion
- Wiring a release gate into PR CI

Do NOT use it for:

- General code linting (ruff, eslint, etc.)
- Runtime monitoring (use observability/tracing)
- Behavior testing (use evals)
- Stopping a tool call in flight (use a gateway or guardrail)

### Should I run Shipgate on this PR?

| Trigger in this PR | Run Shipgate? |
|---|---|
| Adds/changes MCP exports, OpenAPI specs, or `tools/*openai*tools*.json` | Yes |
| Adds/changes Codex repo config, hooks, or permission profiles | Yes |
| Adds/changes coding-agent host config, hooks, permissions, MCP servers, or workflows | Yes |
| Adds/changes Codex plugin manifests, marketplace files, `.app.json`, `.mcp.json`, or `SKILL.md` files | Yes |
| Adds/changes `@function_tool`/`@tool` decorators (LangChain, CrewAI, OpenAI Agents SDK) | Yes |
| Adds/changes a Google ADK `Agent`/`LlmAgent` `tools=[...]` list | Yes |
| Adds/changes n8n workflow JSON, credential stubs, or n8n tool inventories | Yes |
| Adds/changes Conductor OSS workflow JSON with AI/MCP tasks | Yes |
| Edits `prompts/`, `policies/`, or `permissions.scopes` in `shipgate.yaml` | Yes |
| Adds/edits `.github/workflows/agents-shipgate.yml` or related CI | Yes |
| Pure read-only doc/test changes with no manifest impact | Skip |
| Refactor with no behavior change to tools or policies | Skip (or dry-run only) |

One known gap in the Google ADK row: an edit that *modifies* a tools list on the `Agent` alias (rather than `LlmAgent`) is not matched, because a bare `Agent(..., tools=[...])` hunk with no ADK import in it cannot be distinguished from CrewAI's by diff text alone. `LlmAgent` changes and whole-file additions in either spelling are covered.

`prompts/` and `policies/` in that row match at any depth and case-insensitively: an edit under `services/foo/policies/` or `enterprise/lib/captain/Prompts/` routes exactly like a repo-root one. That is parity with the verifier, whose trust-root classification has always read those two surfaces as `**/policies/**` and `**/prompts/**` and has always tolerated the case variant a case-insensitive filesystem resolves to the canonical name. The catalog's `glob` and `none_match_glob` predicates match the same way, so a path cannot be a trust root to the verifier and a `no_match` to the router; the Tier B checks (`SHIP-VERIFY-POLICY-WEAKENED`, `SHIP-VERIFY-CI-GATE-REMOVED`, agent-instruction weakening, trigger-catalog drift) select their changed files the same way too, so a case variant cannot be a trust root in Tier A and invisible to the specialized check that carries the severity. `every_file_matches` is deliberately the exception and stays case-sensitive: it is the docs-only rule's own classifier, and `skip_shipgate` beats `run_shipgate`, so folding it would read `src/TEST_agent.py` — a production module on a case-sensitive filesystem — as a test file and skip a PR that adds a tool beside it. The rule is to fold the predicates that can only add evaluation, never the one that can subtract it. The three surfaces that copy this routing — the pre-commit `files:` regex, the `.cursor/rules/agents-shipgate.mdc` activation globs, and the documented copy-paste hook snippets — follow, so a nested governance edit also activates the host instructions and stages the local hook.

`shipgate.yaml` matches at any depth for the same reason. A monorepo keeps one manifest per project directory, so an edit to `services/refund/shipgate.yaml` — the file that declares that project's agent, purpose, and tool surface — routes exactly like a root-level one; a root-only rule reported it as `no_match`. A nested manifest is also an opt-in: `verify --preview` treats the changed project's own `shipgate.yaml` as the repo-already-adopted signal and routes verification to that manifest rather than to a root one governing a different boundary.

Two implicit triggers also fire even when no row above matches:

- **Repo already opted in (shipgate.yaml present in the workspace)** — run on every PR; the manifest's existence is the opt-in.
- **(Optional) Refactor or framework upgrade that may shift the extracted tool surface** — dry-run only; bumping `openai-agents`, `langchain`, `crewai`, `google-adk`, or `conductor-oss` can change static extraction even without app-code edits. The rule needs both halves of that evidence: the package token **and** a changed dependency manifest. A bare token — a README that mentions `google-adk`, a sample that imports it — is not a version bump and no longer routes as one. The manifest set is `DEPENDENCY_MANIFEST_GLOBS` (`agents_shipgate.core.dependency_manifests`), projected into `triggers.json` and pinned by the contract test; it covers Python (`pyproject.toml`, `requirements*.{txt,in}`, `constraints*.{txt,in}`, `poetry.lock`, `uv.lock`, `pdm.lock`, `pylock*.toml`, `Pipfile*`, conda), Node (`package.json`, npm/pnpm/yarn/bun locks), and the JVM (`pom.xml`, Gradle build files and version catalogs).

A machine-readable mirror of these triggers lives at [`docs/triggers.json`](docs/triggers.json). Coding agents that have not yet adopted Shipgate can fetch the file (raw URL: `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/triggers.json`), apply the rules to a PR diff, and decide whether to propose `agents-shipgate detect`. The catalog is stable for `0.x` and pinned by the public-surface contract test against this prose table — if you change a row above, update `triggers.json` in the same commit. To evaluate a diff locally, use the first-class `trigger` subcommand:

```bash
# From a list of changed paths (and optional diff body for diff_contains rules):
agents-shipgate trigger --changed-files changed.txt --diff pr.diff --json
# Or straight from git (the ONLY mode that shells out to git):
agents-shipgate trigger --base origin/main --head HEAD --json
agents-shipgate trigger --list-rules --json
```

The command emits a stable JSON verdict: `should_run` (alias of `run_shipgate`), `force_run`, `dry_run_recommended`, `skip_reason`, `matched_rules`, `changed_files`, `diff_tokens`, plus `input_status`, `evaluation_status`, and `surface_exclusions` (catalog schema `0.4`). A *skip* verdict is withheld in two states, and `should_run`/`run_shipgate`/`skip`/`skip_reason` are `null` in both — never read `null` as `false`. `evaluation_status: not_evaluated` means the diff could not be read in full, so an unread diff is never reported as `no_match`; `next_action.kind` is `input_required`. `evaluation_status: unclassified` means the diff *was* read in full and no rule classified some or all of the changed files — a fact about the catalog, not about the PR — so the skip is withheld and `next_action` routes forward to the scan; `surface_exclusions.entries[]` names the files nobody classified. A matched `run_shipgate`/`force_run` rule also overrides the stop block, because a capability match in the diff is evidence the whole-workspace negative did not account for; read `stop_conditions_terminal` to see whether a fired stop actually decided. A *run* verdict is still published, because rule matching is monotone — evidence that already matched cannot be un-matched by the bytes that are missing — and it arrives as `evaluation_status: evaluated` with `should_run: true`. That evidence may be a rule matched on the change set or `force_run` from a manifest that is present regardless of the diff, so read `matched_rules` before attributing it. Branch on `evaluation_status`, not on `should_run` alone. The developer entry point `python -m agents_shipgate.triggers shipgate.yaml prompts/refund.md` is preserved.

**Stop conditions.** Stop and do not run `init` only when **all** of these hold:

- `agents-shipgate detect --json` returns `is_agent_project: false`, AND
- `suggested_sources` is empty (no MCP/OpenAPI hits flowing in as `mcp` or `openapi`), AND
- `codex_plugin_candidates` is empty (no Codex plugin package or marketplace hits), AND
- `python_parse_truncated` is `false` — every negative above is a claim about the whole workspace, and a run whose Python parse stopped at its cap read only part of one, AND (this is the raw parse bit, not `agent_scope_truncated`: that one additionally requires more than one candidate scope, so a single-scope workspace whose only agent sits past the cap leaves it false)
- no `shipgate.yaml` already exists in the workspace, AND
- the user did not explicitly request a scan.

A `detect` payload that does not carry every one of those keys leaves the block unevaluable: `trigger` reports `stop_conditions_evaluated: false` and infers no stop. Re-run `detect` with the current CLI rather than reading an absent key as `false`.

Otherwise proceed to `init`. MCP/OpenAPI tool-surface repos and Codex plugin package repos register as `is_agent_project: false` because they have no Python framework imports — but they are valid Shipgate targets. MCP/OpenAPI hits surface as `suggested_sources`; Codex plugin hits surface as `codex_plugin_candidates`. The trigger table above is the authoritative go/no-go.

---

## Five common agent tasks

### Task 1 · Add the gate to an existing repo

```bash
pipx install agents-shipgate
agents-shipgate init --workspace . --write
# edit shipgate.yaml to replace any CHANGE_ME values
agents-shipgate scan -c shipgate.yaml
```

`init` writes a manifest with `CHANGE_ME` placeholders for `agent.name` and `agent.declared_purpose`. Replace them by reading the agent's prompt or main file.

### Task 2 · Read findings programmatically

Always parse `agents-shipgate-reports/report.json`, not the markdown.

The canonical field list — `release_decision`, `capability_facts` / `declared_intentions` / `misalignments` / `release_consequence` / `suggested_scenarios`, `tool_surface_facts` / `tool_surface_diff`, and `action_surface_facts` / `action_surface_diff` — lives in [`docs/agent-contract-current.md`](docs/agent-contract-current.md#read-these-first-for-release-gating). It updates first when the contract bumps; this file links to it instead of restating the field set.

Other stable top-level fields (full history and semantics live in
[`docs/agent-contract-current.md`](docs/agent-contract-current.md); never
restate version archaeology here):

- `summary.{critical_count, high_count, medium_count, status}` (legacy,
  baseline-blind — do not gate on it)
- `findings[].{id, fingerprint, check_id, severity, tool_name, evidence, recommendation, suppressed}`
- `findings[].{autofix_safe, requires_human_review, suggested_patch_kind, docs_url, provenance_kind, blocks_release}`
- `findings[].policy_routing` (policy-pack owner/reviewer/approval routing metadata only; non-enforcing and not part of `evidence`)
- `findings[].patches[]` (only when scan ran with `--suggest-patches`)
- `baseline.{matched_count, new_count, resolved_count}` · root-reachable `tool_inventory[]` · full `tool_catalog[]` · `codex_plugin_surface`
- `action_surface_facts` / `action_surface_diff`
- `release_decision.evidence_coverage.{binding_coverage,semantic_coverage,evidence_gaps}`
- Audit envelopes: `release_decision.contribution_rules[]`, `policy_audit`,
  `privacy_audit`, `heuristics_filter` — explanatory, never a second gate

The current schema is [`docs/report-schema.v0.42.json`](docs/report-schema.v0.42.json). Emitted reports carry `report_schema_version: "0.42"`; `surface_exclusions` records every subject a stage removed from the analysed surface and whether the release decision saw it, typed predicate support prevents heuristic evidence from being upgraded by policy severity or block metadata, and verify-native reports bind the content-addressed request and decision. A `passed` result requires a complete static binding graph from its entry points plus complete, conflict-free identity, effect, authority, and applicable-policy evidence for every reachable action. Every release decision explicitly carries `static_analysis_only: true`, `runtime_behavior_verified: false`, and `static_verdict_disclaimer`; packet §1 mirrors them. Binding, semantic, and policy-applicability gaps are not Findings and cannot be suppressed or baselined. See [`docs/passed-verdict-contract.md`](docs/passed-verdict-contract.md), [`docs/verification-reproducibility.md`](docs/verification-reproducibility.md), and [`docs/agent-contract-current.md`](docs/agent-contract-current.md). v0.41 remains frozen at [`docs/report-schema.v0.41.json`](docs/report-schema.v0.41.json).

**Release gating signal**: prefer `release_decision.decision` (`"blocked" | "review_required" | "insufficient_evidence" | "passed"`) over `summary.status`. The new field is **baseline-aware** — a baseline-matched critical surfaces in `release_decision.review_items` (accepted debt), not `release_decision.blockers`. `summary.status` stays baseline-blind for v0.7 compatibility, so a baseline-matched-only critical produces both `summary.status = "release_blockers_detected"` AND `release_decision.decision = "review_required"` (intentional divergence — see [STABILITY.md](STABILITY.md#release_decisiondecision-vs-summarystatus)). `insufficient_evidence` (added v0.14) signals that the scan saw too many low-confidence tools or source-loader warnings to be trustworthy; consumers that switch on the enum must fall back to `review_required` for unknown future values.

For a step-by-step reader's primer with anti-patterns and concrete code rewrites, see [`docs/report-reading-for-agents.md`](docs/report-reading-for-agents.md).

### Task 3 · Suppress a finding with a reason

```yaml
# shipgate.yaml
checks:
  ignore:
    - check_id: SHIP-DOC-MISSING-DESCRIPTION
      tool: legacy_search
      reason: tool deprecated 2026-Q2
```

`reason` is required and non-empty; the manifest fails validation otherwise.
Suppressions apply to Findings only. They cannot accept, hide, or close a
semantic evidence gap.

### Task 4 · Save a baseline before enabling strict CI

```bash
agents-shipgate baseline save -c shipgate.yaml --out .agents-shipgate/baseline.json \
  --owner <human> --reason "<why accepted>" --expires <YYYY-MM-DD>
```

`--owner`/`--reason`/`--expires` (v0.13+) record who accepted the debt, why,
and the review-by date on newly-accepted entries. They are human-declared
values: an agent must ask the user, never invent them, and blank values are
rejected. `--apply-to-existing` fills the fields into existing entries that
lack them without overwriting previously-set values.

Then in CI:

```bash
agents-shipgate scan -c shipgate.yaml \
  --baseline .agents-shipgate/baseline.json \
  --ci-mode strict --fail-on critical,high
```

Strict mode fails CI only on **new** findings (those not in the baseline).
`agents-shipgate baseline status --json` reports accepted-debt aging
(owner, age, expiry); with `--require-owner` / `--require-expiry` /
`--max-age-days N` it exits `20` on violations (advisory exit `0` without
gate flags) — parse `violations[]` from the JSON, then route to a human:
acknowledging debt is a human decision.

### Task 5 · Explain a check or a specific finding

For static catalog metadata about a check ID (rationale, fires-when, recommendation):

```bash
agents-shipgate explain SHIP-POLICY-APPROVAL-MISSING --json
```

Returns the full `CheckMetadata` with `id`, `category`, `default_severity`, `description`, `rationale`, `fires_when`, `evidence_fields`, `recommendation`.

For a contextual explanation tied to a specific finding from a real scan (catalog metadata + the finding's evidence + a 3–5 sentence templated prose summary):

```bash
agents-shipgate explain-finding fp_<fingerprint> \
    --from agents-shipgate-reports/report.json --json
```

Returns the canonical Finding fields plus `metadata` (CheckMetadata for the check_id) and `explanation` — a deterministic prose summary suitable for direct quotation in a PR comment or chat reply. The companion prompt is [`prompts/explain-finding-to-user.md`](prompts/explain-finding-to-user.md).

---

## Agent FAQ

### Where is the manifest schema?

Use [`docs/manifest-v0.1.json`](docs/manifest-v0.1.json) for machine
validation and [`docs/manifest-v0.1.md`](docs/manifest-v0.1.md) for prose.

### Where is the report schema?

Parse `agents-shipgate-reports/report.json` and validate against
[`docs/report-schema.v0.42.json`](docs/report-schema.v0.42.json) (current).
Older reports (`report_schema_version: "0.10"`) validate against the
frozen [`docs/report-schema.v0.10.json`](docs/report-schema.v0.10.json).
Do not scrape Markdown when JSON is available.

### How do I add a new check?

Follow [`docs/architecture.md`](docs/architecture.md) and update the check
registry, tests, [`docs/checks.md`](docs/checks.md), and
[`docs/checks.json`](docs/checks.json). Check IDs must not change after
publication.

### How do I add a new framework adapter?

Start with [`docs/framework-adapter-checklist.md`](docs/framework-adapter-checklist.md).
Adapters must be static by default: no user-code import, no network access, no
agent execution.

### Where are runnable examples?

Use [`samples/README.md`](samples/README.md) for sample agents and
[`docs/examples.md`](docs/examples.md) for a narrative overview. The fastest
fixture is `agents-shipgate fixture run support_refund_agent`.

### What vocabulary should I use in user-facing copy?

Use the [canonical names](#canonical-names) table above and the website
glossary: https://threemoonslab.com/glossary/.

---

## Schemas

For the short, current statement of "which fields to read", see [`docs/agent-contract-current.md`](docs/agent-contract-current.md). It is the single file that updates first when the contract bumps; the table below lists the underlying schemas.

| What | Path | Stable |
|---|---|---|
| Manifest schema | [`docs/manifest-v0.1.json`](docs/manifest-v0.1.json) | `0.1` |
| Report schema (current) | [`docs/report-schema.v0.42.json`](docs/report-schema.v0.42.json) | `0.42` |
| Report schema (v0.38 frozen reference) | [`docs/report-schema.v0.38.json`](docs/report-schema.v0.38.json) | `0.38` |
| Report schema (v0.37 frozen reference) | [`docs/report-schema.v0.37.json`](docs/report-schema.v0.37.json) | `0.37` |
| Report schema (v0.34 frozen reference) | [`docs/report-schema.v0.34.json`](docs/report-schema.v0.34.json) | `0.34` |
| Report schema (v0.33 frozen reference) | [`docs/report-schema.v0.33.json`](docs/report-schema.v0.33.json) | `0.33` |
| Report schema (v0.32 frozen reference) | [`docs/report-schema.v0.32.json`](docs/report-schema.v0.32.json) | `0.32` |
| Report schema (v0.31 frozen reference) | [`docs/report-schema.v0.31.json`](docs/report-schema.v0.31.json) | `0.31` |
| Report schema (v0.30 frozen reference) | [`docs/report-schema.v0.30.json`](docs/report-schema.v0.30.json) | `0.30` |
| Report schema (v0.29 frozen reference) | [`docs/report-schema.v0.29.json`](docs/report-schema.v0.29.json) | `0.29` |
| Report schema (v0.28 frozen reference) | [`docs/report-schema.v0.28.json`](docs/report-schema.v0.28.json) | `0.28` |
| Report schema (v0.27 frozen reference) | [`docs/report-schema.v0.27.json`](docs/report-schema.v0.27.json) | `0.27` |
| Report schema (v0.26 frozen reference) | [`docs/report-schema.v0.26.json`](docs/report-schema.v0.26.json) | `0.26` |
| Report schema (v0.25 frozen reference) | [`docs/report-schema.v0.25.json`](docs/report-schema.v0.25.json) | `0.25` |
| Verify-run schema | [`docs/verify-run-schema.v3.json`](docs/verify-run-schema.v3.json) | `shipgate.verify_run/v3` |
| Verification plan schema | [`docs/verification-plan-schema.v1.json`](docs/verification-plan-schema.v1.json) | `shipgate.verification_plan/v1` |
| Verification unit result schema | [`docs/verification-unit-result-schema.v1.json`](docs/verification-unit-result-schema.v1.json) | `shipgate.verification_unit_result/v1` |
| Verification artifact manifest schema | [`docs/verification-artifact-manifest-schema.v1.json`](docs/verification-artifact-manifest-schema.v1.json) | `shipgate.verification_artifact_manifest/v1` |
| Verification receipt schema | [`docs/verification-receipt-schema.v1.json`](docs/verification-receipt-schema.v1.json) | `shipgate.verification_receipt/v1` |
| Agent handoff schema | [`docs/agent-handoff-schema.v5.json`](docs/agent-handoff-schema.v5.json) | `shipgate.agent_handoff/v5` |
| Agent boundary result schema | [`docs/agent-boundary-result-schema.v1.json`](docs/agent-boundary-result-schema.v1.json) | `shipgate.agent_boundary_result/v1` |
| Codex boundary result schema (deprecated frozen projection) | [`docs/codex-boundary-result-schema.v2.json`](docs/codex-boundary-result-schema.v2.json) | `shipgate.codex_boundary_result/v2` |
| Report schema (v0.24 frozen reference) | [`docs/report-schema.v0.24.json`](docs/report-schema.v0.24.json) | `0.24` |
| Report schema (v0.23 frozen reference) | [`docs/report-schema.v0.23.json`](docs/report-schema.v0.23.json) | `0.23` |
| Report schema (v0.22 frozen reference) | [`docs/report-schema.v0.22.json`](docs/report-schema.v0.22.json) | `0.22` |
| Report schema (v0.21 frozen reference) | [`docs/report-schema.v0.21.json`](docs/report-schema.v0.21.json) | `0.21` |
| Report schema (v0.20 frozen reference) | [`docs/report-schema.v0.20.json`](docs/report-schema.v0.20.json) | `0.20` |
| Report schema (v0.19 frozen reference) | [`docs/report-schema.v0.19.json`](docs/report-schema.v0.19.json) | `0.19` |
| Report schema (v0.18 frozen reference) | [`docs/report-schema.v0.18.json`](docs/report-schema.v0.18.json) | `0.18` |
| Report schema (v0.17 frozen reference) | [`docs/report-schema.v0.17.json`](docs/report-schema.v0.17.json) | `0.17` |
| Report schema (v0.16 frozen reference) | [`docs/report-schema.v0.16.json`](docs/report-schema.v0.16.json) | `0.16` |
| Report schema (v0.15 frozen reference) | [`docs/report-schema.v0.15.json`](docs/report-schema.v0.15.json) | `0.15` |
| Report schema (v0.14 frozen reference) | [`docs/report-schema.v0.14.json`](docs/report-schema.v0.14.json) | `0.14` |
| Report schema (v0.13 frozen reference) | [`docs/report-schema.v0.13.json`](docs/report-schema.v0.13.json) | `0.13` |
| Report schema (v0.12 frozen reference) | [`docs/report-schema.v0.12.json`](docs/report-schema.v0.12.json) | `0.12` |
| Report schema (v0.11 frozen reference) | [`docs/report-schema.v0.11.json`](docs/report-schema.v0.11.json) | `0.11` |
| Report schema (v0.10 frozen reference) | [`docs/report-schema.v0.10.json`](docs/report-schema.v0.10.json) | `0.10` |
| Report schema (v0.9 frozen reference) | [`docs/report-schema.v0.9.json`](docs/report-schema.v0.9.json) | `0.9` |
| Report schema (v0.8 frozen reference) | [`docs/report-schema.v0.8.json`](docs/report-schema.v0.8.json) | `0.8` |
| Report schema (v0.7 frozen reference) | [`docs/report-schema.v0.7.json`](docs/report-schema.v0.7.json) | `0.7` |
| Report schema (v0.6 frozen reference) | [`docs/report-schema.v0.6.json`](docs/report-schema.v0.6.json) | `0.6` |
| Packet schema (Release Evidence Packet, latest) | [`docs/packet-schema.v0.17.json`](docs/packet-schema.v0.17.json) | `0.17` |
| Agent result schema (current) | [`docs/agent-result-schema.v2.json`](docs/agent-result-schema.v2.json) | `agent_result_v2` |
| Verifier schema (current) | [`docs/verifier-schema.v0.5.json`](docs/verifier-schema.v0.5.json) | `0.5` |
| Agent handoff schema (current) | [`docs/agent-handoff-schema.v5.json`](docs/agent-handoff-schema.v5.json) | `shipgate.agent_handoff/v5` |
| Preflight schema (current) | [`docs/preflight-schema.v0.3.json`](docs/preflight-schema.v0.3.json) | `0.3` |
| Host-grants inventory schema | [`docs/host-grants-inventory-schema.v0.2.json`](docs/host-grants-inventory-schema.v0.2.json) | `0.2` |
| Host-grants baseline schema | [`docs/host-grants-baseline-schema.v0.2.json`](docs/host-grants-baseline-schema.v0.2.json) | `0.2` |
| Host-grants drift schema | [`docs/host-grants-drift-schema.v0.2.json`](docs/host-grants-drift-schema.v0.2.json) | `0.2` |
| Capability standard | [`docs/capability-standard.md`](docs/capability-standard.md) | `0.5` |
| Capability lock schema | [`docs/capability-lock-schema.v0.8.json`](docs/capability-lock-schema.v0.8.json) | `0.8` |
| Capability lock diff schema | [`docs/capability-lock-diff-schema.v0.9.json`](docs/capability-lock-diff-schema.v0.9.json) | `0.9` |
| Governance benchmark catalog schema | [`docs/governance-benchmark-catalog-schema.v0.2.json`](docs/governance-benchmark-catalog-schema.v0.2.json) | `0.2` |
| Governance benchmark result schema | [`docs/governance-benchmark-result-schema.v0.2.json`](docs/governance-benchmark-result-schema.v0.2.json) | `0.2` |
| Check catalog | [`docs/checks.json`](docs/checks.json) | regenerated each release |
| Anti-patterns (what NOT to write) | [`samples/_anti_patterns/`](samples/_anti_patterns/) | reference |
| Minimal manifest example | [`docs/manifest-v0.1.example.minimal.yaml`](docs/manifest-v0.1.example.minimal.yaml) | reference |

For VS Code / Cursor live YAML validation, every manifest produced by `init` includes:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/manifest-v0.1.json
```

---

## Stable command surface

Promised to not break in `0.x` minor versions. See [STABILITY.md](STABILITY.md) for the full contract.

| Command | Stable flags |
|---|---|
| `agents-shipgate scan` | `-c`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--diff-from`, `--changed-files`, `--no-plugins`, `--no-heuristics`, `--verbose`, `--packet`/`--no-packet`, `--packet-format` |
| `agents-shipgate evidence-packet` | `--from`, `--out`, `--format`, `--json` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json` |
| `agents-shipgate doctor` | `-c`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate contract` | `--json` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate explain-finding` | `<fingerprint>`, `--from`, `--no-plugins`, `--json` |
| `agents-shipgate findings` | `--from`, `--provenance-kind`, `--include-suppressed`, `--json` |
| `agents-shipgate trigger` | `--workspace`, `--changed-files`, `--diff`, `--base`, `--head`, `--manifest-present`/`--no-manifest-present`, `--user-requested`, `--list-rules`, `--json` |
| `agents-shipgate bootstrap` | `--workspace`, `--confidence`, `--no-ci`, `--no-apply`, `--json` |
| `agents-shipgate list-checks` | `--json`, `--no-plugins` |
| `agents-shipgate baseline save` | `-c`, `--out`, `--owner`, `--reason`, `--expires`, `--apply-to-existing` |
| `agents-shipgate baseline status` | `--baseline`, `--as-of`, `--require-owner`, `--require-expiry`, `--max-age-days`, `--json` (gate flags exit `20` on violations) |
| `agents-shipgate fixture` | `list`, `run`, `copy`, `verify` |
| `agents-shipgate self-check` | `--json` |
| `agents-shipgate agent handoff` | `--from`, `--report`, `--verify-run`, `--out`, `--json` |

Newer commands (stable intent, flags may still evolve):

| Command | Purpose |
|---|---|
| `shipgate audit --host` | Zero-config, read-only static inventory of coding-agent host grants with per-host coverage; deterministic repository scope by default, optional `--scope local-static`. Works without `shipgate.yaml`. |
| `agents-shipgate mcp-serve` | Local read-only stdio MCP server (`[mcp]` extra) exposing `shipgate.check`, `shipgate.preflight`, `shipgate.explain`, `shipgate.capabilities`, and `shipgate.handoff`. See [`docs/mcp-server.md`](docs/mcp-server.md). |
| `agents-shipgate org status` | Local organization governance projection over exception hygiene, policy-pack pins, host-grant drift, and registry readiness; `--json` available and governance violations exit `20`. |
| `agents-shipgate registry` | `ingest --attestation <file>` / `query` / `report --bypass` — local capability-release ledger over attestations. |
| `agents-shipgate install-hooks` | Claude Code hooks: PreToolUse trust-root boundary (`ask`/`deny`), PostToolUse trigger nudge, Stop verify. |

### Release Evidence Packet (v0.16)

`scan` emits a reviewer-shaped Release Evidence Packet alongside
`report.{md,json}` by default; outputs land at
`agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the
`[pdf]` extras). The packet is derived from the report JSON, is a local
artifact only, and never gates — §1's verdict derives from
`release_decision.decision` alone, and §10 always lists what the packet did
NOT prove. Use `--no-packet` / `--packet-format` on `scan`, and
`agents-shipgate evidence-packet --from <packet.json|report.json>` to
re-render. The full packet contract (fixed sections, disclaimers,
`evidence_matrix` rules) lives in
[STABILITY.md §Release Evidence Packet](STABILITY.md#release-evidence-packet-v017)
and [`docs/agent-contract-current.md`](docs/agent-contract-current.md#read-these-for-release-review).

Exit codes (stable):

| Code | Meaning |
|---|---|
| `0` | Pass (advisory or strict-no-blockers) |
| `2` | Manifest config error |
| `3` | Input parse error (file missing, malformed, path traversal blocked, file too large) |
| `4` | Other Agents Shipgate error |
| `20` | Strict-mode gate failure |

---

## What you can't do (intentionally)

This section is the **CLI's** invariants. For the **agent's** behavioral boundary — what an agent driving Shipgate may assert in PR comments and review summaries — see [`docs/agent-autofix-boundary.md`](docs/agent-autofix-boundary.md).

- The CLI does not modify user code; it only reads.
- The CLI does not connect to MCP servers; it reads exported JSON only.
- Tool sources outside the manifest directory are rejected (path traversal containment).
- Files larger than 10 MB are rejected.
- Plugins are off by default (`AGENTS_SHIPGATE_ENABLE_PLUGINS=1` to enable; `--no-plugins` to force off).

---

## When you make changes to this repo

**Run the CLI as `./shipgate …` from the repository root** — `python shipgate …`
on Windows, which does not read a shebang. That is the one canonical command
here, for contributors and coding agents alike, and it is what every example in
`CONTRIBUTING.md` uses. Emitted commands name whichever spelling starts it, so
follow `next_actions[].executable` rather than assuming one token. `./shipgate scan -c shipgate.yaml`
is `agents-shipgate scan -c shipgate.yaml`, with three differences that matter
in a checkout: it runs *this* tree's `src/` rather than whatever copy `PATH`
resolves to, it selects a supported interpreter (`AGENTS_SHIPGATE_PYTHON`, else
the project virtualenv — the main checkout's, if this is a `git worktree`), and
it needs no installation and no `PYTHONPATH`. Recovery commands it prints name
the launcher, so they are runnable exactly as printed.

Use a bare `agents-shipgate` only to check what an *installed* build does. If a
command behaves as though your edit never happened, run
`./shipgate doctor --config shipgate.yaml --json` and read `environment`
(above): it states which interpreter ran, which package was imported, which
checkout you are standing in, and what disagrees.

The launcher stops at the repository boundary, and so should edits that spread
it. Everything written *into another repo* — the sections above, the adoption
kits, `.cursorrules`, `.claude/commands/`, `skills/`, `.agents/skills/`, the
snippets in `docs/target-repo-agent-snippets.md`, and every block
`init --write --agent-instructions=…` renders — keeps saying
`agents-shipgate`, because those run where the package is installed and there
is no launcher. It is also the same reason durable artifacts stay canonical:
those bytes are pinned by render hashes, and an absolute path from one machine
does not belong in them.

- Run `python -m ruff check .` and `python -m pytest` before committing.
- Bumping a check's behavior requires updating the test suite and any golden fixtures under `samples/*/expected/`.
- New checks must include: code in `src/agents_shipgate/checks/<category>.py` plus a `BUILTIN_CHECKS` entry in `checks/registry.py`, metadata in `docs/checks/<category>.yaml` (loaded into `CHECK_METADATA` at registry import time by `agents_shipgate.checks._metadata_loader`), a test in `tests/`, and a row in `docs/checks.md`. After editing YAML, regenerate `docs/checks.json` with `python scripts/generate_schemas.py`.
- Do not change check IDs in published versions; always add new ones.
- If you regenerate the JSON schemas, run `python scripts/generate_schemas.py` and commit every changed file under `docs/`.

---

## Reusable prompts

Prebuilt prompts for common workflows live in [`prompts/`](prompts/):

- [`decide-shipgate-relevance.md`](prompts/decide-shipgate-relevance.md) — apply [`docs/triggers.json`](docs/triggers.json) to decide whether Shipgate should run at all
- [`add-shipgate-to-repo.md`](prompts/add-shipgate-to-repo.md) — bootstrap a repo
- [`fix-top-finding.md`](prompts/fix-top-finding.md) — iterate on a single finding
- [`recommend-fixes.md`](prompts/recommend-fixes.md) — walk all active findings and surface targeted fix recommendations across the four autofix-policy classes
- [`explain-finding-to-user.md`](prompts/explain-finding-to-user.md) — translate one finding into 3–5 sentences of user-facing prose; companion to `agents-shipgate explain-finding`
- [`stabilize-strict-mode.md`](prompts/stabilize-strict-mode.md) — tune → baseline → promote
- [`triage-false-positive.md`](prompts/triage-false-positive.md) — override vs suppress decision
- [`upgrade-shipgate-version.md`](prompts/upgrade-shipgate-version.md) — bump agents-shipgate version safely (regenerate baseline if needed)

For downstream repos, use [`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md)
to copy Shipgate trigger rules into `AGENTS.md`, `CLAUDE.md`, Cursor rules,
PR templates, and advisory CI. Use
[`docs/agent-adoption-harness.md`](docs/agent-adoption-harness.md) to evaluate
whether coding agents discover and use Shipgate without being prompted by name.

### Editor / agent integrations

Per-agent install guides for dropping Shipgate into your own agent project:

- [`docs/agents/use-with-claude-code.md`](docs/agents/use-with-claude-code.md) — install the `/shipgate` slash command and `agents-shipgate` auto-discoverable skill. Source surfaces ship at [`.claude/commands/shipgate.md`](.claude/commands/shipgate.md) and [`skills/agents-shipgate/`](skills/agents-shipgate/) (named `agents-shipgate` to avoid colliding with the slash command — Claude Code lets a same-named skill preempt a command). The skill bundles the recipes in [`skills/agents-shipgate/prompts/`](skills/agents-shipgate/prompts/) and a starter advisory CI workflow at [`skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml`](skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml); when you change anything in [`prompts/`](prompts/) or `examples/github-actions/01-advisory-pr-comment.yml`, sync the bundled copy.
- [`docs/agents/use-with-codex.md`](docs/agents/use-with-codex.md) — install the canonical `AGENTS.md` snippet plus repo-scoped Codex skill. Source surfaces ship at [`.agents/skills/agents-shipgate/`](.agents/skills/agents-shipgate/) and are generated into downstream repos with `agents-shipgate init --write --agent-instructions=agents-md,codex-skill`. The default `all` kit does not install skill bundles. The skill is Codex-optimized: concise `SKILL.md`, on-demand references, and an advisory CI template.
- [`docs/agents/use-with-cursor.md`](docs/agents/use-with-cursor.md) — drop the canonical `.cursor/rules/agents-shipgate.mdc` auto-attach rule (from [`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md)) into your repo. The rule fires whenever a chat touches `shipgate.yaml`, an MCP/OpenAPI spec, a tool JSON, or a `.py` file.

---

## Verification

After you (the agent) complete a task involving Agents Shipgate, verify:

1. `agents-shipgate self-check --json` returns `"ready": true`.
2. `agents-shipgate contract --json` matches the installed CLI contract you expect.
3. The user's `shipgate.yaml` has no `CHANGE_ME` placeholders.
4. A scan completes with exit code 0 (advisory mode) and writes `report.json`.
5. The user's repo `.gitignore` includes `agents-shipgate-reports/` (do not commit reports).
