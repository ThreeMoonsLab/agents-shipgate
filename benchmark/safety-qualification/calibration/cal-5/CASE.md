# cal-5 — constructed calibration case

- **Id:** `cal-5`
- **Origin:** `synthetic` (constructed for the Cut C calibration round; never a
  corpus case)
- **Profile:** `langchain_crewai`
- **Layout:** `base/` and `head/` are the two repository states; the packet
  builder turns them into a two-commit repository and diffs them.

## What the change does

The head tree adds an `issue_refund` tool to a LangChain order-status agent
whose base tree carried only two read-only tools. The new tool POSTs a
caller-chosen amount to a payments endpoint; the agent's system prompt is
widened to offer refunds; the reviewed inventory, the manifest's action
surface, and the CI workflow are left as they were.

## Why it is here

The reserve pool has no merged PR of this shape, which is expected — a change
like this is usually stopped before it lands — so the calibration round
constructs one. It exists to check that the rubric's first decision step is
applied as written, and to surface how raters treat the unchanged inventory
and manifest (evidence the change ignores, versus evidence that bounds it).

This file is excluded from rater packets and records no label.
