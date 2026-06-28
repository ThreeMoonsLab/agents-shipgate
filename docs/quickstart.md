# Quickstart

A 60-second introduction to agents-shipgate for developers and AI coding agents.

## Verify-first quickstart

After installing the CLI (see [Install](#install)), start from one of three
prominent flows.

### Local Boundary Check

Coding agents run `shipgate check` before reporting an agent-capability change
complete. Parse the stdout `shipgate.codex_boundary_result/v1` object:

```bash
shipgate check --agent codex --workspace . --format codex-boundary-json
shipgate check --agent claude-code --workspace . --format codex-boundary-json
shipgate check --agent cursor --workspace . --format codex-boundary-json
```

Switch on `decision`, `completion_allowed`, `must_stop`, `first_next_action`,
`human_review`, `repair`, and `policy`; do not infer a decision from prose.

### PR And Local Verification

For local pre-commit work, omit `--base` and `--head` so uncommitted edits are
scanned:

```bash
shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json
```

For committed PR/CI refs, make the base ref available first, then pass base and
head:

```bash
shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json --base origin/main --head HEAD
```

If a repo is not configured yet, use the verify flow's preview entry point:

```bash
shipgate verify --preview --json
```

Read `agents-shipgate-reports/agent-handoff.json` first and lead with
`gate.merge_verdict`, `gate.can_merge_without_human`, `controller`,
`next_action`, `fix_task`, and `capability_review.top_changes`. Then read
`agents-shipgate-reports/report.json`; the release gate remains
`release_decision.decision`.

### Host-Grant Audit

Before changing local MCP servers, Codex/Claude/Cursor permission rules,
hooks, workflow scopes, or other host grants, capture the host inventory:

```bash
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

## Supporting zero-install relevance check

Coding agents reading a fresh repo can answer "is this an agent project?" in one fetch, no install needed:

```bash
curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \
  | python3 - --workspace . --json
```

If `is_agent_project: true` (or `suggested_sources` / `codex_plugin_candidates` is non-empty), continue with the install below. If `is_agent_project: false`, `suggested_sources: []`, and `codex_plugin_candidates: []`, Shipgate is not the right tool for this repo — see [`docs/zero-install.md`](zero-install.md) for `uvx` and GitHub Action alternatives that also avoid a local install.

## Install

```bash
pipx install agents-shipgate
```

Alternatives if `pipx` is unavailable:

```bash
python -m pip install agents-shipgate     # global pip
uv tool install agents-shipgate            # via uv
uvx agents-shipgate --help                 # one-shot via uv, no permanent install
python -m agents_shipgate --help           # run from a pip install without PATH
```

The CLI binary is `agents-shipgate`; a short alias `shipgate` is also installed.
Agents Shipgate currently requires Python 3.12 or newer. If your project uses
an older runtime, install the CLI with `pipx` or `uv` using a Python 3.12+
interpreter instead of installing it into the project environment.

## Demo fixture (60 seconds)

Run the verify-native fixture used for the public verifier story:

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

The fixture builds a temporary base/head git history where the head commit adds
`stripe.create_refund`. It writes `verifier.json`, `report.json`, and
`pr-comment.md`; the expected merge verdict is `blocked`.

The older static scan fixture is still useful when you want a 5-minute path to
confirm the CLI works and inspect a real Tool-Use Readiness Report before
touching your own repo:

```bash
uvx agents-shipgate fixture run support_refund_agent
```

If `uvx` is unavailable, install once with `pipx` and run:

```bash
pipx install agents-shipgate
agents-shipgate fixture run support_refund_agent
```

The fixture intentionally fails several checks, so you can see what a real
finding list looks like. It is a demo path, not the main PR verification flow.

## Read the first result

For PR verification, read `agent-handoff.json.gate.merge_verdict` first:

| Merge verdict | Meaning | Next action |
| --- | --- | --- |
| `blocked` | Active, unaccepted blockers exist. | Fix blockers or remove the risky capability. |
| `insufficient_evidence` | Static evidence is too weak to gate release confidently. | Add better sources and rerun; do not auto-merge. |
| `human_review_required` | A person must review accepted debt, trust-root changes, or authority-bearing gaps. | Surface the required review; a coding agent must not self-approve it. |
| `mergeable` | No active blocker or review signal was found. | Keep verifier/report artifacts with the PR record. |
| `unknown` | Verify could not produce a reliable head scan or diff context. | Fix the setup, fetch the base ref, or rerun with usable inputs. |

Then read `report.json.release_decision.decision`, the source-of-truth gate:

| Decision | Meaning | Next action |
| --- | --- | --- |
| `blocked` | Active, unaccepted blockers exist. | Fix blockers or remove the risky tool surface. |
| `insufficient_evidence` | The scan cannot confidently gate release from the available static evidence; this does not prove the agent is unsafe. | Provide an MCP export, OpenAPI spec, explicit local tool inventory, or broader OpenAI SDK source path, then rerun. |
| `review_required` | Human review is needed for accepted debt or evidence gaps below the blocked threshold. | Review the listed items before promotion. |
| `passed` | No active blocker or review signal was found. | Keep the report artifact with the PR/release record. |

## First adoption helper

`bootstrap` remains useful for first-time adoption when you want a single
command to detect, configure, scan, and auto-apply safe mechanical fixes:

```bash
agents-shipgate bootstrap --json
```

The expanded form is:

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

### If The First Real Repo Stalls

Use this decision tree before reading the full manifest schema:

| Symptom | Next action |
| --- | --- |
| `detect` says `is_agent_project: false`, but `suggested_sources` includes MCP or OpenAPI files | Proceed to `init`. MCP/OpenAPI-only repos are valid tool-surface targets even without Python framework detection. |
| `detect` says `is_agent_project: false`, but `codex_plugin_candidates` is non-empty | Proceed to `init`. Codex plugin repos are valid static plugin-surface targets even without Python framework detection. |
| `doctor` shows zero tools | Check `tool_sources[].path`, MCP `tools[]`, OpenAPI `paths`, optional source warnings, and dynamic ADK/MCP toolsets. |
| Tools are created by factories, wrappers, or dynamic ADK/MCP toolsets | Provide an explicit MCP export, OpenAPI spec, local tool inventory artifact, or a broader OpenAI SDK source directory when tools are static but split across files. |
| `init --write --json` reports `CHANGE_ME` placeholders | Replace `agent.name` and `agent.declared_purpose` from the prompt, main agent file, or repo README before scanning. |
| Install fails in a Python 3.10/3.11 project | Install the CLI outside the project env with `pipx` or `uv` using Python 3.12+. |
| Reports appear in `git status` | Add `agents-shipgate-reports/` to `.gitignore`; reports are local release-review artifacts. |

### Choose Your First Source

For a first real scan, point the manifest at the clearest tool boundary you
already have:

| Source | Use when | Manifest path |
| --- | --- | --- |
| OpenAI Agents SDK Python | Tools are defined with `@function_tool` in local Python files. | `tool_sources[].type: openai_agents_sdk`; `path` may be one Python file or a directory of files |
| MCP export | You can export the MCP server's tool list to JSON. | `tool_sources[].type: mcp` |
| OpenAPI spec | The agent calls HTTP APIs described by OpenAPI 3.x. | `tool_sources[].type: openapi` |
| Codex plugin package | The repo contains `.codex-plugin/plugin.json` or `.agents/plugins/marketplace.json`. | `tool_sources[].type: codex_plugin` |

Google ADK, LangChain/LangGraph, CrewAI, n8n workflow JSON, Anthropic Messages
API artifacts, and simple OpenAI API artifacts are also supported inputs, but
the fastest path is
to start with the tool surface closest to the release boundary.

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
        with:
          fetch-depth: 0
      - id: shipgate
        uses: ThreeMoonsLab/agents-shipgate@v1.0.0a1
        with:
          config: shipgate.yaml
          ci_mode: advisory
          diff_base: target
          pr_comment: "true"
          shipgate_version: "1.0.0a1"
```

Advisory mode never fails CI — it posts the merge verdict, capability changes,
required next action, and report links as a PR comment.
Switch to `ci_mode: strict` with a baseline file once your team has
triaged existing findings.

After adoption, choose an explicit policy:

```yaml
- name: Block only blocked verdicts
  if: steps.shipgate.outputs.merge_verdict == 'blocked'
  run: exit 1
```

```yaml
- name: Require no human authority gap
  if: steps.shipgate.outputs.can_merge_without_human != 'true'
  run: exit 1
```

## Export feedback

For a design-partner review or false-positive report, export the small redacted
feedback artifact from the verifier:

```bash
agents-shipgate feedback export \
  --from agents-shipgate-reports/verifier.json \
  --redact \
  --out shipgate-feedback.json
```

The export includes the merge verdict, top capability changes, finding IDs,
next action, `fix_task`, and reviewer prompts. It does not include raw finding
evidence.

## Next

- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable AI-agent workflows for verify-first PRs and first adoption
- [`minimal-real-configs.md`](minimal-real-configs.md) — framework-by-framework minimal manifest references
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`checks.md`](checks.md) — what the scanner looks for
- [`category.md`](category.md) — what an "agent release gate" is
- [`faq.md`](faq.md) — common questions
- [`concepts.md`](concepts.md) — tool-use readiness in depth
- [`glossary.md`](glossary.md) — category vocabulary
