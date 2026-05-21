---
name: agents-shipgate
description: Use when the user wants to add a local-first, static Tool-Use Readiness release gate for an AI agent's tool surface, run agents-shipgate scans, fix or triage Shipgate findings, add Shipgate to CI, or interpret a shipgate report. Triggers on phrases like "add shipgate", "release readiness for my agent", "tool-use readiness", "scan my agent", "shipgate scan", "shipgate.yaml", "agents-shipgate-reports/report.json", "fix shipgate finding".
---

# agents-shipgate skill

`agents-shipgate` is a local-first, static Tool-Use Readiness release gate for AI agent tool surfaces. It analyzes `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Anthropic Messages API artifacts, Google ADK files, LangChain/LangGraph files, CrewAI files, OpenAI API artifacts, Codex plugin packages and marketplaces, n8n workflow JSON) and emits deterministic findings as Markdown, JSON, and SARIF.

It does **not** run agents, call tools, invoke LLMs, connect to MCP servers, or send telemetry by default. Static analysis only; audited exceptions are pinned in `tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`.

> The skill name is intentionally `agents-shipgate` (not `shipgate`) so it does not collide with the `/shipgate` slash command shipped at `.claude/commands/shipgate.md` — Claude Code lets a skill with the same name preempt a command, which would bypass the bootstrap flow the slash command is meant to deliver.

## When to use this skill

- The user asks to add Tool-Use Readiness or pre-merge checks to an agent project.
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
- **Report JSON**: `report_schema_version: "0.19"`. Read `release_decision.decision` (`"blocked" | "review_required" | "insufficient_evidence" | "passed"`) **first** for release gating — it is baseline-aware. `insufficient_evidence` (added v0.14) fires when evidence coverage is degraded past threshold (at least half of scanned tools low-confidence — `ceil(N × 0.5)` with a minimum of 1 — or 4+ source warnings); switch on the enum with a `review_required` fallback for unknown values. For privacy audit read `privacy_audit` (v0.18+) to confirm default redaction ran before public artifacts were written; `redacted_paths[]` contains structural paths and counts only. For severity-override audit read the top-level `policy_audit.severity_overrides_applied[]` block (v0.17+) — every manifest-driven severity change carries `{check_id, default_severity, applied_severity, manifest_path, reason, tier_crossed, direction, expires}`. For per-finding decision audit read `release_decision.contribution_rules[]` (v0.17+) — one row per `report.findings` entry with `category` ∈ `{blocker, review_item, excluded}` and `rule` ∈ `{policy_block_new, severity_block_new, policy_baseline_accepted, severity_baseline_accepted, review_required, sub_threshold, suppressed}`. For Action Surface Diff read `action_surface_facts`, `action_surface_diff`, and `findings[].blocks_release` (v0.16+) to understand added/removed/modified external actions and explicit release-policy blockers. For one-fetch summarization read the top-level `agent_summary` block (v0.12+) — `{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}`. For per-finding routing read `findings[].agent_action` (v0.12+; `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`) instead of synthesizing one from `autofix_safe`/`requires_human_review`/`suggested_patch_kind`. To filter findings by source reliability read `findings[].provenance_kind` (v0.15+; `static_declaration | ast_extraction | keyword_heuristic | regex_heuristic | policy_pack`) — independent of `confidence`. Codex plugin facts, when present, live under `codex_plugin_surface` (v0.13+). Do not gate on `summary.status` for new consumers; it is preserved for v0.7 callers and is baseline-blind. The full field list lives in [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md#read-these-first-for-release-gating); this skill links there instead of restating it. v0.11 adds optional `findings[].source.{path, start_line, end_line, start_column, pointer}` provenance keys (kept in v0.19). v0.19 adds the optional `Finding.policy_evidence_source` and `ReleaseDecisionItem.{source, policy_evidence_source}` fields for reviewer-grade dual-source provenance — high-risk findings that fire because of a missing manifest mitigation can carry both the tool location AND the manifest-pointer line. Reports validate against [`docs/report-schema.v0.19.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.19.json) (active emitted version). Frozen-reference older schemas (kept for legacy/pre-v0.19 reports): [`v0.18`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.18.json), [`v0.17`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.17.json), [`v0.16`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.16.json), [`v0.15`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.15.json), [`v0.14`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.14.json), [`v0.13`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.13.json), [`v0.12`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.12.json), [`v0.11` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.11.json), [`v0.10` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.10.json), [`v0.9` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.9.json), [`v0.8` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.8.json), and [`v0.7` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.7.json).
- **Release Evidence Packet**: `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras) is emitted alongside the report by default. The packet has fixed reviewer sections governed by [`docs/packet-schema.v0.6.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/packet-schema.v0.6.json) (latest; v0.6 adds `ReleaseDecisionItem.{source, policy_evidence_source}` for reviewer-grade dual-source provenance over the v0.5 baseline). See [STABILITY.md §Release Evidence Packet](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md#release-evidence-packet-v06). Use the packet for reviewer-shaped output; use the report for finding details.
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
