# Agents Shipgate · Agent Instructions

Authoritative instructions for AI coding agents (Claude Code, Codex, Cursor, Aider) working **with** this repository or a project that uses Agents Shipgate.

> If you are a human, the README and the [wiki](https://github.com/ThreeMoonsLab/agents-shipgate/wiki) are the right places to start. This file is optimized for agent ingest: short, copy-pasteable, machine-friendly.

---

## What this project is

Static release-readiness scanner for AI agent tool surfaces. Reads `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Google ADK Python/config files) and produces deterministic findings.

- **Inputs:** MCP · OpenAPI · OpenAI Agents SDK · Google ADK
- **Outputs:** Markdown · JSON · SARIF
- **Trust:** Static-by-default. No agent execution, tool calls, LLM calls, or network access.

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

## Agent mode

Every command supports JSON output for programmatic consumption:

```bash
agents-shipgate init --workspace . --write --json
agents-shipgate scan -c shipgate.yaml                    # already produces report.json
agents-shipgate doctor --json
agents-shipgate explain SHIP-POLICY-APPROVAL-MISSING --json
agents-shipgate list-checks --json
agents-shipgate self-check --json
agents-shipgate fixture list --json
```

Errors carry a structured `next_action` when run with `AGENTS_SHIPGATE_AGENT_MODE=1`:

```bash
$ AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c missing.yaml
Config error: Config file not found: missing.yaml
{"error": "config_not_found", "path": "missing.yaml", "next_action": "agents-shipgate init --workspace . --write"}
```

---

## When to use this tool

Use it when the user is:

- Writing or modifying an AI agent that calls tools (MCP servers, OpenAPI APIs, `@function_tool`-decorated Python, Google ADK)
- Adding a new tool, scope, or policy to an existing agent
- Preparing an agent for production promotion
- Wiring a release gate into PR CI

Do NOT use it for:

- General code linting (ruff, eslint, etc.)
- Runtime monitoring (use observability/tracing)
- Behavior testing (use evals)
- Stopping a tool call in flight (use a gateway or guardrail)

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

Always parse `agents-shipgate-reports/report.json`, not the markdown. Stable fields:

- `summary.{critical_count, high_count, medium_count, status}`
- `findings[].{id, fingerprint, check_id, severity, tool_name, evidence, recommendation, suppressed}`
- `baseline.{matched_count, new_count, resolved_count}`
- `tool_inventory[]`

The full schema is at [`docs/report-schema.v0.3.json`](docs/report-schema.v0.3.json) and what's-stable is documented in [STABILITY.md](STABILITY.md).

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

### Task 5 · Explain a finding

```bash
agents-shipgate explain SHIP-POLICY-APPROVAL-MISSING --json
```

Returns the full `CheckMetadata` with `id`, `category`, `default_severity`, `description`, `rationale`, `fires_when`, `evidence_fields`, `recommendation`.

---

## Schemas

| What | Path | Stable |
|---|---|---|
| Manifest schema | [`docs/manifest-v0.1.json`](docs/manifest-v0.1.json) | `0.1` |
| Report schema | [`docs/report-schema.v0.3.json`](docs/report-schema.v0.3.json) | `0.3` |
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
| `agents-shipgate scan` | `-c`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--no-plugins`, `--verbose` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json` |
| `agents-shipgate doctor` | `-c`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate list-checks` | `--json`, `--no-plugins` |
| `agents-shipgate baseline save` | `-c`, `--out` |
| `agents-shipgate fixture` | `list`, `run`, `copy`, `verify` |
| `agents-shipgate self-check` | `--json` |

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

- [`add-shipgate-to-repo.md`](prompts/add-shipgate-to-repo.md) — bootstrap a repo
- [`fix-top-finding.md`](prompts/fix-top-finding.md) — iterate on a single finding
- [`stabilize-strict-mode.md`](prompts/stabilize-strict-mode.md) — tune → baseline → promote
- [`triage-false-positive.md`](prompts/triage-false-positive.md) — override vs suppress decision

Slash commands for Claude Code: [`.claude/commands/shipgate.md`](.claude/commands/shipgate.md).

---

## Verification

After you (the agent) complete a task involving Agents Shipgate, verify:

1. `agents-shipgate self-check --json` returns `"ready": true`.
2. The user's `shipgate.yaml` has no `CHANGE_ME` placeholders.
3. A scan completes with exit code 0 (advisory mode) and writes `report.json`.
4. The user's repo `.gitignore` includes `agents-shipgate-reports/` (do not commit reports).
