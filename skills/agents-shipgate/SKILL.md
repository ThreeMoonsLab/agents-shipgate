---
name: agents-shipgate
description: Use when the user wants to add release-readiness checks for an AI agent's tool surface, run agents-shipgate scans, fix or triage Shipgate findings, add Shipgate to CI, or interpret a shipgate report. Triggers on phrases like "add shipgate", "release readiness for my agent", "tool-use readiness", "scan my agent", "shipgate scan", "shipgate.yaml", "agents-shipgate-reports/report.json", "fix shipgate finding".
---

# agents-shipgate skill

`agents-shipgate` is a static release-readiness scanner for AI agent tool surfaces. It analyzes `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Anthropic Messages API artifacts, Google ADK files, LangChain/LangGraph files, CrewAI files) and emits deterministic findings as Markdown, JSON, and SARIF.

It does **not** run agents, call tools, invoke LLMs, connect to MCP servers, or send telemetry. Static analysis only.

> The skill name is intentionally `agents-shipgate` (not `shipgate`) so it does not collide with the `/shipgate` slash command shipped at `.claude/commands/shipgate.md` — Claude Code lets a skill with the same name preempt a command, which would bypass the bootstrap flow the slash command is meant to deliver.

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

Pick the matching task and follow the linked recipe verbatim. Recipes are bundled inside this skill so behavior is pinned to the installed version and works offline. Each prompt is self-contained: install commands, exit codes, and `AGENTS_SHIPGATE_AGENT_MODE=1` error handling are in the prompt itself.

| Task | Recipe |
|---|---|
| Decide whether Shipgate should run at all (apply `docs/triggers.json` against the PR) | [`prompts/decide-shipgate-relevance.md`](prompts/decide-shipgate-relevance.md) |
| Bootstrap a repo (install, init, scan, report) | [`prompts/add-shipgate-to-repo.md`](prompts/add-shipgate-to-repo.md) |
| Add Shipgate to CI for the first time (advisory, PR comment) | See "First-time CI setup" below; copy [`ci-recipes/advisory-pr-comment.yml`](ci-recipes/advisory-pr-comment.yml) |
| Fix the highest-severity finding | [`prompts/fix-top-finding.md`](prompts/fix-top-finding.md) |
| Recommend fixes across all active findings | [`prompts/recommend-fixes.md`](prompts/recommend-fixes.md) |
| Explain a single finding in user-facing prose (3–5 sentences for a PR comment / chat reply) | [`prompts/explain-finding-to-user.md`](prompts/explain-finding-to-user.md); pair with `agents-shipgate explain-finding <fingerprint> --from agents-shipgate-reports/report.json --json` |
| Triage a suspected false positive | [`prompts/triage-false-positive.md`](prompts/triage-false-positive.md) |
| Promote advisory CI to strict CI (assumes advisory is already running) | [`prompts/stabilize-strict-mode.md`](prompts/stabilize-strict-mode.md) |
| Upgrade agents-shipgate version | [`prompts/upgrade-shipgate-version.md`](prompts/upgrade-shipgate-version.md) |

Always:

1. Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on stderr.
2. Parse `agents-shipgate-reports/report.json` (stable contract), not the markdown.
3. Confirm with the user before any command that writes files (`init --write`, `baseline save`).

## First-time CI setup (advisory)

If the user has no Shipgate CI yet, default to **advisory** mode — never strict, never with a baseline. The promotion path comes later, only after findings have been reviewed.

1. Confirm the repo has `shipgate.yaml` and a clean local scan (`agents-shipgate scan -c shipgate.yaml --ci-mode advisory` exits `0`). If not, run the bootstrap recipe first.
2. Create `.github/workflows/agents-shipgate.yml` from [`ci-recipes/advisory-pr-comment.yml`](ci-recipes/advisory-pr-comment.yml). It runs on every pull request, posts a summary comment, uploads the report as an artifact, and never fails the job.
3. Confirm `permissions: pull-requests: write` is acceptable to the user before committing — required for the PR comment.
4. Push and open a test PR. Verify the agents-shipgate comment appears.
5. **Stop here.** Promotion to strict mode is a separate task — only run [`prompts/stabilize-strict-mode.md`](prompts/stabilize-strict-mode.md) after the user has reviewed the advisory output and decided which findings they accept.

For non-GitHub CI (GitLab, CircleCI, Jenkins, Azure Pipelines, Buildkite, Bitbucket, pre-commit) refer to https://github.com/ThreeMoonsLab/agents-shipgate/tree/main/examples or `docs/integrations.md` in the upstream repo. Always start in advisory mode.

## Stable contracts (rely on these)

- **CLI surface** is frozen for `0.x` — see https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md.
- **Installed CLI contract**: when available, run `agents-shipgate contract --json` to verify local schema versions, `release_decision.decision`, and manual-review signal fields. Older installs should use [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md) or upgrade before automating against the local contract command.
- **Report JSON**: `report_schema_version: "0.12"`. Read `release_decision.decision` (`"blocked" | "review_required" | "passed"`) **first** for release gating — it is baseline-aware. For one-fetch summarization read the new top-level `agent_summary` block (v0.12+) — `{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}`. For per-finding routing read `findings[].agent_action` (v0.12+; `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`) instead of synthesizing one from `autofix_safe`/`requires_human_review`/`suggested_patch_kind`. Do not gate on `summary.status` for new consumers; it is preserved for v0.7 callers and is baseline-blind. The full field list lives in [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md#read-these-first-for-release-gating); this skill links there instead of restating it. v0.11 adds optional `findings[].source.{path, start_line, end_line, start_column, pointer}` provenance keys (kept in v0.12). Reports validate against [`docs/report-schema.v0.12.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.12.json) (current); older reports validate against [`docs/report-schema.v0.11.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.11.json), [`docs/report-schema.v0.10.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.10.json), [`docs/report-schema.v0.9.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.9.json), [`docs/report-schema.v0.8.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.8.json), and [`docs/report-schema.v0.7.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.7.json) (frozen references).
- **Release Evidence Packet**: `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras) is emitted alongside the report by default. The packet has ten always-present sections governed by [`docs/packet-schema.v0.3.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/packet-schema.v0.3.json) (current) — see [STABILITY.md §Release Evidence Packet](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md#release-evidence-packet-v03). Use the packet for reviewer-shaped output; use the report for finding details.
- **Single source of truth for the contract**: [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md). When the schema bumps, that file updates first.
- **Exit codes**: `0` pass, `2` config error, `3` parse error, `4` other error, `20` strict-mode gate failure.
- **Check IDs** (e.g. `SHIP-POLICY-APPROVAL-MISSING`) are stable; new ones may be added but existing ones will not be renamed or repurposed.

## Boundaries (do not violate)

- Do not claim a finding is fixed without re-running `agents-shipgate scan` and showing the diff in counts.
- Do not silently suppress findings — `checks.ignore` requires a `reason` and the manifest validator rejects empty reasons.
- Do not commit `agents-shipgate-reports/` — it's regenerated each run; add it to `.gitignore`.
- Do not run `agents-shipgate baseline save` until the user has reviewed the initial findings; baselining ratchets in noise.
- Do not enable strict CI as the first CI step. Always start advisory.
- Do not modify checks in `agents-shipgate`'s own source — that's upstream repo work.

## If something errors out

Set `AGENTS_SHIPGATE_AGENT_MODE=1` and re-run. The CLI appends a JSON line to stderr with `{error, message, next_action}`. Follow the `next_action`. The error kinds emitted by the current CLI:

| Error kind | Fix |
|---|---|
| `config_error` | Manifest is missing, malformed, or fails validation. Common cause: no `shipgate.yaml` yet — run `agents-shipgate init --workspace . --write`. |
| `config_already_exists` | `init --write` was run with an existing `shipgate.yaml`. Edit the file in place or remove it before re-running. |
| `input_parse_error` | A file referenced from the manifest (`tool_sources[].path`, baseline, policy pack) is missing, malformed, or resolves outside the manifest directory. Correct the path. |
| `unknown_check_id` | The check ID passed to `explain` does not exist. Run `agents-shipgate list-checks --json` to enumerate. |
| `other_error` / `internal_error` | Unexpected failure. Re-run with `--verbose` and include the output if filing an issue. |

For deeper troubleshooting see https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/troubleshooting.md.
