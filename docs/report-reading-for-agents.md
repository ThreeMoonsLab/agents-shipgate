# Reading the Report (For Agents)

A reader's primer for `agents-shipgate-reports/report.json`. Walks the file in the order a new consumer should read it.

> **Audience.** New agent or CI consumers parsing `agents-shipgate-reports/report.json` for the first time. If you only need the field index, see [`agent-contract-current.md`](agent-contract-current.md). If you need verify-first PR commands or first-adoption flows, see [`agent-recipes.md`](agent-recipes.md).

---

## TL;DR

**This primer is the `report.json` / CI-gate read path.** If you are an autonomous coding agent deciding *complete, act, or stop*, read `agents-shipgate-reports/agent-handoff.json` first: switch on `control.state`, then use `gate.merge_verdict` as the deterministic projection of the release gate. See [`agent-contract-current.md` § Two read entry points](agent-contract-current.md#two-read-entry-points).

**Read `release_decision.decision` first.** It is the gating signal — `"blocked" | "review_required" | "insufficient_evidence" | "passed"`, baseline-aware, stable since v0.8 (`insufficient_evidence` added v0.14). Switch on the enum with a `review_required` fallback for unknown future values per the [STABILITY.md additivity contract](../STABILITY.md#what-may-change-additively-in-any-minor-release). Everything else in the report is detail you reach for *after* the gate decision is captured.

```python
import json
report = json.loads(open("agents-shipgate-reports/report.json").read())
gate = report["release_decision"]["decision"]   # blocked | review_required | insufficient_evidence | passed
```

The CLI's stable contract names this signal explicitly: run `agents-shipgate contract --json` and inspect `gating_signal` — it is always `release_decision.decision` in runtime contract v20 (see [`STABILITY.md`](../STABILITY.md) §"Runtime contract JSON").

---

## Step-by-step

### Step 1 · `release_decision.decision`

Branch on the four values (treat unknown future values as `review_required` per the [STABILITY.md additivity contract](../STABILITY.md#what-may-change-additively-in-any-minor-release)):

Precedence (highest first): `blocked` → `review_required` (active high/critical) → `insufficient_evidence` → `review_required` (other) → `passed`.

| `decision` | Meaning | Agent behavior |
|---|---|---|
| `"blocked"` | Active, unaccepted blockers exist. CI will fail in strict mode. | Surface blockers; do not auto-merge; do not assert evidence categories — see [`agent-autofix-boundary.md`](agent-autofix-boundary.md). |
| `"insufficient_evidence"` (v0.14+) | The scan cannot establish a defensible static pass. In v0.29 this includes any action with unknown, inferred-only, protocol-defaulted, partial, conflicting, invalid, or incomplete required effect/authority evidence, plus the existing extraction-confidence and source-warning thresholds. This does not prove the agent is unsafe. | Read `semantic_coverage` and work each `evidence_gaps[].next_action`, **starting with the first row whose `next_action.path` is non-empty** — that is the row `reason` and `agent_summary.first_recommended_action` also name (v0.16+), not necessarily `evidence_gaps[0]`. When no row carries a path there is nothing to open, and gathering deeper sources really is the next step. Effect/authority declarations are human assertions: never auto-write them. Do not auto-merge. |
| `"review_required"` | Review items exist (often baseline-matched accepted debt, capability/intent misalignments, or sub-threshold evidence gaps). This **also** covers a degraded-evidence case that carries an active (non-baseline-accepted) high/critical finding — the verdict names the concern instead of the vaguer `insufficient_evidence`, but the evidence gap is still present in `evidence_coverage`. One to three source warnings without blockers also land here. | Surface review items as a human handoff. Safe mechanical patches may still apply via `apply-patches --confidence high` — **unless** evidence is degraded (check `evidence_coverage.low_confidence_tool_count` / `source_warning_count`), in which case treat it like `insufficient_evidence`: start with the first `evidence_gaps[]` row whose `next_action.path` is non-empty, and gather deeper sources only when no row carries one. Note that `reason` stays severity-driven on this verdict and does **not** name a gap, while `agent_summary.first_recommended_action` does when one is addressable — read the gap rows, not the reason, for the evidence step. `verify`'s `fix_task` already routes degraded-evidence cases to a human. |
| `"passed"` | Every in-scope action has complete, conflict-free static surface, effect, and authority evidence; all applicable controls were evaluated; and no policy condition requires review. This is not runtime proof. | Mechanical patches (if any) may apply; otherwise nothing to do. Preserve the runtime-safety disclaimer when summarizing. |

The decision is **baseline-aware**: a baseline-matched critical surfaces in `release_decision.review_items` (accepted debt), not in `release_decision.blockers`. Compare with the legacy `summary.status` field, which is *baseline-blind* — see Anti-patterns below.

Runtime contract v20 can attach an externally signed authorization to a new
verifier artifact and route one exact guarded operation, but it never rewrites
this report verdict. The report remains `review_required`; autonomous agents
must read `agent-handoff.json.control.state` to distinguish a human stop from
that narrowly authorized next action.

Before summarizing any verdict, preserve the machine boundary:
`release_decision.static_analysis_only` is always `true`,
`runtime_behavior_verified` is always `false`, and
`static_verdict_disclaimer` is the canonical non-runtime statement. Packet §1
mirrors all three fields.

> **Don't switch on the verdict label to detect degraded evidence.** Read `release_decision.evidence_coverage.{semantic_coverage, low_confidence_tool_count, source_warning_count, evidence_gaps[]}`. Semantic gaps are not Findings, so suppression, baselines, severity overrides, `--no-heuristics`, and `human_ack` cannot clear them.

### Step 2 · `release_decision.{reason, blockers, review_items, fail_policy.would_fail_ci}`

Once you have the decision, read the supporting fields:

- `release_decision.reason` — one-sentence explanation suitable for a PR comment.
- `release_decision.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}` — explicit static-only boundary; preserve it in every human or agent-facing projection.
- `release_decision.blockers[]` — items that block this run; reference shape `{id, fingerprint, check_id, severity, title, baseline_status}`. The full Finding payload is in `findings[]`.
- `release_decision.review_items[]` — items the human reviewer should look at; same reference shape.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches the process exit code that CI will see.
- `release_decision.fail_policy.{ci_mode, fail_on, new_findings_only, exit_code}` — full CI policy.
- `release_decision.evidence_coverage.{level, human_review_recommended, low_confidence_tool_count, source_warning_count}` — extraction/source coverage.
- `release_decision.evidence_coverage.semantic_coverage` (v0.29+) — `{total_actions, pass_eligible_actions, gap_count, review_concern_count, reason_counts}` for the normalized action surface.
- `release_decision.evidence_coverage.evidence_gaps[]` — ordered, typed human-routed remediation rows; follow their source/manifest pointers and accepted values instead of guessing. Semantic declaration placeholders are explicitly `suggested_patch_kind: "manual"`, `auto_apply: false`, and `requires_human_review: true`.
- `release_decision.baseline_delta.{matched_count, new_count, resolved_count}` — what changed vs. the loaded baseline.

The GitHub Action exposes a subset as outputs (v0.8+): `decision`, `blocker_count`, `review_item_count`, `ci_would_fail`.

### Step 3 · `verifier_summary` (v0.22+)

When present, `verifier_summary` is the one-fetch controller surface for AI
coding workflows. It is a composition, not a second decision engine:
`verifier_summary.verdict` mirrors `release_decision.decision` exactly.

Read it for:

- `by_severity` and `by_reason_code` — active-finding histograms.
- `capability_delta_summary` — counts of added, removed, broadened, and
  narrowed capability-change members.
- `protected_surface_touched` — true when verify-mode findings show a
  trust-root edit.
- `policy_weakened` — true when policy weakening was detected or failed safe
  to review.
- `human_ack_required` / `human_ack_satisfied` — declared human authority
  state. A coding agent must not synthesize acknowledgement.
- `top_reason_codes[]` — ranked top-five reason codes for compact summaries.

If `protected_surface_touched`, `policy_weakened`, or
`human_ack_required` is true, surface the human-review requirement. Do not
respond by suppressing findings, lowering severity, expanding a baseline, or
removing Shipgate CI; those are the bypass patterns the verifier checks are
designed to make visible.

`agents-shipgate verify` also writes
`agents-shipgate-reports/verifier.json`. Lead with `control.state`, then read
`authorization`, `merge_verdict`, `can_merge_without_human`,
`control.next_action`, `fix_task`, and
`capability_review.top_changes`; then confirm
`report.json.release_decision.decision`, which remains the release gate.
`merge_verdict` is a deterministic projection for controller flow, not a second
decision engine. `base_status`, `base_notes`, and artifact paths explain
orchestration only.

### Step 4 · `findings[]`

Walk findings only after capturing the gate decision. Filter `suppressed: true` entries; they are kept in the report for traceability but are not active.

```python
active = [f for f in report["findings"] if not f.get("suppressed")]
critical = [f for f in active if f["severity"] == "critical"]
```

Per-finding stable fields (see [`AGENTS.md`](../AGENTS.md) Task 2 for the full list):

- `id`, `fingerprint`, `check_id`, `severity`, `category`, `title`, `recommendation`, `suppressed`
- `tool_name` (string or null)
- `evidence` (per-check object — keys depend on `check_id`; see [`checks.md`](checks.md))

Group by `severity` to summarize; cite `check_id` so the user can run `agents-shipgate explain <check_id>` for rationale.

For reviewer triage by source reliability, filter on
`findings[].provenance_kind` with the dedicated command:

```bash
agents-shipgate findings --from agents-shipgate-reports/report.json \
  --provenance-kind keyword_heuristic,regex_heuristic --json
```

This is not a gate signal. It does not change severity, release decisions,
fingerprints, baselines, or CI exit codes.

### Step 5 · Per-finding autofix fields (v0.7+)

For every active finding, inspect:

- `autofix_safe` (bool) — true iff every patch is non-manual and `confidence == "high"`.
- `requires_human_review` (bool) — always the inverse of `autofix_safe`.
- `suggested_patch_kind` — `"set_pointer" | "append_pointer" | "remove_pointer" | "manual" | "none"`.
- `docs_url` — link to the rationale page on `checks.md`.

Use these to decide whether to call `apply-patches --confidence high --apply` or surface the finding for manual review. The full mechanical policy lives in [`autofix-policy.md`](autofix-policy.md). The behavioral boundary — what an agent may *write* about a finding even if it cannot mechanically patch it — lives in [`agent-autofix-boundary.md`](agent-autofix-boundary.md).

### Step 6 · Release Evidence Packet (for human-review framing)

Alongside `report.json`, scan emits a reviewer-shaped Release Evidence Packet at `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras). Read `packet.json` when you need:

- `human_in_the_loop.runtime_control_disclaimer` — the canonical disclaimer that local HITL evidence is not runtime-enforcement proof. Surface this verbatim when you summarize approval/confirmation findings.
- `human_in_the_loop.source_provenance[]` — traces local validation artifacts when available.
- §1 verdict — derives from `release_decision.decision` only. Never derive a verdict from `summary.status`.
- §10 ("What this packet did NOT prove") — always lists prompt robustness, runtime behavior, model correctness, adversarial resistance.

The packet schema is `0.12`; full schema at [`docs/packet-schema.v0.12.json`](packet-schema.v0.12.json). It projects current report binding and semantic coverage plus gap remediation; v0.11 and older versions are frozen references.

---

## Anti-patterns

### Don't lead with `summary.status`

`summary.status` is preserved for v0.7 callers and is **baseline-blind**. A baseline-matched-only critical produces both `summary.status = "release_blockers_detected"` AND `release_decision.decision = "review_required"` — intentional divergence. New consumers must use `release_decision.decision`.

If you find code like this, rewrite it:

```python
# WRONG: baseline-blind, deprecated for new consumers
if report["summary"]["status"] == "release_blockers_detected":
    fail("blockers")
```

```python
# RIGHT: baseline-aware gate signal (v0.8+)
decision = report["release_decision"]["decision"]
if decision == "blocked":
    fail("blockers")
elif decision == "insufficient_evidence":  # v0.14+
    surface_for_human_review_with_evidence_gathering_prompt()
elif decision == "review_required":
    surface_for_human_review()
# unknown future values: treat as review_required per STABILITY.md
elif decision != "passed":
    surface_for_human_review()
```

See [`agent-contract-current.md`](agent-contract-current.md) §"Don't use for new gating" and [`STABILITY.md`](../STABILITY.md) §"`release_decision.decision` vs `summary.status`."

### Don't scrape `report.md`

The Markdown is for humans. The JSON is the contract. Specifically:

- Markdown headings, bullets, and emoji can change between minor releases.
- The JSON shape is governed by the schema and frozen across `0.x.y` releases (see [`STABILITY.md`](../STABILITY.md)).

If you need a one-line PR-comment summary, build it from `release_decision.reason` plus `summary.{critical_count, high_count}` — not by extracting prose from `report.md`.

### Don't assert evidence categories from prose

A `recommendation` field reads like prose ("Add an approval policy for `refund_customer`") but it is *guidance*, not *evidence of enforcement*. Surfacing the prose is fine; turning it into a claim that approval is now enforced is not. See [`agent-autofix-boundary.md`](agent-autofix-boundary.md) for the full list of categories that require human review.

### Don't ignore `report_schema_version`

Older reports may carry an older schema. Validate against the right frozen schema before reading fields that may not exist. See Schema versioning below.

---

## Errors and `next_action`

Set `AGENTS_SHIPGATE_AGENT_MODE=1` for every CLI call. On failure, the CLI emits a one-line `next_action` JSON object on **stderr** (the report file may not be produced). Shape:

```json
{"error": "config_error", "message": "...", "next_action": "...", "next_actions": [{"kind": "...", "command": "...", "why": "...", "expects": "..."}]}
```

`next_actions[]` items follow the `NextAction` shape:

| Field | Type | Notes |
|---|---|---|
| `kind` | `"command" \| "edit" \| "review" \| "stop"` | What the agent should do next. |
| `command` | string \| null | Shell command (when `kind == "command"`). |
| `path` | string \| null | File path (when `kind == "edit"`); may be `file:line`. |
| `why` | string | One-sentence reason. |
| `expects` | string \| null | What success should look like. |

Surface the `next_action` to the user rather than scraping prose. The full diagnostic-code catalog and ranking rules live in [`diagnostics.md`](diagnostics.md).

---

## Schema versioning

`report.json` carries a `report_schema_version` field. Validate against the matching schema before reading version-specific fields.

| Schema | Current | Frozen references | File |
|---|---|---|---|
| Report | `0.34` | `0.33`, `0.32`, `0.31`, `0.30`, `0.29`, `0.28`, `0.27`, `0.26`, `0.25`, `0.24`, `0.23`, `0.22`, `0.21`, `0.20`, `0.19`, `0.18`, `0.17`, `0.16`, `0.15`, `0.14`, `0.13`, `0.12`, `0.11`, `0.10`, `0.9`, `0.8`, `0.7`, `0.6`, `0.5`, `0.4`, `0.3`, `0.2`, `0.1` | [`report-schema.v0.34.json`](report-schema.v0.34.json) |
| Packet | `0.12` | `0.11`, `0.10`, `0.9`, `0.8`, `0.7`, `0.6`, `0.5`, `0.4`, `0.3`, `0.2`, `0.1` | [`packet-schema.v0.12.json`](packet-schema.v0.12.json) |
| Manifest | `0.1` | — | [`manifest-v0.1.json`](manifest-v0.1.json) |
| CLI contract | `19` | — | `agents-shipgate contract --json` |

To detect the version programmatically:

```python
version = report.get("report_schema_version", "0.6")  # pre-v0.7 reports may omit
```

Frozen schemas are kept in `docs/` so older reports remain machine-validatable. See [`STABILITY.md`](../STABILITY.md) for the full guarantees on what fields are stable across `0.x` and what may change.

---

## See also

- [`agent-contract-current.md`](agent-contract-current.md) — current field index for `report.json`; updates first when the contract bumps.
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what conclusions an agent may publish without human review.
- [`autofix-policy.md`](autofix-policy.md) — mechanical patch policy and the four classes of findings.
- [`agent-recipes.md`](agent-recipes.md) — verify-first PR commands and first-adoption helper flow.
- [`diagnostics.md`](diagnostics.md) — full diagnostic-code catalog and `NextAction` ranking.
- [`STABILITY.md`](../STABILITY.md) — what won't break across `0.x`.
- [`AGENTS.md`](../AGENTS.md) Task 2 — one-paragraph version of this primer.
