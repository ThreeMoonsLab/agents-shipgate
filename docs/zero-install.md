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
  "excluded_sources": [{"type": "mcp", "path": "...", "reason": "..."}],
  "codex_plugin_candidates": [{"mode": "package", "path": "..."}],
  "agent_scope": "single",
  "agent_project_candidates": [{"path": ".", "marker": "pyproject.toml", "agent_names": [...]}],
  "agent_scope_truncated": false,
  "python_parse_truncated": false,
  "next_action": "agents-shipgate init --workspace .",
  "workspace_signals": {...},
  "script_version": "0.5.0"
}
```

Like the canonical CLI, the script parse-probes each glob-matched MCP/OpenAPI candidate before suggesting it — a filename match is not a guarantee. A Cursor plugin `mcp.json` is an `mcpServers`-style host config, not an MCP tools-array export; suggesting it would make the next `init --write` → `scan` step fail. Rejected candidates appear under `excluded_sources` (`{type, path, reason}`) instead of `suggested_sources`. The probe is **JSON-only** (stdlib has no YAML parser): a `.json` candidate the adapters would reject is excluded here too, while a `.yaml`/`.yml` OpenAPI spec is always kept as a suggestion (never wrongly dropped). The real-world miss this guards against — `mcpServers`-style host configs — is always JSON, so the probe is exact where it matters.

An MCP server whose tool surface exists **only as TypeScript or Go registration sites** — `mongodb-js/mongodb-mcp-server`, `grafana/mcp-grafana`, `github/github-mcp-server` — is detected here too, and suggested as `{"type": "mcp_server_source", "path": "..."}`. That is 100% of the population this script is pointed at: a repository that has not adopted Shipgate. Until v0.5.0 of the script the reader lived only in the installed CLI, so the documented first command answered "Stop, not an agent project" on exactly the repositories the CLI reported as agent projects with 61, 110 and 114 tools.

Porting it means a second implementation of the load-bearing matcher — the masking lexer and the five registration idioms. It is held to the CLI's answers by a shared conformance corpus rather than by inspection: every positive sample, the whole adversarial sweep, the path predicate and both escape grammars live once in [`tests/mcp_idiom_corpus.py`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/tests/mcp_idiom_corpus.py) and are driven through both readers, compared site by site with the byte span of each. Neither reader can change its answer on a case either of them has ever been asked about without the other following.

Like `agents-shipgate detect`, the script silently skips common fixture corpus directories such as `fixtures/`, `_fixtures/`, `__fixtures__/`, `testdata/`, `test_data/`, `test-fixtures/`, `test_fixtures/`, `golden/`, and `goldens/` when they are below the selected workspace. Point `--workspace` directly at a fixture project if you intentionally want to classify that fixture itself.

The script and the canonical CLI are pinned to **structural verdict parity** by [`tests/test_zero_install_detector.py`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/tests/test_zero_install_detector.py): same `is_agent_project`, same fired frameworks, same suggested sources, same excluded sources, same Codex plugin candidates, and the same manifest-scope verdict (`agent_scope`, `agent_scope_truncated`, `python_parse_truncated`, plus `agent_project_candidates[]`) for every sample in `samples/`. The scope verdict is pinned because an agent that consults the zero-install path must not adopt a scope the CLI would refuse: on a workspace whose agents live in several self-contained projects, both report `agent_scope: "ambiguous"` and neither recommends initializing the root. `agent_scope_truncated` is pinned for the same reason one step down: when the Python parse stopped at its cap in a workspace holding more than one project root, `agent_project_candidates[]` is a lower bound rather than an enumeration, and a caller that reads a truncated list as complete concludes its own project is not an agent project. `python_parse_truncated` is the wider fact both detectors carry: whether the parse stopped at its cap at all, which is what makes every whole-workspace negative — `is_agent_project: false` included — unsafe to act on. Field-by-field byte parity is not pinned and not promised — the script is not a drop-in replacement for the CLI.

`agent_name_candidates` is the one field pinned byte for byte, including its ranking and each entry's `rationale[]`. It is not a yes/no signal: it names the agent a generated manifest would declare as the reviewed identity, so a script that ranked differently would point you at a different agent than `init` does.

**When to use this:** you're a coding agent (Claude Code, Codex, Cursor) deciding *whether* to propose Shipgate. The script tells you in one fetch + one Python invocation. The full flow (`init`, `scan`, `apply-patches`) requires the actual install.

**Constraints:** Python 3.12+ on the runner. Evidence/reason strings and absolute framework scores are simplified — the verdict is what's pinned, not the prose.

The workspace inventory does match the canonical CLI's — `git ls-files` when Git can read the workspace, a contained filesystem walk otherwise. That is a correctness requirement, not a speed one: a `.gitignore`d module is invisible to `init`, so a script that walked it anyway could name an agent `init` will never write. Paths escaping the workspace through a symlink are dropped for the same reason.

Git's output is read incrementally against a 16 MiB bound. If that bound is exceeded the script **exits non-zero** rather than falling back to a filesystem walk — the same failure the canonical CLI raises. A fallback would do exactly the work the bound exists to refuse, and would answer from a different inventory than `init` used.

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
      - uses: ThreeMoonsLab/agents-shipgate@v0.15.0
        with:
          ci_mode: advisory
          diff_base: target
          pr_comment: 'true'
          shipgate_version: '0.15.0'
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

When the detector script returns `is_agent_project: true`, the natural next step is the first-adoption helper flow ([AGENTS.md § Single-turn agent flow](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/AGENTS.md#single-turn-agent-flow-v06)); after adoption, use `agents-shipgate verify` for ongoing PRs:

```bash
pipx install agents-shipgate
agents-shipgate detect --json
agents-shipgate init --write --ci --json
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json
agents-shipgate apply-patches --from agents-shipgate-reports/report.json --confidence high --apply
```

The `script_version` field on the detector's output lets a downstream tool know whether the verdict came from the zero-install script or the canonical CLI; subsequent steps in the flow always use the canonical CLI.
