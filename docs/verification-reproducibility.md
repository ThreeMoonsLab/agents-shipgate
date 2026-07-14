# Verification Identity and Reproduction

Agents Shipgate `0.16.0b6` makes a verification request, its execution, its
decision, and its emitted files independently identifiable. This closes the
previous ambiguity where a mutable branch name, a local cache hit, or an
unbound collection of JSON files could be presented as the same verification.

The terminal trust root is
`agents-shipgate-reports/verification-receipt.json`, validated by
[`verification-receipt-schema.v1.json`](verification-receipt-schema.v1.json).
Read it before the handoff or report. A receipt is written last and only after
all referenced artifacts exist.

## Identity graph

```text
Git subject ──> subject_id ──┐
input blobs ─> input_set_id ├─> request_id ─> unit_result_ids
engine set ──> engine_id ───┤                         │
tasks ───────> task_ids ────┘                         v
                                         decision_id + artifact_set_id
                                                      │
                                                      v
                                                  receipt_id
```

All IDs use SHA-256 over canonical UTF-8 JSON with sorted keys. The request
binds:

- resolved base, head, tree, merge-base, and source/evaluated commit facts;
- an exact committed tree or a hashed working-tree overlay;
- the manifest, tool sources, policy packs, baseline, comparison report,
  changed-file content, diff content, evaluation date, and behavior-affecting
  options;
- the Agents Shipgate version and installed package-content digest,
  Python/runtime requirements, installed dependency RECORD closure, adapter
  set, plugin distribution set, and policy catalog; and
- the normalized task list.

Committed snapshots are materialized from Git objects with `git ls-tree` and
`git cat-file`. They do not use `git archive`, so `.gitattributes`
`export-ignore` and `export-subst` cannot change the evaluated bytes. Symlinks
and submodules fail closed for archived verification inputs.

`attempt_id` is diagnostic and deliberately excluded from `receipt_id`.
Changing an authoritative input, result, decision, or artifact changes the
corresponding content ID. Reusing a base-scan cache does not change the public
verification identity or artifacts, and cached reports are accepted only when
their sidecar content hash validates.

## Local verification and portable execution validation

Normal `agents-shipgate verify` emits the complete graph automatically:

```text
verification-plan.json
verification-input.diff
verification-base-report.json (when a base comparison is evaluated)
verification-unit-result.json
verify-run.json
verifier.json
agent-handoff.json
verification-artifacts.json
verification-receipt.json
```

`verification prepare` can create a portable request plan and exact diff before
evaluation for schedulers that want to inspect or transport inputs:

```bash
agents-shipgate verification prepare --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD \
  --out agents-shipgate-reports/verification-plan.json
```

Preparation does not evaluate policy and cannot produce a verifier or receipt.
The current v1 CLI uses the plan emitted by a normal verifier run when replacing
its local execution-validation unit with a transported worker result:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json

agents-shipgate verification worker \
  --plan agents-shipgate-reports/verification-plan.json \
  --diff agents-shipgate-reports/verification-input.diff \
  --workspace . \
  --out agents-shipgate-reports/verification-unit-result.json

agents-shipgate verification assemble \
  --plan agents-shipgate-reports/verification-plan.json \
  --unit-result agents-shipgate-reports/verification-unit-result.json \
  --verifier agents-shipgate-reports/verifier.json \
  --artifacts-root agents-shipgate-reports \
  --out agents-shipgate-reports/verification-receipt.json

agents-shipgate verification reproduce \
  --receipt agents-shipgate-reports/verification-receipt.json \
  --artifacts-root agents-shipgate-reports
```

A worker validates the installed engine and every supplied input hash,
including the exact diff bytes and bundled base report, and may emit normalized
intermediate representation only. The schema rejects worker-provided
`decision`, `merge_verdict`, `control`, or `can_merge_without_human` fields.
The assembler is the sole decision closure and rejects missing, failed,
foreign-request, duplicate, or wrong-task results.

The v1 protocol intentionally defines one deterministic `evaluate` task. The
worker command validates execution compatibility and input transport; it does
not offload the scan or policy engine. The verifier remains the sole policy
engine and the assembler only re-closes its decision over the supplied worker
unit. Therefore this release establishes a portable fail-closed execution
boundary, not distributed evaluation, parallel speedup, or arbitrary sharding.
Those claims require deterministic extraction/evaluation IR, partitioning, and
cross-executor equivalence qualification that are not present in v1.

## Time and GitHub Actions

The plan's `evaluation_date` is content-bound provenance, derived from the
evaluated head commit unless explicitly declared during plan preparation. It
is not authority to extend reviewer-owned trust. Baseline exceptions, severity
overrides, and override acknowledgements use the later of `evaluation_date`
and the verifier's wall-clock date. A backdated commit therefore cannot keep an
expired grant active; a future-dated commit can only make the gate more
restrictive. Explicit test-only `today` injection remains available inside the
library, but neither `verify` nor portable workers expose it as a bypass.

This means hard trust decay is intentionally monotonic rather than timeless:
re-evaluating an old request after an exception expires may produce a stricter
decision. The historical receipt still proves the exact artifacts and decision
assembled at its evaluation; it does not renew expired human consent. Workers
do not evaluate these expiries and their local clocks cannot weaken the main
verifier's result.

The GitHub Action evaluates `${{ github.sha }}` by default and separately
records `${{ github.event.pull_request.head.sha }}` when present. This prevents
the source PR head from being confused with GitHub's synthetic merge commit.
The Action exports `request_id`, `receipt_id`, `decision_id`,
`artifact_set_id`, and `verification_receipt_json` only after validating the
receipt and all referenced hashes.

## Boundaries

This contract proves artifact and input identity and closure of one receipt
graph around a deterministic static evaluation. It does not independently
prove that an executor performed the bound evaluation, runtime agent behavior,
remote service state, credential enforcement, build provenance, or that an
executor is trustworthy merely because it self-reports a matching descriptor.
Signatures, trusted execution, and CI identity remain separate distribution
controls. Legacy verify-run, handoff, attestation, registry, and organization
artifacts remain readable, but only current artifacts carrying the receipt
graph qualify as reproducible verification evidence.
