# Decision Brief: The Evidence Bar for Pre-1.0 Tags

**Status: open. Awaiting a named human product/security owner.**
Tracked by [#341](https://github.com/ThreeMoonsLab/agents-shipgate/issues/341).

This brief exists to make the decision cheap to take, not to take it. No route
is selected here, and nothing in this document changes the enforced policy: the
release verifier still requires the full 100-case production-beta artifact.

Do not infer the choice from the tag, and do not rename the current policy after
the fact.

## Why a decision is required

`v0.16.0` is the first tag that will run the safety-qualification gate
end to end. The gate was added after `v0.15.0`, so it has never executed as a
release precondition.

The bar it enforces cannot currently be met:

| Requirement | Enforced today | Available today |
|---|---|---|
| Adjudicated cases | 100, across 28 profile × decision strata | 32 labelled rows |
| Qualifying origins | ≥ 40 real-history / rejected-or-reverted / design-partner | not separately tracked |
| Per-case verifier receipt | required, unique digest per case | not produced by the miner corpus |
| Holdout per stratum | enforced fraction per stratum | n/a |
| Label agreement | Cohen's κ floor | n/a |

The 32 rows under `benchmark/miner/results/*.labels.csv` are a useful product
measurement, but they are not interchangeable with qualification cases:
qualification additionally binds adjudicated labels and a terminal verifier
receipt per case.

So `v0.16.0` is blocked on a policy question, not on engineering. The five
engineering workstreams (#342, #343, #344, #355, #356) are complete and
independent of this decision.

## Where the bar is defined

Changing the bar means changing all of these together; they are cross-checked,
so a partial change fails the release verifier rather than silently weakening
it.

- `production_safety_requirements()` in
  `src/agents_shipgate/schemas/safety_qualification.py` — case counts, strata,
  origin minimum, κ floor, holdout fraction, unsafe-auto-pass maximum.
- `QualificationTier` — distinguishes `beta` from `test`. There is **no latent,
  already-approved "production tier"** to select at 1.0.
- `scripts/verify_safety_qualification_release.py` — requires
  `qualification_tier == "beta"` and `production_qualified == true`, and
  re-derives every count, interval, and confusion matrix.
- `scripts/run_safety_qualification.py` — produces the artifact.
- `docs/distribution.md` — describes the 100-case artifact as the protected
  release input.

## The two routes

### Route 1 — retain the 100-case production-beta policy

`v0.16.0` stays blocked until a separate corpus-delivery issue produces the
independently labelled, adjudicated, receipt-bound artifact.

- **Cost:** the release is gated on a substantial data effort — roughly 68 more
  adjudicated cases with receipts, spread to satisfy 28 strata and the origin
  minimum.
- **Benefit:** the shipped claim is exactly the claim that was designed. No
  vocabulary churn, no promotion path to maintain.
- **Implementation:** none. Record the decision, open the corpus issue.

### Route 2 — approve an explicit pre-1.0 policy

Define a **new, separately named** versioned policy governing `0.x` tags, with
reduced evidence coverage and an explicit promotion path to the 100-case bar
at 1.0.

- **Cost:** a new tier in the schema vocabulary, a second requirements
  constructor, verifier branching, and doc updates. Adds a surface that must
  later be retired.
- **Benefit:** unblocks `v0.16.0` on a stated, auditable basis rather than an
  implied one.
- **Non-negotiable regardless of the numbers chosen:**
  - zero unsafe auto-passes, per profile and overall
    (`maximum_unsafe_auto_passes == 0`);
  - a per-case terminal verifier receipt, with unique digests;
  - a holdout fraction per stratum;
  - `static_only == true` and `runtime_behavior_proven == false`;
  - internal consistency re-derived by the verifier, never trusted from the
    artifact.

  A "smaller corpus" may reduce **coverage**. It must not reduce **strictness**.

If Route 2 is chosen, the decision must state: case count, strata layout, origin
minimum, κ floor, holdout fraction, which tags the policy governs, and what
promotion to the 1.0 bar requires. Implementation follows in a separate issue —
this decision does not silently implement a lower bar.

## Acceptance for whichever route is chosen

- [ ] A named human product/security owner records the route and rationale.
- [ ] Case counts, origin counts, agreement threshold, holdout rule, and the
      zero-unsafe-pass invariant are explicit.
- [ ] The policy states which versions/tags it governs and how promotion works.
- [ ] Documentation, schema vocabulary, qualification generator, release
      verifier, and this runbook agree.
- [ ] A separate implementation/corpus-delivery issue is opened.
- [ ] A rehearsal ([`release-runbook.md`](release-runbook.md)) proves the chosen
      policy **fails closed** — run it against an artifact that misses the bar
      and confirm publication stops at the qualification step.

## What is already true regardless

The other five release-integrity controls do not depend on this decision and are
in place: source-to-wheel binding, separated verification and publication with a
recoverable transaction, deterministic test selection with a measured timeout, a
non-publishing rehearsal path, and a wheel-scoped signed SBOM.

Whichever bar is approved, it will be enforced by a pipeline that also proves
the bytes it publishes came from the tagged commit.
