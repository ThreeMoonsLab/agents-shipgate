# Agents Shipgate · Agent Instructions

Authoritative instructions for AI coding agents (Claude Code, Codex, Cursor, Aider) working **with** this repository or a project that uses Agents Shipgate.

> If you are a human, the README and the [wiki](https://github.com/ThreeMoonsLab/agents-shipgate/wiki) are the right places to start. This file is optimized for agent ingest: short, copy-pasteable, machine-friendly.

---

## What this project is

Static release-readiness gate for AI agent tool surfaces. Reads `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Anthropic Messages API tool/prompt artifacts, Google ADK Python/config files, LangChain/LangGraph Python files, CrewAI Python files, n8n workflow JSON/stubs) and produces deterministic findings.

- **Inputs:** MCP · OpenAPI · OpenAI Agents SDK · Anthropic Messages API · Google ADK · LangChain/LangGraph · CrewAI · n8n
- **Outputs:** Markdown · JSON · SARIF
- **Trust:** Static-by-default. No agent execution, tool calls, LLM calls, or network access.

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

> Static release-readiness gate for AI agent tool surfaces.

This single sentence is the source of truth for the GitHub repo description, [README.md](README.md), the [wiki Home page](https://github.com/ThreeMoonsLab/agents-shipgate/wiki/Home), and the marketing site `<meta name="description">`. Keep them in sync.

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

In a repo that contains an agent and its tools:

```bash
agents-shipgate init --workspace . --write
agents-shipgate scan -c shipgate.yaml
```

Reports land at `agents-shipgate-reports/report.{md,json}`.

To verify your install on a known fixture without writing any YAML:

```bash
agents-shipgate fixture run support_refund_agent
```

---

## Single-turn agent flow (v0.6+)

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
  means stop early.
- **`init`** — auto-detects by default. `--ci` writes
  `.github/workflows/agents-shipgate.yml`; orthogonal to `--write`. Use
  `--minimal` for the pre-v0.6 CHANGE_ME-heavy template.
  `--agent-instructions=all` (or a comma-separated subset of
  `agents-md,claude-md,cursor,pr-template`) renders agent-facing snippets to
  stdout; combined with `--write` it commits them to the target repo via
  managed `<!-- agents-shipgate:start -->` markers (idempotent — safe to
  rerun). Strict CI and baselines remain opt-in human decisions; the flag
  emits advisory guidance only.
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

Errors carry a structured `next_action` (single string, back-compat) and `next_actions` (ranked list) when run with `AGENTS_SHIPGATE_AGENT_MODE=1`:

```bash
$ AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c missing.yaml
Config error: Config file not found: missing.yaml
{"error": "config_error", "message": "...", "next_action": "agents-shipgate detect --workspace . --json", "next_actions": [{"kind": "command", "command": "agents-shipgate detect --workspace . --json", "why": "..."}, {"kind": "command", "command": "agents-shipgate init --workspace . --write", "why": "..."}]}
```

The full set of error kinds emitted in agent mode: `config_error`, `config_already_exists`, `input_parse_error`, `unknown_check_id`, `unknown_fingerprint`, `other_error`, `internal_error`, `malformed_patch`. `unknown_fingerprint` is emitted by `explain-finding` when the fingerprint doesn't match any entry in the supplied report; the payload includes `suggestion` (a close-match fingerprint, when one exists) and `source_report`.

The machine-readable catalog of error kinds — exit codes, typical causes, additional fields per kind, recovery hints — lives at [`docs/errors.json`](docs/errors.json). Pre-fetch it once and pattern-match the `error` field instead of re-deriving the recovery vocabulary from this prose.

`detect --json` and each `doctor --json` payload also carry `diagnostics: [...]` and `next_actions: [...]` fields. `next_action` (single string) remains the rank-1 action projected to a string; `next_actions` is the ranked list with `kind`, `command|path`, `why`, and `expects` fields. See [docs/diagnostics.md](docs/diagnostics.md) for the full catalog and schema.

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

- Writing or modifying an AI agent that calls tools (MCP servers, OpenAPI APIs, `@function_tool`-decorated Python, Google ADK, LangChain/LangGraph, CrewAI, n8n)
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
| Adds/changes Codex plugin manifests, marketplace files, `.app.json`, `.mcp.json`, or `SKILL.md` files | Yes |
| Adds/changes `@function_tool`/`@tool` decorators (LangChain, CrewAI, OpenAI Agents SDK) | Yes |
| Adds/changes n8n workflow JSON, credential stubs, or n8n tool inventories | Yes |
| Edits `prompts/`, `policies/`, or `permissions.scopes` in `shipgate.yaml` | Yes |
| Adds/edits `.github/workflows/agents-shipgate.yml` or related CI | Yes |
| Pure read-only doc/test changes with no manifest impact | Skip |
| Refactor with no behavior change to tools or policies | Skip (or dry-run only) |

Two implicit triggers also fire even when no row above matches:

- **Repo already opted in (shipgate.yaml present in the workspace)** — run on every PR; the manifest's existence is the opt-in.
- **(Optional) Refactor or framework upgrade that may shift the extracted tool surface** — dry-run only; bumping `openai-agents`, `langchain`, `crewai`, or `google-adk` can change AST extraction even without app-code edits.

A machine-readable mirror of these triggers lives at [`docs/triggers.json`](docs/triggers.json). Coding agents that have not yet adopted Shipgate can fetch the file (raw URL: `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/triggers.json`), apply the rules to a PR diff, and decide whether to propose `agents-shipgate detect`. The catalog is stable for `0.x` and pinned by the public-surface contract test against this prose table — if you change a row above, update `triggers.json` in the same commit. To verify a path list locally:

```bash
python -m agents_shipgate.triggers shipgate.yaml prompts/refund.md
python -m agents_shipgate.triggers --list-rules --json
```

**Stop conditions.** Stop and do not run `init` only when **all** of these hold:

- `agents-shipgate detect --json` returns `is_agent_project: false`, AND
- `suggested_sources` is empty (no MCP/OpenAPI hits flowing in as `mcp` or `openapi`), AND
- `codex_plugin_candidates` is empty (no Codex plugin package or marketplace hits), AND
- no `shipgate.yaml` already exists in the workspace, AND
- the user did not explicitly request a scan.

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

The canonical field list — `release_decision`, `capability_facts` / `declared_intentions` / `misalignments` / `release_consequence` / `suggested_scenarios`, and `tool_surface_facts` / `tool_surface_diff` — lives in [`docs/agent-contract-current.md`](docs/agent-contract-current.md#read-these-first-for-release-gating). It updates first when the contract bumps; this file links to it instead of restating the field set.

Other stable top-level fields:

- `summary.{critical_count, high_count, medium_count, status}` (status preserved for v0.7 compat — see note below)
- `findings[].{id, fingerprint, check_id, severity, tool_name, evidence, recommendation, suppressed}`
- `findings[].{autofix_safe, requires_human_review, suggested_patch_kind, docs_url}` (v0.7+)
- `findings[].patches[]` (v0.6+, only when scan ran with `--suggest-patches`)
- `baseline.{matched_count, new_count, resolved_count}`
- `tool_inventory[]`
- `codex_plugin_surface` (v0.13+, static Codex plugin package/marketplace facts)

The full schema is at [`docs/report-schema.v0.13.json`](docs/report-schema.v0.13.json) (current; emitted reports carry `report_schema_version: "0.13"`). v0.13 adds the `codex_plugin_surface` block over v0.12's per-finding `agent_action` enum and top-level `agent_summary` block. Older reports validate against [`docs/report-schema.v0.12.json`](docs/report-schema.v0.12.json) (frozen reference). What's-stable is documented in [STABILITY.md](STABILITY.md).

**Release gating signal**: prefer `release_decision.decision` (`"blocked" | "review_required" | "passed"`) over `summary.status`. The new field is **baseline-aware** — a baseline-matched critical surfaces in `release_decision.review_items` (accepted debt), not `release_decision.blockers`. `summary.status` stays baseline-blind for v0.7 compatibility, so a baseline-matched-only critical produces both `summary.status = "release_blockers_detected"` AND `release_decision.decision = "review_required"` (intentional divergence — see [STABILITY.md](STABILITY.md#release_decisiondecision-vs-summarystatus)).

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

### Task 4 · Save a baseline before enabling strict CI

```bash
agents-shipgate baseline save -c shipgate.yaml --out .agents-shipgate/baseline.json
```

Then in CI:

```bash
agents-shipgate scan -c shipgate.yaml \
  --baseline .agents-shipgate/baseline.json \
  --ci-mode strict --fail-on critical,high
```

Strict mode fails CI only on **new** findings (those not in the baseline).

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
[`docs/report-schema.v0.13.json`](docs/report-schema.v0.13.json) (current).
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
| Report schema (current) | [`docs/report-schema.v0.13.json`](docs/report-schema.v0.13.json) | `0.13` |
| Report schema (v0.12 frozen reference) | [`docs/report-schema.v0.12.json`](docs/report-schema.v0.12.json) | `0.12` |
| Report schema (v0.11 frozen reference) | [`docs/report-schema.v0.11.json`](docs/report-schema.v0.11.json) | `0.11` |
| Report schema (v0.10 frozen reference) | [`docs/report-schema.v0.10.json`](docs/report-schema.v0.10.json) | `0.10` |
| Report schema (v0.9 frozen reference) | [`docs/report-schema.v0.9.json`](docs/report-schema.v0.9.json) | `0.9` |
| Report schema (v0.8 frozen reference) | [`docs/report-schema.v0.8.json`](docs/report-schema.v0.8.json) | `0.8` |
| Report schema (v0.7 frozen reference) | [`docs/report-schema.v0.7.json`](docs/report-schema.v0.7.json) | `0.7` |
| Report schema (v0.6 frozen reference) | [`docs/report-schema.v0.6.json`](docs/report-schema.v0.6.json) | `0.6` |
| Packet schema (Release Evidence Packet) | [`docs/packet-schema.v0.3.json`](docs/packet-schema.v0.3.json) | `0.3` |
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
| `agents-shipgate scan` | `-c`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--diff-from`, `--no-plugins`, `--verbose`, `--packet`/`--no-packet`, `--packet-format` |
| `agents-shipgate evidence-packet` | `--from`, `--out`, `--format`, `--json` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json` |
| `agents-shipgate doctor` | `-c`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate contract` | `--json` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate explain-finding` | `<fingerprint>`, `--from`, `--no-plugins`, `--json` |
| `agents-shipgate bootstrap` | `--workspace`, `--confidence`, `--no-ci`, `--no-apply`, `--json` |
| `agents-shipgate list-checks` | `--json`, `--no-plugins` |
| `agents-shipgate baseline save` | `-c`, `--out` |
| `agents-shipgate fixture` | `list`, `run`, `copy`, `verify` |
| `agents-shipgate self-check` | `--json` |

### Release Evidence Packet (v0.3)

`scan` emits a reviewer-shaped Release Evidence Packet alongside `report.{md,json}` by default. The packet is a curated synthesis with fixed reviewer sections derived from the in-memory scan; outputs land at `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` when the optional `[pdf]` extras are installed). For the field-level packet contract, see [`docs/agent-contract-current.md`](docs/agent-contract-current.md#read-these-for-release-review) and [STABILITY.md §Release Evidence Packet](STABILITY.md#release-evidence-packet-v03).

```bash
pipx install agents-shipgate                  # md, json, html packet outputs
pipx install 'agents-shipgate[pdf]'           # adds packet.pdf via weasyprint
agents-shipgate scan -c shipgate.yaml         # default: emit packet
agents-shipgate scan -c shipgate.yaml --no-packet                    # skip
agents-shipgate scan -c shipgate.yaml --packet-format md,json,html,pdf
# Re-render from the existing packet (full fidelity):
agents-shipgate evidence-packet --from agents-shipgate-reports/packet.json --format html,pdf
# Or rebuild from a CI-archived report.json (degraded — see §10 of the output):
agents-shipgate evidence-packet --from agents-shipgate-reports/report.json --format md,html
```

Rules of the packet contract (do not break in 0.x):
- The packet is **derived from JSON** (the in-memory scan) and is a **local artifact only** — no hosted/SaaS view.
- §10 ("What this packet did NOT prove") **always** lists the four canonical disclaimers verbatim — prompt robustness, runtime behavior, model correctness, adversarial resistance — regardless of run state.
- All reviewer sections are **always present** in `packet.json`, including `tool_surface_diff`. Sections that have no evidence render with `status: "not_declared"` (or `"informational"`) and refer the reviewer to §10.
- §8 (`human_in_the_loop`) always carries `runtime_control_disclaimer`. When local validation artifacts are available, `source_provenance[]` traces approval traces, override logs, high-risk exclusions, promotion criteria, and manifest requirements.
- §1 verdict (`PASSED` / `REVIEW REQUIRED` / `BLOCKED`) derives from `release_decision.decision` only. CI behavior (`fail_policy`) is rendered separately as metadata, not as the verdict source.
- The current manifest schema does **not** model `agent.memory`. §7 always renders "not declared, see §10" until a future schema bump adds the field.

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

- Run `python -m ruff check .` and `python -m pytest` before committing.
- Bumping a check's behavior requires updating the test suite and any golden fixtures under `samples/*/expected/`.
- New checks must include: code in `src/agents_shipgate/checks/`, metadata in `checks/registry.py:CHECK_METADATA`, a test in `tests/`, and a row in `docs/checks.md`.
- Do not change check IDs in published versions; always add new ones.
- If you regenerate the JSON schemas, run `python scripts/generate_schemas.py` and commit `docs/manifest-v0.1.json` + `docs/checks.json`.

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
- [`docs/agents/use-with-codex.md`](docs/agents/use-with-codex.md) — drop the canonical `AGENTS.md` snippet (from [`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md)) into your repo. Codex reads `AGENTS.md` natively. Codex Skills (`.agents/skills/<name>/SKILL.md` repo-scoped or `$HOME/.agents/skills/<name>/SKILL.md` user-scoped; invoked with `/skills` or `$<name>`) are also supported, but this repo does not currently ship a Codex skill bundle — the parallel to [`skills/agents-shipgate/`](skills/agents-shipgate/) has not been authored. The `AGENTS.md` snippet is the minimal on-ramp that works today.
- [`docs/agents/use-with-cursor.md`](docs/agents/use-with-cursor.md) — drop the canonical `.cursor/rules/agents-shipgate.mdc` auto-attach rule (from [`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md)) into your repo. The rule fires whenever a chat touches `shipgate.yaml`, an MCP/OpenAPI spec, a tool JSON, or a `.py` file.

---

## Verification

After you (the agent) complete a task involving Agents Shipgate, verify:

1. `agents-shipgate self-check --json` returns `"ready": true`.
2. `agents-shipgate contract --json` matches the installed CLI contract you expect.
3. The user's `shipgate.yaml` has no `CHANGE_ME` placeholders.
4. A scan completes with exit code 0 (advisory mode) and writes `report.json`.
5. The user's repo `.gitignore` includes `agents-shipgate-reports/` (do not commit reports).
