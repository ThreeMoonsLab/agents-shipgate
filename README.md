# Agents Shipgate

[![PyPI](https://img.shields.io/pypi/v/agents-shipgate)](https://pypi.org/project/agents-shipgate/)
[![Python](https://img.shields.io/pypi/pyversions/agents-shipgate)](https://pypi.org/project/agents-shipgate/)
[![GitHub Action](https://img.shields.io/badge/GitHub%20Action-marketplace-blue)](https://github.com/marketplace/actions/agents-shipgate)
[![License](https://img.shields.io/pypi/l/agents-shipgate)](LICENSE)
[![CI](https://github.com/ThreeMoonsLab/agents-shipgate/actions/workflows/ci.yml/badge.svg)](https://github.com/ThreeMoonsLab/agents-shipgate/actions/workflows/ci.yml)

Static release-readiness scanner for AI agent tool surfaces.
**Inputs:** OpenAI Agents SDK · Google ADK · MCP · OpenAPI · OpenAI Agents API.
**Outputs:** Markdown · JSON · SARIF.

## Install

```bash
pipx install agents-shipgate
# or in CI:
# - uses: ThreeMoonsLab/agents-shipgate@v0.3.0
```

## 60-second run

```bash
agents-shipgate init --workspace . --write
agents-shipgate scan -c shipgate.yaml
# writes agents-shipgate-reports/report.md and report.json
```

To verify your install on a known fixture without writing any YAML:

```bash
agents-shipgate fixture run support_refund_agent
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass (advisory mode or strict-no-blockers) |
| `2` | Manifest config error |
| `3` | Input parse error (file missing, malformed, path traversal blocked) |
| `4` | Other Agents Shipgate error |
| `20` | Strict-mode gate failure |

## For coding agents

Agents Shipgate is designed to be agent-friendly. If you're a coding agent (Claude Code, Codex, Cursor, Aider) reading this repo:

- **[`AGENTS.md`](AGENTS.md)** — canonical agent-facing instructions: install, run, common tasks, JSON-mode flags, error semantics
- **[`STABILITY.md`](STABILITY.md)** — what won't break across `0.x` versions
- **[`prompts/`](prompts/)** — reusable prompts for common workflows
- **[`docs/manifest-v0.1.json`](docs/manifest-v0.1.json)** + **[`docs/report-schema.v0.3.json`](docs/report-schema.v0.3.json)** — JSON Schemas for live editor validation
- **[`docs/checks.json`](docs/checks.json)** — machine-readable check catalog

Every command has a `--json` form. Errors emit a structured `next_action` line on stderr when `AGENTS_SHIPGATE_AGENT_MODE=1`.

## Why this exists

Once an AI agent can refund, email, cancel, deploy, or modify a record, every tool change becomes a release event. Code review catches code; eval suites catch behavior; observability catches runtime. None of them answer the release question: *given the tool surface declared in this PR, do we have explicit approval policies, scope coverage, idempotency evidence, and review readiness for every action?*

Shipgate produces a deterministic answer to that question, before promotion.

## Findings Gallery

The bundled support-refund fixture demonstrates the kind of release risks Agents Shipgate is designed to surface:

```text
## Agents Shipgate

Status: Release blockers detected
Critical: 2 - High: 14 - Medium: 2
Human review: recommended

Top findings:
1. stripe.create_refund lacks a declared approval policy
2. stripe.create_refund lacks idempotency evidence
3. Manifest declares broad permission scopes
```

- `stripe.create_refund` lacks a declared approval policy, so a financial action could ship without an explicit human review gate.
- `stripe.create_refund.amount` lacks a maximum bound, weakening blast-radius control.
- `stripe.create_refund` lacks idempotency evidence while retry behavior is known, risking duplicate refunds.
- `wildcard_mcp_tools.*` exposes a wildcard tool surface, making review incomplete.
- `gmail.send_customer_email` overlaps a prohibited external-communication action without a matching confirmation policy.

## Why Not Just...

| Alternative | Gap Agents Shipgate Covers |
| --- | --- |
| Unit tests | Tests usually validate code paths, not the released tool surface and declared policies. |
| Code review | Reviewers miss generated specs, MCP exports, broad scopes, and missing approval policies. |
| Runtime traces | Useful later, but they arrive after behavior exists. Agents Shipgate runs before promotion. |
| Nothing | Tool-surface drift becomes a production surprise. |

## Quickstart

Use Agents Shipgate as a [GitHub Action](#github-action) on every PR, or run the CLI locally.

Install the published package:

```bash
python -m pip install agents-shipgate
agents-shipgate --version
```

Install from a source checkout when developing locally:

```bash
python -m pip install -e ".[dev]"
agents-shipgate init --workspace . --write
agents-shipgate doctor --config shipgate.yaml
agents-shipgate scan --config shipgate.yaml
```

Or install directly from GitHub when testing the latest unreleased source:

```bash
python -m pip install "git+https://github.com/ThreeMoonsLab/agents-shipgate@main"
```

Try the bundled fixture:

```bash
agents-shipgate scan --config samples/support_refund_agent/shipgate.yaml
agents-shipgate scan --config samples/simple_openai_api_agent/shipgate.yaml
agents-shipgate scan --config samples/google_adk_agent/shipgate.yaml
agents-shipgate scan --config samples/clean_read_only_agent/shipgate.yaml
```

## CI Behavior

CI is advisory by default:

```bash
agents-shipgate scan --config shipgate.yaml --ci-mode advisory
```

Strict mode exits with code `20` only when unsuppressed critical findings exist.
Configuration, input parsing, and internal tool errors use `2`, `3`, and `4` respectively:

```bash
agents-shipgate scan --config shipgate.yaml --ci-mode strict
```

For existing projects, save the current reviewed findings as a local baseline and
fail strict CI only on new unsuppressed findings:

```bash
agents-shipgate baseline save --config shipgate.yaml --out .agents-shipgate/baseline.json
agents-shipgate scan --config shipgate.yaml --baseline .agents-shipgate/baseline.json --ci-mode strict
```

Teams can override severities and CI failure thresholds:

```yaml
checks:
  severity_overrides:
    SHIP-AUTH-MISSING-SCOPE: critical
ci:
  fail_on:
    - critical
    - high
```

## Google ADK

v0.3 adds static Google ADK support for Python entrypoints and Agent Config YAML. The adapter detects `LlmAgent`/`Agent` definitions, function tools, `OpenAPIToolset`, `McpToolset`, callbacks, plugins, sub-agents, eval references, and explicit local tool inventories without importing ADK code.

```yaml
version: "0.1"
project:
  name: adk-support-agent
agent:
  name: support-agent
  declared_purpose:
    - handle support cases
environment:
  target: production_like
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
google_adk:
  eval_sets:
    - evals/support.eval.json
  tool_inventories:
    - inventories/adk-mcp-tools.json
```

Dynamic ADK toolsets produce warnings or findings unless you provide explicit MCP, OpenAPI, or local tool inventory inputs.

## Who It Is For

| Buyer | Pain | Pitch | Next step |
| --- | --- | --- | --- |
| Platform engineer shipping a first production agent | "I don't know what I don't know." | Audits manifest and tool schemas for release risks code review misses. | Run `agents-shipgate init --workspace . --write`. |
| Security or GRC reviewer | "Agents bypass existing controls." | Creates a static tool-surface audit trail for review. | Review the [check catalog](docs/checks.md). |
| AI PM with a shipping deadline | "Security review blocks us late." | Gives teams self-serve pre-review before formal approval. | Scan the [support-refund fixture](samples/support_refund_agent/shipgate.yaml). |

## Limitations

Agents Shipgate is a static, manifest-first scanner. It is intentionally narrow:

- It does not run agents, call tools, invoke LLMs, or verify model availability.
- It does not verify runtime behavior, latency, prompt quality, or routing decisions.
- It does not replace dynamic security testing or human security review of the underlying systems.
- It only inspects what is declared in `shipgate.yaml`, local OpenAPI specs, MCP exports, simple OpenAI API artifacts, optional SDK AST metadata, and static Google ADK inputs; tools that are not declared or statically discoverable are not scanned.
- The manifest remains `version: "0.1"` in v0.3 so existing configs keep working. Reports add `report_schema_version: "0.3"` while preserving the v0.1 payload keys.

See [ROADMAP.md](ROADMAP.md) for what is planned next.

## Trust Model

**Agents Shipgate does not import user code, run agents, call tools, call LLMs, connect to MCP servers, make network calls, or collect telemetry by default.**

See [Trust model](docs/trust-model.md) and [Security policy](SECURITY.md) for the default local-only guarantees and disclosure process.

## GitHub Action

Use a pinned release tag for CI. Set `permissions: contents: read` and run on `pull_request`:

```yaml
name: Agents Shipgate

on:
  pull_request:

permissions:
  contents: read

jobs:
  agents-shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - id: agents-shipgate
        uses: ThreeMoonsLab/agents-shipgate@v0.3.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          output_dir: agents-shipgate-reports
```

For PR comments, add `pull-requests: write` to the job's `permissions` and set `pr_comment: "true"`.

Inputs: `config`, `ci_mode` (`advisory` or `strict`), `fail_on`, `baseline`, `baseline_mode`, `no_plugins`, `output_dir`, `upload_artifact`, `pr_comment`, `github_token`, `shipgate_version`.

Outputs: `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `baseline_matched_count`, `baseline_resolved_count`, `adk_agent_count`, `adk_dynamic_toolset_count`, `report_json`, `report_markdown`, `report_sarif`, `exit_code`.

Set `shipgate_version` to install a pinned PyPI release instead of the action source when your workflow requires package/version parity.

## Pricing And Open Source Stance

Agents Shipgate is and will remain free OSS for individuals and teams running it on their own infrastructure. The core manifest-first scanner, built-in checks, Markdown report, and JSON report are intended to remain open source. We do not collect telemetry and do not require an account.

If hosted dashboards, SSO, org-wide baselines, approval workflows, or trace-based evidence emerge, they should live in a separate optional product rather than moving core OSS functionality behind a paywall.

## Docs

- [Agent Release Gate category](docs/category.md)
- [Manifest v0.1](docs/manifest-v0.1.md)
- [Check catalog](docs/checks.md)
- [Baseline workflow](docs/baseline.md)
- [JSON report schema v0.3](docs/report-schema.v0.3.json)
- [Trust model](docs/trust-model.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Integration recipes](docs/integrations.md)
- [Distribution plan](docs/distribution.md)
- [JSON report schema v0.2](docs/report-schema.v0.2.json)
- [JSON report schema v0.1](docs/report-schema.v0.1.json)
