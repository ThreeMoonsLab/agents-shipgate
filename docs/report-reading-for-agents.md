# Reading the Report (For Agents)

A reader's primer for `agents-shipgate-reports/report.json`. Walks the file in the order a new consumer should read it.

> **Audience.** New agent or CI consumers parsing `agents-shipgate-reports/report.json` for the first time. If you only need the field index, see [`agent-contract-current.md`](agent-contract-current.md). If you need the canonical 4-call flow, see [`agent-recipes.md`](agent-recipes.md).

---

## TL;DR

**Read `release_decision.decision` first.** It is the gating signal — `"blocked" | "review_required" | "passed"`, baseline-aware, stable since v0.8. Everything else in the report is detail you reach for *after* the gate decision is captured.

```python
import json
report = json.loads(open("agents-shipgate-reports/report.json").read())
gate = report["release_decision"]["decision"]   # blocked | review_required | passed
```

The CLI's stable contract names this signal explicitly: run `agents-shipgate contract --json` and inspect `gating_signal` — it is always `release_decision.decision` in the current contract (see [`STABILITY.md`](../STABILITY.md) §"Runtime contract JSON").

---

## Step-by-step

### Step 1 · `release_decision.decision`

Branch on the three values:

| `decision` | Meaning | Agent behavior |
|---|---|---|
| `"blocked"` | Active, unaccepted blockers exist. CI will fail in strict mode. | Surface blockers; do not auto-merge; do not assert evidence categories — see [`agent-autofix-boundary.md`](agent-autofix-boundary.md). |
| `"review_required"` | Review items exist (often baseline-matched accepted debt or capability/intent misalignments). | Surface review items as a human handoff; safe mechanical patches may still apply via `apply-patches --confidence high`. |
| `"passed"` | No active blockers, no review items. | Mechanical patches (if any) may apply; otherwise nothing to do. |

The decision is **baseline-aware**: a baseline-matched critical surfaces in `release_decision.review_items` (accepted debt), not in `release_decision.blockers`. Compare with the legacy `summary.status` field, which is *baseline-blind* — see Anti-patterns below.

### Step 2 · `release_decision.{reason, blockers, review_items, fail_policy.would_fail_ci}`

Once you have the decision, read the supporting fields:

- `release_decision.reason` — one-sentence explanation suitable for a PR comment.
- `release_decision.blockers[]` — items that block this run; reference shape `{id, fingerprint, check_id, severity, title, baseline_status}`. The full Finding payload is in `findings[]`.
- `release_decision.review_items[]` — items the human reviewer should look at; same reference shape.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches the process exit code that CI will see.
- `release_decision.fail_policy.{ci_mode, fail_on, new_findings_only, exit_code}` — full CI policy.
- `release_decision.evidence_coverage.{level, human_review_recommended, low_confidence_tool_count, source_warning_count}` — coverage for the evidence sections.
- `release_decision.baseline_delta.{matched_count, new_count, resolved_count}` — what changed vs. the loaded baseline.

The GitHub Action exposes a subset as outputs (v0.8+): `decision`, `blocker_count`, `review_item_count`, `ci_would_fail`.

### Step 3 · `findings[]`

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

### Step 4 · Per-finding autofix fields (v0.7+)

For every active finding, inspect:

- `autofix_safe` (bool) — true iff every patch is non-manual and `confidence == "high"`.
- `requires_human_review` (bool) — always the inverse of `autofix_safe`.
- `suggested_patch_kind` — `"set_pointer" | "append_pointer" | "remove_pointer" | "manual" | "none"`.
- `docs_url` — link to the rationale page on `checks.md`.

Use these to decide whether to call `apply-patches --confidence high --apply` or surface the finding for manual review. The full mechanical policy lives in [`autofix-policy.md`](autofix-policy.md). The behavioral boundary — what an agent may *write* about a finding even if it cannot mechanically patch it — lives in [`agent-autofix-boundary.md`](agent-autofix-boundary.md).

### Step 5 · Release Evidence Packet (for human-review framing)

Alongside `report.json`, scan emits a reviewer-shaped Release Evidence Packet at `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras). Read `packet.json` when you need:

- `human_in_the_loop.runtime_control_disclaimer` — the canonical disclaimer that local HITL evidence is not runtime-enforcement proof. Surface this verbatim when you summarize approval/confirmation findings.
- `human_in_the_loop.source_provenance[]` — traces local validation artifacts when available.
- §1 verdict — derives from `release_decision.decision` only. Never derive a verdict from `summary.status`.
- §10 ("What this packet did NOT prove") — always lists prompt robustness, runtime behavior, model correctness, adversarial resistance.

The packet schema is `0.3`; full schema at [`docs/packet-schema.v0.3.json`](packet-schema.v0.3.json).

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
if report["release_decision"]["decision"] == "blocked":
    fail("blockers")
elif report["release_decision"]["decision"] == "review_required":
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
| Report | `0.10` | `0.9`, `0.8`, `0.7`, `0.6`, `0.5`, `0.4`, `0.3`, `0.2`, `0.1` | [`report-schema.v0.10.json`](report-schema.v0.10.json) |
| Packet | `0.3` | — | [`packet-schema.v0.3.json`](packet-schema.v0.3.json) |
| Manifest | `0.1` | — | [`manifest-v0.1.json`](manifest-v0.1.json) |
| CLI contract | `1` | — | `agents-shipgate contract --json` |

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
- [`agent-recipes.md`](agent-recipes.md) — canonical 4-call flow.
- [`diagnostics.md`](diagnostics.md) — full diagnostic-code catalog and `NextAction` ranking.
- [`STABILITY.md`](../STABILITY.md) — what won't break across `0.x`.
- [`AGENTS.md`](../AGENTS.md) Task 2 — one-paragraph version of this primer.
