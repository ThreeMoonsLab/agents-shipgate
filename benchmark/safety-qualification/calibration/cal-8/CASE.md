# cal-8 — constructed calibration case

- **Id:** `cal-8`
- **Origin:** `synthetic` (constructed for the Cut C calibration round; never a
  corpus case)
- **Profile:** `langchain_crewai`
- **Layout:** `base/` and `head/` are the two repository states.

## What the change does

`cal-6`'s move into a runtime capability profile, plus one tool declared as a
literal in the same diff: `dispatch_tow`, which POSTs to a dispatch service and
bills the account for the callout. Part of the new surface is nameable; the
rest is not.

## Why it is here

This is the shape a real repository most often has, and the one where
`insufficient_evidence` is most likely to destroy value. A rater — or a gate —
that answers "insufficient evidence" and stops has thrown away the one finding
the user could act on, which is sitting in the diff with a name.

The case does not presume its own answer. What it is run for is to see whether
the nameable half survives into the rationale and the citations at all, under
either label. If it does not, the guide's requirement that
`insufficient_evidence` name what would resolve it is not enough on its own,
and the rule needs a second half about what a partial answer must still report.

This file is excluded from rater packets and records no label.
