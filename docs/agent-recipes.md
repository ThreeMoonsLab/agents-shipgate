# Agent recipes

Copy-pasteable workflows for AI coding agents (Claude Code, Codex, Cursor,
Aider) that need to drive `agents-shipgate` end-to-end without prompting
the user. Every command is read-only or schema-validated;
static-by-default, with audited exceptions pinned in
[`tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`](../tests/test_adapter_static_only.py).

> If you are a human, [`quickstart.md`](quickstart.md) is the friendlier
> entry point. This page is structured for agents that consume `--json`.

---

## Recipe 0 · Verify an agent-related PR

Use this before claiming completion on a PR or local diff that changes tools,
MCP/OpenAPI surfaces, prompts, permissions, policies, release gates, or
`shipgate.yaml`.

```bash
agents-shipgate verify --preview --json
agents-shipgate preflight --workspace . --plan - --json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

For local uncommitted work, omit `--base`/`--head`. For committed PR/CI refs,
make the base ref available first because `verify` never fetches. Read
`agents-shipgate-reports/agent-handoff.json` first and lead with
`control.state`, `gate.merge_verdict`, `gate.can_merge_without_human`,
`next_action`, `fix_task`, and `capability_review.top_changes[]`. Fall back to
`verifier.json` only for older installed CLIs that do not report runtime
contract 14.
Then read `report.json.release_decision.decision`, which remains the only
release gate.

Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules,
policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex
plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, run
`agents-shipgate preflight --workspace . --plan - --json` with a
`PreflightPlanV1` object. Legacy `--changed-files` remains available. Switch on
`control.state`. If it is `review_publishable`, a human must approve the merge
and you may still commit, push, and update the PR; if it is
`human_review_required`, stop for a human; if it is
`agent_action_required`, perform only the exact coding-agent action and command
in `control.next_action`.

Do not claim completion unless `control.state` is `complete`. Conversation-level
acknowledgement never changes control state; only a newly generated verifier
artifact can clear an obligation.

## Recipe 1 · First adoption helper

Use this when a repo doesn't yet have `shipgate.yaml` and the user wants a
scan-oriented first pass. The verifier-first path is
`verify --preview --json` →
`init --write --json` →
`verify --base origin/main --head HEAD`. The helper below remains useful when a
coding agent should also apply high-confidence manifest cleanup in the same
turn. Ongoing PR work should use Recipe 0.

```bash
agents-shipgate detect --json
agents-shipgate init --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches \
    --from agents-shipgate-reports/report.json \
    --confidence high --apply
```

### Step 1 · `detect --json` (read-only)

Consume the response to decide whether to proceed. Key fields:

- Detection silently skips common fixture corpus directories such as
  `fixtures/`, `_fixtures/`, `__fixtures__/`, `testdata/`, `test_data/`,
  `test-fixtures/`, `test_fixtures/`, `golden/`, and `goldens/` when they
  are below the selected workspace. Point `--workspace` directly at a
  fixture project if you intentionally want to classify that fixture itself.
- `is_agent_project` — `true` when at least one Python framework
  scored ≥ 2.0 with a strong signal.
- `frameworks[]` — per-framework scores + evidence + candidate file
  paths.
- `agent_name_candidates[]` — ranked best-first, each
  `{value, source, role, path, rank_score, selectable, rationale[]}`.
  **Take the first entry whose `selectable` is `true`** — that is the value
  `init` writes as `agent.name`. When none is selectable the manifest keeps
  its `CHANGE_ME` placeholder rather than asserting an identity nothing
  reliably declares. Ordering is decided by, in effect:
  - `role` — `root_agent` (bound as `App(root_agent=…)`, or assigned to the
    conventional `root_agent` symbol) outranks `agent`, which outranks
    `sub_agent` (named inside another agent's `sub_agents=[…]` /
    `handoffs=[…]`). `workspace_dir` is the directory-name fallback and is
    never selectable.
  - `path` — a name declared in product code outranks one declared in test
    code, which names fixtures. This dominates: a test fixture that builds
    an `App(root_agent=…)` still ranks below a plain agent the shipped code
    declares.
  - corroboration — a value the project name independently agrees with
    ranks above one only a single site declares.
  - a quality floor — values under three significant characters, and
    generic scaffolding names (`agent`, `foo`, `test`, …), are ranked last
    and marked `selectable: false`.

  One rule overrides all four: if the workspace declares an application root
  whose name cannot be resolved statically — a dynamic expression, a factory
  call, a symbol bound more than once — then **nothing** is selectable, and
  the `rationale[]` says why. Anything still ranked is by construction not
  the root, so writing it would declare a worker as the reviewed identity.

  `rationale[]` states which of those applied, so a ranking change is
  visible in the output rather than silently changing what the manifest
  claims. `name=` values that come from a module constant or an
  `os.environ.get("…", "…")` default in the same package are resolved
  statically (one hop, no code executed) and say so in `rationale[]`.

  All of this reads Python's binding rules or declines — a spelling is never
  taken as provenance. `Agent`/`LlmAgent`/`App` are resolved through the
  binding that reaches *the call site*: a framework constructor imported
  under an alias is recognised, and one shadowed by a local `def`/`class`,
  bound only after the call, bound conditionally, or replaced through an
  attribute (`adk.Agent = fake`) is not. Dotted spellings are held to the
  same standard — the head must prove a framework module.
  Left unresolved rather than guessed: a symbol bound more than once
  anywhere in the file; one assigned under an `if`/`try`/loop; one rebound
  in an enclosing scope (a function body executes when it is called, not
  where it is written);
  one whose import could resolve to two different in-workspace modules; an
  `os.getenv` spelling that is not a provably unshadowed stdlib import; and
  anything at all in a file carrying `from x import *` — until a later
  explicit binding re-establishes what a spelling means. Bindings that carry
  no assignment count too: `del`, `class`, `except … as`, `case`, and a
  `global`/`nonlocal` store routed to another scope all retire the agent a
  name used to hold. Scopes follow Python's own — comprehensions have their
  own, definition headers (defaults, decorators, annotations, class bases)
  are evaluated in the enclosing one, and a root declared inside a
  conditionally defined function is contingent on that branch.
- `project_name_candidates[]` — `{value, source}` only. Project names have
  no hierarchy to rank, so they carry none of the fields above. The
  `pyproject` source seeds `project.name`, never `agent.name`.
- `suggested_sources[]` — MCP/OpenAPI files matched by glob AND accepted
  by the real input adapters, so `init` never writes a `tool_sources`
  entry that `scan` rejects at parse time. These do NOT bump
  `is_agent_project` on their own.
- `excluded_sources[]` — `{type, path, reason}` for glob matches the
  input adapters reject (e.g. an `mcpServers`-style host config such as
  a Cursor plugin `mcp.json`, or a Swagger 2.0 document). Do not add
  these to `tool_sources`; the `reason` says what `scan` would fail on.
- `codex_plugin_candidates[]` — Codex plugin package or marketplace
  artifacts matched by convention. These also do NOT bump
  `is_agent_project` on their own.

**Stop condition.** Stop and skip `init` only when ALL of:

- `is_agent_project` is `false`, AND
- `suggested_sources` is empty, AND
- `codex_plugin_candidates` is empty, AND
- `python_parse_truncated` is `false` — each negative above is a claim about
  the whole workspace, and a run whose Python parse stopped at its cap read
  only part of one. This is the raw parse bit, not `agent_scope_truncated`,
  which additionally requires more than one candidate scope, AND
- no `shipgate.yaml` already exists, AND
- the user did not explicitly request a scan.

A payload that does not carry every one of those keys leaves the condition
unevaluable — `trigger` reports `stop_conditions_evaluated: false` and infers
no stop. Re-run `detect` with the current CLI rather than reading an absent
key as `false`.

Otherwise proceed. MCP/OpenAPI-only tool-surface repos and Codex plugin
package repos surface as `is_agent_project: false` but should still be
onboarded — their sources will land in `tool_sources` during `init`.

### Step 2 · `init --write --ci --json`

Auto-detection runs again inside `init` and writes:

- `shipgate.yaml` with `tool_sources` populated per detected framework
  candidate file.
- `.github/workflows/agents-shipgate.yml` (if `--ci` is set; refuses
  to overwrite an existing workflow file or one that already calls
  `ThreeMoonsLab/agents-shipgate@*` from a sibling workflow).

Key response fields:

- `manifest_status`: `"written"` | `"skipped_existing"` |
  `"refused_unresolved_scope"` | `"not_attempted"`.
- `workflow.status` (when `--ci`): `"written"` | `"skipped_existing_target"`
  | `"skipped_cross_reference"`.
- `placeholders[]` — entries the template intentionally leaves as
  `CHANGE_ME` because no high-confidence signal was available. Each has
  a `path` (YAML-pointer-ish location) and `current` value. Replace
  these before scanning.
- `auto_detected.agent_name` — the value the manifest carries
  (`null` when the template fell back to `CHANGE_ME`; matches the YAML
  exactly).
- `auto_detected.agent_scope`: `"single"` | `"ambiguous"` | `"unknown"`,
  with `auto_detected.agent_project_candidates[]` naming every self-contained
  project (project-marker directory) that defines an agent. `"unknown"` means
  discovery hit its Python-file cap in a workspace with several project roots,
  so the verdict would otherwise have depended on which files were read
  first.
- `auto_detected.python_parse_truncated`: whether the Python parse stopped at
  its cap at all. Every whole-workspace negative — `is_agent_project: false`
  included — is unsafe to act on while this is `true`. The recovery is
  mechanical and the emitted `next_actions[0]` carries it:
  `detect --max-python-files <workspace_signals.python_file_total> --json`, a
  bound that covers every Python file and so cannot hit the cap again.
- `auto_detected.agent_scope_truncated`: whether that candidate list is an
  enumeration or a lower bound. `true` means the Python parse stopped at its
  cap in a workspace holding more than one candidate project scope, so any
  project in the part of the tree that was not read is missing from the list —
  do **not** conclude a project is absent from it. Re-run
  `detect --max-python-files <n> --json` first.
- `auto_detected.workspace_signals.project_root_count` bounds that claim: an
  uncapped, filename-only census of the directories that could be a manifest
  scope (every project-marker directory, plus the workspace root itself, which
  is a candidate whether or not it carries a marker). `init` emits the same
  block `detect` does, so the number its refusal message quotes is readable
  structurally.

`--ci` is orthogonal to `--write`: each gets its own overwrite-refusal.
Exit code is the max of per-action outcomes; manifest-error and
workflow-skip can co-occur. The workflow lands at the repository root —
GitHub loads workflows from nowhere else — named `agents-shipgate.yml` for a
root manifest and `agents-shipgate-<project>.yml` for a scoped one, because
the action takes a single `config` scalar and one shared file would leave
every project after the first ungated. Read `workflow.path`.

`refused_unresolved_scope` (exit `2`) is the one outcome where **nothing**
is written — not the manifest, not the workflow, not the agent-instruction
snippets, not the reports `.gitignore` block. It fires when agents live in
more than one project under this workspace (because one `agent.name` and one
`declared_purpose` cannot describe them all) and when discovery was capped
before it could tell. Re-run with `--workspace` pointed at one of
`agent_project_candidates[].path` rather than retrying the same command — the
emitted `next_actions[]` commands repeat whatever setup flags you passed.
`--allow-unresolved-scope` accepts a single manifest for the workspace as a
whole, and `--minimal` is never scope-gated because it adopts no detected name
or tool surface.

### Step 3 · `scan -c shipgate.yaml --suggest-patches --format json`

Writes to `agents-shipgate-reports/report.json`. Read it, walk
`findings[]` filtering on `suppressed`. Per-finding fields you can rely
on today:

- `check_id`, `title`, `severity`, `category`, `evidence`,
  `confidence`, `recommendation`.
- `patches[]` (only when `--suggest-patches` is set) — list of
  patch objects with `kind` ∈ `{set_pointer, append_pointer,
  remove_pointer, manual}`. Non-manual patches additionally carry
  `confidence` ∈ `{low, medium, high}`, `target_file`, `pointer`,
  `target_format`, `rationale`, `target_sha256`.
- `manifest_dir` (top-level on the report) — absolute path to the
  directory containing `shipgate.yaml`. `apply-patches` enforces a
  containment check against this.

When `--suggest-patches` is set, every active (unsuppressed) finding
has at least one patch. Manual-only findings (e.g. trace approval
flips, per-check policy decisions) carry a single `ManualPatch` with
`instructions` instead of a machine-applicable patch.

Optional dynamic-validation handoff:

```bash
agents-shipgate scenario suggest \
    --from agents-shipgate-reports/report.json \
    --out agents-shipgate-reports/suggested-scenarios.yaml
```

This YAML is a concrete per-finding/per-tool fan-out of
`report.json.suggested_scenarios[]`, not a separate scenario engine.
Suppressed findings are omitted; baseline-matched findings remain because
they are accepted debt, not resolved risk.

### Step 4 · `apply-patches --confidence high --apply`

Default `--confidence high` only auto-applies patches whose `confidence`
field is `"high"`. Today that's the 3 stale-manifest removals
(`SHIP-MANIFEST-STALE-{SUPPRESSION,POLICY,RISK-OVERRIDE}`). Scope
coverage appends ship at `medium` and require explicit
`--confidence medium` to apply.

`apply-patches` is dry-run by default — `--apply` is required to
mutate files. Containment-checked: any `target_file` outside
`report.manifest_dir` aborts with exit code 5 before SHA verification.

### Step 5 (optional) · Summarize for the user

When the flow completes, summarize `report.json`:

- `release_decision.decision` (`"blocked" | "review_required" | "insufficient_evidence" | "passed"`)
  — the v0.8+ release-gate signal (`insufficient_evidence` added v0.14).
  Prefer this over `summary.status`, which stays baseline-blind for
  backwards compat. Switch on the value with a `review_required`
  fallback for unknown future values.
- `release_decision.reason` (one-sentence explanation).
- Top 3 active critical/high findings with their `check_id`,
  `tool_name` (when present), and `recommendation`.
- Whether any patches were applied (count from
  `apply-patches --json` output's `files`).

Link findings back to [`docs/checks.md#<id>`](checks.md) so the user
can read full check rationale.

---

## Recipe 2 · Add Shipgate to a repo that already has tool surfaces

Same as Recipe 1, but `detect` may report `is_agent_project: false`
when the repo only ships MCP exports or OpenAPI specs. Per the soft
stop rule above, proceed anyway when `suggested_sources` is non-empty.

`init` will populate `tool_sources` from those globs. The rest of the
flow (steps 2-5) is identical.

### First-real-repo recovery rules

When the first repo scan does not produce useful tools, follow these
rules before changing code:

- If `detect --json` has MCP/OpenAPI `suggested_sources`, continue to
  `init` even when `is_agent_project` is `false`.
- If `doctor` shows zero tools, inspect `tool_sources[].path`, MCP
  `tools[]`, OpenAPI `paths`, optional source warnings, and dynamic
  ADK/MCP warnings.
- If tools are created by factories, wrappers, runtime imports, or
  dynamic ADK/MCP toolsets, provide an explicit MCP export, OpenAPI
  spec, or local tool inventory artifact.
- Replace every `CHANGE_ME` value in `shipgate.yaml` before scanning;
  use the prompt, main agent file, README, or owner-provided context.
- Agents Shipgate requires Python 3.12+. If the project runtime is
  older, install the CLI outside the project env with `pipx` or `uv`.
- Ensure `agents-shipgate-reports/` is listed in `.gitignore`.

---

## Recipe 3 · Re-scan after editing the manifest

When the user has already replaced `CHANGE_ME` placeholders or added
policies:

```bash
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches \
    --from agents-shipgate-reports/report.json \
    --confidence high --apply
```

`run_id` is deterministic for the same input — if the report's
`run_id` is unchanged from the previous run, nothing semantic about
the manifest+tool-surface changed.

---

## Recipe 4 · Suppress a check or finding

When a finding is a known false positive, edit `shipgate.yaml`:

```yaml
checks:
  ignore:
    - check_id: SHIP-DOC-MISSING-DESCRIPTION
      tool: support_lookup_v2  # optional; omit to suppress for ALL tools
      reason: "Tool description matches the upstream OpenAPI summary."
```

`reason` is required — empty reasons fail manifest validation. Re-run
`scan` to confirm the finding is gone (it will appear in `findings[]`
with `suppressed: true` rather than disappearing from the report).

If you suppress a check that no longer fires, the next scan emits
`SHIP-MANIFEST-STALE-SUPPRESSION` — auto-removable via
`apply-patches`.

---

## Recipe 5 · Add Shipgate to CI without changing existing workflows

```bash
agents-shipgate init --workspace . --ci  # no --write
```

Without `--write`, the manifest is printed to stdout (don't write a
new one). With `--ci`, the workflow file is still written orthogonally
unless an existing workflow already references the action — in which
case `workflow.status: "skipped_cross_reference"` and the path of the
existing workflow is reported in `cross_reference_path`.

---

## Output handling

- Always pass `--json` (where supported) and parse the result. The
  human-readable stdout is unstable; the JSON shape is the contract.
- `scan` does not have `--json`; instead pass `--format json` and read
  `agents-shipgate-reports/report.json`.
- Errors emit a structured `next_action` JSON line on stderr when
  `AGENTS_SHIPGATE_AGENT_MODE=1` is set. Surface that path to the user
  rather than scraping prose.

## Pre-flight reminder

`agents-shipgate-reports/` is a local artifact directory. Before
committing, ensure it's listed in `.gitignore`:

```gitignore
agents-shipgate-reports/
```

`init` does not touch `.gitignore` — leave that to the user or follow
up with an explicit edit.

---

## Reference

- [`docs/agent-autofix-boundary.md`](agent-autofix-boundary.md) — what
  an agent may do mechanically vs. what must defer to a human reviewer.
- [`docs/report-reading-for-agents.md`](report-reading-for-agents.md) —
  reader's primer for `agents-shipgate-reports/report.json`.
- [`docs/checks.md`](checks.md) — full check catalog with rationale
- [`docs/autofix-policy.md`](autofix-policy.md) — which findings are
  safe to apply, which need review, and how `apply-patches --confidence`
  filters them
- [`docs/minimal-real-configs.md`](minimal-real-configs.md) —
  framework-specific minimal manifests
- [`AGENTS.md`](../AGENTS.md) — top-level agent instructions, install,
  trigger table
