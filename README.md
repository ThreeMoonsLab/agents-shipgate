# Agents Shipgate

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue)

**The pre-flight check your agent release is missing.**

Agents Shipgate is an **Agent Release Gate**: a static, manifest-first scanner that catches risky agent tool configurations at PR time. It reads `shipgate.yaml`, local MCP tool exports, local OpenAPI specs, simple OpenAI API prompt/tool/schema artifacts, and optional OpenAI Agents SDK AST metadata, then writes Markdown and JSON reports for release review.

An agent release gate is the static, manifest-based pre-flight check that runs on agent PRs before promotion to staging or production. Today, most agent teams ship without one.

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

Install from a source checkout:

```bash
python -m pip install -e ".[dev]"
agents-shipgate init --workspace . --write
agents-shipgate doctor --config shipgate.yaml
agents-shipgate scan --config shipgate.yaml
```

Or install directly from GitHub until the PyPI package is published:

```bash
python -m pip install "git+https://github.com/ThreeMoonsLab/agents-shipgate@main"
```

Try the bundled fixture:

```bash
agents-shipgate scan --config samples/support_refund_agent/shipgate.yaml
agents-shipgate scan --config samples/simple_openai_api_agent/shipgate.yaml
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
- It only inspects what is declared in `shipgate.yaml`, local OpenAPI specs, MCP exports, simple OpenAI API artifacts, and optional SDK AST metadata; tools that are not declared are not scanned.
- The manifest remains `version: "0.1"` in v0.2 so existing configs keep working. Reports add `report_schema_version: "0.2"` while preserving the v0.1 payload keys.

See [ROADMAP.md](ROADMAP.md) for what is planned next.

## Trust Model

**Agents Shipgate does not import user code, run agents, call tools, call LLMs, connect to MCP servers, make network calls, or collect telemetry by default.**

See [Trust model](docs/trust-model.md) and [Security policy](SECURITY.md) for the default local-only guarantees and disclosure process.

## GitHub Action

The action can run from `main` during pre-release or from a pinned tag after a release. Set `permissions: contents: read` and run on `pull_request`:

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
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
      - id: agents-shipgate
        uses: ThreeMoonsLab/agents-shipgate@main
        with:
          config: shipgate.yaml
          ci_mode: advisory
          output_dir: agents-shipgate-reports
```

For PR comments, add `pull-requests: write` to the job's `permissions` and set `pr_comment: "true"`.

Inputs: `config`, `ci_mode` (`advisory` or `strict`), `fail_on`, `baseline`, `baseline_mode`, `no_plugins`, `output_dir`, `upload_artifact`, `pr_comment`, `github_token`, `shipgate_version`.

Outputs: `status`, `critical_count`, `high_count`, `medium_count`, `baseline_new_count`, `report_json`, `report_markdown`, `exit_code`.

Once Agents Shipgate is published to PyPI, set `shipgate_version` to install a pinned PyPI release instead of the action source.

## Pricing And Open Source Stance

Agents Shipgate is and will remain free OSS for individuals and teams running it on their own infrastructure. The core manifest-first scanner, built-in checks, Markdown report, and JSON report are intended to remain open source. We do not collect telemetry and do not require an account.

If hosted dashboards, SSO, org-wide baselines, approval workflows, or trace-based evidence emerge, they should live in a separate optional product rather than moving core OSS functionality behind a paywall.

## Docs

- [Agent Release Gate category](docs/category.md)
- [Manifest v0.1](docs/manifest-v0.1.md)
- [Check catalog](docs/checks.md)
- [Baseline workflow](docs/baseline.md)
- [JSON report schema v0.2](docs/report-schema.v0.2.json)
- [Trust model](docs/trust-model.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Integration recipes](docs/integrations.md)
- [Distribution plan](docs/distribution.md)
- [JSON report schema v0.1](docs/report-schema.v0.1.json)
