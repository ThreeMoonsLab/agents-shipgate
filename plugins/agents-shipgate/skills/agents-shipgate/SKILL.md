---
name: agents-shipgate
description: Use when the user wants to run the prominent Agents Shipgate flows — `shipgate check`, `agents-shipgate verify`, or `shipgate audit --host` — for AI agent capability changes, PR release readiness, or coding-agent host grants.
---

# Agents Shipgate

Agents Shipgate is the deterministic merge gate for AI-generated agent capability changes — a local-first, static Tool-Use Readiness review. It reads `shipgate.yaml` plus local tool sources and writes deterministic reports as Markdown, JSON, SARIF, and supporting Release Evidence Packets.

Use this skill when a task touches agent tools, MCP exports, OpenAPI specs, prompts that constrain tool use, permissions/scopes, approval or confirmation policies, `shipgate.yaml`, Shipgate CI, or `agents-shipgate-reports/report.json`.

Do not use it for general linting, runtime monitoring, evals, model-output quality, or runtime guardrail enforcement. Shipgate is static-only: no agent execution, no tool calls, no LLM calls, no MCP server connections, and no telemetry by default.

## Workflow

1. For local checks, verifier runs, host audits, and supporting recovery commands, read `references/recipes.md`.
2. For reading `report.json`, summarizing release decisions, or deciding what may be auto-applied, read `references/report-reading.md`.
3. Before running Shipgate CLI commands, require a CLI whose `agents-shipgate contract --json` reports `minimum_control_contract_version: 24`: run `command -v agents-shipgate`, `agents-shipgate --version`, and `agents-shipgate contract --json`. If it is missing or stale, tell the user to install or upgrade `agents-shipgate`. The Codex plugin supplies workflows, not the scanner binary.
4. Set `AGENTS_SHIPGATE_AGENT_MODE=1` before running Shipgate commands so errors include structured `next_action` JSON.
5. Default first-time CI to advisory mode. Do not enable release-blocking CI or save a baseline until a human has reviewed current findings.
6. For local agent control, run `shipgate check --agent codex --workspace . --format agent-boundary-json` and read the stdout `shipgate.agent_boundary_result/v2` object. Switch on `control.state`; follow only `control.next_action`, `control.allowed_next_commands`, and `control.human_review`. Treat `decision` as diagnostic context only.
7. Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules, policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, plan to run `agents-shipgate verify` before completion and route trust-root review to a human when the verifier requires it.
8. For full PR verification, validate `agents-shipgate-reports/verification-receipt.json` first, then read `agent-handoff.json` and switch on `control.state`; read `verifier.json` for detailed control state, `verify-run.json` for the request graph, and `report.json` for reviewer detail. `report.json.release_decision.decision` remains the release gate. Refresh with `agents-shipgate agent control --workspace .` — which refuses the read when HEAD, the tree, or the working tree has moved since the decision — before you act on any of that, and again before enforcing a cached `must_stop`, before commit/push/PR update, before merge, and before declaring the task complete. A non-zero exit means no control identity is current and you hold no authority; if `current_control_id` changed, discard every cached control state and restart from the new identity. A result remembered from earlier in the conversation never outranks the current pointer, in either direction. What that command prints depends on the installed CLI, so read `agents-shipgate contract --json` once: when it reports `agent_control_schema_version`, `agent control` returns that compact `shipgate.agent_control/v1` object — `control_state`, `permissions`, `next_actor`, `next_action` — and routing on it alone is enough, with `--format pointer` returning the raw `current-control.json`; when it does not, the CLI predates the envelope and `agent control` returns the raw pointer, whose fields are `lifecycle_state` and nested `control.state`.
9. Auto-apply only high-confidence safe patches. Do not auto-assert action effect, action authority, agent bindings, approval, confirmation, idempotency, broad-scope, prohibited-action, or runtime-trace evidence.
10. Ensure `.gitignore` covers `agents-shipgate-reports/` before committing.

## Fast Paths

- CLI preflight: run `command -v agents-shipgate`, `agents-shipgate --version`, and `agents-shipgate contract --json`. Continue only when the installed CLI reports `minimum_control_contract_version: 24`; if it is missing or stale, ask the user to install or upgrade `agents-shipgate`.
- Agent-native check: run `shipgate check --agent codex --workspace . --format agent-boundary-json`; read only the JSON result for routing. Four shapes: continue, repair, verify-and-report (a graded `require_review` set — finish the work, then name every `pending_review[]` item in the summary), and stop.
- Agent-related PR/CI diff: run `agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json` after making the base ref available. For local uncommitted work, omit `--base`/`--head` so the working tree is scanned. `verify` never fetches.
- Existing manifest / ongoing PR: run `agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json`.
- Unconfigured repo or uncertain relevance: run `agents-shipgate verify --preview --json`.
- Host grants: run `shipgate audit --host --json --out agents-shipgate-reports/host-grants.json`.

## Boundaries

- Do not claim a finding is fixed without re-running `agents-shipgate verify` and reporting the new merge verdict and release decision.
- Do not self-approve trust-root changes; when `agents-shipgate verify` returns human review required, surface it to a human.
- Before finishing an agent-related diff, run `shipgate check --agent codex --workspace . --format agent-boundary-json` and follow `shipgate.agent_boundary_result/v2.control.state`. Only `complete` permits completion; a human route cannot be cleared by conversation-level acknowledgement.
- Do not bypass the verifier by suppressing findings, lowering severity, expanding baselines or waivers, removing Shipgate CI, or weakening agent instructions; verify-mode `SHIP-VERIFY-*` checks make those trust-root edits release-visible.
- Do not silently suppress findings. Suppressions require a non-empty `reason`.
- Do not commit generated reports.
- Do not edit the upstream `agents-shipgate` check implementation unless the user is working in the Agents Shipgate repo itself.
