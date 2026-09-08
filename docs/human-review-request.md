# Human review request

`verify` can publish one complete-evidence documentation-quality question as
`agents-shipgate-reports/human-review-request.json`, with schema
[`shipgate.human_review_request/v1`](human-review-request-schema.v1.json).
The unsigned request tells a reviewer exactly what needs a decision and tells
an integration which postcondition it must check. It grants no permission.

## When a question is available

The first class is `documentation_quality_complete_evidence`. A committed-tree
run with a repository manifest, plugins disabled, complete extraction,
semantics, identity and binding coverage, and only active medium
`SHIP-DOC-MISSING-DESCRIPTION` review items can publish it. A baseline-accepted
item is not eligible. The real `docs.lookup` fixture exercises this lifecycle;
it is not evidence that documentation warnings are the product wedge.

| Release decision | Request publication |
| --- | --- |
| `review_required` | Only the complete-evidence class above |
| `blocked` | No request; repair the blocker through existing control |
| `insufficient_evidence` | No request; recover the missing evidence |
| `passed` | No question remains for this class |

Critical findings, gate-governing or gate-weakening changes, missing base
inputs, worktree overlays and partial surfaces retain their existing routes.
The request does not broaden any class of accepted risk. A later verifier run
removes an old optional request before publishing its current artifacts.

## Read the question and its scope

`questions[]` contains one plain-language question per canonical `review_items[]`
row. Each names the finding's title; the corresponding row carries the source
paths, finding identity, severity and scope. The question offers **accept**,
**reject** or **dispute** for that exact documentation concern.

An eligible actor is an externally authenticated human whose eligibility is
established by trusted context. The change's author and bots are excluded.
Writing a username or acceptance sentence in a repository file proves neither
identity nor eligibility. `postcondition` requires a current, unexpired decision
on the complete review set, bound to the trusted review instance as well as
this static request. A GitHub PR number is provider-owned context: the static
scanner cannot infer it from a branch name or an environment variable.

| Decision | Meaning | Remaining obligation |
| --- | --- | --- |
| Accepted | Record that the eligible reviewer accepts this concern in this exact scope | Preserve the release gate and control; acceptance alone grants no merge or completion |
| Rejected | Record that the reviewer requires the concern to be addressed | Address it and obtain new verification evidence |
| Disputed | Record disagreement as triage evidence | Resolve the dispute; the finding is still active |

The machine values are `record_acceptance_preserve_gate`,
`record_rejection_preserve_gate` and `record_dispute_preserve_gate`.
`remaining_obligations` is `existing_release_gate_and_control`;
`grants_merge_authority` and `grants_completion_authority` are always false.
The [read-only decision evaluator](human-review-decision.md) returns separate
applicability evidence for a trusted integration. There is no decision-ingestion
CLI. Continue by presenting the request to a human and following current
`control.next_action`; prose acknowledgement never clears it. GitHub authentication and acquisition belong
to [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337).

## Identity and verification

The request reuses the existing verification graph:

| Field | Binds |
| --- | --- |
| `repository_id` | Repository identity from the verification subject |
| `base_commit_sha`, `merge_base_sha`, `base_tree_sha`, `head_tree_sha`, `source_head_commit_sha` | The compared committed change |
| `subject_id` | The canonical verification subject |
| `input_set_id` | Config, policy packs, baseline, source and change blobs, evaluation options/date |
| `engine_requirement_id` | Engine requirements, including the built-in policy catalog digest |
| `verification_request_id` | The existing plan's subject, inputs and engine requirements |
| `decision_id` | The existing decision and verification-unit result identity |
| `review_set_id` | Every canonical review item, using the push-authorization projection unchanged |
| `review_request_id` | The full question, scope and postcondition, excluding only this id and schema version |

There is no independently computed policy hash. A later authenticated decision
must additionally bind a **trusted review-instance identity** (for example the
repository's GitHub PR), preventing replay into another PR with identical trees.

Read current control and validate its terminal receipt and artifact closure
before trusting a request from disk. The request is listed in
`verifier.artifacts.human_review_request_json`, hashed by the existing artifact
manifest and bound by the terminal receipt. JSON Schema validates grammar;
Pydantic additionally verifies canonical scope and content identities, which
JSON Schema cannot recompute. A well-formed unsigned file alone is not proof
that the current verifier produced it.

## Static artifact boundary

The decision evaluator consumes validated artifacts and returns separate
external evaluation evidence for the integration to persist alongside the
signed decision. It writes no files and preserves the bytes of the
current static set: `report.json`, `report.md`, `report.sarif`; `packet.json`,
`packet.md`, `packet.html` when emitted; capability locks and their diff;
`capability-delta-attestation.json`; `verification-plan.json`,
`verification-unit-result.json`; and `human-review-request.json` itself.

Control/handoff overlays and terminal receipt bindings may describe external
evidence separately. They are not part of that byte-invariance promise. In
all cases `report.json.release_decision.decision` is the release decision and
only current control supplies operational permissions. Existing signed push
authorization retains its own signature domain and never grants merge or
completion.

## Compatibility

Runtime contract 30 advertises the new standalone artifact. The minimum control
contract stays at 21. No existing persisted grammar changes:

| Surface | Existing schema | Change |
| --- | --- | --- |
| Verifier | `verifier-schema.v0.16.json` | Optional artifact-map entry only; schema unchanged |
| Agent handoff | `agent-handoff-schema.v8.json` | None |
| Preflight | `preflight-schema.v0.4.json` | None |
| Agent result | `agent-result-schema.v3.json` | None |
| Agent boundary result | `agent-boundary-result-schema.v2.json` | None |
| Verify run | `verify-run-schema.v5.json` | Existing artifact-reference map binds another file; schema unchanged |
| Control envelope | `agent-control-schema.v1.json` | None |

`HumanControlAction.expects` remains null and the shared action union is
unchanged. Historical schemas keep their original bytes and stored payloads
keep their original grammar. Consumers that ignore an unknown artifact-map
entry continue using their existing control reader; a request reader must
explicitly support the new schema.
