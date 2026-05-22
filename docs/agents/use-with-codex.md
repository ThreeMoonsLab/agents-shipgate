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
