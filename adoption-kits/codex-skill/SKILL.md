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
3. Before running Shipgate CLI commands, require `agents-shipgate >=0.11.0`: run `command -v agents-shipgate` and `agents-shipgate --version`. If it is missing or older than 0.11.0, tell the user to run `pipx install agents-shipgate` and then `pipx upgrade agents-shipgate`; if `pipx` is unavailable, use `python -m pip install -U "agents-shipgate>=0.11"`. The Codex plugin supplies workflows, not the scanner binary.
4. Set `AGENTS_SHIPGATE_AGENT_MODE=1` before running Shipgate commands so errors include structured `next_action` JSON.
5. Default first-time CI to advisory mode. Do not enable release-blocking CI or save a baseline until a human has reviewed current findings.
6. For verify runs, read `agents-shipgate-reports/verifier.json` first and lead with `merge_verdict`; then parse `report.json` and use `release_decision.decision` as the release gate.
7. Auto-apply only high-confidence safe patches. Do not auto-assert approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
8. Ensure `.gitignore` covers `agents-shipgate-reports/` before committing.

## Fast Paths

- CLI preflight: run `command -v agents-shipgate` and `agents-shipgate --version`. Continue only when the installed CLI is `>=0.11.0`; if it is missing or stale, ask the user to run `pipx install agents-shipgate` followed by `pipx upgrade agents-shipgate`, or `python -m pip install -U "agents-shipgate>=0.11"` when `pipx` is unavailable.
- First adoption: run `agents-shipgate detect --workspace . --json`, then follow `references/recipes.md`.
- Local agent-related diff: run `agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json`. Add `--base origin/main --head HEAD` only for committed PR/CI verification after making the base ref available.
- Existing manifest / ongoing PR: run `agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json`.
- First GitHub CI: copy `assets/advisory-pr-comment.yml` to `.github/workflows/agents-shipgate.yml`.
- Explain one finding: run `agents-shipgate explain-finding <fingerprint> --from agents-shipgate-reports/report.json --json`.
- Triage heuristic findings: run `agents-shipgate findings --from agents-shipgate-reports/report.json --provenance-kind keyword_heuristic,regex_heuristic --json`.

## Boundaries

- Do not claim a finding is fixed without re-running `agents-shipgate scan` and reporting the new decision/counts.
- Before finishing an agent-related diff in a repo with `shipgate.yaml`, run `agents-shipgate verify` or report the exact `agents-shipgate trigger` skip verdict.
- Do not bypass the verifier by suppressing findings, lowering severity, expanding baselines or waivers, removing Shipgate CI, or weakening agent instructions; verify-mode `SHIP-VERIFY-*` checks make those trust-root edits release-visible.
- Do not silently suppress findings. Suppressions require a non-empty `reason`.
- Do not commit generated reports.
- Do not edit the upstream `agents-shipgate` check implementation unless the user is working in the Agents Shipgate repo itself.
