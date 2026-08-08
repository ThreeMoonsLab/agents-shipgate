# Verification Identity and Reproduction

Agents Shipgate `0.16.0b7` makes a verification request, its execution, its
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
- the manifest, every input an adapter is configured to read, policy packs,
  baseline, comparison report, changed-file content, diff content, evaluation
  date, and behavior-affecting options;
- the Agents Shipgate version and installed package-content digest,
  Python/runtime requirements, installed dependency RECORD closure, adapter
  set, plugin distribution set, and policy catalog; and
- the normalized task list.

"Every input an adapter reads" is `plan.inputs.tool_sources`, and it covers
more than the `tool_sources` manifest block. `prompt_files`, the framework
blocks (`openai_api`, `anthropic`, `google_adk`, `langchain`, `crewai`, `n8n`,
`codex_plugins`), `validation.evidence`, `checks.policy_packs`, and
`agent.sdk.entrypoint` are all adapter inputs — and so is anything reached
*through* one of them, such as a Google ADK `McpToolset` inventory or an
OpenAPI spec named only from Python. Those transitive inputs are invisible to
the manifest, so identity is taken at the read boundary rather than from
declarations: each producer snapshots the tree it evaluates and records what
the adapters open. A worktree run snapshots the worktree; a committed-tree run
snapshots the archived tree it scans; `verification prepare` loads sources —
statically, deciding nothing — for the same reason. Report output directories,
`organization.audit.registry` (existence-tested, never read), and
`baseline.audit_log` are deliberately not inputs.

The snapshot supplies the hashed bytes as well as the path list. Plan blobs are
never re-read from disk after capture, so a plan cannot pair a path list taken
at one instant with content taken at another, and a receipt cannot attest to
bytes the scan did not evaluate.

Because `prepare` reads inputs, it fails on a manifest whose inputs cannot be
loaded. That is the same condition under which `verify` fails, and it is
exactly when a prepared plan could not honestly claim an input set. Enumerating
the manifest's declared paths remains only as a fallback for a plan built with
no snapshot at all, and that fallback rejects a declared path resolving outside
the input root, since it cannot be hashed portably.

Committed snapshots are materialized from Git objects with `git ls-tree` and
`git cat-file`. They do not use `git archive`, so `.gitattributes`
`export-ignore` and `export-subst` cannot change the evaluated bytes. Symlinks
and submodules fail closed for archived verification inputs.

Worktree verification evaluates one merge-base-to-effective-worktree diff,
including staged and unstaged changes, instead of concatenating a committed
range with a HEAD-relative overlay. The request separately binds the exact
HEAD-relative overlay path set and each path's presence, content hash, and Git
file mode. A path changed in both layers therefore has one policy-evaluation
record while the terminal receipt still identifies the complete overlay.

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

## Reviewed authorization overlay

Runtime contract v19 can project one externally authorized coding-agent action
without changing the release decision. This is an overlay on a completed
verification graph, not an input that lets a worker or repository assert human
approval.

The flow starts with a normal successful verification whose decision is
`review_required` and whose exact effective plugin mode is false. Run both
verification passes with `--no-plugins` when third-party plugins could be
enabled. The protected authorization executor rejects plugin-enabled plans
before engine validation and never loads third-party plugin or adapter entry
points. The consumer validates that run's terminal receipt and all referenced
hashes. A trusted host then builds an unsigned
`shipgate.human_authorization_request/v1` from the current `request_id`,
`subject_id`, `decision_id`, source receipt/artifact-set/engine/executor
identities, repository and tree identities, the complete
ordered review set and its `review_set_id`, and one typed operation. The host
must show that complete request to an authenticated human before signing it.
Agents Shipgate can build this deterministic unsigned challenge without
creating authority:

```bash
agents-shipgate authorization request \
  --receipt agents-shipgate-reports/verification-receipt.json \
  --artifacts-root agents-shipgate-reports \
  --remote origin \
  --destination-ref refs/heads/<branch> \
  --expected-lease-oid <reviewed-remote-oid> \
  --out agents-shipgate-reports/human-authorization-request.json
```

The signed `shipgate.human_authorization/v1` grant uses a domain-separated
Ed25519 detached signature. The private key and signing operation belong to the
trusted host or authenticator; Agents Shipgate ships neither a private key nor
a signing/approval CLI. The
`shipgate.human_authorization_trust_policy/v1` file must live outside the
evaluated workspace and be protected from coding-agent writes. On POSIX, the
loader uses the OS account home's fixed path
`~/.config/agents-shipgate/human-authorization-trust-policy.json`; it ignores
`HOME` and `XDG_CONFIG_HOME` for trust-policy selection. A repository file, PR
comment, environment assertion, or conversation-level acknowledgement cannot
become a trust anchor merely by matching the schema.

Content addressing proves internal byte closure, not who produced the
receipt. `authorization request` copies `source_receipt_id` and
`source_artifact_set_id` from the validated prior receipt, but the later verify
and execute passes do not transport that prior closure. Those two fields are
therefore signer-authenticated provenance labels, not independently verified
provenance claims by Agents Shipgate. Before signing, the host must either
rerun verification in its own trusted worker or verify a trusted-CI
attestation over those IDs (and the bound engine and executor). An
agent-generated self-consistent receipt is not provenance.
The request also exposes the evaluated base commit and merge base. The exact
source commit transitively binds all parents and reachable objects, so the
signer must review the complete ancestry—not just the final tree diff. The
executor serializes that full graph with a 512 MiB ceiling and 120-second
timeout; a production broker should enforce tighter disk, memory, and CPU
quotas. The compressed pack ceiling does not bound expanded-object indexing
memory or CPU, so the broker should use a cgroup, container, or equivalent host
quota.

The signed bytes are exactly the ASCII domain
`shipgate.human_authorization/v1`, one NUL byte, then the UTF-8 encoding of the
statement JSON serialized with sorted keys, no insignificant whitespace, and
non-ASCII characters preserved. Public keys are 32 raw Ed25519 bytes and
signatures are 64 raw bytes, both encoded as canonical unpadded base64url;
`key_id` is `sha256:<hex>` over the raw public key. The checked-in
[`human-authorization-signature-v1.json`](human-authorization-signature-v1.json)
is the cross-implementation conformance vector.

The grant is consumed by a second run:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json \
  --no-plugins \
  --authorization /path/outside/workspace/human-authorization.json
```

That run recomputes the verification plan, unit, release decision, trees, and
complete review set before validating the external signature and its scope and
TTL. This ordering avoids a circular request identity: the grant is not added
to the verification input set whose `request_id` it signs. When accepted, the
grant and `shipgate.human_authorization_evaluation/v1` projection are added to
the final artifact closure, so the newly written terminal receipt binds the
authorization result and exact command.

Authorization v1 permits only one typed Git-push operation. It binds the exact
commit whose tree was evaluated, a canonical credential-free HTTPS destination
whose repository identity matches the verified repository, one full
destination ref, and one expected remote OID. Synthetic PR merge verification
is not eligible to authorize a different source parent; verify the actual PR
head commit separately. Acceptance requires a
successful `review_required` verification. It changes only operational
control: `control.state` becomes `agent_action_required` for that sole command.
The static decision remains `review_required`, `merge_verdict` remains
`human_review_required`, `can_merge_without_human` remains false, and
`completion_allowed` remains false. After the operation, the agent must rerun
verification; the authorization does not itself establish completion or
mergeability. The exposed command is the guarded
`agents-shipgate authorization execute` consumer, not the underlying Git
command. It revalidates the receipt, signature, current external trust policy,
clock, engine, repository, and commit at execution time. Receipt artifacts are
opened without following symlinks and parsed from one immutable validated byte
snapshot. The executor copies the reachable commit graph into a temporary bare
store, validates object IDs and connectivity, and disables Git replacement
objects, hooks, local/global/system configuration, and HTTP redirects before
issuing the signed force-with-lease operation.

An invalid signature, unknown key, inaccessible or workspace-local trust
policy, expired/not-yet-valid window (including expiry after verification but
before execution), repository mismatch, changed request,
subject, tree, decision, review item, destination ref, source commit, or lease
OID fails closed. A non-accepted evaluation exposes no command and produces
zero allowed commands. The verifier does not query the remote; replay resistance
for a ref that moved after approval comes from Git enforcing the exact signed
`--force-with-lease=<ref>:<expected-oid>` argument. The protocol does not itself
authenticate a Codex or Claude Code UI user: a separately implemented host
signing adapter must perform that authentication and keep the signing key
outside the agent's authority.

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

The GitHub Action evaluates `${{ github.sha }}` by default. A default
`pull_request` synthetic-merge receipt deliberately carries no executable
source authority; verify `${{ github.event.pull_request.head.sha }}` as the
explicit head in a separate run before requesting push authorization.
The Action exports `request_id`, `receipt_id`, `decision_id`,
`artifact_set_id`, and `verification_receipt_json` only after validating the
receipt and all referenced hashes.

## Boundaries

This contract proves artifact and input identity and closure of one receipt
graph around a deterministic static evaluation. It does not independently
prove that an executor performed the bound evaluation, runtime agent behavior,
remote service state, credential enforcement, build provenance, or that an
executor is trustworthy merely because it self-reports a matching descriptor.
The v1 human-authorization signature proves only the exact operational grant
described above. It records source receipt, artifact-set, engine, and executor
IDs, but the signer must authenticate those IDs through trusted CI or its own
rerun. Trusted execution and CI identity remain separate distribution
controls. The host must protect the trust policy, key, sanitized launcher
environment, interpreter, entire virtual environment and `site-packages`
(including startup `.pth` files), dependencies, credentials, and separately
installed distribution from the coding agent. Same-UID modes are not isolation,
and editable installs rooted in
the authorized workspace are ineligible. Without that broker boundary no
authorization command is safe to expose. The guarded executor is POSIX-only in
v1 and authorization remains disabled on Windows. V1 authorizes only the exact push;
reviewed patch/apply operations require a future typed operation. Legacy
verify-run, handoff, attestation, registry, and
organization artifacts remain readable, but only current artifacts carrying
the receipt graph qualify as reproducible verification evidence.
