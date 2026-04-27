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

## First scan

In a repo containing an agent and its tools:

```bash
agents-shipgate init --workspace . --write
agents-shipgate scan -c shipgate.yaml
```

`init --write` produces a `shipgate.yaml` with `CHANGE_ME` placeholders for
`agent.name` and `agent.declared_purpose`. Replace the placeholders before
running `scan`.

Reports land at `agents-shipgate-reports/report.md` and `report.json`
(the default formats). To also write SARIF for GitHub's code-scanning UI:

```bash
agents-shipgate scan -c shipgate.yaml --format markdown,json,sarif
```

The bundled GitHub Action emits all three formats by default.

## Verify on a known fixture

Without writing any YAML:

```bash
agents-shipgate fixture run support_refund_agent
```

This runs against a bundled fixture that intentionally fails several checks,
so you can confirm the install works and see what a real finding list looks
like.

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
      - uses: ThreeMoonsLab/agents-shipgate@v0.3.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          pr_comment: "true"
```

Advisory mode never fails CI — it posts the finding list as a PR comment.
Switch to `ci_mode: strict` with a baseline file once your team has
triaged existing findings.

## Next

- [`docs/manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`docs/checks.md`](checks.md) — what the scanner looks for
- [`docs/category.md`](category.md) — what an "agent release gate" is
- [`docs/faq.md`](faq.md) — common questions
- [`docs/concepts.md`](concepts.md) — tool-use readiness in depth
- [`docs/glossary.md`](glossary.md) — category vocabulary
