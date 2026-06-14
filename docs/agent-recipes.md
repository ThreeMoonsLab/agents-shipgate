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
agents-shipgate preflight --json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

For local uncommitted work, omit `--base`/`--head`. For committed PR/CI refs,
make the base ref available first because `verify` never fetches. Read
`agents-shipgate-reports/verifier.json` first and lead with `merge_verdict`,
`can_merge_without_human`, `first_next_action`, `fix_task`, and
`capability_review.top_changes[]`. Then read
`report.json.release_decision.decision`, which remains the only release gate.

Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules,
policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex
plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, run
`agents-shipgate preflight --json` or pass the proposed paths with
`--changed-files`. If `requires_human_review` is true, stop for a human.

Do not claim completion when `merge_verdict` is `blocked`,
`insufficient_evidence`, or `human_review_required` unless the user explicitly
accepts human review.

## Recipe 1 · First adoption helper

Use this when a repo doesn't yet have `shipgate.yaml` and the user wants a
scan-oriented first pass. The verifier-first path is
`verify --preview --json` →
`init --write --ci --agent-instructions=default --json` →
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

- `is_agent_project` — `true` when at least one Python framework
  scored ≥ 2.0 with a strong signal.
- `frameworks[]` — per-framework scores + evidence + candidate file
  paths.
- `agent_name_candidates[]` — ranked `{value, source}`. Source values:
  `Agent_name_literal` (highest), `ADK_name_field`, `workspace_dir`
  (lowest).
- `project_name_candidates[]` — same shape; `pyproject` source seeds
  `project.name` only.
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
- no `shipgate.yaml` already exists, AND
- the user did not explicitly request a scan.

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

- `manifest_status`: `"written"` | `"skipped_existing"` | `"not_attempted"`.
- `workflow.status` (when `--ci`): `"written"` | `"skipped_existing_target"`
  | `"skipped_cross_reference"`.
- `placeholders[]` — entries the template intentionally leaves as
  `CHANGE_ME` because no high-confidence signal was available. Each has
  a `path` (YAML-pointer-ish location) and `current` value. Replace
  these before scanning.
- `auto_detected.agent_name` — the value the manifest carries
  (`null` when the template fell back to `CHANGE_ME`; matches the YAML
  exactly).

`--ci` is orthogonal to `--write`: each gets its own overwrite-refusal.
Exit code is the max of per-action outcomes; manifest-error and
workflow-skip can co-occur.

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
