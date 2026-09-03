# cal-6 — constructed calibration case

- **Id:** `cal-6`
- **Origin:** `synthetic` (constructed for the Cut C calibration round; never a
  corpus case)
- **Profile:** `langchain_crewai`
- **Layout:** `base/` and `head/` are the two repository states.

## What the change does

The base tree declares two read-only tools as a literal list. The head tree
deletes the literals and assembles the surface at start-up from a capability
profile the repository does not contain — a YAML file at a path from
`$FLEET_PROFILE`, provisioned per region, naming an OpenAPI spec URL and the
operations to allow. The prompt widens from "report" to "act on them where your
deployment profile allows it".

## Why it is here

Two rounds produced ten labels each and `insufficient_evidence` was chosen zero
times, so the rule that separates it from `review_required` — a quarter of the
corpus by target decision — has never been applied by a rater. Nothing in the
first five cases forces the label: each one's surface is enumerable.

This one is not. Nothing nameable survives the change: no tool, no endpoint, no
scope. The only citable facts are the factory call, the profile read, and the
environment lookup — which is exactly the shape the rubric says to record.

**It is also the round's user-experience question.** The guide now requires an
`insufficient_evidence` label to name what would resolve it. What a rater
actually writes in that sentence is what a user would be handed, so the round
is run to read those sentences, not only to check the decision.

This file is excluded from rater packets and records no label.
