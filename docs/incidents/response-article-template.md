# Template: `[incident]` and the merge-time detection boundary

> Status: draft · Owner: `[name]` · Target publication: `[24–48 hours after
> public confirmation]` · Last source check: `[UTC timestamp]`

## What is publicly established

In two or three sentences, state only facts supported by public sources.

- Primary source: `[vendor advisory, maintainer issue, or release record]`
- Corroborating source: `[public technical analysis, when needed]`
- Affected release or date: `[value stated by the source]`

Do not reproduce vulnerable code, leaked data, private reports, or an
unverified attribution.

## The incident shape

Name the review failure in product-independent terms: `[agent weakens its own
gate | governed edits governance | capability change rides release | other]`.
Separate the documented incident from the constructed fixture.

## Replay

Select and link the closest fixture from the
[incident fixture index](README.md):

```bash
uvx agents-shipgate fixture run [fixture-name]
```

Record the generated values, never a hand-written approximation:

- `report.json.release_decision.decision`: `[value]`
- `verifier.json.merge_verdict`: `[value]`
- `can_merge_without_human`: `[value]`
- check IDs and changed paths: `[values]`

## What the verifier catches

Describe the smallest deterministic fact established by the fixture. Link the
specific report evidence and distinguish a block from a human-review route.

## What it does not prove

State that the fixture is static, constructed, and not a reproduction of the
vendor's bug. Name relevant runtime, identity, signing, credential, and release
controls that remain outside the verdict.

## Publication checklist

- Re-run from the tagged package version named in the article.
- Confirm sources are public and still support every factual claim.
- Confirm the fixture contains no vendor code or operational exploit.
- Copy verdicts and check IDs from fresh JSON artifacts.
- Link the relevant fixture write-up and any honest expected-fail gap.
- Ask a human reviewer to approve trust-root or incident-attribution language.
