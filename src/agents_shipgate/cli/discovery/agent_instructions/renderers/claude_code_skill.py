"""Render the repo-scoped Claude Code skill bundle.

The canonical checked-in copy lives under ``skills/agents-shipgate``.
This renderer deliberately keeps a hard-coded copy so the installed wheel can
generate the skill without relying on repository files being present.
Snapshot tests keep the two copies in sync.
"""

from __future__ import annotations

from agents_shipgate import __version__


def render_files() -> dict[str, str]:
    """Return relative file path -> UTF-8 text for the Claude Code skill bundle."""
    return {
        ".claude/skills/agents-shipgate/SKILL.md": _SKILL_MD,
        ".claude/skills/agents-shipgate/prompts/add-shipgate-to-repo.md": _ADD_SHIPGATE_MD,
        ".claude/skills/agents-shipgate/prompts/decide-shipgate-relevance.md": _DECIDE_RELEVANCE_MD,
        ".claude/skills/agents-shipgate/prompts/explain-finding-to-user.md": _EXPLAIN_FINDING_MD,
        ".claude/skills/agents-shipgate/prompts/fix-top-finding.md": _FIX_TOP_FINDING_MD,
        ".claude/skills/agents-shipgate/prompts/recommend-fixes.md": _RECOMMEND_FIXES_MD,
        ".claude/skills/agents-shipgate/prompts/stabilize-strict-mode.md": _STABILIZE_STRICT_MD,
        ".claude/skills/agents-shipgate/prompts/triage-false-positive.md": _TRIAGE_FP_MD,
        ".claude/skills/agents-shipgate/prompts/upgrade-shipgate-version.md": _UPGRADE_VERSION_MD,
        ".claude/skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml": _ADVISORY_CI_YML,
    }


def render_bundle_text() -> str:
    """Return a human-readable dry-run rendering of the full bundle."""
    chunks: list[str] = []
    for path, text in render_files().items():
        chunks.append(f"--- {path} ---\n{text.rstrip()}\n")
    return "\n".join(chunks)


# SHA-256 hashes of every prior render, keyed by bundle-relative file path.
# When a rendered file changes after the first shipped Claude Code skill
# release, move that file's previous current-render hash into this dict so
# `init --agent-instructions=claude-code-skill --write` can safely migrate
# v(N-1) files. Leave the dict empty while there is no prior shipped Claude
# Code skill bundle.
PRIOR_RENDER_SHA256: dict[str, tuple[str, ...]] = {}

_ACTION_VERSION = __version__


_SKILL_MD = """\
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
- **Report JSON**: `report_schema_version: "0.20"`. Read `release_decision.decision` (`"blocked" | "review_required" | "insufficient_evidence" | "passed"`) **first** for release gating — it is baseline-aware. `insufficient_evidence` (added v0.14) fires when evidence coverage is degraded past threshold (at least half of scanned tools low-confidence — `ceil(N × 0.5)` with a minimum of 1 — or 4+ source warnings); switch on the enum with a `review_required` fallback for unknown values. For privacy audit read `privacy_audit` (v0.18+) to confirm default redaction ran before public artifacts were written; `redacted_paths[]` contains structural paths and counts only. For severity-override audit read the top-level `policy_audit.severity_overrides_applied[]` block (v0.17+) — every manifest-driven severity change carries `{check_id, default_severity, applied_severity, manifest_path, reason, tier_crossed, direction, expires}`. For per-finding decision audit read `release_decision.contribution_rules[]` (v0.17+) — one row per `report.findings` entry with `category` ∈ `{blocker, review_item, excluded}` and `rule` ∈ `{policy_block_new, severity_block_new, policy_baseline_accepted, severity_baseline_accepted, review_required, sub_threshold, suppressed}`. For Action Surface Diff read `action_surface_facts`, `action_surface_diff`, and `findings[].blocks_release` (v0.16+) to understand added/removed/modified external actions and explicit release-policy blockers. For one-fetch summarization read the top-level `agent_summary` block (v0.12+) — `{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}`. For per-finding routing read `findings[].agent_action` (v0.12+; `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`) instead of synthesizing one from `autofix_safe`/`requires_human_review`/`suggested_patch_kind`. To filter findings by source reliability read `findings[].provenance_kind` (v0.15+; `static_declaration | ast_extraction | keyword_heuristic | regex_heuristic | policy_pack`) — independent of `confidence`. Codex plugin facts, when present, live under `codex_plugin_surface` (v0.13+). Do not gate on `summary.status` for new consumers; it is preserved for v0.7 callers and is baseline-blind. The full field list lives in [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md#read-these-first-for-release-gating); this skill links there instead of restating it. v0.11 adds optional `findings[].source.{path, start_line, end_line, start_column, pointer}` provenance keys (kept in v0.19). v0.19 adds the optional `Finding.policy_evidence_source` and `ReleaseDecisionItem.{source, policy_evidence_source}` fields for reviewer-grade dual-source provenance — high-risk findings that fire because of a missing manifest mitigation can carry both the tool location AND the manifest-pointer line. Reports validate against [`docs/report-schema.v0.20.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.20.json) (active emitted version). Frozen-reference older schemas (kept for legacy/pre-v0.19 reports): [`v0.18`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.18.json), [`v0.17`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.17.json), [`v0.16`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.16.json), [`v0.15`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.15.json), [`v0.14`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.14.json), [`v0.13`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.13.json), [`v0.12`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.12.json), [`v0.11` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.11.json), [`v0.10` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.10.json), [`v0.9` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.9.json), [`v0.8` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.8.json), and [`v0.7` (frozen)](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/report-schema.v0.7.json).
- **Release Evidence Packet**: `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras) is emitted alongside the report by default. The packet has fixed reviewer sections governed by [`docs/packet-schema.v0.6.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/packet-schema.v0.6.json) (latest; v0.6 adds the top-level `evidence_matrix` compact review section AND `ReleaseDecisionItem.{source, policy_evidence_source}` for reviewer-grade dual-source provenance over the v0.5 baseline). See [STABILITY.md §Release Evidence Packet](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md#release-evidence-packet-v06). Use the packet for reviewer-shaped output; use the report for finding details.
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
"""


_ADD_SHIPGATE_MD = """\
# Prompt · Add Agents Shipgate to a repo

You are working in a repo that may contain an AI agent — likely one of: an MCP server tool list (`*mcp*.json` or `.agents-shipgate/*.json`), an OpenAPI spec the agent calls, a Codex plugin package (`.codex-plugin/plugin.json`) or marketplace (`.agents/plugins/marketplace.json`), a Python file with `@function_tool` / `@tool` decorators (OpenAI Agents SDK, LangChain, CrewAI), a Google ADK agent in `agent.py`, an Anthropic Messages API artifact set under `prompts/`/`tools/anthropic-tools.json`/`policies/anthropic-policy.yaml`, or an OpenAI API artifact set under `prompts/`/`tools/openai-tools.json`/`openai-config.json`.

Your job is to drive the canonical 4-call flow end-to-end in one tool-using
turn, which adds a local-first, static Tool-Use Readiness release gate.

## Your task

1. **Install the tool:**
   ```bash
   pipx install agents-shipgate
   ```
   If `pipx` is unavailable, use `python -m pip install agents-shipgate` and verify with `agents-shipgate --version`.

2. **Sanity-check the install** before touching the user's code:
   ```bash
   agents-shipgate self-check --json
   ```
   Confirm `"ready": true`. If not, surface the failure to the user.

   When available, verify the installed CLI contract locally:
   ```bash
   agents-shipgate contract --json
   ```
   Read `report_schema_version`, `packet_schema_version`, `gating_signal`, and
   `manual_review_signals[]`; prefer these local values over stale docs. If the
   command is not recognized on an older install, continue after `self-check`
   using [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md)
   and upgrade before relying on local contract verification in automation.

3. **Detect:**
   ```bash
   agents-shipgate detect --workspace . --json
   ```
   Read the response: `is_agent_project`, `frameworks[]` (per-framework score + evidence + candidate files), `agent_name_candidates[]`, `suggested_sources[]` (MCP/OpenAPI files matched by glob).

   **Stop only when ALL of these hold:** `is_agent_project: false`, `suggested_sources` is empty, `codex_plugin_candidates` is empty, no `shipgate.yaml` already exists in the workspace, AND the user did not explicitly request a scan. Otherwise proceed — MCP/OpenAPI tool-surface repos and Codex plugin package repos register as `is_agent_project: false` because they have no Python framework imports, but they are valid Shipgate targets. MCP/OpenAPI hits surface as `suggested_sources`; Codex plugin hits surface as `codex_plugin_candidates`.

4. **Generate a starter manifest + GitHub Actions workflow:**
   ```bash
   agents-shipgate init --workspace . --write --ci --json
   ```
   The `--json` form returns:
   - `manifest_status`: `"written"` | `"skipped_existing"` | `"not_attempted"`
   - `workflow.status` (with `--ci`): `"written"` | `"skipped_existing_target"` | `"skipped_cross_reference"`
   - `placeholders[]` — entries the template intentionally left as `CHANGE_ME` because no high-confidence signal was available
   - `auto_detected.agent_name` — the value the manifest carries (`null` when the template fell back to `CHANGE_ME`)

   `--ci` writes `.github/workflows/agents-shipgate.yml` orthogonally to `--write`. Each gets its own overwrite-refusal check; existing workflows that already call `ThreeMoonsLab/agents-shipgate` skip with a distinct `cross_reference_path`.

5. **Replace placeholders.** Walk `placeholders[]` from the JSON output. On a fresh workspace the template typically leaves two:
   - `agent.name: CHANGE_ME` — replace with the agent's actual role (no strong `Agent(name="…")` literal was found in the source).
   - `agent.declared_purpose[]: CHANGE_ME` — replace with a one-line description of what the agent should do (auto-init can't infer this; the schema requires a non-empty value).

   Read the agent's prompt or main file to derive both. Skipping this leaves an invalid adoption artifact — the manifest validates but downstream consumers see meaningless defaults.

6. **Run the scan with patch suggestions:**
   ```bash
   agents-shipgate scan -c shipgate.yaml --suggest-patches --format json --ci-mode advisory
   ```
   The report lands at `agents-shipgate-reports/report.json`. The Release Evidence Packet lands at `agents-shipgate-reports/packet.{md,json,html}`. Parse `report.json`; Codex plugin facts, when present, live under `codex_plugin_surface`.

   **Read these first for release gating (v0.8+):**
   - `release_decision.decision` ∈ `{"blocked", "review_required", "insufficient_evidence", "passed"}` — baseline-aware. This is the gating signal. `insufficient_evidence` (v0.14+) fires when evidence coverage is degraded past threshold; treat unknown future values as `review_required`.
   - `release_decision.{reason, blockers, review_items, fail_policy.would_fail_ci}`

   **Read these for release review (v0.9+):**
   - `capability_facts[]`, `declared_intentions[]`, `misalignments[]`, `release_consequence`, `suggested_scenarios[]`

   **Per-finding fields:**
   - `check_id`, `severity`, `category`, `tool_name`, `recommendation`, `suppressed`
   - `autofix_safe`, `requires_human_review`, `suggested_patch_kind`, `docs_url` (v0.7+)
   - `patches[]` (only with `--suggest-patches`) — each has `kind` ∈ `{set_pointer, append_pointer, remove_pointer, manual}` plus `confidence` + `target_file` + etc. for non-manual kinds.

   **Top-level:** `manifest_dir` (absolute path of the manifest's directory — used by `apply-patches` for the containment check). `summary.{status, critical_count, high_count, medium_count}` is preserved for v0.7 callers and is baseline-blind — do not gate on `summary.status` for new consumers. Full contract: [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md).

7. **Apply the safe patches:**
   ```bash
   agents-shipgate apply-patches --from agents-shipgate-reports/report.json --confidence high --apply --json
   ```
   Default `--confidence high` only mutates patches whose `confidence` field is `"high"`. Today that's the 3 stale-manifest removals. Scope-coverage appends ship at `medium` and require explicit `--confidence medium` to apply. ManualPatches are never auto-applied.

   **Decision tree** for walking the report:
   ```
   for finding in active_findings:
       if finding.suggested_patch_kind in ("manual", "none"):
           surface_to_user(finding)              # Surface; do NOT auto-apply.
           continue
       if finding.autofix_safe is True:
           plan_to_apply(finding)                # Will be applied at --confidence high.
           continue
       surface_for_medium_review(finding)        # Medium-confidence — opt-in only.
   ```

   Trace findings (`SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING`) are permanent ManualPatch by policy. Implement the runtime gate; never edit the trace recording — that patches the evidence, not the agent. See [`docs/autofix-policy.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/autofix-policy.md) for the full classification.

8. **Add `agents-shipgate-reports/` to `.gitignore`** if it isn't already. The reports are scan artifacts, not source.

9. **Report back to the user**:
   - `release_decision.decision` and `release_decision.reason` (the gating signal — baseline-aware, v0.8+)
   - Blocker / review-item counts (`len(release_decision.blockers)` / `len(release_decision.review_items)`)
   - The path to the Release Evidence Packet (`agents-shipgate-reports/packet.md`) for reviewer-shaped output
   - The top 3 active critical/high findings (use `report.json`, not stdout)
   - Which patches were applied (count from `apply-patches --json` output's `files`)
   - Any check IDs the user should investigate first — link to `docs_url` from the finding for full rationale, or use `agents-shipgate explain <CHECK_ID> --json` for the same content via CLI

## What to do if the scan errors out

Set `AGENTS_SHIPGATE_AGENT_MODE=1` and re-run. The CLI will append a JSON line to stderr with `{error, message, next_action}`. Follow the `next_action`.

Common errors and fixes:

| Error | Fix |
|---|---|
| `Config file not found: shipgate.yaml` | Run `agents-shipgate init --workspace . --write` first |
| `Input path '...' resolves outside manifest directory` | The declared `tool_sources[].path` is outside the manifest dir. Move the spec inside the tree, symlink it, or copy it |
| `Invalid shipgate.yaml: ... Did you mean X?` | A field is at the wrong nesting level; move it as suggested |
| `Containment violation` (apply-patches exit 5) | A patch's `target_file` resolved outside `report.manifest_dir`. Re-run scan to refresh; never patch arbitrary system files |

## What NOT to do

- Do **not** commit `agents-shipgate-reports/` — it's regenerated each run.
- Do **not** run `agents-shipgate baseline save` until the user has reviewed the initial findings. Baselining ratchets in noise that strict CI will silently ignore. The right time to baseline is **after** the user has decided which findings they accept.
- Do **not** suppress findings without a real `reason` — the manifest validator rejects empty reasons, and the `reason` field is the audit trail when someone asks "why is this OK?"
- Do **not** use `risk_overrides.tools.{tool}.remove_tags` to silence a finding without checking whether the heuristic is actually wrong. Prefer `checks.ignore` with a reason.
- Do **not** edit a trace recording to flip `approved` or `confirmed` — implement the runtime gate instead.

## Verification before reporting success

- `agents-shipgate-reports/report.json` exists and parses as JSON
- `report.json` carries `report_schema_version: "0.11"` (or higher) and a non-empty `manifest_dir`
- `report.json` carries a non-null `release_decision.decision` — this is the field to surface to the user
- `shipgate.yaml` has no `CHANGE_ME` values (comments containing the literal `CHANGE_ME` are informational and OK)
- `.gitignore` contains `agents-shipgate-reports/` (or equivalent)
- If `--ci` ran with `workflow.status: "written"`: `.github/workflows/agents-shipgate.yml` exists and references `ThreeMoonsLab/agents-shipgate@v…`
- The user knows the top 3 findings and at least one suggested next step
"""


_DECIDE_RELEVANCE_MD = """\
# Prompt · Decide whether Agents Shipgate is relevant

You are working in a repo or reviewing a PR and need to decide whether
to propose Agents Shipgate as the next step. The other prompts in
[`prompts/`](https://github.com/ThreeMoonsLab/agents-shipgate/tree/main/prompts)
assume relevance is already established — this one runs **before** that
decision and tells you yes or no with a rationale.

The decision is fully data-driven: it does not depend on prose-reading.
[`docs/triggers.json`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/triggers.json) is the machine-readable
mirror of the AGENTS.md trigger table; you fetch (or read) it and apply
the rules to the changed file list.

## Your task

1. **Identify the changed file set.** Repo-relative, forward slashes:
   - PR context: `git diff --name-only origin/main...HEAD`
   - Working tree: `git status --short` (uncommitted)
   - User-pasted diff: parse `diff --git a/<path> b/<path>` headers

2. **Fetch the trigger catalog.** Either:
   - **Local repo** (already adopted Shipgate): read `docs/triggers.json` directly.
   - **Remote** (target repo without Shipgate): fetch
     `https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/triggers.json`.
   - The catalog has `schema_version: "0.1"` and is stable for `0.x`.

3. **Apply the rules.** Two equivalent options:

   **Option A — read the JSON yourself.** Walk `rules[]`. For each rule,
   evaluate `rule.when` against the changed file list **and** the unified
   diff body — several rules use `diff_contains` predicates (e.g.
   `@function_tool`) that a path-only listing cannot satisfy. The
   predicate vocabulary is documented in `triggers.json` under
   `predicate_vocabulary`; the action precedence is in
   `action_precedence`. See the decision tree below.

   **Option B — call the bundled evaluator** (when Shipgate is installed).
   Use the `--git-diff` flag so paths AND diff body come from git in one
   call; piping `git diff --name-only` alone causes `diff_contains` rules
   (decorators, framework tokens, Action URL) to silently never fire:
   ```bash
   agents-shipgate self-check --json    # confirm install
   python -m agents_shipgate.triggers \\
       --git-diff origin/main...HEAD --json
   ```
   For uncommitted changes pass `--git-diff` with no revspec — that
   runs `git diff HEAD` (covers BOTH staged and unstaged tracked
   changes) plus `git ls-files --others --exclude-standard` to add
   untracked file paths. Untracked files contribute paths only; their
   content is not in `diff_text`, so `diff_contains` rules won't fire
   on a brand-new file until you `git add` it (or pass `--diff-text`
   manually). If your repo already has a manifest, also pass
   `--manifest-present` so the `force_run` rule can fire.
   The output shape is `{run_shipgate, dry_run_recommended,
   matched_rules, stop_conditions_fired, rationale, schema_version}`.

4. **Emit the decision.** Always reply in this exact JSON shape so
   downstream automation can parse you:

   ```json
   {
     "run_shipgate": true,
     "dry_run_recommended": false,
     "matched_rules": [
       {"id": "TRIGGER-MCP-EXPORT-CHANGED", "action": "run_shipgate"}
     ],
     "rationale": "PR adds tools/mcp-export.json; MCP exports are a tool surface."
   }
   ```

5. **Then act.**
   - If `run_shipgate: true` and Shipgate is installed:
     ```bash
     agents-shipgate detect --workspace . --json
     ```
     Then follow [`prompts/add-shipgate-to-repo.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/add-shipgate-to-repo.md)
     for the canonical 4-call flow.
   - If `run_shipgate: true` and Shipgate is **not** installed: install
     it (`pipx install agents-shipgate`) and run `detect`. If the user
     prefers a zero-install first step, point them at the GitHub Action
     (`ThreeMoonsLab/agents-shipgate@v0.10.0`) instead.
   - If `run_shipgate: false` and `dry_run_recommended: true`: propose
     a non-mutating scan only — never propose `init --write` based on a
     dry-run match alone. Phrase it as "X may have shifted the tool
     surface; want me to run a read-only scan against the existing
     manifest?" If there's no existing manifest, surface the
     `matched_rules` and let the user choose.
   - If `run_shipgate: false` and `dry_run_recommended: false`: **do
     not propose Shipgate.** Recommend whatever the actual review need
     is (lint, type check, unit test, security scan). Mentioning
     Shipgate when no rule matched is noise.

## Decision tree (when reading `triggers.json` by hand)

```
For each changed file path AND the unified diff body:
    For each rule in triggers.rules:
        if rule.when matches → record (rule.id, rule.action)

stop_fired := every clause in triggers.stop_conditions holds
              (requires running detect first; if you haven't, treat as false)

# Action precedence (highest first), see triggers.json:action_precedence:
if stop_fired:                                 → run = false
elif any action == "force_run":                → run = true   (manifest present)
elif any action == "skip_shipgate":            → run = false  (skip beats run)
elif any action == "run_shipgate":             → run = true
elif any action == "dry_run":                  → run = false, dry_run_recommended = true
else:                                          → run = false  (no rules matched)
```

Why `skip_shipgate` beats `run_shipgate`: a brittle `diff_contains` match
(e.g. `@tool` mentioned in README prose) should not override the explicit
"this is a docs-only PR with no tool surface impact" signal.

Why `force_run` overrides `skip_shipgate`: an existing `shipgate.yaml` is
the operational opt-in; even a docs-only PR in such a repo gets scanned
because the cost is low (advisory) and tool-adjacent prose changes can
matter.

## What NOT to do

- Do **not** propose Shipgate based on filename guesses ("looks like an
  AI agent"). The trigger catalog is the source of truth — if no rule
  matches, the answer is no.
- Do **not** silently fall back to "yes, run it" when you can't fetch
  `triggers.json`. Surface the fetch failure to the user and ask.
- Do **not** invent rule IDs in the output. Every entry in
  `matched_rules` must come from `triggers.json`.
- Do **not** treat the **negative control** ("update docs only") as a
  reason to propose Shipgate. The `TRIGGER-DOCS-ONLY-NEGATIVE` rule
  fires `skip_shipgate` for a reason — and it covers test-only PRs
  too, not just `*.md`.
- Do **not** propose `agents-shipgate init --write` on a `dry_run`-only
  match. `dry_run_recommended: true` justifies a non-mutating `scan`
  against an existing manifest, nothing more.
- Do **not** rely on bare `--git-diff` for brand-new untracked files
  to fire `diff_contains` rules. Bare flag covers tracked changes
  (staged + unstaged) and untracked file *paths*, but not untracked
  file *content*. `git add` first, or pass `--diff-text` explicitly.

## Verification before reporting

- Output is valid JSON with the keys `run_shipgate`,
  `dry_run_recommended`, `matched_rules`, `rationale`.
- Every `matched_rules[].id` exists in the loaded `triggers.json`.
- If `run_shipgate: true`, the next-step command is named.
- If `run_shipgate: false` AND `dry_run_recommended: true`, exactly
  one Shipgate command appears (a non-mutating `scan` against an
  existing manifest) — never `init --write`.
- If `run_shipgate: false` AND `dry_run_recommended: false`, no
  Shipgate command appears anywhere in your reply.
"""


_EXPLAIN_FINDING_MD = """\
# Prompt · Explain a single Agents Shipgate finding to a user

You need to translate one `report.json` finding into prose for a human
who has never read the Shipgate docs. Companion to `agents-shipgate
explain-finding <fingerprint>`, which gives you the structured payload
you'll quote.

This is for the moment when an agent has run a scan, identified the top
finding (via `agent_summary.first_recommended_action.why` or by walking
`findings[]`), and now has to summarize it for a PR comment, chat
reply, or commit message. The user shouldn't have to follow a doc link
to understand what's going on.

## Your task

1. **Get the fingerprint.** Read it from `agent_summary.first_recommended_action.why` if that names a `check_id` and tool, then look up the matching `findings[].fingerprint` in `report.json`. Otherwise pick the highest-severity active finding (`critical > high > medium > low`) and read `fingerprint` directly from that entry.

2. **Run `explain-finding` to get the structured payload.**
   ```bash
   agents-shipgate explain-finding <FINGERPRINT> \\
       --from agents-shipgate-reports/report.json --json
   ```
   The output carries:
   - `check_id`, `title`, `severity`, `category` — what the check is.
   - `tool_name`, `tool_id` — the affected tool (may be null for manifest-level checks).
   - `evidence` — the structured evidence the check captured.
   - `recommendation` — the check author's verbatim suggested fix.
   - `agent_action` — `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`.
   - `metadata` — full `CheckMetadata` (rationale, fires_when, evidence_fields, docs_url) when the check is in the catalog.
   - `explanation` — a deterministic 3–5-sentence prose summary you can quote verbatim or rewrite.

3. **Write the prose for the user.** Three to five sentences, in this order:
   1. **What.** Name the check (`check_id` is fine), the affected tool (`tool_name`), and the severity in one sentence. If the check has no `tool_name`, name what the check examined (e.g. "the manifest", "permissions").
   2. **Why it matters.** Pull from `metadata.rationale` or `metadata.fires_when`. If neither exists, paraphrase the `recommendation`. Avoid verbatim verbose catalog text — translate "limited automation review" into plain English.
   3. **What you'll do (or want).** Map `agent_action` to a concrete next step:
      - `auto_apply`: "I can apply the fix automatically — say yes and I'll run `apply-patches --confidence high --apply`."
      - `propose_patch_for_review`: "There's a suggested patch but the confidence is medium/low (or there's a manual sibling). Want me to show the diff before applying?"
      - `escalate_to_human`: "There's no automatic fix. Here's the recommended remediation: [paraphrase recommendation]. Want me to draft the change for you to review?"
      - `suppress_with_reason`: "If you want to accept this risk, I can add a suppression with reason. What should the reason say?"
      - `informational`: "No action needed; flagging for awareness."
   4. *(Optional)* **Where to learn more.** If `metadata.docs_url` exists, link it.
   5. *(Optional)* **Suppression status.** If `suppressed` is true, mention that — otherwise omit.

4. **Cite evidence sparingly.** Only quote a specific evidence value when it makes the explanation concrete (e.g. naming the broken parameter, the file path from `source.ref`). Do not dump the whole `evidence` dict.

5. **Format for the surface.** PR comments and chat support markdown — use a code span for `check_id` and `tool_name`. Plain text emails should drop the backticks but keep the structure.

## Example

Input (from `explain-finding fp_f092940f62fbb012 --from ... --json`):
```json
{
  "check_id": "SHIP-POLICY-APPROVAL-MISSING",
  "severity": "critical",
  "tool_name": "stripe.create_refund",
  "agent_action": "escalate_to_human",
  "recommendation": "Declare an approval policy or remove the tool.",
  "metadata": {
    "rationale": "High-risk actions need explicit approval before promotion.",
    "fires_when": "Financial/destructive risk exists without approval policy."
  }
}
```

Good prose for a PR comment:

> The Tool-Use Readiness scan flagged a critical issue: `stripe.create_refund` doesn't declare an approval policy in `shipgate.yaml`. High-risk actions like refunds need an explicit human approval gate before they can ship — without one, an agent could trigger a refund on its own without review. There's no automatic fix here. The right remediation is to either add `policies.require_approval_for_tools: [stripe.create_refund]` (with a reviewer-visible approval trace) or remove the tool from this release surface. Want me to draft the manifest change for you?

Bad prose for the same input:

> Finding `fp_f092940f62fbb012`: `SHIP-POLICY-APPROVAL-MISSING` fired with severity `critical` on `stripe.create_refund`. autofix_safe=false, requires_human_review=true. evidence: risk_tags=[financial_action, destructive]. recommendation: "Declare an approval policy or remove the tool."

The bad version is true but unreadable — it dumps the JSON instead of translating it.

## What NOT to do

- Do **not** quote the structured `explanation` field verbatim if it's robotic. It's a deterministic baseline; rewrite for tone when needed.
- Do **not** fabricate consequences. If the check's `rationale` doesn't say "could trigger a refund," don't say it. Stay grounded in catalog text.
- Do **not** propose `apply-patches` for `escalate_to_human` findings — the user has to decide on the fix manually.
- Do **not** propose adding a `checks.ignore` entry as the default response. Suppression is a real choice, but it's the last resort and needs an audit-trail-quality reason. Use the [`triage-false-positive.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/prompts/triage-false-positive.md) prompt for that workflow.
- Do **not** include the fingerprint string in the user-facing prose unless they specifically asked for it. Fingerprints are agent-to-agent identifiers, not human-friendly labels.

## Verification before sending the message

- The user-facing prose names the affected tool (or what the check examined) at least once.
- The severity is mentioned somewhere (a word like "critical" or "medium-severity" — not just the JSON token).
- The action sentence matches the finding's `agent_action`. If the message says "I'll apply this automatically," `agent_action` must be `auto_apply`.
- No raw JSON dumps in the prose — translate, don't quote.
- If `metadata.docs_url` exists, include it (or link text equivalent).
"""


_FIX_TOP_FINDING_MD = """\
# Prompt · Fix the top Agents Shipgate finding

You are working in a repo with `shipgate.yaml` already in place. Run a scan and fix the highest-severity unsuppressed finding.

## Your task

1. **Run a scan and locate the top finding.**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```
   Read `agents-shipgate-reports/report.json`. For v0.12+ reports the easy path is `agent_summary.first_recommended_action.why` — for most `blocked`/`review_required` verdicts it names the top finding's `check_id` and `tool_name` directly. Three exceptions to expect:

   - **`insufficient_evidence` verdict** (v0.14+; the scan saw too many low-confidence tools or 4+ source warnings to gate release). There is no specific finding to fix; the action's `why` describes the evidence situation and recommends gathering deeper sources (MCP/OpenAPI inputs, eval traces, additional source files). Follow that guidance instead of looking for a top finding.
   - **Evidence-coverage-driven `review_required`** (sub-threshold low-confidence/static evidence; no specific finding to fix). The action's `why` describes the evidence situation and recommends gathering MCP/OpenAPI inputs or eval traces — there is no `check_id` to parse out. If you see "low-confidence evidence" or "static-only" in the why-text, follow that guidance instead of looking for a top finding.
   - **`auto_appliable_patches > 0`**. The action proposes `apply-patches`; the why-text names the apply-patches command, not a specific finding. Walk `findings[]` for the actual top entry.

   Fall back to picking the entry with the highest severity (`critical > high > medium > low > info`) and `"suppressed": false` whenever the action doesn't name a finding directly.

2. **Look up the check definition.**
   ```bash
   agents-shipgate explain <CHECK_ID> --json
   ```
   This returns the `CheckMetadata` with `description`, `rationale`, `fires_when`, `evidence_fields`, `recommendation`.

3. **Diagnose the fix.** There are exactly four legitimate responses to a finding. v0.12+ reports project the routing via `agent_action`:

   | Response | When | `agent_action` (v0.12+) |
   |---|---|---|
   | **Add the missing policy / scope / annotation** to `shipgate.yaml` | The check is correct; the manifest just hadn't declared the safeguard yet | `propose_patch_for_review` (a `set_pointer`/`append_pointer` patch is attached) or `escalate_to_human` (no patch — you write the entry by hand) |
   | **Override the heuristic** via `risk_overrides.tools.{tool}.{tags,remove_tags}` | The risk classification is wrong (e.g. a GET endpoint that picked up the `destructive` tag because of a misleading operationId) | `escalate_to_human` |
   | **Suppress the finding** via `checks.ignore` with a `reason` | The check is correct but you've decided to accept the risk explicitly (e.g. "tool deprecated 2026-Q2") | `escalate_to_human` (the future `suppress_with_reason` value is reserved for checks that pre-classify themselves as suppressible) |
   | **Fix the underlying tool definition** | The tool spec itself is wrong (missing description, broad scope, free-form action field) | `escalate_to_human` |

4. **Apply the fix.** Edit either `shipgate.yaml` or the tool source file. Do not delete tools wholesale to silence findings.

5. **Re-scan and confirm the count went down.**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```
   The previously-failing fingerprint should be gone from `report.json`.

6. **Report back**:
   - What was the original finding (check ID, tool, severity)
   - Which of the four response types you used
   - The diff to `shipgate.yaml` (or other file) you applied
   - The new finding count

## Common fixes by check ID

| Check | Typical fix |
|---|---|
| `SHIP-POLICY-APPROVAL-MISSING` | Add the tool to `policies.require_approval_for_tools` with a reason |
| `SHIP-POLICY-CONFIRMATION-MISSING` | Add the tool to `policies.require_confirmation_for_tools` |
| `SHIP-SIDEFX-IDEMPOTENCY-MISSING` | Add an `idempotency_key` parameter, set `idempotentHint: true` annotation, or list under `policies.require_idempotency_for_tools` |
| `SHIP-AUTH-MISSING-SCOPE` | Declare the scope on the tool (in OpenAPI security or MCP metadata) and in `permissions.scopes` |
| `SHIP-AUTH-MANIFEST-BROAD-SCOPE` | Replace `*` / `admin` with the specific operation scope(s) |
| `SHIP-DOC-MISSING-DESCRIPTION` | Add a 20+ char description to the tool definition |
| `SHIP-SCHEMA-BROAD-FREE-TEXT` | Constrain the parameter with an enum, structured schema, or narrower fields |
| `SHIP-SCHEMA-MISSING-BOUNDS` | Add `maximum` to the numeric parameter |
| `SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE` | Declare the tools through MCP/OpenAPI for higher-confidence inventory; or move target to staging |

## What NOT to do

- Do not blanket-suppress an entire check. Suppressions are per-tool unless the check is genuinely irrelevant for this repo.
- Do not write `reason: "false positive"` without explanation. Reviewers should be able to read the reason and understand the decision in 60 seconds.
- Do not edit `agents-shipgate-reports/`. It's regenerated each run.

## Verification

- The previously-failing finding's fingerprint is no longer present in `report.json`
- The fix is committed in a single, focused diff (manifest change + reason)
- If you used `checks.ignore`, the `reason` is concrete (a date, a ticket link, or "tool deprecated; see roadmap")
"""


_RECOMMEND_FIXES_MD = """\
# Prompt · Recommend fixes for active Agents Shipgate findings

You are working in a repo with `shipgate.yaml` already in place and want a coordinated remediation pass across **all** active findings — not just the top one. Walk every finding, classify it against the current autofix policy, and surface targeted fix recommendations. Apply only the safe, high-confidence patches (after preview + explicit confirmation); leave the rest for human review with concrete advice.

## Your task

1. **Always run a fresh v0.8+ scan with patches.** Do not reuse a stale report — earlier scans may be pre-v0.7 (no remediation fields), pre-v0.8 (no `release_decision`), or may lack `patches[]` (no `--suggest-patches`). Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on stderr.
   ```bash
   AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c shipgate.yaml \\
       --suggest-patches --format json --ci-mode advisory
   ```
   Read `agents-shipgate-reports/report.json`. Verify `report_schema_version` is `"0.8"` or higher. Filter `findings[]` to entries with `"suppressed": false`.

2. **Bucket each active finding into one of four classes.** Read `agent_action` (v0.12+; deterministic projection of patches/autofix/human-review fields) to bucket each active finding directly. If `agent_action` is missing (older v0.11 or earlier reports), fall back to the legacy three-field check shown in the right column. The buckets correspond to [`docs/autofix-policy.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/autofix-policy.md):

   | Bucket | `agent_action` (v0.12+) | Legacy fallback (v0.11 or earlier) | Example check IDs |
   |---|---|---|---|
   | **A. Safe auto-fix** | `auto_apply` | `autofix_safe == true` | `SHIP-MANIFEST-STALE-{SUPPRESSION,POLICY,RISK-OVERRIDE}` when the match is unique |
   | **B. Medium-confidence config fix** | `propose_patch_for_review` | `autofix_safe == false` AND `suggested_patch_kind` ∈ `{set_pointer, append_pointer, remove_pointer}` | `SHIP-AUTH-SCOPE-COVERAGE-MISSING` |
   | **C. Manual** | `escalate_to_human` (with `suggested_patch_kind == "manual"`) | `suggested_patch_kind == "manual"` | Documentation, schema bounds, owner gaps, ADK/LangChain/CrewAI metadata, and the never-auto-fix trace findings |
   | **D. No patch emitted** | `escalate_to_human` (with `suggested_patch_kind == "none"`) | `suggested_patch_kind == "none"` | The generator emitted nothing — but the finding can still be high/critical (e.g. low-confidence inventory). Treat as **human triage**, not informational. |
   | (skip) | `informational` | `suppressed == true` | Already-suppressed findings; show counts only. |

   For one-fetch counts read the top-level `agent_summary` block (v0.12+):
   `agent_summary.auto_appliable_patches` is the bucket-A count, and
   `agent_summary.needs_human_review` is buckets B + C + D combined
   (every active finding the user must weigh in on before applying —
   medium/low-confidence patches AND escalations). To split bucket B
   from bucket C+D you have to walk `findings[].agent_action` —
   agent_summary deliberately does not disaggregate them, since the
   distinction is an implementation detail of the patch-confidence
   policy rather than a release-gate signal. Use
   `agent_summary.first_recommended_action.command` as your default
   suggestion when bucket A is non-empty.

3. **Build a recommendation card per finding.** For each, present:
   - `check_id`, `title`, `severity`, `tool_name`, `confidence`
   - The verbatim `recommendation` string (per-finding fix text from the check author)
   - `docs_url` as a markdown link (when non-null)
   - **Concrete fix step** — branch on patch kind, since the patch shapes differ:
     - `set_pointer` / `append_pointer`: show `target_file`, `pointer`, `value`, `confidence`, `rationale`
     - `remove_pointer`: show `target_file`, `pointer`, `confidence`, `rationale`
     - `manual`: show `instructions` verbatim. `ManualPatch` has only `kind` and `instructions` — do NOT try to read `target_file`/`pointer`/`value`; they don't exist.
     - No patches (bucket D): use `evidence` and `source` to make `recommendation` concrete — quote the offending parameter name, the file path from `source.ref`, the manifest key. Generic advice is not acceptable here.

4. **Present the prioritised plan.** Severity-ordered (critical → high → medium → low → info), grouped by bucket within each severity tier. Show counts per bucket up front. For low/info findings in bucket D, summary-link via `docs_url` rather than full cards — avoid wall-of-text.

5. **Decision points — ask the user explicitly. Always preview before mutating.**
   - **Bucket A (safe auto-fix).** First run a **dry-run** (omit `--apply`):
     ```bash
     agents-shipgate apply-patches \\
         --from agents-shipgate-reports/report.json \\
         --confidence high
     ```
     Show the user the planned file diffs. Only after explicit confirmation, re-run with `--apply --json`. Never silently apply.
   - **Bucket B (medium-confidence config).** Surface the patches with their `pointer` and `value`. Tell the user the opt-in command (`apply-patches --confidence medium`) and that they must read the appended values first — scope strings can encode policy choices. Do not apply on the user's behalf in this recipe.
   - **Bucket C (manual).** Ask whether to walk through them now or defer. For deep dive on a single finding, cross-link to [`fix-top-finding.md`](fix-top-finding.md). Never edit a trace recording to silence `SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING` — that patches the evidence, not the agent. Implement the runtime gate instead.
   - **Bucket D (no patch).** Ask whether to walk through them — these need diagnosis, not patch application. Cross-link to [`fix-top-finding.md`](fix-top-finding.md); the four-response decision tree (add policy / override / suppress / fix tool spec) applies.

6. **Re-scan after applying any Bucket A patches.** Show the diff in `summary.{critical_count, high_count, medium_count}`. Confirm the previously-fixed fingerprints are gone from `report.json`.

7. **Report back**:
   - Counts per bucket (A/B/C/D) and per severity
   - What was applied (from `apply-patches --apply --json` output's `files`)
   - What remains, with one clear next action per remaining bucket
   - Any cross-links the user should follow ([`fix-top-finding.md`](fix-top-finding.md), [`triage-false-positive.md`](triage-false-positive.md))

## What NOT to do

- Do **not** run `apply-patches --apply` without showing the dry-run preview first AND getting explicit user confirmation, even when `autofix_safe == true`.
- Do **not** apply `--confidence medium` patches in this recipe. They are opt-in only and require the user to read the appended values.
- Do **not** edit a trace recording to silence `SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING`. Trace findings are class-four "never auto-fix" per the autofix policy. Implement the runtime approval/confirmation gate.
- Do **not** recommend `checks.ignore` as a fix here. That's the [`triage-false-positive.md`](triage-false-positive.md) workflow's job — cross-link to it.
- Do **not** claim a finding is fixed without re-running `agents-shipgate scan` and showing the diff in counts.
- Do **not** invent recommendations not grounded in `recommendation`, `evidence`, `patches[].instructions`, or `docs_url`. Use evidence to make advice concrete; do not replace check-author guidance with a guess.

## Verification

- A fresh `report.json` exists, validates as `report_schema_version: "0.8"` (or higher; v0.12+ exposes `agent_action` and `agent_summary`), and was generated with `--suggest-patches`.
- Each presented card cites a concrete location: `target_file` + `pointer` for non-manual patches, `instructions` verbatim for manual patches, file path + parameter name from `evidence`/`source` for bucket D.
- If Bucket A patches were applied: re-scan shows lower active counts AND the previously-failing fingerprints are absent from the new `report.json`.
- If only B/C/D were surfaced: counts are unchanged (expected); the user has a clear list of next actions.
"""


_STABILIZE_STRICT_MD = """\
# Prompt · Stabilize Agents Shipgate strict mode

The user has Agents Shipgate running in **advisory** mode and wants to graduate to **strict** mode (CI fails on findings) without surprising contributors.

## The pattern

1. Run a fresh scan and inventory the active findings.
2. Tune `risk_overrides` and `checks.ignore` for genuine false positives, with reasons.
3. Save a baseline of everything that's left.
4. Switch CI to strict mode with the baseline applied — only NEW findings fail.
5. Pick a severity threshold; usually start with `critical`, raise to `[critical, high]` later.

## Your task

1. **Inventory current findings.**
   ```bash
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```
   Look at `agents-shipgate-reports/report.json` `summary.critical_count`, `high_count`, `medium_count`. If the active list is small (< 20 unique check IDs), consider just fixing them rather than baselining.

2. **Tune false positives.** For each unique check ID, decide:
   - True positive that should be fixed → use the `fix-top-finding.md` prompt to apply a real fix.
   - True positive that the team explicitly accepts (deprecated tool, known limitation) → add to `checks.ignore` with a real `reason`.
   - False positive (heuristic misfire) → use `risk_overrides.tools.{tool}.remove_tags` or add tags via `risk_overrides.tools.{tool}.tags`.

3. **Save the baseline:**
   ```bash
   agents-shipgate baseline save -c shipgate.yaml \\
     --out .agents-shipgate/baseline.json
   ```

4. **Commit the baseline:**
   ```bash
   git add .agents-shipgate/baseline.json
   git commit -m "Baseline shipgate findings ($N criticals, $M highs)"
   ```

5. **Update the CI workflow.** Replace the existing advisory step with strict + baseline. Use [`examples/github-actions/03-strict-with-baseline.yml`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/examples/github-actions/03-strict-with-baseline.yml) as the template:
   ```yaml
   - uses: ThreeMoonsLab/agents-shipgate@v0.10.0
     with:
       ci_mode: strict
       fail_on: critical
       baseline: .agents-shipgate/baseline.json
       pr_comment: 'true'
   ```

6. **Verify the gate fires correctly.** In a throwaway branch, deliberately introduce a new finding (e.g. add a wildcard scope) and confirm CI fails. Revert before merging.

## When to refresh the baseline

| Situation | Action |
|---|---|
| Found a false positive after baselining | Add a `checks.ignore` entry; do **not** re-baseline |
| Fixed several findings | Re-baseline so resolved ones disappear: `agents-shipgate baseline save ...` |
| Upgraded shipgate to a version with new checks | New check IDs surface as new findings; fix or suppress, then re-baseline |
| Added new tools that have no policy yet | Each new tool's findings are `new` and will fail; fix or accept, then re-baseline |

Re-baselining is just running `baseline save` again. Diff the new file vs the old in code review so the team sees what's been accepted.

## Promotion to `[critical, high]`

After a sprint or two of strict-on-critical, the active high-severity list usually compresses enough to flip on. Update `fail_on: critical,high` and re-baseline.

## What NOT to do

- Do **not** baseline in your first run as a "shortcut to make CI green." That hides the existing risk surface from review.
- Do **not** baseline findings that have a real fix — fix them first, baseline only what you're explicitly accepting.
- Do **not** write `--fail-on critical,high` without a baseline if the repo has many existing high findings; CI will fail on day one and contributors will mute the workflow.

## Verification

- `.agents-shipgate/baseline.json` is committed and contains `findings[]`
- CI workflow uses `ci_mode: strict` and `baseline: .agents-shipgate/baseline.json`
- A test PR that adds a deliberate new critical finding fails CI
- A test PR that doesn't change the tool surface passes CI
"""


_TRIAGE_FP_MD = """\
# Prompt · Triage a suspected Agents Shipgate false positive

The user thinks a specific finding is wrong. You need to decide whether to override the heuristic, suppress the finding, or convince the user that the check is correct.

## Your task

1. **Read the full finding.** From `agents-shipgate-reports/report.json`:
   ```json
   {
     "id": "fp_...",
     "check_id": "SHIP-...",
     "tool_name": "...",
     "severity": "...",
     "evidence": { ... },
     "recommendation": "..."
   }
   ```
   And the check definition:
   ```bash
   agents-shipgate explain <CHECK_ID> --json
   ```

2. **Read the actual tool definition.** Look up the OpenAPI / MCP / SDK source:
   - For OpenAPI: open the spec at the path given in `findings[].source.ref`
   - For MCP: open the JSON file
   - For SDK: open the `.py` file at the line given in `source.location`

3. **Apply the decision tree:**

   ```
   Is the heuristic wrong about the tool?
   (e.g. "destructive" tag on a GET; "financial_action" tag on a non-financial scope)
       → YES: override via risk_overrides.tools.{tool}.remove_tags
       → NO:  continue

   Is the check fundamentally inapplicable to this tool?
   (e.g. SHIP-DOC-MISSING-DESCRIPTION on an internal-only tool slated for removal)
       → YES: suppress via checks.ignore with a concrete reason
       → NO:  continue

   The check is correct. Fix the tool definition.
       → use the fix-top-finding.md prompt
   ```

## Override vs suppress — which to use

| Use `risk_overrides` when | Use `checks.ignore` when |
|---|---|
| The risk **classification** is wrong | The classification is right but the team accepts the risk |
| You want to remove a tag (e.g. `remove_tags: [destructive]`) | You want to suppress one specific finding |
| The fix benefits all checks that consume that tag | The acceptance is per-check, per-tool |
| Example: a `get_records` GET picks up `destructive` from substring "destroy" | Example: a documented internal-only tool with no description |

**Rule of thumb:** if the fix would silence multiple findings naturally, use `risk_overrides`. If you want to acknowledge one specific finding by name, use `checks.ignore`.

## Required: a concrete `reason`

Both `checks.ignore` entries and `risk_overrides` entries take a `reason`. Empty reasons fail manifest validation. Good reasons answer "why is this OK?" in a way a future reviewer can verify:

| Bad reason | Better reason |
|---|---|
| `false positive` | `GET endpoint; "destroy" appears in operationId only because it returns destroy-status` |
| `not applicable` | `Tool deprecated 2026-Q2; deletion tracked in JIRA-1234` |
| `team decision` | `Reviewed by platform-eng 2026-04-10; see ADR-007` |

## Re-run and confirm

After editing the manifest:

```bash
agents-shipgate scan -c shipgate.yaml --ci-mode advisory
```

The previously-failing fingerprint should be gone (overridden) or marked `"suppressed": true` (suppressed) in `report.json`.

## When the heuristic is genuinely buggy

If you've found a real classifier bug — the kind that affects many users, not just this tool — file an issue tagged `false-positive` at https://github.com/ThreeMoonsLab/agents-shipgate/issues with:

- The check ID
- A minimal reproduction (manifest fragment + tool source)
- The current behavior vs. expected behavior

The risk classifier in `core/risk_hints.py` improves through reports.

## Verification

- The decision (override / suppress / fix) is documented in the manifest with a reason.
- The previously-failing fingerprint is gone or `"suppressed": true` in the next scan.
- The `reason` would be understandable to a reviewer who hasn't seen the finding.
"""


_UPGRADE_VERSION_MD = """\
# Prompt · Upgrade Agents Shipgate version

Bump the agents-shipgate version pinned in CI and the development environment.

## Your task

1. **Read the changelog** for the gap between the current and target version:
   - https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/CHANGELOG.md
   - Specifically look for entries under "Breaking changes" and "New checks added".

2. **Update the pin in three places** (in this order):

   a. **`pyproject.toml`** (if the project depends on shipgate as a dev dep):
      ```toml
      [project.optional-dependencies]
      dev = ["agents-shipgate==<NEW>", ...]
      ```

   b. **CI workflow** at `.github/workflows/shipgate.yml`:
      ```yaml
      - uses: ThreeMoonsLab/agents-shipgate@v<NEW>
        with:
          shipgate_version: '<NEW>'
      ```

   c. **Pre-commit config** at `.pre-commit-config.yaml` (if present):
      ```yaml
      repos:
        - repo: https://github.com/ThreeMoonsLab/agents-shipgate
          rev: v<NEW>
      ```

3. **Run a local scan** with the new version:
   ```bash
   pipx upgrade agents-shipgate
   agents-shipgate --version    # confirm the new version is in PATH
   agents-shipgate scan -c shipgate.yaml --ci-mode advisory
   ```

4. **Compare the new finding count to the baseline.** If `report.json` shows new finding fingerprints (any with `"baseline_status": "new"`):
   - These are usually new checks added in the upgrade. Read the changelog "New checks added" section.
   - For each new check ID, decide: fix, override, or suppress (see [`triage-false-positive.md`](triage-false-positive.md)).

5. **Re-baseline if the new findings are accepted:**
   ```bash
   agents-shipgate baseline save -c shipgate.yaml \\
     --out .agents-shipgate/baseline.json
   ```

6. **Commit** the version bumps + the new baseline (if regenerated) in one PR. Title: `Upgrade agents-shipgate v<OLD> → v<NEW>`.

## Stability guarantees

Per [`STABILITY.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/STABILITY.md), within `0.x`:

- Existing check IDs do not change names or fingerprint algorithms.
- Existing CLI flags do not break.
- The JSON report's stable fields persist.

So a `0.2.x → 0.3.x` upgrade should not silently break existing suppressions or baselines. If it does, that's a stability bug — file an issue.

## What may legitimately change

- Risk-classifier keyword sets (false-positive tuning). Use `risk_overrides` to pin specific behavior.
- New checks fire (additive). Triage with the prompts above.
- Markdown report layout (parse `report.json` instead).

## Verification

- `agents-shipgate --version` reflects the new version
- CI workflow uses the new version
- A scan completes without error
- The baseline file (if used) is up to date
"""


_ADVISORY_CI_YML = f"""\
# Advisory PR comment.
# Recommended starting point — runs the scanner on every PR, posts a summary
# comment, uploads the report as an artifact, and never fails the job.
name: Agents Shipgate (advisory)

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  shipgate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v{_ACTION_VERSION}
        with:
          ci_mode: advisory
          diff_base: target
          pr_comment: 'true'
          shipgate_version: '{_ACTION_VERSION}'
"""
