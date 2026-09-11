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
  prove their exhaustiveness. A row's optional `recovery` describes the
  evidence for a missing input or reader limitation only where the loader
  actually classified it; absence does not assign an owner.

## Source recovery evidence

The optional `evidence_gaps[].recovery` object is copied unchanged from the
report. Read `kind` and `reason`, not warning prose or `authorable_by`:

| `kind` | SDK evidence in this first supported slice | Next step |
|---|---|---|
| `input_unavailable` | Configured SDK entrypoint not found (`sdk_entrypoint_not_found`). The SDK loader currently reports this warning for either required or optional entries; the required-source execution-contract mismatch is tracked in #585. | Restore the existing file named by `next_action.path`; a declaration does not replace it. |
| `reader_limitation` | Two literal lists of tool names joined by `+` (`sdk_literal_tool_list_concatenation_unsupported`). The binding reader has no branch for this static form. | Retain the source as a reproducer for an Agents Shipgate reader repair. This does not assert what the deployed agent can access. |
| `unresolved` | An unresolved tools expression (`sdk_tools_expression_unresolved`), or distinct raw source warnings that collapse to one public identity (`ambiguous_warning_identity`). | Establish the missing input or reader limitation before assigning a repair owner. An ambiguous source gets no guessed path. |

Other loaders and SDK warning shapes remain unclassified. A dynamic expression
is not proof of a product defect. Facts are joined to their own raw source and
warning before privacy redaction; ambiguity after redaction stays unresolved.
`source_ref` preserves the observed file/line and `next_action.path` preserves
the file to inspect. The CLI and report/packet source-warning sections show the
same remedy. PR summaries retain up to three recovery explanations even when
the full fix task exceeds the comment budget, and point to the complete report.

This metadata is an additive field on the existing open `EvidenceGap` object,
including its report v0.43, packet v0.18, verifier v0.16 and qualification v6
projections. Older rows omit it on round-trip. It adds no verdict, threshold,
declaration claim, or permission: `review_warning` and `authorable_by` retain
their existing meaning, and an actual IE remains an exact-score miss.

## Scoring and compatibility

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

This diagnostic slice does not issue signed qualification. The same-byte
qualification read repair was delivered in [#578](https://github.com/ThreeMoonsLab/agents-shipgate/pull/578).
Final-wheel collection/scoring in #512 still precedes independent signing in
#509; neither named recovery nor ordinary tests supply that evidence.
