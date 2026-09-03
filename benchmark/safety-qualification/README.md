# Evidence-Backed Pass Safety Qualification

This directory is the runbook for the `0.16.0` safety qualification.
The repository does **not** ship fabricated human labels or a passing result.
Until a real frozen corpus and its verifier receipts exist,
`safety-qualification.json` must not be published as qualified.

## Inputs

The runner consumes four independently content-addressed inputs:

1. A built `agents-shipgate` wheel. The runner reads wheel metadata without
   importing or executing it and records the wheel SHA-256.
2. A `shipgate.safety_corpus/v4` JSON/YAML corpus. Every case has two blind,
   attributable labels (`security_governance` and `framework_tooling`), an
   evidence-backed final decision, a remediation condition, and a third-party
   adjudication for every disagreement. `frozen_labels_sha256` must match the
   canonical label payload. **Who produces the two labels is governed by
   [Amendment 1](../../docs/release-evidence-policy-decision.md#amendment-1--the-pre-10-labeling-protocol-and-the-participant-validation-gate):**
   for the `pre_1_0` corpus, two independent agent sessions on different model
   families under its six admissibility conditions, with the owner
   adjudicating every disagreement; the `beta` (1.0) corpus commits to human
   primary labels. After labels are frozen and receipts exist, the
   non-gating [participant-validation gate](participant-validation.md) sends
   each case's real author/reviewer a case card for validation.
3. A `shipgate.safety_receipt_index/v4` JSON/YAML index created only after
   labels are frozen. It binds the exact wheel, corpus, labels, policy bundle,
   and one terminal `shipgate.verification_receipt/v1` plus its
   `shipgate.verify_run/v3` projection per case.
4. The qualification policy file(s) or directory. Directory hashing includes
   every file by stable relative path.

Receipt entries must point to real `verify` artifacts. Each receipt needs
successful base and head tree-bound runs plus content-addressed
`verifier_json` and `report_json` artifacts. The report must use the schema
`required_report_schema_version` pins (`0.43` today, identical in both
policies and asserted equal to what the engine emits by
`test_the_qualification_gate_demands_the_schema_the_engine_emits`),
contain binding and semantic coverage, and agree with the verifier receipt. Missing,
failed, unknown, hash-mismatched, or fallback receipts fail closed; the runner
never substitutes a cold-start scan result.

## What to build first

[`strata-inventory.csv`](strata-inventory.csv) maps the known candidate pool onto
the 28 profile × decision cells, so mining aims at the empty ones; how to read
and maintain it is in [`strata-inventory.md`](strata-inventory.md). It is a
sourcing plan, not evidence: it carries no label, no verdict and no receipt, and
it is **not** an admissible rater input — it names a target decision for every
slot, so a rater session that has read it produces no admissible label.

## Which policy to build for

**Two named policies exist. Build for the one the release's version admits —
they are not interchangeable, and a corpus sized for one fails every stratum of
the other.**

| Wheel version | Policy | Tier | Cases |
|---|---|---|---|
| `0.x` (epoch 0, major 0) | pre-1.0 | `pre_1_0` | 56 |
| `1.0` and later | production | `beta` | 100 |

A `0.x` release may also publish on a production-policy artifact — more
evidence than the tag requires is never rejected — but nothing goes the other
way. The route, the numbers and the rationale were approved by a named
product/security owner in
[`docs/release-evidence-policy-decision.md`](../../docs/release-evidence-policy-decision.md)
(issue #341).

### Production acceptance policy (`beta`, 100 cases)

- 100 cases with exact declared MCP/OpenAPI, OpenAI Agents SDK,
  LangChain/CrewAI, Google ADK, n8n, multi-agent/handoff, and coding-agent strata:
  30 `passed`, 20 `review_required`, 20 `insufficient_evidence`, 30 `blocked`.
- At least 40 real-history, rejected/reverted, or design-partner cases.
- Cohen's κ ≥ 0.80 across the two independent primary labels.
- At least 20% holdout in every profile/outcome stratum. Because cases are
  indivisible, the minimum is `ceil(stratum_size × 0.20)`.
- Unsafe auto-pass `0/70`; blocked exact `30/30`; safe pass at least `27/30`;
  exact review at least `19/20`; exact insufficient-evidence at least `19/20`.
- Zero per-profile unsafe pass and zero invalid or missing receipts.

### Pre-1.0 acceptance policy (`pre_1_0`, 56 cases)

Less coverage, identical strictness. Same seven profiles, same four outcomes,
**exactly two cases in each of the 28 strata** — not the production weighting
scaled down, which would empty the smallest cells.

- 56 cases: 14 `passed`, 14 `review_required`, 14 `insufficient_evidence`,
  14 `blocked`, two per profile/outcome pair.
- At least 23 real-history, rejected/reverted, or design-partner cases — the
  same 40% share the production policy demands.
- Cohen's κ ≥ 0.80. **Unchanged.**
- At least 20% holdout per stratum. **Unchanged** — at two cases per stratum
  that is `ceil(2 × 0.20) = 1` holdout, leaving room for one tuning case. The
  floor is a *minimum*: marking both cases holdout is accepted, because holdout
  evidence was never tuned on and more of it is stronger. Nothing requires a
  tuning case, which would be a ceiling on holdout.
- Unsafe auto-pass `0/42`; blocked exact `14/14`; safe pass at least `13/14`;
  exact review `14/14`; exact insufficient-evidence `14/14`. These are the
  production *rates* rounded up, so the smaller corpus has **less** tolerance
  for error, not more.
- Zero per-profile unsafe pass and zero invalid or missing receipts.
  **Unchanged.**

### What the CLI will and will not do for you

`--policy-tier` selects between the two named policies. It has no
threshold-relaxation flags, and it cannot select a policy the wheel's version
does not admit:

- `auto` (default) — pre-1.0 for a `0.x` wheel, production otherwise. An
  unparsable version falls to production, never to the cheaper policy.
- `production` — always available, on any version.
- `pre-1.0` — **refused** for a `1.0`-or-later wheel, before any scoring.

Tests can inject smaller requirements into the Python API, but those artifacts
are marked `qualification_tier: test`, can never set
`production_qualified: true`, and are rejected by both release gates.

Both release gates re-derive the strata and every floor above from the artifact's
own cases — the standard-library sealing gate included. A corpus with the right
*total* but the wrong distribution, or one that misses a single exact-match
floor, fails at both. Every case needs a unique, non-blank id and a terminal
verifier decision: an absent decision is a missing case, not a low score.

The result envelope is `shipgate.safety_qualification/v5`, and a `pre_1_0`
artifact may not claim an earlier one — those readers admit `beta` and `test`
only. Both gates also check the artifact's declared `requirements` block
field-for-field against the approved policy, including
`required_report_schema_version`, which nothing in `cases` can attest.

## Run

```bash
PYTHONPATH=src python scripts/run_safety_qualification.py \
  --wheel dist/agents_shipgate-0.16.0-py3-none-any.whl \
  --corpus /secure/frozen-corpus.json \
  --receipts /secure/receipt-index.json \
  --policy /secure/qualification-policy/ \
  --out safety-qualification.json --json
```

Exit `0` means the exact named policy the version selects passed, with no
failures. Exit `1` means a complete artifact was emitted with sorted
`failures[]`. Input/schema errors — including asking for a policy the version
does not admit — exit `2` before scoring.

`production_qualified` keeps meaning "met the 100-case bar": a passing
`pre_1_0` artifact reports it `false`, and both release gates reject an
artifact that claims otherwise.

The deterministic output records input hashes, exact requirements, strata,
case outcomes, overall and per-profile confusion matrices, Wilson 95%
intervals, and failures. It contains no timestamp. Sign and attach only the
artifact produced from the release wheel after independent benchmark-owner
review; this runner does not possess or invent signing authority.

In the independently controlled signing job, create the bundle explicitly:

```bash
sigstore sign --bundle safety-qualification.sigstore.json safety-qualification.json
```

Release promotion consumes the signed result and the same wheel through
protected `pypi` environment variables. See
[`docs/distribution.md`](../../docs/distribution.md#protected-qualification-inputs)
for the exact variable contract. The tag workflow verifies the signer, a
qualification tier the version admits, tag/version, and wheel digest before it
can publish.

The release workflow treats the configured signer identity and the signed
qualification result as trust inputs. It does not cryptographically prove that
the signer is organizationally independent of the tag pusher, re-run the
underlying receipts during promotion, or prove that labelers were blind; those
properties depend on protected-environment governance and benchmark-owner
process. Repository administrators must lock signer-variable changes behind
reviewers who are independent of the release initiator.

The origin minimum — `minimum_qualified_origins = 40` under the production
policy, `23` under the pre-1.0 one — accepts a combined
count of real-history, rejected/reverted, and design-partner cases. It does not
enforce a four-week observation window or three distinct design partners.
Those remain external beta rollout stop conditions and must be reviewed from
the rollout record before promoting affected profiles; do not describe them as
properties enforced by `safety-qualification.json`.
