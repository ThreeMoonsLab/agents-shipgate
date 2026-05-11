# Pre-commit hook recipe

Drop-in [pre-commit](https://pre-commit.com/) hook for running Agents Shipgate locally on every commit that touches a tool-surface artifact.

The canonical hook manifest lives at the **repository root** ([`/.pre-commit-hooks.yaml`](../../.pre-commit-hooks.yaml)) — that's where pre-commit looks when a downstream repo points at this project. This directory only contains the longer write-up.

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

`pre-commit autoupdate` will keep the `rev:` pin current. pre-commit clones this repo, reads [`/.pre-commit-hooks.yaml`](../../.pre-commit-hooks.yaml) from its root, installs the `agents-shipgate` package, and invokes the binary.

Three hook IDs are exposed from the root manifest:

| Hook ID | Mode | Stage(s) | When it fires |
|---|---|---|---|
| `agents-shipgate` | advisory (never blocks) | `pre-commit`, `pre-push` | Any staged tool-surface artifact |
| `agents-shipgate-strict` | strict (`--fail-on critical`) | `pre-push` | Any staged tool-surface artifact |
| `agents-shipgate-validate` | manifest doctor only | `pre-commit` | Only `shipgate.yaml` |

Pick one based on whether you want the commit/push to block (`-strict`) or just surface findings (`agents-shipgate`).

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

The `files:` regex in [`/.pre-commit-hooks.yaml`](../../.pre-commit-hooks.yaml) mirrors the [`docs/triggers.json`](../../docs/triggers.json) glob set, so the hook activates only when a staged change touches an actual tool-surface artifact:

- `shipgate.yaml`
- MCP exports (`*mcp*.json`, `.agents-shipgate/*.json`)
- OpenAPI/Swagger specs (`*openapi*.{yaml,yml,json}`, `*swagger*.{yaml,yml,json}`)
- Static tool inventories (`*tools*.json`)
- Prompts (`prompts/*`) and policies (`policies/*`)

A pure docs/test commit doesn't trigger the scan — same semantic as the AGENTS.md trigger table.

## Advisory vs. strict

Use the `agents-shipgate-strict` hook ID for the strict variant, or override the `entry:` in your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/ThreeMoonsLab/agents-shipgate
    rev: v0.10.0
    hooks:
      - id: agents-shipgate
        entry: agents-shipgate scan -c shipgate.yaml --ci-mode strict --fail-on critical
```

Pair strict mode with a baseline ([`baseline.md`](../../docs/baseline.md)) so existing accepted findings don't fail every commit.

## What about the GitHub Action?

The pre-commit hook and the GitHub Action are independent. The hook gives you a fast local check; the Action is the authoritative gate on PR. Most teams run both — the hook catches obvious regressions before push, the Action enforces the team-wide policy on the merge.

See [`docs/integrations.md`](../../docs/integrations.md) for the GitHub Action recipe.
