---
name: agents-shipgate
description: Use when the user wants to add or run Agents Shipgate as a local-first, static Tool-Use Readiness release gate for an AI agent's tool surface; review or prepare a tool-using agent for release; scan MCP, OpenAPI, OpenAI Agents SDK, Anthropic, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API, Codex plugin, or n8n tool artifacts; add advisory CI; or interpret, fix, triage, suppress, or explain a Shipgate finding.
---

# Agents Shipgate

Agents Shipgate is a local-first, static Tool-Use Readiness release gate for AI agent tool surfaces. It reads `shipgate.yaml` plus local tool sources and writes deterministic reports as Markdown, JSON, SARIF, and Release Evidence Packets.

Use this skill when a task touches agent tools, MCP exports, OpenAPI specs, prompts that constrain tool use, permissions/scopes, approval or confirmation policies, `shipgate.yaml`, Shipgate CI, or `agents-shipgate-reports/report.json`.

Do not use it for general linting, runtime monitoring, evals, model-output quality, or runtime guardrail enforcement. Shipgate is static-only: no agent execution, no tool calls, no LLM calls, no MCP server connections, and no telemetry by default.

## Workflow

1. For relevance decisions, bootstrap, scanning, CI setup, finding fixes, false-positive triage, strict-mode promotion, or version upgrades, read `references/recipes.md`.
2. For reading `report.json`, summarizing release decisions, or deciding what may be auto-applied, read `references/report-reading.md`.
3. Set `AGENTS_SHIPGATE_AGENT_MODE=1` before running Shipgate commands so errors include structured `next_action` JSON.
4. Default first-time CI to advisory mode. Do not enable release-blocking CI or save a baseline until a human has reviewed current findings.
5. Always parse `agents-shipgate-reports/report.json`, not Markdown. Use `release_decision.decision` as the release signal.
6. Auto-apply only high-confidence safe patches. Do not auto-assert approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
7. Ensure `.gitignore` covers `agents-shipgate-reports/` before committing.

## Fast Paths

- First adoption: run `agents-shipgate detect --workspace . --json`, then follow `references/recipes.md`.
- Existing manifest: run `agents-shipgate scan -c shipgate.yaml --suggest-patches --format json`.
- First GitHub CI: copy `assets/advisory-pr-comment.yml` to `.github/workflows/agents-shipgate.yml`.
- Explain one finding: run `agents-shipgate explain-finding <fingerprint> --from agents-shipgate-reports/report.json --json`.
- Triage heuristic findings: run `agents-shipgate findings --from agents-shipgate-reports/report.json --provenance-kind keyword_heuristic,regex_heuristic --json`.

## Boundaries

- Do not claim a finding is fixed without re-running `agents-shipgate scan` and reporting the new decision/counts.
- Do not silently suppress findings. Suppressions require a non-empty `reason`.
- Do not commit generated reports.
- Do not edit the upstream `agents-shipgate` check implementation unless the user is working in the Agents Shipgate repo itself.
