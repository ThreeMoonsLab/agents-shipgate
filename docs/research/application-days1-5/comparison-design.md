# Proposed application comparison inputs (#580 / #655)

Status: selected implementation design, 2026-09-24; **not a shipped API**.
This settles the Days 1–5 input contract before Weeks 2–3 implementation.

## User outcome

From a fresh unconfigured repository and precise PR refs, show what changed in
each observed agent's callable tool surface, with source evidence and named
limits. A missing manifest must not erase readable application changes. Do not
infer effects, business authority or a deployed root to obtain a result.

## One subject, independent sides

Extend the existing comparison/receipt identities, not the decision engine.
Bind requested base/head, actual merge base and compared commit/tree IDs,
engine version/contract, reader/config options, and each side's scope and input
content digests. Record discovery bounds, unread paths, excluded dependencies
and observed agent/tool wiring. Canonical ordering and deterministic hashing
must include the origin of each input selection.

Select base and head independently. A head source list may seed candidate
search on base; it cannot establish base completeness or the old deployed root.
Manifest-backed, discovery-derived and reviewer-selected scopes remain distinct
provenance. Reuse already extracted per-agent edges (#867) even when a deployed
root is unresolved; label them observed wiring rather than root reachability.

| Input situation | Required evidence and result |
| --- | --- |
| Existing app, no manifest at base | Discover/read base independently; missing configuration is not an empty surface |
| Added directory | Complete Git tree establishes absence only at that exact path; broader agent absence needs an explicit bounded subject and reviewed identity claim |
| Renamed scope | Bind old and new locations plus Git/content evidence; a file rename alone does not prove agent identity |
| Ambiguous move / split / merge | Keep separate observations and unresolved correspondence; do not invent additions/removals |
| Unread base, truncated census, missing objects | Named incomplete comparison; never zero changes or a manufactured empty base |
| Unrelated gitlink or symlink | Retain a named materialization limitation until safe bounded reading is supported; dependencies crossing it remain unresolved |
| Readable independent agents plus partial edge | Preserve proven rows with per-agent coverage limits; never label whole application complete |

## Advisory comparison versus verification

Generated comparison inputs establish source-observed structure only. They must
carry an explicit generated origin through plan, report, receipt and consumers.
They cannot be relabeled as an independently scanned, reviewed verifier base or
satisfy historical qualification bars. Existing manifest/policy/trust-root
deltas remain visible. Existing release decisions and current-control identity
rules remain authoritative; advisory rows grant no publication or merge rights.

Thus revise #655's absolute “never unavailable” aim to “show every established
structural change and precisely identify unresolved comparisons.” Missing Git
objects, dynamic wiring and ambiguous subjects are legitimate limitations.
Reject copying head inventory into base and reject keyword inference presented
as declared effect. No purpose/effect/authority/agent-binding auto-fill.

## Minimal delivery and contract review

1. Extend existing paired input selection for exact trees and independent scopes
   (#580). Keep fail-closed verifier behavior until all input joins are supported.
2. Add manifest-free advisory paired extraction (#655), consumed by existing
   capability projection. Reader support is unchanged in this increment.
3. Preserve per-agent direct edges (#867), then expand only the reproduced
   #864 → #865 and #866 shapes. Re-run all four real examples after each change.
4. Before adding fields, choose versioned model and current JSON projections,
   enumerate frozen predecessor down-projections under STABILITY.md, and test
   current and legacy consumers. Field names in this proposal are concepts,
   not a premature public schema or a promised new CLI flag.

## Required engineering acceptance

- Fixed added/renamed scope cases google/adk-samples#1975/#1977 from #580,
  bound to its recorded SHAs; new manifest stays a trust-root change.
- Paired add/remove/unchanged tool, relocation, ambiguous rename, unrelated
  deletion, multiple roots and unread/truncated base controls.
- Declarations without wiring never count as reachable; unchanged definitions
  with changed bindings do count when their per-agent wiring is established.
- Identical refs, input bytes, engine/options yield identical canonical evidence;
  changing either side's input, scope or generated origin invalidates identity.
- Advisory completeness cannot satisfy reviewed-base qualification or mint a
  release permission. Negative tests cover forged provenance and missing base.
- Each accepted public PR has exact refs, command/engine identity, nonempty
  PR-specific rows, source-reviewed tool coverage and direction, and a concrete
  reviewer decision. Ten passing examples are required by #868; incomplete
  examples remain failures for that acceptance even if some rows are useful.
