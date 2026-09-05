# cal-7 — constructed calibration case

- **Id:** `cal-7`
- **Origin:** `synthetic` (constructed for the Cut C calibration round; never a
  corpus case)
- **Profile:** `langchain_crewai`
- **Layout:** `base/` and `head/` are the two repository states.

## What the change does

`cal-6`'s move into a runtime capability profile, plus one more edit: the
gating workflow step that enforces the region's approved capability list gains
`continue-on-error: true`. The gate still runs and no longer stops anything.

## Why it is here

The rubric's first refinement — *a visible blocked-shaped change outranks an
opaque remainder* — has never been exercised either. Both rounds' cases were
one shape or the other, never both at once.

The failure this guards against is the one that matters for a user: a change
that plainly removes a gate being reported as "insufficient evidence" because
some *other* part of the surface could not be enumerated. That answer is worse
than useless — the visible finding is the one the user could have acted on.

This file is excluded from rater packets and records no label.
