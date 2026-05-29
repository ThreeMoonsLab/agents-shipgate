# Use Agents Shipgate with Codex

OpenAI Codex reads repo-level `AGENTS.md` instructions and repo-scoped Codex
Skills under `.agents/skills/<name>/`. Agents Shipgate ships both surfaces:
the `AGENTS.md` snippet tells Codex when to run the gate, and the
`agents-shipgate` skill gives Codex the detailed workflows for bootstrap,
scanning, report reading, advisory CI, and finding triage.

| Surface | What it does | Source path in this repo |
|---|---|---|
| `AGENTS.md` snippet | Tells Codex when Shipgate is relevant and names the canonical commands. | [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) §`AGENTS.md` |
| Codex skill | Repo-scoped skill Codex can invoke explicitly with `$agents-shipgate` or implicitly when the task matches. | [`.agents/skills/agents-shipgate/`](../../.agents/skills/agents-shipgate/) |
| Reusable prompts | Longer copy-paste recipes for agents that do not use skills. | [`prompts/README.md`](../../prompts/README.md) |

## Install In Your Agent Repo

From the root of the project where Codex should run Shipgate:

```bash
pipx install agents-shipgate
agents-shipgate init --workspace . --write --agent-instructions=agents-md,codex-skill
```

To install every supported agent surface at once:

```bash
agents-shipgate init --workspace . --write --agent-instructions=all
```

The `codex-skill` target writes `.agents/skills/agents-shipgate/`. It is
idempotent and safe to rerun; user-edited skill files are not overwritten.

## Customize Generated Skill Content

The installed package ships the default skill content offline. A downstream
repo can override selected files without patching the wheel by adding
`.agents-shipgate/adoption-kit.yaml`:

```yaml
schema_version: 1
targets:
  codex-skill:
    overrides_dir: .agents-shipgate/adoption-kit/codex-skill
```

Files under the override directory are relative to
`.agents/skills/agents-shipgate/`, for example `SKILL.md`,
`references/recipes.md`, or `assets/advisory-pr-comment.yml`. Pass a
different config path with `--agent-instructions-kit <path>`.

## Verify

Open Codex in the project and run two checks:

1. Ask: "prepare this agent repo for production release and add appropriate
   CI preflight checks." Codex should use the AGENTS.md snippet or the
   `agents-shipgate` skill, run `agents-shipgate detect --workspace . --json`,
   and continue only when Shipgate is relevant.
2. Ask with explicit skill invocation: "$agents-shipgate scan this agent and
   summarize the release decision." Codex should read
   `agents-shipgate-reports/report.json`, not Markdown, and lead with
   `release_decision.decision`.

If both pass, the repo has the Codex adoption surface installed.

## Verify An Agent PR

On any PR that changes agent tools, MCP exports, OpenAPI specs, prompts,
permissions, policies, CI gates, or `shipgate.yaml`, Codex should run the
verifier before claiming the work is done:

```bash
agents-shipgate verify --base origin/main --head HEAD --json
```

Then read `agents-shipgate-reports/verifier.json` and **lead with
`merge_verdict`** (`mergeable` / `human_review_required` /
`insufficient_evidence` / `blocked` / `unknown`). It is a deterministic
projection of `release_decision.decision`, which remains the gate in
`agents-shipgate-reports/report.json`. Read `capability_review.top_changes[]`
next to see the highest-signal tool/action access changes, and check
`trust_root_touched`.

Codex must not claim completion when `merge_verdict` is `blocked`,
`insufficient_evidence`, or `human_review_required` unless the user has
explicitly accepted the human-review requirement. When `first_next_action.actor`
is `human` — approval, confirmation, idempotency, broad-scope, prohibited-action,
or acknowledgement decisions — Codex surfaces the item for a person rather than
resolving it.

And Codex must **never** weaken `shipgate.yaml`, the Shipgate CI workflow,
`AGENTS.md`, policy packs, baselines, waivers, or suppressions just to make
Shipgate pass — that edit is itself a trust-root change the gate will flag. See
[`../use-cases/ai-generated-agent-prs.md`](../use-cases/ai-generated-agent-prs.md)
for the full PR-verification walkthrough.

## What The Skill Covers

The Codex skill is intentionally smaller than the Claude Code skill bundle.
It loads a concise `SKILL.md` first, then only reads references when needed:

- `references/recipes.md` — relevance, bootstrap, advisory CI, fixing,
  explaining, suppressing, strict promotion, and version upgrades.
- `references/report-reading.md` — release decision, `agent_summary`,
  `findings[].agent_action`, and the manual-review boundary.
- `assets/advisory-pr-comment.yml` — first-time GitHub Action template.

Codex must preserve the same safety boundary as every other agent:

- It may install, detect, init, scan, summarize, add advisory CI, apply
  high-confidence mechanical patches, and add `agents-shipgate-reports/` to
  `.gitignore`.
- It must not invent approval, confirmation, idempotency, broad-scope,
  prohibited-action, or runtime-trace evidence.

For Claude Code, see [`use-with-claude-code.md`](use-with-claude-code.md).
For Cursor, see [`use-with-cursor.md`](use-with-cursor.md).
