# Calibrating the `insufficient_evidence` threshold

The release decision raises `insufficient_evidence` (IE) when, with no
blockers, static extraction is too weak to gate confidently. The threshold is
two constants in
[`src/agents_shipgate/ci/release_decision.py`](../../src/agents_shipgate/ci/release_decision.py):

```python
_LOW_CONFIDENCE_TOOL_RATIO   = 0.5   # IE if low_confidence_tools >= ceil(0.5 * total_tools)
_MAX_TOLERATED_SOURCE_WARNINGS = 3   # IE if source_warning_count > 3
```

They have shipped since v0.14 and were never tuned against data. This note
records the attempt to calibrate them, the honest finding, and the precise
conditions under which they should be revisited. The numbers below are
reproduced by the tests named at the end — they come from data on disk, not
prose.

## The question

Are `0.5` and `3` the right constants — i.e. does the gate raise IE on (and
only on) scans whose evidence is genuinely too weak to gate? Moving them moves
the **IE rate**, a headline metric.

## What calibration needs

For each scan: `low_confidence_tool_count`, `source_warning_count`,
`total_tools` (the threshold's inputs) **and** a ground-truth label — was IE
the correct call, or could the scan have gated as passed / review / blocked?
You cannot tell whether a threshold is too strict or too loose without the
label.

## What the corpora actually contain

**Mined real history** (`results/2026-W24-mined.*`, `results/2026-W25-mined.*`)
— 241 merged PRs, 9 decided, IE-dominated, and **unlabeled** (the human pass in
[`LABELING.md`](LABELING.md) is unstarted). Two further gaps made the rows
unusable for calibration even descriptively:

- `tools_scanned` was captured from the wrong place (`summary`, which carries
  no tool count) and came back `null` on every row — the ratio denominator was
  missing entirely. **Fixed** in `evaluate._tool_count` (now reads
  `tool_surface.total_tools`); future mines record it. The committed corpus
  predates the fix and still shows `null` — re-mine to populate.
- The row schema records `evidence_gaps` (low-confidence tools **+** source
  warnings, combined) but not the split, so the two threshold terms can't be
  separated. Splitting them is a `MinedRow` schema change, which forces a full
  re-mine (the corpus-integrity guard pins the CSV columns) — deferred to the
  next mine, not done speculatively.

**Constructed labeled fixtures** (`results/constructed.*`,
[`constructed.py`](constructed.py)) — 7 fixtures with definitional labels, run
through the live engine. Their measured evidence coverage:

| fixture | label | decision | low-conf | warns | tools | exercises IE threshold? |
|---|---|---|---:|---:|---:|---|
| `openai_agents_sdk_agent` | needs_human | `insufficient_evidence` | 2 | 0 | 2 | **yes** (2/2 = 1.0 ≥ 0.5) |
| `support_refund_agent` | must_block | `blocked` | 1 | 1 | 8 | no — blockers outrank IE |
| `ai_generated_refund_pr` | must_block | `blocked` | 0 | 0 | 2 | no |
| `agent_weakens_gate` | must_block | `blocked` | 0 | 0 | 1 | no |
| `clean_read_only_agent` | safe_to_merge | `passed` | 0 | 0 | 1 | no |
| `hitl_evidence_covered_agent` | safe_to_merge | `passed` | 0 | 0 | 1 | no |
| `hitl_evidence_agent` | needs_human | `review_required` | 0 | 0 | 1 | no |

## Finding

Exactly **one** labeled case (`openai_agents_sdk_agent`) exercises the IE
threshold, and it sits at the robust extreme — *every* tool is low-confidence
(ratio 1.0), so it is classified IE for any ratio in `(0, 1]`. A single point
at the extreme **cannot distinguish** `0.3` from `0.5` from `0.7`. The source
-warning constant (`3`) is exercised by no labeled case at all.

So neither corpus can justify *moving* the constants: the real corpus is
unlabeled and was missing the denominator; the constructed corpus has labels
but only one threshold-exercising point, and it is uninformative about where in
`(0, 1]` the boundary belongs.

## Decision

**Hold `0.5` / `3`.** No available data supports a change, and an unjustified
change would move the IE rate blindly. The constants are now *examined and
guarded* rather than unexamined — two guards with distinct jobs:

- **Threshold edits** → `test_ie_threshold_constants_are_frozen`
  (`tests/test_release_decision.py`) asserts the constants equal `0.5` / `3`.
  Changing either fails CI, so a recalibration is a deliberate edit that must
  update this note alongside it.
- **Extraction regressions** → `test_ie_threshold_is_exercised_and_robust_on_the_labeled_coverage_fixture`
  (`tests/test_miner_constructed.py`) re-runs the one labeled IE fixture; if
  extraction later resolves its dynamic surface the verdict flips and this
  fails. It does *not* catch threshold edits — the point sits at ratio 1.0, so
  it stays above any threshold in `(0, 1]` (which is the whole reason it can't
  calibrate the constant).
- **The denominator** → `test_record_head_report_*` (`tests/test_miner.py`)
  lock the `tools_scanned` capture fix so the next mine records it.

## When to revisit

Recalibrate when **both** prerequisites are met:

1. The human labeling pass ([`LABELING.md`](LABELING.md)) produces a labeled
   decided set (target ≥ 50, including near-threshold cases — not only the
   ratio-1.0 framework cores).
2. A re-mine populates `tools_scanned` (fix shipped) and, ideally, a
   low-confidence / source-warning split (next `MinedRow` schema bump).

Then sweep `(ratio, max_warnings)` against the labeled set, pick the point that
maximizes IE precision/recall, and update this note + the constants together.

## Considered and declined: an `extraction_coverage` ratio report field

A precomputed `extraction_coverage` ratio (`low_confidence_tool_count /
total_tools`) on the report was considered as a sibling to this work and
**declined** under the [surface-discipline gate](../../CONTRIBUTING.md#surface-discipline):

- It moves **no** headline metric. The IE rate is moved by the *threshold*
  (above), not by exposing the ratio.
- It is fully derivable by any consumer from fields the report already carries
  (`release_decision.evidence_coverage.low_confidence_tool_count` and the tool
  count in `tool_surface.total_tools`), and the structured
  `evidence_coverage.evidence_gaps[]` already enumerates each gap with a
  remediation `next_action`.
- "Legibility / completeness" is an explicitly rejected justification for new
  surface, and a new report field is a schema bump.

Per the gate, the default is not to add it. Revisit only if a concrete
consumer names a headline metric the raw ratio (not the existing counts) moves.
