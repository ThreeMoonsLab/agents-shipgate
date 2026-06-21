---
name: agents-shipgate
description: Use when the user wants to add or run Agents Shipgate — the deterministic merge gate for AI-generated agent capability changes — on an AI agent's tool surface; review or prepare a tool-using agent for release; scan MCP, OpenAPI, OpenAI Agents SDK, Anthropic, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API, Codex plugin, or n8n tool artifacts; add advisory CI; or interpret, fix, triage, suppress, or explain a Shipgate finding.
---

# Agents Shipgate

Agents Shipgate is the deterministic merge gate for AI-generated agent capability changes — a local-first, static Tool-Use Readiness review. It reads `shipgate.yaml` plus local tool sources and writes deterministic reports as Markdown, JSON, SARIF, and Release Evidence Packets.

Use this skill when a task touches agent tools, MCP exports, OpenAPI specs, prompts that constrain tool use, permissions/scopes, approval or confirmation policies, `shipgate.yaml`, Shipgate CI, or `agents-shipgate-reports/report.json`.

Do not use it for general linting, runtime monitoring, evals, model-output quality, or runtime guardrail enforcement. Shipgate is static-only: no agent execution, no tool calls, no LLM calls, no MCP server connections, and no telemetry by default.

## Workflow

1. For relevance decisions, bootstrap, verifier runs, scanning, CI setup, finding fixes, false-positive triage, strict-mode promotion, or version upgrades, read `references/recipes.md`.
2. For reading `report.json`, summarizing release decisions, or deciding what may be auto-applied, read `references/report-reading.md`.
3. Before running Shipgate CLI commands, require a CLI whose `agents-shipgate contract --json` reports `contract_version: "7"` or newer: run `command -v agents-shipgate`, `agents-shipgate --version`, and `agents-shipgate contract --json`. If it is missing or stale, tell the user to install or upgrade `agents-shipgate`. The Codex plugin supplies workflows, not the scanner binary.
4. Set `AGENTS_SHIPGATE_AGENT_MODE=1` before running Shipgate commands so errors include structured `next_action` JSON.
5. Default first-time CI to advisory mode. Do not enable release-blocking CI or save a baseline until a human has reviewed current findings.
6. For local agent control, run `shipgate check --agent codex --workspace . --format codex-boundary-json` and read the stdout `shipgate.codex_boundary_result/v1` object. Switch on `decision`; follow `first_next_action`, `repair`, and `human_review`.
7. Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules, policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, run `agents-shipgate preflight --workspace . --plan - --json` with a `PreflightPlanV1` object. Legacy `--changed-files`/`--diff` shorthands remain available. If `requires_human_review` is true or `first_next_action.actor` is `human`, stop and route to a human.
8. For full PR verification, read `agents-shipgate-reports/agent-handoff.json` first, then `verifier.json` for detailed controller state, then `verify-run.json` for reproducibility metadata, then `report.json` for reviewer detail; `report.json.release_decision.decision` remains the release gate.
9. Auto-apply only high-confidence safe patches. Do not auto-assert approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
10. Ensure `.gitignore` covers `agents-shipgate-reports/` before committing.

## Fast Paths

- CLI preflight: run `command -v agents-shipgate`, `agents-shipgate --version`, and `agents-shipgate contract --json`. Continue only when the installed CLI reports `contract_version: "7"` or newer; if it is missing or stale, ask the user to install or upgrade `agents-shipgate`.
- Protected-surface preflight: run `agents-shipgate preflight --workspace . --plan - --json` before touching trust roots; include `changed_files[]` or `diff_text` in the plan when you have concrete planned paths.
- Agent-native check: run `shipgate check --agent codex --workspace . --format codex-boundary-json`; read only the JSON result for continue/repair/stop routing.
- First adoption: run `agents-shipgate detect --workspace . --json`, then follow `references/recipes.md`.
- Agent-related PR/CI diff: run `agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json` after making the base ref available. For local uncommitted work, omit `--base`/`--head` so the working tree is scanned. `verify` never fetches.
- Existing manifest / ongoing PR: run `agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json`.
- First GitHub CI: copy `assets/advisory-pr-comment.yml` to `.github/workflows/agents-shipgate.yml`.
- Explain one finding: run `agents-shipgate explain-finding <fingerprint> --from agents-shipgate-reports/report.json --json`.
- Triage heuristic findings: run `agents-shipgate findings --from agents-shipgate-reports/report.json --provenance-kind keyword_heuristic,regex_heuristic --json`.

## Boundaries

- Do not claim a finding is fixed without re-running `agents-shipgate scan` and reporting the new decision/counts.
- Do not continue with protected-surface edits when preflight returns `requires_human_review=true`; coding agents must not self-approve trust-root changes.
- Before finishing an agent-related diff, run `shipgate check --agent codex --workspace . --format codex-boundary-json` and follow `shipgate.codex_boundary_result/v1`.
- Do not bypass the verifier by suppressing findings, lowering severity, expanding baselines or waivers, removing Shipgate CI, or weakening agent instructions; verify-mode `SHIP-VERIFY-*` checks make those trust-root edits release-visible.
- Do not silently suppress findings. Suppressions require a non-empty `reason`.
- Do not commit generated reports.
- Do not edit the upstream `agents-shipgate` check implementation unless the user is working in the Agents Shipgate repo itself.
