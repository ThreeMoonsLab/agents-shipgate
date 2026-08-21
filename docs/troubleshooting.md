# Troubleshooting

## `Config file not found: shipgate.yaml`

Create a starter manifest:

```bash
agents-shipgate init --workspace . --write
```

Then inspect the sources before running checks:

```bash
agents-shipgate doctor --config shipgate.yaml
```

## `--workspace does not exist: <path>`

Every command that takes `--workspace` refuses a path that is not there, with
`config_error` and exit 2, before it creates anything. Two causes, both common
on a first run:

- **The clone has not happened yet.** Preview is the first command in the
  adoption path, so it is easy to run one step early. Clone first, then point
  `--workspace` at the checkout.
- **The relative path resolved against a different directory.** The message
  names the directory it resolved against for exactly this case.

`--workspace is not a directory: <path>` is a different mistake: the path
exists but is a file. Point the option at the checkout root, not at a file
inside it (`shipgate.yaml` goes to `--config`).

## Absent, empty, and malformed manifests read differently

| Manifest state | Message | Recovery |
| --- | --- | --- |
| Not on disk | `Config file not found: <path> in <cwd>.` | `init --workspace . --write` — the `next_action` bootstraps rather than edits. |
| Present but empty | `Config file is empty: <path>.` | Edit the file in place; do not re-run `init`, which refuses to overwrite. |
| Present, valid YAML, not a mapping | `Config file must contain a YAML object: <path>` | Edit the file in place. |

If you see "must contain a YAML object" for a file you never created, that is a
bug — the three states are asserted to stay distinguishable.

## First Real Repo Decision Tree

Use this before reading the full manifest schema.

| Symptom | Diagnostic ID | Fix |
| --- | --- | --- |
| `detect --json` returns `is_agent_project: false`, but `suggested_sources` has MCP or OpenAPI files | `SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY` | Continue to `init --workspace . --write`; artifact-only repos are valid Shipgate targets. |
| `detect --json` returns `is_agent_project: false`, but `codex_plugin_candidates` is non-empty | `SHIP-DIAG-CODEX-PLUGIN-PACKAGE-DETECTED` | Continue to `init --workspace . --write`; Codex plugin repos are valid static Shipgate targets. |
| `doctor` shows zero tools | `SHIP-DIAG-ZERO-TOOLS` | Check `tool_sources[].path`, MCP `tools[]`, OpenAPI `paths`, optional source parse warnings, and dynamic toolset warnings. |
| SDK/framework extractor finds no tools | `SHIP-DIAG-DYNAMIC-TOOLSETS-ONLY` | Add an explicit MCP export, OpenAPI spec, or local tool inventory instead of relying on dynamic code discovery. |
| `shipgate.yaml` still has `CHANGE_ME` | `SHIP-DIAG-CHANGE-ME-PLACEHOLDERS` | Replace `agent.name` and `agent.declared_purpose` from prompt, main agent file, or README context before scanning. |
| Required `tool_sources[].path` does not resolve | `SHIP-DIAG-MISSING-SOURCE-FILE` | `doctor --json` reports `unresolved_sources: [...]` and a diagnostic with `kind="edit", path="shipgate.yaml:<line>"`. Fix the path. (`scan` still exits 3 on the same condition — fix it before scanning.) |
| `init --write` exits 2 with `manifest_status: "refused_unresolved_scope"` | — | The manifest scope is unresolved: agents in more than one self-contained project, or discovery capped before it could tell. Re-run with `--workspace` pointed at one of `auto_detected.agent_project_candidates[].path`. See [One repo, several agent projects](#one-repo-several-agent-projects). |
| Install fails in an older project environment | — | Agents Shipgate requires Python 3.12+. Install with `pipx` or `uv` using a Python 3.12+ interpreter. |
| A fix or an upgrade behaves as though it never happened; a documented subcommand looks "missing" | — | Another copy is answering. `doctor --json` reports `environment.mismatches[]` with the interpreter, the imported package, and a runnable recovery command. See [diagnostics.md](diagnostics.md#which-shipgate-answered-the-environment-block). |
| `agents-shipgate` exits with `ModuleNotFoundError` or "bad interpreter" before printing anything | — | The console script outlived the environment that backed it, so nothing in Shipgate can report it. Run `python -m agents_shipgate doctor --json` with a working interpreter: it names the stale wrapper as `console_script_interpreter_missing`. Then reinstall or remove that wrapper. |
| Reports show up as untracked files | — | Add `agents-shipgate-reports/` to `.gitignore`; do not commit reports by default. |

When run under `AGENTS_SHIPGATE_AGENT_MODE=1`, errors carry a `next_actions: [...]` array alongside the legacy `next_action: str`. The full catalog and schema is in [diagnostics.md](diagnostics.md).

## One Repo, Several Agent Projects

A `shipgate.yaml` describes **one** agent surface: one `agent.name`, one
`declared_purpose`, one set of `tool_sources`. In a repository that holds
several self-contained agent projects — the `python/agents/<name>/` layout of
a samples monorepo, an `apps/*` or `packages/*` workspace — a manifest written
at the repository root declares all of them at once, and the alignment layer
that compares what an agent says it does against what it can do has nothing
left to compare.

So Shipgate scopes rather than guesses:

- `verify --preview` derives the `--workspace` it recommends from the changed
  paths — committed *and* uncommitted, because the command it emits runs
  against the working tree. Each is attributed to the nearest directory at or above it that
  carries a project marker (`pyproject.toml`, `package.json`, `go.mod`,
  `Cargo.toml`, `pom.xml`, …), and the recommendation is that project when
  exactly one is claimed. Run its command verbatim. Changes spanning two
  projects, or sitting at the repository root, keep recommending the root as
  before. Documentation and tests cannot outvote code: where two projects are
  claimed, one claimed only by documentation or test paths drops out — the
  trigger catalog's docs-only rule decides which those are — so a README edited
  beside a sibling project does not send the answer back to the root. When the
  changed project already carries its own `shipgate.yaml`, preview routes you
  to `verify` there instead of to setup.
- Preview reads project markers from the **working tree**, because that is the
  tree the `init` it recommends would write to. So `verify --preview --head
  <ref>` while some other commit is checked out establishes no project — the
  directory the pull request adds exists only on `<ref>`. That is not a verdict
  about the change: the control route is `agent_action_required` with a
  `fetch_base` action whose `expects` names what is missing (this worktree, at
  `<ref>`). Check the ref out and re-run the identical command. Plain `verify`
  is unaffected — it reads `--head` from the object database, not the worktree.
- `init --write` refuses a workspace whose agents live in more than one
  project: `manifest_status: "refused_unresolved_scope"`, exit `2`, and
  **nothing written** — no manifest, no CI workflow, no agent-instruction
  snippets, no `.gitignore` block. `auto_detected.agent_project_candidates[]`
  lists each project and the agent names inside it; pick the one your change
  belongs to and re-run with `--workspace <that path>`.
- `--allow-unresolved-scope` writes the single root manifest anyway. Use it
  only when one agent surface genuinely spans those directories.
- Reports follow the workspace: `verify --workspace apps/a` writes
  `apps/a/agents-shipgate-reports/`, which is what the managed `.gitignore`
  block in that project covers, and two projects in one repository never
  overwrite each other's results. An explicit `--out` still resolves against
  the repository root.
- With `--ci`, each manifest gets its own workflow — `agents-shipgate.yml` for
  a repository-root manifest, `agents-shipgate-<project>.yml` for a scoped one.
  The action takes a single `config`, so one shared file would gate whichever
  project initialized first and leave the rest ungated. Read `workflow.path`
  rather than assuming the name.
- `agent_scope: "unknown"` is the same refusal for a different reason:
  discovery stopped at its Python-file cap in a workspace with several project
  roots, so a "single project" answer would just be whichever files were read
  first. Raise `detect --max-python-files`, or name the project directly.
- `agent_scope_truncated: true` says the candidate list is a lower bound, not
  an enumeration: the Python parse stopped at its cap, so a project in the part
  of the tree that was never read is missing from it. If the project you are
  changing is not listed, that is the first thing to rule out — re-run with
  `detect --max-python-files <n>` before concluding it is not an agent project.
  `workspace_signals.project_root_count` is the uncapped count the list is
  measured against.
- `agents-shipgate detect --json` answers the same question without writing
  anything: read `agent_scope`, `agent_scope_truncated`, and
  `agent_project_candidates[]`. Its `next_actions[]` carries the same shape
  `init`'s refusal does — the decision first (`kind: "review"`, no command,
  because naming one candidate would make the pick Shipgate declines to make),
  then one exact `init --workspace <candidate> --write --json` per candidate,
  each with `executable`/`args`. Match on the path of the project you are
  changing and run that entry; the workspace root is never among them, since
  that is the scope `init` refuses.
- `python_parse_truncated: true` is the wider version of the same warning: the
  Python parse stopped at its cap, so *any* whole-workspace negative — most of
  all `is_agent_project: false` — describes the files that were read rather
  than the repository. Nothing publishes a terminal route while it is true;
  the emitted `next_actions[0]` is
  `detect --max-python-files <workspace_signals.python_file_total> --json`,
  which covers every Python file and so cannot land back here.

One manifest per project directory is the supported monorepo shape; each is
verified with `verify --workspace <project> --config shipgate.yaml` from
anywhere in the repository.

## `doctor` Shows Zero Tools

Common causes:

- `tool_sources[].path` points at the wrong file.
- The MCP export does not contain a `tools` array.
- The OpenAPI document has no `paths` object.
- The source is marked `optional: true` and failed to parse.
- A Google ADK source uses dynamic toolsets without explicit MCP/OpenAPI/tool inventory inputs.

Run with verbose logs:

```bash
AGENTS_SHIPGATE_LOG_FORMAT=json agents-shipgate doctor --config shipgate.yaml --verbose
```

## The SDK Extractor Finds Nothing

The OpenAI Agents SDK extractor is AST-only. It recognizes direct `function_tool` decorators and simple import aliases such as:

```python
from agents import function_tool as ft

@ft
def lookup_customer(customer_id: str) -> str:
    ...
```

It intentionally does not execute imports, factories, dynamic wrappers, `Tool.from_fn()` calls, or dynamic tool lists. Declare those tools through MCP/OpenAPI inputs or manifest metadata.

## Google ADK Toolsets Are Reported As Dynamic

Agents Shipgate never runs ADK or connects to MCP servers. For ADK `McpToolset`
or dynamic `OpenAPIToolset` usage, provide explicit local review inputs:

```yaml
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
  - id: support_openapi
    type: openapi
    path: specs/support.openapi.yaml

google_adk:
  tool_inventories:
    - path: inventories/adk-mcp-tools.json
      source_id: adk
```

`source_id` names the tool source the inventory completes. Omit it and the file
becomes an independent source: entries sharing a name with an already-extracted
tool are added *beside* it, which grows the catalog and leaves the
`incomplete_surface` gap that asked for the inventory open. See
[Tool Inventories](manifest-v0.1.md#tool-inventories).

Static `tool_filter` values reduce ADK MCP risk, but they do not enumerate the
tool schemas by themselves. Add an inventory when reviewers need full schema
evidence.

Static OpenAPI spec resolution covers simple literal-path idioms such as
`Path("spec.yaml").read_text()` and `open("spec.yaml").read()`. Module-relative
patterns such as `Path(__file__).parent / "spec.yaml"` are treated as dynamic
by the static extractor. Declare those specs under `tool_sources` or provide a
local inventory
artifact when you want them resolved by the scanner.

## A Finding Is Intentional

Suppress it with a reason:

```yaml
checks:
  ignore:
    - check_id: SHIP-SCHEMA-BROAD-FREE-TEXT
      tool: support.search_kb
      reason: "Search query intentionally accepts free text."
```

Suppressed findings remain in JSON with `suppressed: true` and are excluded from active severity counts.

## A Risk Tag Is Wrong

Use `risk_overrides` to add or remove tags:

```yaml
risk_overrides:
  tools:
    refund_status_lookup:
      tags: ["read_only"]
      remove_tags: ["financial_action"]
      reason: "This endpoint only reads refund status."
```
