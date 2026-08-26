# Evidence-Backed Pass Safety Qualification

This directory is the runbook for the `0.16.0b7` beta safety qualification.
The repository does **not** ship fabricated human labels or a passing result.
Until a real frozen corpus and its verifier receipts exist,
`safety-qualification.json` must not be published as qualified.

## Inputs

The runner consumes four independently content-addressed inputs:

1. A built `agents-shipgate` wheel. The runner reads wheel metadata without
   importing or executing it and records the wheel SHA-256.
2. A `shipgate.safety_corpus/v4` JSON/YAML corpus. Every case has two blind,
   attributable labels (`security_governance` and `framework_tooling`), an
   evidence-backed final decision, a remediation condition, and a third-human
   adjudication for every disagreement. `frozen_labels_sha256` must match the
   canonical label payload.
3. A `shipgate.safety_receipt_index/v4` JSON/YAML index created only after
   labels are frozen. It binds the exact wheel, corpus, labels, policy bundle,
   and one terminal `shipgate.verification_receipt/v1` plus its
   `shipgate.verify_run/v3` projection per case.
4. The qualification policy file(s) or directory. Directory hashing includes
   every file by stable relative path.

Receipt entries must point to real `verify` artifacts. Each receipt needs
successful base and head tree-bound runs plus content-addressed
`verifier_json` and `report_json` artifacts. The report must use schema `0.40`,
contain binding and semantic coverage, and agree with the verifier receipt. Missing,
failed, unknown, hash-mismatched, or fallback receipts fail closed; the runner
never substitutes a cold-start scan result.

## Production acceptance policy

The CLI policy is fixed in code and has no threshold-relaxation flags:

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

Tests can inject smaller requirements into the Python API, but those artifacts
are marked `qualification_tier: test` and can never set
`production_qualified: true`.

## Run

```bash
PYTHONPATH=src python scripts/run_safety_qualification.py \
  --wheel dist/agents_shipgate-0.16.0b7-py3-none-any.whl \
  --corpus /secure/frozen-corpus.json \
  --receipts /secure/receipt-index.json \
  --policy /secure/qualification-policy/ \
  --out safety-qualification.json --json
```

Exit `0` means the exact production policy passed. Exit `1` means a complete
artifact was emitted with `production_qualified: false` and sorted
`failures[]`. Input/schema errors exit `2` before scoring.

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
for the exact six-variable contract. The tag workflow verifies the signer,
production result, tag/version, and wheel digest before it can publish.

The release workflow treats the configured signer identity and the signed
qualification result as trust inputs. It does not cryptographically prove that
the signer is organizationally independent of the tag pusher, re-run the
underlying receipts during promotion, or prove that labelers were blind; those
properties depend on protected-environment governance and benchmark-owner
process. Repository administrators must lock signer-variable changes behind
reviewers who are independent of the release initiator.

The production policy's `minimum_qualified_origins = 40` accepts a combined
count of real-history, rejected/reverted, and design-partner cases. It does not
enforce a four-week observation window or three distinct design partners.
Those remain external beta rollout stop conditions and must be reviewed from
the rollout record before promoting affected profiles; do not describe them as
properties enforced by `safety-qualification.json`.
