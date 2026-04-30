---
name: shipgate
description: Use when the user wants to add release-readiness checks for an AI agent's tool surface, run agents-shipgate scans, fix or triage Shipgate findings, add Shipgate to CI, or interpret a shipgate report. Triggers on phrases like "add shipgate", "release readiness for my agent", "tool-use readiness", "scan my agent", "shipgate scan", "shipgate.yaml", "agents-shipgate-reports/report.json", "fix shipgate finding".
---

# Shipgate skill

`agents-shipgate` is a static release-readiness scanner for AI agent tool surfaces. It analyzes `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Anthropic Messages API artifacts, Google ADK files, LangChain/LangGraph files, CrewAI files) and emits deterministic findings as Markdown, JSON, and SARIF.

It does **not** run agents, call tools, invoke LLMs, connect to MCP servers, or send telemetry. Static analysis only.

## When to use this skill

- The user asks to add release-readiness or pre-merge checks to an agent project.
- The repo already has `shipgate.yaml` or `agents-shipgate-reports/report.json`.
- The user asks to fix, triage, suppress, or explain a Shipgate finding.
- The user wants to add Shipgate to CI (GitHub Actions, GitLab CI, CircleCI).

## When NOT to use this skill

- Generic linting / type checking — use the project's existing tooling.
- Runtime monitoring, evals, or behavioral testing — Shipgate is static-only.
- LLM output quality assessment — out of scope.
- Editing `agents-shipgate`'s own check implementations — that's upstream-repo work, not user-repo work.

## How to act

Pick the matching task and follow the linked prompt verbatim. Each prompt is self-contained and includes install commands, exit-code semantics, and `AGENTS_SHIPGATE_AGENT_MODE=1` error handling.

| Task | Prompt to follow |
|---|---|
| Bootstrap a repo (install, init, scan, report) | https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/add-shipgate-to-repo.md |
| Fix the highest-severity finding | https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/fix-top-finding.md |
| Triage a suspected false positive | https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/triage-false-positive.md |
| Promote advisory CI to strict CI | https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/stabilize-strict-mode.md |
| Upgrade agents-shipgate version | https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/prompts/upgrade-shipgate-version.md |

Always:

1. Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on stderr.
2. Parse `agents-shipgate-reports/report.json` (stable contract), not the markdown.
3. Confirm with the user before any command that writes files (`init --write`, `baseline save`).

## Stable contracts (rely on these)

- **CLI surface** is frozen for `0.x` — see https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md.
- **Report JSON**: `report_schema_version: "0.5"`. Stable fields include `summary.{critical_count, high_count, medium_count, status}` and `findings[].{id, fingerprint, check_id, severity, category, title, recommendation, suppressed}`.
- **Exit codes**: `0` pass, `2` config error, `3` parse error, `4` other error, `20` strict-mode gate failure.
- **Check IDs** (e.g. `SHIP-POLICY-APPROVAL-MISSING`) are stable; new ones may be added but existing ones will not be renamed or repurposed.

## Boundaries (do not violate)

- Do not claim a finding is fixed without re-running `agents-shipgate scan` and showing the diff in counts.
- Do not silently suppress findings — `checks.ignore` requires a `reason` and the manifest validator rejects empty reasons.
- Do not commit `agents-shipgate-reports/` — it's regenerated each run; add it to `.gitignore`.
- Do not run `agents-shipgate baseline save` until the user has reviewed the initial findings; baselining ratchets in noise.
- Do not modify checks in `agents-shipgate`'s own source — that's upstream repo work.

## If something errors out

Set `AGENTS_SHIPGATE_AGENT_MODE=1` and re-run. The CLI appends a JSON line to stderr with `{error, message, next_action}`. Follow the `next_action`.

Common errors:

| Error kind | Fix |
|---|---|
| `config_not_found` | `agents-shipgate init --workspace . --write` |
| `input_parse_error` | The path in `tool_sources[].path` is missing or malformed; correct it |
| `unknown_check_id` | The user passed a check ID that does not exist; run `agents-shipgate list-checks --json` to enumerate |

For deeper troubleshooting see https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/troubleshooting.md.
