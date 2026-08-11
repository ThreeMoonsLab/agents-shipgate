# Pre-commit hook recipe

Drop-in [pre-commit](https://pre-commit.com/) hook for running Agents Shipgate locally on every commit that touches a tool-surface artifact.

The canonical hook manifest lives at the **repository root** ([`/.pre-commit-hooks.yaml`](../../.pre-commit-hooks.yaml)) — that's where pre-commit looks when a downstream repo points at this project. This directory only contains the longer write-up.

## Two ways to wire it up

### A) Canonical form — let pre-commit manage the install

In your repo's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/ThreeMoonsLab/agents-shipgate
    rev: v0.15.0
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
        entry: agents-shipgate verify --config shipgate.yaml --ci-mode advisory --format text
        language: system
        pass_filenames: false
        files: |
          (?ix)^(
            shipgate\.yaml|
            .*tools.*\.json|
            .*mcp.*\.json|
            .*n8n.*\.json|
            (.*/)?\.n8n(/.*)?|
            (.*/)?conductor/.*\.json|
            (.*/)?ai/examples/.*\.json|
            (.*/)?\.codex/(config\.toml|hooks\.json|requirements\.toml)|
            (.*/)?\.claude/(settings(\.local)?\.json|commands/.*)|
            (.*/)?\.cursor/(cli\.json|mcp\.json|rules/.*)|
            (.*/)?\.vscode/mcp\.json|
            (.*/)?\.shipgate/agent-contract\.json|
            (.*/)?(AGENTS(\.override)?|CLAUDE)\.md|
            \.(agents|claude)/skills/.*|
            (.*/)?\.codex-plugin(/.*)?|
            (.*/)?\.agents/plugins(/.*)?|
            .*\.app\.json|
            (.*/)?SKILL\.md|
            .*openapi.*\.(yaml|yml|json)|
            .*swagger.*\.(yaml|yml|json)|
            \.agents-shipgate/.*\.json|
            (.*/)?prompts(/.*)?|
            (.*/)?policies(/.*)?|
            (.*/)?\.github/workflows/.*\.(yaml|yml)
          )$
```

## When the hook fires

The `files:` regex in [`/.pre-commit-hooks.yaml`](../../.pre-commit-hooks.yaml) covers the path leg of every trigger in [`docs/triggers.json`](../../docs/triggers.json) that a path alone can decide, so the hook activates when a staged change touches:

- `shipgate.yaml` (`TRIGGER-SHIPGATE-MANIFEST`)
- MCP exports — `**/*mcp*.json`, `.agents-shipgate/*.json` (`TRIGGER-MCP-EXPORT-CHANGED`)
- OpenAPI/Swagger specs — `**/*openapi*.{yaml,yml,json}`, `**/*swagger*.{yaml,yml,json}` (`TRIGGER-OPENAPI-SPEC-CHANGED`)
- Static tool inventories — `**/*tools*.json` (`TRIGGER-STATIC-TOOL-INVENTORY-CHANGED`)
- n8n workflow JSON — `**/*n8n*.json`, `**/.n8n/**` (`TRIGGER-N8N-WORKFLOW-CHANGED`, path leg)
- Conductor OSS workflow JSON — `**/conductor/**/*.json`, `**/ai/examples/**/*.json` (`TRIGGER-CONDUCTOR-WORKFLOW-CHANGED`, path leg)
- Coding-agent host configuration and instructions — Codex/Claude/Cursor/VS Code config, `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`, skills (`TRIGGER-*-BOUNDARY-*`)
- Codex plugin package files — `.codex-plugin/**`, `.agents/plugins/**`, `**/.app.json`, `**/.mcp.json`, `**/SKILL.md` (`TRIGGER-CODEX-PLUGIN-CHANGED`)
- Prompts and policies — `prompts/**`, `policies/**` (`TRIGGER-PROMPTS-OR-POLICIES`)
- Shipgate CI workflow — `.github/workflows/agents-shipgate.{yml,yaml}` (`TRIGGER-SHIPGATE-CI-WORKFLOW`, path leg)

Matching is case-insensitive, and every clause whose catalog glob is recursive is recursive too: `services/foo/policies/refund.yaml`, `enterprise/lib/captain/Prompts/system.md`, and a nested `AGENTS.md` all stage the same as a repo-root copy. A `dir/**` glob also matches a tracked path named exactly `dir`, so the regex does too. That coverage claim is pinned exhaustively — a catalog rule with a path leg must be listed in the hook fixture table or in its documented exclusion set, so a new rule cannot land uncovered and unnoticed.

### What the hook can't catch

pre-commit's `files:` regex is purely path-based; it cannot inspect diff content. Several triggers in `docs/triggers.json` need the diff body and are therefore **not** covered by this hook:

- `TRIGGER-FUNCTION-TOOL-DECORATOR` — fires when `@function_tool`, `@tool`, or `FunctionTool(` is added to a diff.
- `TRIGGER-GOOGLE-ADK-AGENT-TOOLS-CHANGED` — fires when a `google.adk` module path, an `Agent(`/`LlmAgent(` construction, and a `tools=[...]` argument all appear in a diff.
- `TRIGGER-FRAMEWORK-VERSION-BUMP` — fires when `openai-agents`, `langchain`, `crewai`, `google-adk`, or `conductor-oss` appears in the diff **and** a dependency manifest (`pyproject.toml`, `requirements*.txt`, `package.json`, a lockfile, …) is among the changed files. The path leg alone can't decide it, so the regex can't either.
- `TRIGGER-SHIPGATE-CI-WORKFLOW` also has a diff-only leg (`ThreeMoonsLab/agents-shipgate` string match) that this hook doesn't see.

For full trigger coverage on PRs, rely on the GitHub Action — it runs `agents-shipgate verify` on the configured event and evaluates the full trigger catalog (including the diff-only legs) itself, so diff content doesn't gate discovery the way the path-based `files:` regex does. For local diff-aware checks, `python -m agents_shipgate.triggers --git-diff HEAD` (or `--diff-text "..."`) evaluates the full catalog against any file set or diff payload.

A pure docs/test commit doesn't trigger the scan — same semantic as the AGENTS.md trigger table (`TRIGGER-DOCS-ONLY-NEGATIVE`).

## Advisory vs. strict

Use the `agents-shipgate-strict` hook ID for the strict variant, or override the `entry:` in your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/ThreeMoonsLab/agents-shipgate
    rev: v0.15.0
    hooks:
      - id: agents-shipgate
        entry: agents-shipgate verify --config shipgate.yaml --ci-mode strict --fail-on critical --format text
```

Pair strict mode with a baseline ([`baseline.md`](../../docs/baseline.md)) so existing accepted findings don't fail every commit.

## What about the GitHub Action?

The pre-commit hook and the GitHub Action are independent. The hook gives you a fast local check; the Action is the authoritative gate on PR. Most teams run both — the hook catches obvious regressions before push, the Action enforces the team-wide policy on the merge.

See [`docs/integrations.md`](../../docs/integrations.md) for the GitHub Action recipe.
