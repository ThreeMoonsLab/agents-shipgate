---
name: agents-shipgate
description: 'Run prominent Agents Shipgate flows when a change touches what an AI agent can do: `shipgate check`, `agents-shipgate verify`, or `shipgate audit --host`. Use after adding or modifying MCP servers or tools, tool/function definitions (@tool, @function_tool), OpenAPI specs that describe agent tools, agent prompts, permission scopes, approval or confirmation policies, agent CI workflows, or shipgate.yaml — and before creating a PR for any such change. Also use to verify agent-related PRs, fix or triage Shipgate findings, add Shipgate to CI, or interpret Shipgate verifier/report artifacts. Triggers on phrases like "add shipgate", "verify this agent PR", "merge verdict", "release readiness for my agent", "tool-use readiness", "shipgate check", "agents-shipgate verify", "audit host grants", "shipgate.yaml", "agents-shipgate-reports/verifier.json", "agents-shipgate-reports/report.json", "fix shipgate finding".'
---

# agents-shipgate skill

`agents-shipgate` is the deterministic merge gate for AI-generated agent capability changes — a local-first, static Tool-Use Readiness review. It analyzes `shipgate.yaml` plus tool sources (MCP exports, OpenAPI specs, OpenAI Agents SDK Python files, Anthropic Messages API artifacts, Google ADK files, LangChain/LangGraph files, CrewAI files, OpenAI API artifacts, Codex repo config, Codex plugin packages and marketplaces, n8n workflow JSON, Conductor OSS workflow JSON) and emits deterministic verifier artifacts, findings, Markdown, JSON, and SARIF.

It does **not** run agents, call tools, invoke LLMs, connect to MCP servers, or send telemetry by default. Static analysis only; audited exceptions are pinned in `tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`.

> The skill name is intentionally `agents-shipgate` (not `shipgate`) so it does not collide with the `/shipgate` slash command shipped at `.claude/commands/shipgate.md` — Claude Code lets a skill with the same name preempt a command, which would bypass the bootstrap flow the slash command is meant to deliver.

## When to use this skill

- The current diff adds or modifies MCP server or tool definitions, `@tool`/`@function_tool` decorators, OpenAPI specs describing agent tools, agent prompts, permission scopes, approval/confirmation policies, or agent CI workflows — run the verifier before reporting the change complete or creating a PR.
- The user asks to add Tool-Use Readiness or pre-merge checks to an agent project.
- The user asks whether an AI-generated agent PR can merge.
- The repo already has `shipgate.yaml`, `agents-shipgate-reports/verifier.json`, or `agents-shipgate-reports/report.json`.
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
| Verify an agent-related PR or local diff before finishing | [`prompts/verify-agent-diff.md`](prompts/verify-agent-diff.md) |
| Add Shipgate to CI for the first time (advisory, PR comment) | See "First-time CI setup" below; copy [`ci-recipes/advisory-pr-comment.yml`](ci-recipes/advisory-pr-comment.yml) |
| Fix the highest-severity finding | [`prompts/fix-top-finding.md`](prompts/fix-top-finding.md) |
| Recommend fixes across all active findings | [`prompts/recommend-fixes.md`](prompts/recommend-fixes.md) |
| Explain a single finding in user-facing prose (3–5 sentences for a PR comment / chat reply) | [`prompts/explain-finding-to-user.md`](prompts/explain-finding-to-user.md); pair with `agents-shipgate explain-finding <fingerprint> --from agents-shipgate-reports/report.json --json` |
| Triage a suspected false positive | [`prompts/triage-false-positive.md`](prompts/triage-false-positive.md) |
| Promote advisory CI to strict CI (assumes advisory is already running) | [`prompts/stabilize-strict-mode.md`](prompts/stabilize-strict-mode.md) |
| Upgrade agents-shipgate version | [`prompts/upgrade-shipgate-version.md`](prompts/upgrade-shipgate-version.md) |

Always:

1. Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on stderr (auto-enabled inside Claude Code via the harness's `CLAUDECODE=1` env var, and Cursor via `CURSOR_TRACE_ID`).
2. For local agent control, run `shipgate check --agent claude-code --workspace . --format agent-boundary-json` and read the stdout `shipgate.agent_boundary_result/v2` object. Switch on `control.state`; follow only `control.next_action`, `control.allowed_next_commands`, and `control.human_review`. When `pending_review[]` is non-empty the turn may finish, but name every carried review item in your summary — PR-time verify still routes them to a human. Treat `decision` as diagnostic context only.
3. For verifier runs, validate `agents-shipgate-reports/verification-receipt.json` first, then parse `agent-handoff.json`, `verifier.json`, and `verify-run.json`:
   `control.state`, `merge_verdict`, `can_merge_without_human`, `control.next_action`,
   `fix_task`, and `capability_review.top_changes`. Then parse
   `agents-shipgate-reports/report.json.release_decision.decision`; it is the
   release gate. Refresh with
   `agents-shipgate agent control --workspace .` — which refuses the read when
   HEAD, the tree, or the working tree has moved since the decision — before
   you act on any of that, and again
   before enforcing a cached `must_stop`, before commit/push/PR update, before
   merge, and before declaring the task complete. A non-zero exit means no
   control identity is current and you hold no authority; if
   `current_control_id` changed, discard every cached control state and restart
   from the new identity. A result remembered from earlier in the conversation
   never outranks the current pointer, in either direction. What that command prints depends on the installed CLI, so read `agents-shipgate contract --json` once: when it reports `agent_control_schema_version`, `agent control` returns that compact `shipgate.agent_control/v1` object — `control_state`, `permissions`, `next_actor`, `next_action` — and routing on it alone is enough, with `--format pointer` returning the raw `current-control.json`; when it does not, the CLI predates the envelope and `agent control` returns the raw pointer, whose fields are `lifecycle_state` and nested `control.state`.
4. Before editing `shipgate.yaml`, Shipgate CI, AGENTS/CLAUDE/Cursor rules, policy packs, baselines, waivers, suppressions, Codex hooks/config, Codex plugin manifests, `.mcp.json`, `.app.json`, or `SKILL.md`, plan to run `agents-shipgate verify` before completion and route trust-root review to a human when the verifier requires it.
5. Before finishing an agent-related diff, run `shipgate check --agent claude-code --workspace . --format agent-boundary-json`. For committed PR/CI verification, run `agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json` after making the base ref available. `verify` never fetches. For host grants, run `shipgate audit --host --json --out agents-shipgate-reports/host-grants.json`.
6. Do not bypass the verifier by suppressing findings, lowering severity, expanding baselines or waivers, removing Shipgate CI, or weakening agent instructions; verify-mode `SHIP-VERIFY-*` checks make those trust-root edits release-visible.
7. Confirm with the user before any command that writes files (`init --write`, `baseline save`).

## First-time CI setup (advisory)

If the user has no Shipgate CI yet, default to **advisory** mode — never strict, never with a baseline. The promotion path comes later, only after findings have been reviewed.

1. Confirm the repo has `shipgate.yaml` and a clean local scan (`agents-shipgate scan -c shipgate.yaml --ci-mode advisory` exits `0`). If not, run the bootstrap recipe first.
2. Create `.github/workflows/agents-shipgate.yml` from [`ci-recipes/advisory-pr-comment.yml`](ci-recipes/advisory-pr-comment.yml). It runs on every pull request, posts a summary comment, uploads the report as an artifact, and never fails the job.
3. Confirm `permissions: pull-requests: write` is acceptable to the user before committing — required for the PR comment.
4. Push and open a test PR. Verify the agents-shipgate comment appears.
5. **Stop here.** Promotion to strict mode is a separate task — only run [`prompts/stabilize-strict-mode.md`](prompts/stabilize-strict-mode.md) after the user has reviewed the advisory output and decided which findings they accept.

For non-GitHub CI (GitLab, CircleCI, Jenkins, Azure Pipelines, Buildkite, Bitbucket, pre-commit) refer to https://github.com/ThreeMoonsLab/agents-shipgate/tree/main/examples or `docs/integrations.md` in the upstream repo. Always start in advisory mode.

## Stable contracts (rely on these)

- **CLI surface** follows the current 0.x contract line — see https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md.
- **Installed CLI contract**: when available, run `agents-shipgate contract --json` to verify local schema versions, capability/research surfaces, `release_decision.decision`, and manual-review signal fields. Older installs should use [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md) or upgrade before automating against the local contract command.
- **Verifier JSON**: `verifier_schema_version: "0.13"`. Switch on `control.state`, then read `merge_verdict`, `can_merge_without_human`, `control.next_action`, `fix_task`, `capability_review.top_changes`, `trust_root_touched`, and `policy_weakened` before summarizing an AI-generated PR. `merge_verdict` is a deterministic projection; the gate remains `report.json.release_decision.decision`. Check `diff_status.completeness` before you believe any negative result: only `"complete"` means the PR diff was actually read. Anything else (`reason` is one of `not_attempted`, `refs_missing`, `merge_base_missing`, `unrelated_histories`, `objects_missing`, `metadata_limit_exceeded`, `body_limit_exceeded`, `git_timeout`, `git_failed`) means evidence was missing: follow `remediation`, and never report the PR as unrelated to agent capabilities. Then read `trigger.evaluation_status` for what that cost. `"not_evaluated"` (with `trigger.should_run` `null`) means no verdict exists. `"evaluated"` on an incomplete diff is not a contradiction — evidence that did not depend on the missing bytes already proved Shipgate should run — so honor `should_run: true` instead of overriding it, and still recover the diff before trusting a merge verdict. That evidence is either a rule matched on the change set or, in an already-adopted repository, `force_run: true` from the manifest alone; check `matched_rules` before attributing the verdict to anything the diff showed.
- **Current control pointer**: `agents-shipgate-reports/current-control.json` uses `schema_version: "shipgate.current_control/v1"` and is the one entry point naming which control identity is current. It is invalidated to `lifecycle_state: "in_progress"` before a run starts and published atomically last, so an interrupted run leaves a directory that denies cached control rather than one that still authorizes it. Only a `verify` pointer can carry `control.state: "complete"`, and only with a bound `verification_receipt` that closes that exact request; a `scan` or `preview` pointer never authorizes completion or merge. Reading it is workspace-checked: byte-intact artifacts still describe a workspace that a single commit has moved past, so the reader compares the bound HEAD, tree, and worktree overlay against the live repository and refuses on drift.
- **Verification receipt**: `verification-receipt.json` uses `schema_version: "shipgate.verification_receipt/v1"` and is written last. Validate it before trusting any projected verdict; it content-addresses the request, executor, unit result, decision, and complete artifact set.
- **Verify run JSON**: `verify-run.json` uses `schema_version: "shipgate.verify_run/v4"`, embeds the content-addressed plan and executor, and binds unit-result and decision IDs. `run_id` is an exact compatibility alias of `request_id`; do not treat the run projection as a second gate.
- **Report JSON**: `report_schema_version: "0.39"`. Read `release_decision.decision` first. A `passed` decision requires a complete root-reachable static binding graph plus complete, conflict-free identity, effect, and authority evidence for every reachable action; it does not prove runtime behavior. Preserve `release_decision.static_analysis_only=true`, `runtime_behavior_verified=false`, and `static_verdict_disclaimer` in summaries. Read `release_decision.evidence_coverage.binding_coverage`, `semantic_coverage`, `identity_coverage`, and `policy_gap_count`, then work every `evidence_gaps[].next_action` in order. Binding, semantic, and policy-applicability gaps are not Findings and cannot be suppressed, baselined, severity-overridden, cleared by `--no-heuristics`, or satisfied by `human_ack`; binding, effect, and authority declarations are human assertions and must never be auto-written. Use `tool_catalog[]` for diagnostics and `tool_inventory[]` for the proven reachable surface, and read `surface_exclusions` for what the run removed from analysis — a `newly_unbound_tool` row is a capability this change introduced that no check judged. The current schema is [`docs/report-schema.v0.39.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.39.json); v0.37 is a frozen compatibility reference. See the [current agent contract](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md), [verification identity contract](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/verification-reproducibility.md), and [evidence-backed passed contract](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/passed-verdict-contract.md).
  These binding-backed fields require runtime contract v13; the unambiguous `AgentControl` projection requires runtime contract v14. A contract-v12 CLI emits the frozen v0.30 report and must not be described using the v0.31 binding claim.
- **Release Evidence Packet**: `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras) is a supporting/provisional reviewer artifact. Packet v0.15 projects the verification request and decision IDs plus binding and semantic coverage; it never creates a second gate. See [`docs/packet-schema.v0.16.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/packet-schema.v0.16.json) and [STABILITY.md §Release Evidence Packet](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md#release-evidence-packet-v016).
- **Capability standard**: `agents-shipgate capability export` emits a stable static capability lock (`capability_lock_schema_version: "0.7"`) and `agents-shipgate capability diff` emits a stable semantic diff (`capability_lock_diff_schema_version: "0.8"`). These artifacts implement capability standard v0.5, are supporting/provisional and non-gating, exclude runtime trace evidence, and are documented in [`docs/capability-standard.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/capability-standard.md).
- **Governance benchmark**: `benchmark/agent-pr-governance/cases.yaml` and `scripts/run_governance_benchmark.py` are the stable research benchmark substrate (`governance_benchmark_result_schema_version: "0.2"`), not a release gate. See [`docs/governance-benchmark.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/governance-benchmark.md).
- **Single source of truth for the contract**: [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md). When the schema bumps, that file updates first.
- **Exit codes**: `0` pass, `2` config error, `3` parse error, `4` other error, `20` strict-mode gate failure.
- **Check IDs** (e.g. `SHIP-POLICY-APPROVAL-MISSING`) are stable; new ones may be added but existing ones will not be renamed or repurposed.

## Boundaries (do not violate)

- Do not claim a finding is fixed without re-running `agents-shipgate verify` and reporting the new merge verdict and release decision.
- Do not silently suppress findings — `checks.ignore` requires a `reason` and the manifest validator rejects empty reasons.
- Do not commit `agents-shipgate-reports/` — it's regenerated each run; add it to `.gitignore`.
- Do not run `agents-shipgate baseline save` until the user has reviewed the initial findings; baselining ratchets in noise.
- Do not enable strict CI as the first CI step. Always start advisory.
- Do not modify checks in `agents-shipgate`'s own source — that's upstream repo work.
- Do not weaken Shipgate trust roots to make a verifier run pass. Policy, baseline,
  waiver, CI, trigger-catalog, and agent-instruction changes require human review.

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
