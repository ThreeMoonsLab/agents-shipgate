# Prompt · Add Agents Shipgate to a repo

You are working in a repo that may contain an AI agent — likely one of: an MCP server tool list (`*mcp*.json` or `.agents-shipgate/*.json`), an OpenAPI spec the agent calls, a Codex plugin package (`.codex-plugin/plugin.json`) or marketplace (`.agents/plugins/marketplace.json`), a Python file with `@function_tool` / `@tool` decorators (OpenAI Agents SDK, LangChain, CrewAI), a Google ADK agent in `agent.py`, an Anthropic Messages API artifact set under `prompts/`/`tools/anthropic-tools.json`/`policies/anthropic-policy.yaml`, or an OpenAI API artifact set under `prompts/`/`tools/openai-tools.json`/`openai-config.json`.

Your job is to drive the first-adoption helper flow end-to-end in one
tool-using turn, which adds the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review. Ongoing
agent-related PRs should use `agents-shipgate verify` after this adoption step.

## Your task

1. **Install the tool - pin the version so a stale build can't shadow it.** This flow uses the current verifier, agent-handoff, primary-command, and Codex-boundary contracts and requires **contract v9 or newer**; an older copy lingering on `PATH` may lack the command or schema fields this prompt expects. Prefer a **pinned, zero-install** runner that fetches the exact version every time instead of trusting whatever is already on `PATH`. **Pin it into one variable and use that for every step below**, so no single command can fall through to a stale binary:
   ```bash
   SG="uvx agents-shipgate@1.0.0a1"           # uv: ephemeral, always the pinned build
   # or: SG="pipx run agents-shipgate==1.0.0a1"
   $SG --version                             # confirm the pinned runner resolves
   ```
   Every step below calls `$SG …`; e.g. `$SG verify --preview --json` runs the verify preview through the pinned runner, never a `PATH` copy.

   If you would rather install onto `PATH`, pin the floor and **fail loudly when it resolves older** — a plain `pipx install agents-shipgate` is a no-op when an older build already exists — then set `SG=agents-shipgate`:
   ```bash
   python -m pip install -U --pre agents-shipgate
   agents-shipgate contract --json   # STOP if this reports contract_version < 9 - re-run pinned via uvx agents-shipgate@1.0.0a1
   SG=agents-shipgate                # only after the line above confirms contract v9+
   ```

2. **Sanity-check the install** before touching the user's code:
   ```bash
   $SG self-check --json
   ```
   Confirm `"ready": true`. If not, surface the failure to the user.

   When available, verify the installed CLI contract locally:
   ```bash
   $SG contract --json
   ```
   Read `report_schema_version`, `packet_schema_version`, `gating_signal`, and
   `manual_review_signals[]`; prefer these local values over stale docs. If the
   command is not recognized on an older install, continue after `self-check`
   using [`docs/agent-contract-current.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/agent-contract-current.md)
   and upgrade before relying on local contract verification in automation.

3. **Preview the verify flow:**
   ```bash
   $SG verify --preview --json
   ```
   Read the response and next action. Preview is the first-look verify entry
   point: it does not require a manifest, does not scan, and tells you whether
   to configure Shipgate, skip, or run the full verifier.

   If preview exposes trigger/detection metadata, stop only when all relevance
   signals are absent and the user did not explicitly request Shipgate.
   Otherwise proceed. MCP/OpenAPI tool-surface repos and Codex plugin package
   repos can be valid Shipgate targets even when Python framework detection
   would classify `is_agent_project: false`; look for `suggested_sources` and
   `codex_plugin_candidates` when those fields are present.

4. **Generate a starter manifest + GitHub Actions workflow:**
   ```bash
   $SG init --workspace . --write --ci --json
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
   $SG scan -c shipgate.yaml --suggest-patches --format json --ci-mode advisory
   ```
   The report lands at `agents-shipgate-reports/report.json`. The supporting Release Evidence Packet lands at `agents-shipgate-reports/packet.{md,json,html}`. Parse `report.json`; Codex plugin facts, when present, live under `codex_plugin_surface`.

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
   $SG apply-patches --from agents-shipgate-reports/report.json --confidence high --apply --json
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
   - The path to the supporting Release Evidence Packet (`agents-shipgate-reports/packet.md`) for reviewer-shaped output
   - The top 3 active critical/high findings (use `report.json`, not stdout)
   - Which patches were applied (count from `apply-patches --json` output's `files`)
   - Any check IDs the user should investigate first — link to `docs_url` from the finding for full rationale, or use `$SG explain <CHECK_ID> --json` for the same content via CLI

## What to do if the scan errors out

Re-run the failing `$SG …` command with `AGENTS_SHIPGATE_AGENT_MODE=1` set. The CLI will append a JSON line to stderr with `{error, message, next_action}`. Follow the `next_action`.

Common errors and fixes:

| Error | Fix |
|---|---|
| `Config file not found: shipgate.yaml` | Run `$SG init --workspace . --write` first |
| `Input path '...' resolves outside manifest directory` | The declared `tool_sources[].path` is outside the manifest dir. Move the spec inside the tree, symlink it, or copy it |
| `Invalid shipgate.yaml: ... Did you mean X?` | A field is at the wrong nesting level; move it as suggested |
| `Containment violation` (apply-patches exit 5) | A patch's `target_file` resolved outside `report.manifest_dir`. Re-run scan to refresh; never patch arbitrary system files |

## What NOT to do

- Do **not** commit `agents-shipgate-reports/` — it's regenerated each run.
- Do **not** run `$SG baseline save` until the user has reviewed the initial findings. Baselining ratchets in noise that strict CI will silently ignore. The right time to baseline is **after** the user has decided which findings they accept.
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
