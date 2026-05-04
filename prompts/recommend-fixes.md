# Prompt · Recommend fixes for active Agents Shipgate findings

You are working in a repo with `shipgate.yaml` already in place and want a coordinated remediation pass across **all** active findings — not just the top one. Walk every finding, classify it against the v0.7 autofix policy, and surface targeted fix recommendations. Apply only the safe, high-confidence patches (after preview + explicit confirmation); leave the rest for human review with concrete advice.

## Your task

1. **Always run a fresh v0.7 scan with patches.** Do not reuse a stale report — earlier scans may be pre-v0.7 (no remediation fields) or may lack `patches[]` (no `--suggest-patches`). Set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors emit a `next_action` JSON line on stderr.
   ```bash
   AGENTS_SHIPGATE_AGENT_MODE=1 agents-shipgate scan -c shipgate.yaml \
       --suggest-patches --format json --ci-mode advisory
   ```
   Read `agents-shipgate-reports/report.json`. Verify `report_schema_version` is `"0.7"` or higher. Filter `findings[]` to entries with `"suppressed": false`.

2. **Bucket each active finding into one of four classes.** Use the per-Finding fields (the catalog values are worst-case; per-Finding fields tell the truth for this scan). The buckets come from [`docs/autofix-policy.md`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/autofix-policy.md):

   | Bucket | Detect by | Example check IDs |
   |---|---|---|
   | **A. Safe auto-fix** | `autofix_safe == true` | `SHIP-MANIFEST-STALE-{SUPPRESSION,POLICY,RISK-OVERRIDE}` when the match is unique |
   | **B. Medium-confidence config fix** | `autofix_safe == false` AND `suggested_patch_kind` ∈ `{set_pointer, append_pointer, remove_pointer}` | `SHIP-AUTH-SCOPE-COVERAGE-MISSING` |
   | **C. Manual** | `suggested_patch_kind == "manual"` | Documentation, schema bounds, owner gaps, ADK/LangChain/CrewAI metadata, and the never-auto-fix trace findings |
   | **D. No patch emitted** | `suggested_patch_kind == "none"` | The generator emitted nothing — but the finding can still be high/critical (e.g. low-confidence inventory). Treat as **human triage**, not informational. |

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
     agents-shipgate apply-patches \
         --from agents-shipgate-reports/report.json \
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

- A fresh `report.json` exists, validates as `report_schema_version: "0.7"` (or higher), and was generated with `--suggest-patches`.
- Each presented card cites a concrete location: `target_file` + `pointer` for non-manual patches, `instructions` verbatim for manual patches, file path + parameter name from `evidence`/`source` for bucket D.
- If Bucket A patches were applied: re-scan shows lower active counts AND the previously-failing fingerprints are absent from the new `report.json`.
- If only B/C/D were surfaced: counts are unchanged (expected); the user has a clear list of next actions.
