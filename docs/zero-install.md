# Zero-install paths

Use these when you want to know whether Agents Shipgate is even relevant to your repo without paying the install cost first. Three options, ordered by cheapest first.

## 1. Single-file detector script

A stdlib-only Python script — no `pip install`, no `pipx`, no `uv`. Just fetch and run.

```bash
curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \
  | python3 - --workspace . --json
```

Or save it locally first:

```bash
curl -sSL -o shipgate-detect.py \
  https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py
python3 shipgate-detect.py --workspace . --json
```

The script's output is a **structural subset** of `agents-shipgate detect --json`. It carries the canonical `DetectResult` fields (which is what the verdict — "is this an agent project?" — depends on) plus a `script_version` distinguisher. It does **not** carry the CLI's `diagnostics[]` or `next_actions[]` arrays — those require the full install.

```json
{
  "is_agent_project": true,
  "frameworks": [{"type": "openai_agents_sdk", "score": 4.5, ...}],
  "agent_name_candidates": [...],
  "suggested_sources": [{"type": "mcp", "path": "..."}],
  "codex_plugin_candidates": [{"mode": "package", "path": "..."}],
  "next_action": "agents-shipgate init --workspace .",
  "workspace_signals": {...},
  "script_version": "0.1.0"
}
```

The script and the canonical CLI are pinned to **structural verdict parity** by [`tests/test_zero_install_detector.py`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/tests/test_zero_install_detector.py): same `is_agent_project`, same fired frameworks, same suggested sources, and same Codex plugin candidates for every sample in `samples/`. Field-by-field byte parity is not pinned and not promised — the script is not a drop-in replacement for the CLI.

**When to use this:** you're a coding agent (Claude Code, Codex, Cursor) deciding *whether* to propose Shipgate. The script tells you in one fetch + one Python invocation. The full flow (`init`, `scan`, `apply-patches`) requires the actual install.

**Constraints:** Python 3.12+ on the runner. No git fast path (uses `os.walk` only). Evidence strings and absolute scores are simplified — the verdict is what's pinned, not the prose.

## 2. `uvx` (no permanent install)

[`uv`](https://docs.astral.sh/uv/) lets you run a one-shot command from PyPI without installing into a permanent environment:

```bash
uvx agents-shipgate detect --workspace . --json
uvx agents-shipgate init --workspace . --write --ci --json
uvx agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
```

`uvx` downloads the package into a cache, runs it, and discards the environment. Subsequent invocations reuse the cache so this is fast after the first call.

**When to use this:** the runner has `uv` but the project's environment shouldn't be polluted. Common in monorepos where Shipgate isn't a project dependency.

**Constraints:** `uv` 0.4+ on the runner. The first call downloads the package and its dependencies (a few seconds). Once cached, equivalent in performance to a pipx install.

## 3. GitHub Action — no local install required

If your repo already runs CI, the Shipgate Action runs the canonical flow without anyone installing anything locally:

```yaml
# .github/workflows/agents-shipgate.yml
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

The full template lives at [`examples/github-actions/01-advisory-pr-comment.yml`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/examples/github-actions/01-advisory-pr-comment.yml).

**When to use this:** you have CI but no local development environment for the agent's project (common for non-Python agent projects). The Action posts a PR comment with the verdict on every PR.

**Constraints:** GitHub Actions runner. Results land on the PR, not the developer's terminal. Best for ongoing CI gating, not for first-look exploration.

## Decision matrix

| You want to | Use this |
|---|---|
| Know if Shipgate is relevant to a repo, in one fetch | Detector script (#1) |
| Run the full flow once without committing to install | `uvx` (#2) |
| Gate every PR on the readiness signal | GitHub Action (#3) |
| Use the tool day-to-day | `pipx install agents-shipgate` (the canonical install) |

## Going from zero-install to full install

When the detector script returns `is_agent_project: true`, the natural next step is the canonical 4-call flow ([AGENTS.md § Single-turn agent flow](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/AGENTS.md#single-turn-agent-flow-v06)):

```bash
pipx install agents-shipgate
agents-shipgate detect --json
agents-shipgate init --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches --from agents-shipgate-reports/report.json --confidence high --apply
```

The `script_version` field on the detector's output lets a downstream tool know whether the verdict came from the zero-install script or the canonical CLI; subsequent steps in the flow always use the canonical CLI.
