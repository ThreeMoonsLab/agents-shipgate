# Quickstart

A 60-second introduction to agents-shipgate for developers and AI coding agents.

## Install

```bash
pipx install agents-shipgate
```

Alternatives if `pipx` is unavailable:

```bash
python -m pip install agents-shipgate     # global pip
uv tool install agents-shipgate            # via uv
python -m agents_shipgate --help           # run from a pip install without PATH
```

The CLI binary is `agents-shipgate`; a short alias `shipgate` is also installed.

## First scan (60 seconds against a fixture)

Without writing any YAML:

```bash
agents-shipgate fixture run support_refund_agent
```

This runs against a bundled fixture that intentionally fails several checks,
so you can confirm the install works and see what a real finding list looks
like.

## Second 60 seconds (your real repo)

In a repo containing an agent and its tools, the canonical four-call flow
detects, configures, scans, and auto-applies safe fixes in one turn:

```bash
agents-shipgate detect --json                                              # 1. classify
agents-shipgate init --write --ci --json                                   # 2. manifest + workflow
# 2b. Replace any CHANGE_ME placeholders before scanning (see below)
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json      # 3. scan + suggest
agents-shipgate apply-patches \
    --from agents-shipgate-reports/report.json \
    --confidence high --apply                                              # 4. apply safe fixes
```

`detect` reports whether the workspace looks like an agent project and which
framework(s) are present. `init --write --ci` produces a schema-valid
`shipgate.yaml` (with framework-specific `tool_sources` populated) and an
optional GitHub Actions workflow.

**Replace placeholders before scanning.** `init --write --json` returns a
`placeholders[]` array enumerating every value the template could not infer.
On a fresh workspace the array typically contains both:

- `agent.name: CHANGE_ME` — the agent's role (no strong `Agent(name="…")`
  literal was found).
- `agent.declared_purpose[]: CHANGE_ME` — a one-line description of what
  the agent should do (auto-init can't infer this; the schema requires
  a non-empty value).

Walk `placeholders[]`, edit each one in `shipgate.yaml`, then re-run
`scan`. Skipping this step leaves an invalid adoption artifact — the
manifest validates but downstream consumers see meaningless defaults.

`scan --suggest-patches` attaches a Patch object to every active finding.
`apply-patches --confidence high` mutates only the safe stale-manifest
removals — scope-coverage appends require an explicit `--confidence medium`.

For agent-specific guidance and decision rules, see
[`agent-recipes.md`](agent-recipes.md). For framework-by-framework minimal
manifests, see [`minimal-real-configs.md`](minimal-real-configs.md).

Reports land at `agents-shipgate-reports/report.md` and `report.json`
(the default formats). To also write SARIF for GitHub's code-scanning UI:

```bash
agents-shipgate scan -c shipgate.yaml --format markdown,json,sarif
```

The bundled GitHub Action emits all three formats by default.

## GitHub Action

Drop this into `.github/workflows/shipgate.yml`:

```yaml
name: Agents Shipgate

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  agents-shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ThreeMoonsLab/agents-shipgate@v0.6.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          pr_comment: "true"
```

Advisory mode never fails CI — it posts the finding list as a PR comment.
Switch to `ci_mode: strict` with a baseline file once your team has
triaged existing findings.

## Next

- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable AI-agent workflows for the canonical 4-call flow
- [`minimal-real-configs.md`](minimal-real-configs.md) — framework-by-framework minimal manifest references
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`checks.md`](checks.md) — what the scanner looks for
- [`category.md`](category.md) — what an "agent release gate" is
- [`faq.md`](faq.md) — common questions
- [`concepts.md`](concepts.md) — tool-use readiness in depth
- [`glossary.md`](glossary.md) — category vocabulary
