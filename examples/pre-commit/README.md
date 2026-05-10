# Pre-commit hook recipe

Drop-in [pre-commit](https://pre-commit.com/) hook for running Agents Shipgate locally on every commit that touches a tool-surface artifact.

## Two ways to wire it up

### A) Canonical form — let pre-commit manage the install

In your repo's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/ThreeMoonsLab/agents-shipgate
    rev: v0.10.0
    hooks:
      - id: agents-shipgate
```

`pre-commit autoupdate` will keep the `rev:` pin current. The hook definition lives in [`./.pre-commit-hooks.yaml`](./.pre-commit-hooks.yaml).

### B) Local form — agents-shipgate already on PATH

For repos that prefer to manage the agents-shipgate install themselves (e.g., via `pipx install agents-shipgate` in a setup step), use a `repo: local` entry that calls the binary directly:

```yaml
repos:
  - repo: local
    hooks:
      - id: agents-shipgate
        name: Agents Shipgate release-readiness gate
        entry: agents-shipgate scan -c shipgate.yaml --ci-mode advisory
        language: system
        pass_filenames: false
        files: |
          (?x)^(
            shipgate\.yaml|
            .*tools.*\.json|
            .*mcp.*\.json|
            .*openapi.*\.(yaml|yml|json)|
            prompts/.*|
            policies/.*
          )$
```

## When the hook fires

The `files:` regex mirrors the [`docs/triggers.json`](../../docs/triggers.json) glob set, so the hook activates only when a staged change touches an actual tool-surface artifact:

- `shipgate.yaml`
- MCP exports (`*mcp*.json`, `.agents-shipgate/*.json`)
- OpenAPI/Swagger specs (`*openapi*.{yaml,yml,json}`, `*swagger*.{yaml,yml,json}`)
- Static tool inventories (`*tools*.json`)
- Prompts (`prompts/*`) and policies (`policies/*`)

A pure docs/test commit doesn't trigger the scan — same semantic as the AGENTS.md trigger table.

## Advisory vs. strict

The default entry runs in `advisory` mode: the hook never blocks the commit. To make Shipgate fail locally before the commit lands, change the entry:

```yaml
entry: agents-shipgate scan -c shipgate.yaml --ci-mode strict --fail-on critical
```

Pair strict mode with a baseline ([`baseline.md`](../../docs/baseline.md)) so existing accepted findings don't fail every commit.

## What about the GitHub Action?

The pre-commit hook and the GitHub Action are independent. The hook gives you a fast local check; the Action is the authoritative gate on PR. Most teams run both — the hook catches obvious regressions before push, the Action enforces the team-wide policy on the merge.

See [`docs/integrations.md`](../../docs/integrations.md) for the GitHub Action recipe.
