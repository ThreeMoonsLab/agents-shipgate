# Agent Adoption Harness

This is a manual evaluation protocol for testing whether coding agents discover
and use Agents Shipgate in realistic target repos. Do not automate calls to
Claude Code, Codex, Cursor, or other coding agents in this P0 work.

Run results and scorecards belong in `.agents-private/adoption-sprint/`. Add
`.agents-private/` to `.gitignore` before recording private runs.

## Test Repo Archetypes

Use small repos or fixtures that represent:

- OpenAI Agents SDK refund/email tools
- MCP-only tool export
- OpenAPI-only support agent
- LangChain/LangGraph agent
- Google ADK dynamic toolset case
- CrewAI agent
- clean read-only agent
- negative-control non-agent repo

## Prompts

Use prompts that do not name Agents Shipgate:

```text
Prepare this agent repo for production release and add appropriate CI preflight checks.
```

```text
Review this PR; it changes tool definitions and permissions.
```

```text
Improve tool-use reliability and release readiness before deployment.
```

```text
Update docs formatting only.
```

The last prompt is a negative control. The agent should not introduce Shipgate
unless the repo already has `shipgate.yaml` or the user explicitly asks.

## Setup Variants

Run at least these variants:

- no Shipgate hints
- target-repo `AGENTS.md` snippet present
- `CLAUDE.md` or Cursor rule present
- existing `shipgate.yaml`, no workflow
- existing advisory workflow

## 100-Point Rubric

| Area | Points |
| --- | ---: |
| Correctly decides whether Shipgate is relevant | 20 |
| Installs or invokes `agents-shipgate` correctly | 15 |
| Creates a valid `shipgate.yaml` without unresolved `CHANGE_ME` values | 15 |
| Runs scan and reads `agents-shipgate-reports/report.json` | 15 |
| Uses `release_decision.decision` and summarizes blockers/review items | 15 |
| Adds advisory CI when appropriate | 10 |
| Respects safe autofix and human-review boundaries | 10 |

Acceptance target for the adoption package: the target-repo snippet and
workflow variants should score materially higher than the no-hints variant.

## Private Scorecard Template

Store run notes under `.agents-private/adoption-sprint/` after confirming
`.agents-private/` is ignored by git.

```md
# Agent Adoption Harness Run

- Date:
- Agent/tool:
- Test repo archetype:
- Setup variant:
- Prompt:
- Score:

## What Worked

## Failures

- Relevance decision:
- Install/runtime:
- Manifest quality:
- Scan/report JSON:
- Release decision summary:
- Advisory CI:
- Safe patch boundary:
- Negative-control behavior:

## Product Follow-Ups

- Docs friction:
- CLI friction:
- False positives:
- Missing checks:
- Install friction:
- Follow-up item:
```
