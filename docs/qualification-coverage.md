# Reading qualification coverage misses

The qualification runner scores the frozen human labels against terminal
verifier receipts. An actual `insufficient_evidence` on the approved three-label
corpus is a coverage miss and still loses the applicable exact-outcome score.
It is never relabeled to match an expected outcome.

In `shipgate.safety_qualification/v6`, read `coverage_misses[]` by profile:

- `count` counts recorded actual-IE outcomes; `denominator` includes **every
  corpus case** in that profile, including ones without a valid receipt.
- `rate` is `count / denominator`, rounded to six decimal places. It is a
  **lower bound** while `unscored_case_ids` is nonempty. A zero with unscored
  cases does not establish complete coverage. Those cases retain their existing
  receipt failures and prevent qualification.
- `cases[]` names every actual-IE case and copies the existing typed
  `evidence_gaps` from the validated report object used for scoring. Subject,
  source reference, reason and next action retain their report meaning. No
  report is reread to produce these diagnostics.
- `gap_status: unclassified` means that report recorded no named gap rows.
  It remains a counted miss. `named_gaps` says that rows exist; it does not
  prove their exhaustiveness or identify whether a missing input or a product
  reader fix resolves the case. That distinction remains [#561](https://github.com/ThreeMoonsLab/agents-shipgate/issues/561).

The six existing `intervals[]` metrics keep their numbers, Wilson intervals,
requirements and `passed` booleans. Read `applicability` before presenting a
metric as coverage achieved:

| Value | Meaning |
|---|---|
| `applicable` | The existing outcome-specific policy test applies. |
| `not_applicable` | Expected-IE has no corpus cases and its policy floor is zero. Legacy `0/0`, rate `0`, `passed: true` is **not** a coverage success. |
| `diagnostic` | Overall exactness is descriptive; the existing outcome-specific tests govern qualification. |

A positive expected-IE floor remains applicable and fails when unmet, even
with a zero denominator. No threshold, label, decision value or release
permission changes. Coverage-miss rates add no qualification threshold.

Older v1/v2/v4 artifacts retain their existing beta/test restriction and
normalize to v5; v5 remains readable for all of its original tiers. They have
no coverage diagnostics or applicability field. Absence means **not recorded**,
never zero. A v6 artifact must carry both fields; relabeling those fields as an
older closed grammar is rejected. Corpus and receipt-index versions stay v4.

This diagnostic slice does not issue signed qualification. The artifact
read-consistency defect [#559](https://github.com/ThreeMoonsLab/agents-shipgate/issues/559)
must be resolved before the final candidate scoring in #509.
