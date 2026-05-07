<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readme-header-dark.png">
    <img src="assets/readme-header.png" alt="Agents Shipgate · static release-readiness gate for AI agent tool surfaces" width="100%">
  </picture>
</p>

# Agents Shipgate

[![PyPI](https://img.shields.io/pypi/v/agents-shipgate)](https://pypi.org/project/agents-shipgate/)
[![Python](https://img.shields.io/pypi/pyversions/agents-shipgate)](https://pypi.org/project/agents-shipgate/)
[![GitHub Action](https://img.shields.io/badge/GitHub%20Action-marketplace-blue)](https://github.com/marketplace/actions/agents-shipgate)
[![License](https://img.shields.io/pypi/l/agents-shipgate)](LICENSE)
[![CI](https://github.com/ThreeMoonsLab/agents-shipgate/actions/workflows/ci.yml/badge.svg)](https://github.com/ThreeMoonsLab/agents-shipgate/actions/workflows/ci.yml)

**Static release-readiness gate for AI agent tool surfaces.**

Agents Shipgate is an open-source CLI and GitHub Action that scans MCP,
OpenAPI, OpenAI Agents SDK, Anthropic Messages API, Google ADK,
LangChain/LangGraph, CrewAI, and OpenAI API artifacts, then writes a
deterministic **Tool-Use Readiness Report** before your agent gets
production-like permissions.

No agent execution. No LLM calls. No MCP server connections. No scanner network
calls. No scanner telemetry. Apache-2.0.

## Use this when

Run Agents Shipgate when a PR adds or changes agent tool surfaces or the policy
evidence around them:

- MCP exports, OpenAPI specs, or local tool inventories.
- OpenAI Agents SDK, Google ADK, LangChain/LangGraph, CrewAI, Anthropic
  Messages API, or OpenAI API artifact tool definitions.
- Prompts, permission scopes, approval policies, confirmation policies,
  prohibited actions, or `shipgate.yaml`.
- GitHub Actions or CI release gates for a tool-using AI agent.

## Try it in 60 seconds

```bash
pipx install agents-shipgate
agents-shipgate fixture run support_refund_agent
```

This runs a bundled fixture with no manifest required and prints:

```text
Fixture: support_refund_agent
Decision: blocked
Blockers: 2  Review items: 16
Counts:  critical=2 high=14 medium=2
Reports: <tempdir>/reports
Fixture copy at <tempdir>; pass --keep to retain after the run.
```

Both blockers are on `stripe.create_refund`: missing approval policy and missing idempotency evidence. The fixture writes `report.{md,json}` and `packet.{md,json,html}` into the temp `reports/` directory. To scan your own repo and write the standard `agents-shipgate-reports/` directory, see [Scan your repo](#scan-your-repo) below.

![Sample Tool-Use Readiness Report showing 2 critical, 14 high, and 2 medium findings on the support_refund_agent fixture, including a missing approval policy on stripe.create_refund.](assets/sample-report.png)

## Scan your repo

```bash
agents-shipgate init --workspace . --write
agents-shipgate scan -c shipgate.yaml
```

Reports land at `agents-shipgate-reports/report.{md,json,sarif}`; the Release Evidence Packet lands at `agents-shipgate-reports/packet.{md,json,html}`.

Install alternatives (your agent project does **not** need Python 3.12 — install the CLI separately):

```bash
python -m pip install agents-shipgate    # global pip
uv tool install agents-shipgate          # via uv
```

## Adopt in one turn (for AI coding agents)

The v0.6 single-turn flow takes a workspace from "looks like an agent
project" to "Shipgate integrated, scan green or with safe patches
applied, CI workflow drafted":

```bash
agents-shipgate detect --json                                          # 1. classify
agents-shipgate init --write --ci --json                               # 2. manifest + workflow
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json  # 3. scan + suggest
agents-shipgate apply-patches --from agents-shipgate-reports/report.json \
    --confidence high --apply                                          # 4. apply safe trivial fixes
```

`init --ci` writes `.github/workflows/agents-shipgate.yml`. `apply-patches`
is dry-run by default and refuses to mutate anything outside the
manifest's directory.

For agents driving this flow programmatically, see
[`docs/agent-recipes.md`](docs/agent-recipes.md). For framework-by-framework
minimal manifests, see [`docs/minimal-real-configs.md`](docs/minimal-real-configs.md).

## Use in CI

```yaml
- uses: ThreeMoonsLab/agents-shipgate@v0.8.0
  with:
    config: shipgate.yaml
    ci_mode: advisory
```

Set `pr_comment: "true"` to post a compact PR summary:

![Preview of the optional Agents Shipgate PR comment showing release blockers, severity counts, top findings, and report artifacts.](assets/pr-comment-preview.png)

## What it scans

| Input | Status |
|---|---|
| Model Context Protocol (MCP) exports | Supported |
| OpenAPI 3.x specs | Supported |
| OpenAI Agents SDK Python entrypoints | Supported |
| Anthropic Messages API artifacts | Supported |
| Google ADK Python and YAML config | Supported |
| LangChain/LangGraph static Python inputs | Supported |
| CrewAI static Python inputs | Supported |
| OpenAI API artifacts | Supported |

## What it produces

- **Tool-Use Readiness Report** — `agents-shipgate-reports/report.{md,json,sarif}`. Markdown for human release review, JSON for tools and coding agents (current schema [v0.10](docs/report-schema.v0.10.json); gating signal is `release_decision.decision`), SARIF for GitHub code-scanning workflows.
- **Release Evidence Packet** — `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras). Reviewer-shaped synthesis with ten always-present sections (release decision, capability/intent, high-risk surface, approval coverage, idempotency risk, scope coverage, memory isolation, human-in-the-loop, dynamic scenarios, not_proven). Governed by [packet schema v0.3](docs/packet-schema.v0.3.json) — see [STABILITY.md §Release Evidence Packet](STABILITY.md#release-evidence-packet-v03).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass (advisory mode or strict-no-blockers) |
| `2` | Manifest config error |
| `3` | Input parse error (file missing, malformed, path traversal blocked) |
| `4` | Other Agents Shipgate error |
| `20` | Strict-mode gate failure |

## For coding agents

Human readers can skip this section; it exists so coding agents can find the
repo's machine-readable contracts quickly.

Agents Shipgate is designed to be agent-friendly. If you're a coding agent (Claude Code, Codex, Cursor, Aider) reading this repo:

- **[`docs/agent-contract-current.md`](docs/agent-contract-current.md)** — single source of truth for the current schema versions and which JSON fields to read. Updated whenever the contract bumps; other agent-facing surfaces link here instead of restating the contract.
- **[`AGENTS.md`](AGENTS.md)** — canonical agent-facing instructions: install, run, common tasks, JSON-mode flags, error semantics
- **[`STABILITY.md`](STABILITY.md)** — what won't break across `0.x` versions
- **[`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md)** — copyable snippets for adding Shipgate trigger rules to downstream agent repos
- **[`docs/agent-adoption-harness.md`](docs/agent-adoption-harness.md)** — manual protocol for checking whether coding agents discover and use Shipgate
- **[`prompts/`](prompts/)** — reusable prompts for common workflows
- **[`skills/agents-shipgate/`](skills/agents-shipgate/)** + **[`.claude/commands/shipgate.md`](.claude/commands/shipgate.md)** — self-contained Claude Code skill (bundled prompts and CI recipe) and `/shipgate` slash command. See [`docs/agents/use-with-claude-code.md`](docs/agents/use-with-claude-code.md) to install in your own project.
- **[`docs/ai-search-summary.md`](docs/ai-search-summary.md)** — human-readable summary for AI search, answer engines, and coding agents
- **[`docs/manifest-v0.1.json`](docs/manifest-v0.1.json)** + **[`docs/report-schema.v0.10.json`](docs/report-schema.v0.10.json)** — JSON Schemas for live editor validation (current; emitted reports carry `report_schema_version: "0.10"`). v0.10 adds `tool_surface_facts` and `tool_surface_diff`; read `release_decision.decision` for release gating in new consumers.
- **[`docs/checks.json`](docs/checks.json)** — machine-readable check catalog

Every command has a `--json` form. Errors emit a structured `next_action` line on stderr when `AGENTS_SHIPGATE_AGENT_MODE=1`.

## Why this exists

Once an AI agent can refund, email, cancel, deploy, or modify a record, every tool change becomes a release event. Code review catches code; eval suites catch behavior; observability catches runtime. None of them answer the release question: *given the tool surface declared in this PR, do we have explicit approval policies, scope coverage, idempotency evidence, and review readiness for every action?*

Agents Shipgate produces a deterministic answer to that question, before promotion.

## Findings Gallery

The bundled support-refund fixture demonstrates the kind of release risks Agents Shipgate is designed to surface:

```text
## Release Decision

Decision: blocked
Reason: 2 active findings block release.
Blockers: 2
Review items: 16
Fail policy: would_fail_ci=false (exit 0)

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

## See it block a PR

The fastest way to understand what changes for a reviewer: walk through a Golden PR. Each one ships a sample manifest, the resulting report, the release decision, and the recommended PR-comment summary an agent should post.

- [`openai-agents-sdk-refund-agent`](examples/golden-prs/openai-agents-sdk-refund-agent/README.md) — refund agent adds `stripe.create_refund`. Shipgate decides `blocked` because approval policy and idempotency evidence are missing. Includes the recommended Markdown PR-comment template.
- [`mcp-only-tool-server`](examples/golden-prs/mcp-only-tool-server/README.md) — MCP server with no Python framework imports; demonstrates the MCP-only adoption path.
- [`openapi-support-agent`](examples/golden-prs/openapi-support-agent/README.md) — OpenAPI-described tool surface; shows scope-coverage findings.

## Why Not Just...

| Alternative | Gap Agents Shipgate Covers |
| --- | --- |
| Unit tests | Tests usually validate code paths, not the released tool surface and declared policies. |
| Code review | Reviewers miss generated specs, MCP exports, broad scopes, and missing approval policies. |
| Runtime traces | Useful later, but they arrive after behavior exists. Agents Shipgate runs before promotion. |
| Nothing | Tool-surface drift becomes a production surprise. |

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

Agents Shipgate supports static Google ADK extraction for Python entrypoints and Agent Config YAML. The adapter detects `LlmAgent`/`Agent` definitions, function tools, `OpenAPIToolset`, `McpToolset`, callbacks, plugins, sub-agents, eval references, and explicit local tool inventories without importing ADK code.

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

## LangChain And CrewAI

Agents Shipgate includes static Python extraction for LangChain/LangGraph and
CrewAI. The adapters parse Python AST only; they do not import framework
packages or user modules. The supported LangChain/LangGraph patterns target
LangChain Core 0.3+, LangChain 1.x `create_agent`, and LangGraph 0.2+ source
shapes.

```yaml
tool_sources:
  - id: langchain_agent
    type: langchain
    path: agent.py
  - id: crewai_agent
    type: crewai
    path: crew.py
```

For dynamic or prebuilt tool surfaces, provide explicit local inventory files:

```yaml
langchain:
  tool_inventories:
    - inventories/langchain-tools.json
crewai:
  tool_inventories:
    - inventories/crewai-tools.json
```

## Policy Packs

v0.4 adds local declarative YAML policy packs for organization-specific release
rules. Policy packs are static data and run without importing code.

```yaml
checks:
  policy_packs:
    - path: policies/org-release.yaml
```

```bash
agents-shipgate scan --config shipgate.yaml --policy-pack policies/org-release.yaml
```

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
- It only inspects what is declared in `shipgate.yaml`, local OpenAPI specs, MCP exports, simple OpenAI API artifacts, optional SDK AST metadata, and static Google ADK/LangChain/CrewAI inputs; tools that are not declared or statically discoverable are not scanned.
- The manifest remains `version: "0.1"` so existing configs keep working. Current reports carry `report_schema_version: "0.10"` and add tool-surface facts/diff fields while preserving the stable payload contract documented in the report schema.

See [ROADMAP.md](ROADMAP.md) for what is planned next.

## Trust Model

**Agents Shipgate does not import user code, run agents, call tools, call LLMs, connect to MCP servers, make network calls, or collect telemetry by default.**

See [Trust model](docs/trust-model.md) and [Security policy](SECURITY.md) for the default local-only guarantees and disclosure process.

## GitHub Action

Drop this full advisory workflow into `.github/workflows/agents-shipgate.yml`. It runs on every PR, posts a summary comment, uploads the report and packet as workflow artifacts, and never fails the job. This is the same file shipped at [`examples/github-actions/01-advisory-pr-comment.yml`](examples/github-actions/01-advisory-pr-comment.yml).

```yaml
name: Agents Shipgate (advisory)

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  shipgate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.10.0
        with:
          ci_mode: advisory
          diff_base: target
          pr_comment: 'true'
          shipgate_version: '0.10.0'
```

Switch to `ci_mode: strict` only after your team has reviewed the advisory output. See [`examples/github-actions/`](examples/github-actions/) for strict / baseline / SARIF / multi-config / changed-paths recipes.

Inputs: `config`, `ci_mode` (`advisory` or `strict`), `fail_on`, `baseline`, `baseline_mode`, `diff_from`, `diff_base`, `policy_packs`, `no_plugins`, `output_dir`, `upload_artifact`, `pr_comment`, `github_token`, `shipgate_version`. Set `diff_base: target` for a best-effort target-branch scan in PRs; shallow checkout, missing config, schema mismatch, or scan failure disables the diff and leaves the release gate unchanged.

Outputs: `decision`, `blocker_count`, `review_item_count`, `ci_would_fail`, `diff_enabled`, `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `baseline_matched_count`, `baseline_resolved_count`, `adk_agent_count`, `adk_dynamic_toolset_count`, `report_json`, `report_markdown`, `report_sarif`, `exit_code`. Prefer `decision` and `ci_would_fail` over legacy `status` for new release gates.

Set `shipgate_version` to install a pinned PyPI release instead of the action source when your workflow requires package/version parity.

## Pricing And Open Source Stance

Agents Shipgate is and will remain free OSS for individuals and teams running it on their own infrastructure. The core manifest-first scanner, built-in checks, Markdown report, and JSON report are intended to remain open source. We do not collect telemetry and do not require an account.

If hosted dashboards, SSO, org-wide baselines, approval workflows, or trace-based evidence emerge, they should live in a separate optional product rather than moving core OSS functionality behind a paywall.

Teams shipping production-like tool-using agents can read the
[design partner notes](docs/design-partners.md) for early review criteria and
contact details.

## Docs

- [Agent Release Gate category](docs/category.md)
- [Manifest v0.1](docs/manifest-v0.1.md)
- [Check catalog](docs/checks.md)
- [Policy packs](docs/policy-packs.md)
- [Baseline workflow](docs/baseline.md)
- [JSON report schema v0.10](docs/report-schema.v0.10.json)
- [Trust model](docs/trust-model.md)
- [AI search summary](docs/ai-search-summary.md)
- [Design partners](docs/design-partners.md)
- [Runtime inventory design note](docs/runtime-inventory.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Integration recipes](docs/integrations.md)
- [Distribution plan](docs/distribution.md)
- [JSON report schema v0.2](docs/report-schema.v0.2.json)
- [JSON report schema v0.1](docs/report-schema.v0.1.json)
