# Finding attribution

`tool_surface_diff.finding_deltas` answers a question about identity: is this
fingerprint in the base report? Three very different changes give it the same
answer. A repository weakness the pull request never touched, a change that
bounds the capability without perfecting it, and a change that widens the
capability all land in one `unchanged_findings` row.

`tool_surface_diff.finding_attributions[]` (#515) answers the other question —
what did *this change* do to the bound this finding depends on — for the
findings the shipped comparison profiles can actually speak about. It is an
optional diagnostic. It changes no finding, fingerprint, support
classification, severity, baseline, exit code or release decision, it
introduces no check ID, and `finding_exclusion_eligible` is `false` on every
row. There is no scope flag: `--scope diff` / `--scope tree` and the receipt
question they imply are still #515's open work.

## What a row is, and is not

Each row names one active finding and one class:

| Class | Means |
|---|---|
| `widened_by_change` | A bound this finding depends on was removed, or the compared domain of the capability it names grew. |
| `improved_not_resolved` | A bound was added or narrowed and the finding still stands — an improvement, not the finding's cause. |
| `standing_weakness` | The finding matched the base and every *modeled* bound it depends on is unchanged. |
| `unresolved` | No comparable evidence, a refused direction, or a contradiction on the same capability. |

`standing_weakness` is the class most easily misread. It says no modeled bound
changed. It does not say the capability is unchanged, because
`dependency_coverage` is still `incomplete`: no profile has yet proved a
complete shared-helper, import and configuration closure for a capability
(#557). A finding is not droppable because it carries this class.

## Where the direction comes from

The projection reads no source, reconstructs no tree and runs no second diff.
Every direction it reports is copied verbatim out of a comparison row that
`compare_operations` (`openapi_delete/v1`,
[contract](operation-attribution.md)) or `compare_guard_dependencies`
(`sdk_boolean_guard/v1`) already published, and the row carries that profile's
own spelling in `evidence[].direction` so it can be joined back to the
comparison it came from.

`evidence[].effect` is the one place the two vocabularies are reconciled. A
compared domain that is neither equal to nor a subset of the base contains at
least one member the base did not declare, so `changed` and `predicate_changed`
are recorded as `widening` — a proved statement about the compared sets, not a
risk judgement.

`evidence[].base_pointer` / `head_pointer` name the compared bound on each
side: the declaring OpenAPI document plus its JSON pointer, or the guard
module and line. A pointer printed under a `guard_predicate` axis has to name
the guard, so an observation whose guard location is unknown publishes no
pointer for that side rather than substituting the tool declaration.

## The three refusals

**Only predicate-linked evidence classifies.** A profile row earns
`link: "predicate"` by naming the finding's own fingerprint, which today only
`openapi_delete/v1` does. A row joined by canonical tool id is `link:
"capability"`: evidence about the same capability whose relation to *this*
finding's predicate is unproved. A tool's display name is never a join;
a guard comparison without a canonical tool id, or a finding without one, is
not joined at all. And because a profile row names a *fingerprint*, two active
findings answering to one fingerprint make every row about them `unresolved`:
no comparison can say which of them it names.

**Capability-linked evidence can withdraw a claim, never establish one.** It
never promotes a finding to a direction of its own, and the two negative claims
are withdrawn by different things, because they claim different amounts.

`standing_weakness` says nothing anywhere on the capability got worse, so *any*
same-capability row that is not `unchanged` defeats it — a widened guard
predicate, a literal true-return domain that grew, or an axis the profile could
not compare at all.

`improved_not_resolved` is narrower: a specific bound this finding depends on
demonstrably narrowed. An axis elsewhere on the capability that could not be
compared does not refute that, so it is withdrawn only by a demonstrated
`widening` — where calling the change an improvement would misstate the net
direction.

**Absence is not agreement.** A finding no profile can compare draws no row.
The number of them is published as `tool_surface_diff.unattributed_findings`,
not only as prose, because `report.md` renders three diff notes and a fourth is
dropped — the one statement that stops an empty attribution reading as a clean
one cannot live where it can be truncated. A finding carrying neither a
fingerprint nor an id has no identity to join on at all, which is the strongest
form of "not compared", so it is counted rather than skipped.

A run that asks no diff question answers none: where neither profile is active,
or where there is no base — no `--diff-from` report, no baseline snapshot and no
reconstructed Git operation base — the block is absent entirely rather than
filled with `unresolved` rows against every finding of every plain `scan`. The
missing base is already explicit in `tool_surface_diff.notes`. Where a base
exists but the identity buckets do not name a fingerprint, `identity` is
`unresolved`; it is never reported as `new`.

## Boundary

Rows are ordered most-consequential first and then by finding identity, so a
rerun on an unchanged repository prints an unchanged block and the Markdown
section's eight-row limit cannot hide a widening behind a documentation
finding. `report.json` carries every row; `report.md` spells the rows that
reached a direction and counts the rest — both the rows that stayed
`unresolved` and the findings that drew no row at all — because one
`unresolved` sentence per finding on a shared capability buries the decided
rows printed beside it.
Neither surface can disagree with the other about a class: both read the same
rows, and only this projection produces them.

The regression inputs are isolated synthetic fixtures and paired committed
repositories, not deployed wiring, human qualification labels or runtime
observations. #557 owns the dependency closure this projection reports as
incomplete; #515 owns the scope flag, the receipt question, decision
consumption and its TypeScript MongoDB `cal-1` case; #563/#312 still require
independently reviewed historical evidence. This projection satisfies none of
those bars and does not freeze report 1.0 (#569).
