# Release Evidence Policy: The Approved Bar for Pre-1.0 Tags

**Status: decided.**
**Route 2 — an explicit pre-1.0 policy.**
**Owner: Pengfei Hu (`pengfei-threemoonslab`), product/security. Recorded
2026-08-29.** Tracked by
[#341](https://github.com/ThreeMoonsLab/agents-shipgate/issues/341).

Before this decision the repository had exactly one release policy — 100
adjudicated, receipt-bound cases — written as the claim `1.0` should make, and
enforced for every tag including `0.x`. No corpus met it, so no tag could
publish, and the newest published build stayed `v0.15.0`.

This document records the approved alternative, the numbers that define it, the
tags it governs, and what promotion to the 1.0 bar requires. It is the source
the code is written from; where the two disagree, the code is wrong.

## The decision

A second **named** policy now exists. It reduces how much evidence a `0.x` tag
must carry. It reduces nothing about how that evidence is judged.

| | `beta` (production) | `pre_1_0` (approved here) |
|---|---|---|
| Governs | `1.0` and later — and any tag, if offered | `0.x` tags only |
| Adjudicated cases | 100 | **56** |
| Strata (profile × decision) | 28, weighted | **28, two cases each** |
| Qualifying origins | ≥ 40 (40%) | **≥ 23** (40%, rounded up) |
| Cohen's κ floor | 0.80 | **0.80** — unchanged |
| Holdout per stratum | ≥ 20% | **≥ 20%** — unchanged |
| Unsafe auto-passes | 0, per profile and overall | **0** — unchanged |
| Per-case verifier receipt | required, unique digest | **required** — unchanged |
| `static_only` / `runtime_behavior_proven` | `true` / `false` | **unchanged** |
| Safe passes | ≥ 27 of 30 (90%) | **≥ 13 of 14** (92.9%) |
| Blocked exact | ≥ 30 of 30 (100%) | **≥ 14 of 14** (100%) |
| Review exact | ≥ 19 of 20 (95%) | **≥ 14 of 14** (100%) |
| Insufficient-evidence exact | ≥ 19 of 20 (95%) | **≥ 14 of 14** (100%) |
| Report schema | `0.42` | **`0.42`** — unchanged |

### Why these numbers

**Two cases per stratum, not a scaled-down copy of the production weighting.**
Production allocates unevenly — 20 cases to `mcp_openapi_declared_binding`, 10
to `google_adk`. Scaling that shape to 56 pushes the smallest cells to zero or
one. A zero-count cell deletes every observation of a profile × outcome pair,
which is a reduction in *strictness*, not in coverage: the gate stops being able
to fail for that combination at all. Two per cell is the smallest allocation
that keeps all 28 cells non-empty **and** leaves room for a tuning/holdout split
in every cell at the unchanged 20% holdout fraction — at one case per cell,
`ceil(1 × 0.20) = 1` forces the single case to be holdout and no cell can hold a
tuning case at all.

**What is enforced is a holdout floor, not a 1/1 split.** Each cell must carry
at least `ceil(size × 0.20)` holdout cases — one, at this size. A corpus that
marks *more* cases holdout is accepted, and deliberately so: holdout evidence is
evidence the engine was never tuned on, so more of it is stronger, and a
minimum on tuning cases would be a *maximum* on holdout. The gate must never
reject a corpus for being more conservative than required.

**Exact-match floors are the production rates, rounded up.** 27 of 30 and 13 of
14 are the same 90% demand at two sizes; 12 of 14 would not be. Rounding up
means three of the four floors land on 100% at this size — the smaller corpus
buys less tolerance for error, not more. That is the correct direction: fewer
cases already widen every Wilson interval, so the *claim* is weaker even though
the *gate* is not.

**The origin floor is the same share, not a smaller one.** 40% of the corpus
must be real-history, rejected-or-reverted, or design-partner in both policies.

**Nothing on the invariant list moved.** Zero unsafe auto-passes per profile and
overall, a unique terminal verifier receipt per case, the holdout fraction, the
κ floor, `static_only`, and verifier re-derivation of every count are
byte-identical between the two policies. A smaller corpus may reduce
**coverage**. It must not reduce **strictness**.

### Which tags it governs, and how promotion works

The governing policy is a function of the **version**, applied by the pipeline:

- **epoch 0, major version 0** (`0.16.0b7`, `0.16.0`, `0.17.0`) → `pre_1_0` is
  sufficient. `beta` is also accepted: an artifact is never rejected for
  carrying more evidence than its tag requires.
- **anything else** (`1.0.0`, `1.0.0rc1`, `9.9.9`, `1!0.1`) → `beta` only.
- **an unparsable version** → `beta` only. The fallback is the strictest
  policy, never the cheapest. "Unparsable" means anything that is not a
  complete PEP 440 version: `0garbage` and `0.16.0garbage` are *not* pre-1.0,
  even though they begin with a zero.

This is not "inferring the choice from the tag", which #341 forbade. The choice
was made here, by a named human, and *is* a rule keyed to the version range;
the pipeline applies that approved rule rather than selecting a bar of its own.
Nothing in the artifact influences which policy applies to it.

**Promotion to 1.0 is not automatic and has no shortcut.** There is no path by
which `pre_1_0` evidence qualifies a `1.0` tag. Shipping 1.0 requires the full
100-case artifact, which is what corpus-delivery issue
[#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456) exists to
produce.
When it lands, `pre_1_0` stops being reachable by construction — every tag from
then on has a major version of at least 1 — and the tier is retired from the
vocabulary in the same change that ships the 1.0 bar.

### What was rejected, and why

**Route 1 — keep the 100-case bar for every tag.** Its benefit is real: the
shipped claim is exactly the claim that was designed, with no second policy to
maintain or retire. Its cost is that publication stays blocked on roughly 100
adjudicated, receipt-bound cases that do not exist, while evaluators keep
installing `v0.15.0` — an older, less-verified build than the one being
withheld. Withholding a better build behind a 1.0-grade evidence bar makes
users less safe, not more. That is the trade the owner declined.

**Renaming the 100-case policy.** Explicitly rejected by #341 and not done:
`beta` still means exactly what it meant, with the same thresholds and the same
constructor. `pre_1_0` is additive.

**Reusing `production_qualified` for both tiers.** The flag keeps meaning "met
the 100-case bar". A `pre_1_0` artifact reports `production_qualified: false`,
and the artifact schema refuses to *construct* one that says otherwise — so the
producer cannot emit the inconsistency, rather than every reader having to
catch it after signing. A field that quietly changed meaning would be the
easiest way for this decision to be misread later.

## Where the bar is defined

Changing the bar means changing all of these together. The first five are
cross-checked, so a partial change fails a release verifier rather than
silently weakening it. The last two are not, and are the more dangerous
omission for exactly that reason: nothing fails, and the corpus gets built to
the wrong shape.

- `production_safety_requirements()` and `pre_release_safety_requirements()` in
  `src/agents_shipgate/schemas/safety_qualification.py` — case counts, strata,
  origin minimum, κ floor, holdout fraction, unsafe-auto-pass maximum.
- `QualificationTier` — `beta`, `pre_1_0`, `test`. `tier_for_requirements()`
  names a tier from what the thresholds *are*, so an ad-hoc threshold set scores
  as `test` and can never release. `SafetyQualificationResultV1` additionally
  refuses to construct an artifact whose `production_qualified` disagrees with
  its tier, or one that claims a legacy envelope while carrying `pre_1_0`. Both
  are grammar changes, so the envelope advances
  `shipgate.safety_qualification/v4 → v5`; see the migration note in
  [`STABILITY.md`](../STABILITY.md).
- `scripts/run_safety_qualification.py` — produces the artifact, and selects the
  policy from the wheel version (`--policy-tier` may opt *up* to production;
  it refuses to opt down for a 1.0-or-later wheel).
- `scripts/verify_safety_qualification_release.py` — the exhaustive gate.
  Re-derives every count, interval and confusion matrix from the governing
  policy.
- `scripts/verify_qualification_binding.py` — **the sealing gate.** Standard
  library only, so it *restates* each tier's whole `requirements` block —
  strata, exact-match floors, holdout minimum, origin and κ floors, and the
  report schema version — rather than importing it, re-derives from the raw
  cases everything the cases can attest, and compares the artifact's own
  declared `requirements` against the restatement for the rest;
  `test_the_stdlib_policy_table_matches_the_named_policies` binds every field
  of the two copies. Restating only a case count is not enough — it cannot
  tell 56 correctly stratified cases from 56 identical ones, nor notice a
  corpus two safe passes below its floor, which is exactly the class of
  weakening this gate exists to stop. *This site is easy to miss: an earlier
  draft of this brief listed only five.*
- `scripts/_release_support.py` — the version→tier rule, shared by both gates
  and the producer. It requires a **complete** PEP 440 parse: a version it
  cannot fully parse is not pre-1.0, so it falls to the production bar.
- `benchmark/safety-qualification/README.md` — **the corpus owner's runbook**,
  and the one that decides what actually gets built. A change that left this
  behind would aim the corpus-delivery effort at the wrong shape entirely,
  which no gate can detect and no verifier error would explain.
- `docs/distribution.md`, [`release-runbook.md`](release-runbook.md) and
  `docs/INDEX.md` — the operator-facing description of the protected release
  input, and the index agents walk to find it.

## Acceptance

- [x] A named human product/security owner records the route and rationale —
      Pengfei Hu, 2026-08-29, above.
- [x] Case counts, origin counts, agreement threshold, holdout rule, and the
      zero-unsafe-pass invariant are explicit — the table above.
- [x] The policy states which versions/tags it governs and how promotion works.
- [x] Documentation, schema vocabulary, qualification generator, release
      verifiers, and this runbook agree — enforced by the tests named above,
      not only asserted here.
- [x] A separate corpus-delivery issue is opened for the 56-case artifact and
      the 100-case 1.0 bar —
      [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456).
- [ ] A rehearsal proves the chosen policy **fails closed** — dispatch
      **Release Rehearsal** against an artifact that misses the bar and confirm
      publication stops at the qualification step. The deterministic half of
      this is already covered:
      `test_a_pre_1_0_artifact_publishes_a_0_x_tag_and_nothing_later` and
      `test_the_sealer_reads_the_governing_policy_from_the_version_not_the_artifact`
      prove the same bytes that pass a `0.x` tag fail a `1.0` one, at both
      gates.

## What is already true regardless

The other five release-integrity controls do not depend on this decision and
are in place: source-to-wheel binding, separated verification and publication
with a recoverable transaction, deterministic test selection with a measured
timeout, a non-publishing rehearsal path, and a wheel-scoped signed SBOM.

Whichever bar applies, it is enforced by a pipeline that also proves the bytes
it publishes came from the tagged commit.
