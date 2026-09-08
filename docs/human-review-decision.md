# Human review decision

An integration can evaluate an externally signed decision on the current
[human review request](human-review-request.md), through
`agents_shipgate.core.human_review_decision.evaluate_human_review_decision`.
The function is read-only: it returns separate evaluation evidence, writes no
file, executes no operation, and grants no authority. There is no ingestion CLI
or GitHub authentication in this slice. [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337)
owns authenticated acquisition, isolated credentials, persistence and PR presentation.

## What a result means

The signed `shipgate.human_review_decision/v1` contains the complete request,
trusted review-instance and eligibility-context identities, authenticated
principal, outcome, reason, validity window and detached Ed25519 proof. Its
content identity covers both statement and proof.

The returned `shipgate.human_review_evaluation/v1` distinguishes:

| Status | Meaning | Continuation |
| --- | --- | --- |
| `applicable` | This signature, scope, reviewer and validity window matched the current evidence and host trust when evaluated | The existing human-owned `next_action`, copied exactly; no shell command |
| `not_applicable` | The decision could not be validated for the current request | No continuation; inspect `reason_codes[]` and recover the named missing or stale prerequisite |

`applicable` does not mean a record was saved. The trusted integration persists
the signed decision and evaluation separately, including their real evaluated
time, source receipt, current control, trust-policy and review-instance
identities. An old stored evaluation remains audit history, never current
authority: re-evaluate against live inputs before presenting a continuation.

`accepted`, `rejected` and `disputed` are distinct outcomes, including in
historical records. Acceptance records the reviewer's decision about this
concern. Rejection requests a repair; dispute supplies triage evidence. None
suppresses a finding, edits a declaration or clears the release gate.
Every result has `authority: "none"` and
`remaining_obligations: "existing_release_gate_and_control"`.

| Existing release decision | Evaluator boundary |
| --- | --- |
| `review_required` | Only the complete-evidence documentation-quality request class specified by #536 is eligible |
| `blocked` | No applicable review decision for this slice |
| `insufficient_evidence` | Recover the missing evidence through existing control |
| `passed` | No supported review question remains |

## Trust comes from the host

`TrustedReviewEligibility` is a host-supplied context, not a JSON proof of its
own authenticity. Its caller must establish the repository and current review
instance, authenticated human principals, eligible reviewers, all change
authors and bots. Author self-review and bots are refused. Constructing the
dataclass or spelling a GitHub username proves none of these facts. The test
signer is isolated fixture data, not evidence of production token isolation.

The evaluator independently loads the existing fixed external
`~/.config/agents-shipgate/human-authorization-trust-policy.json` policy through the host trust loader. Its
repository restrictions, key identity and validity, principal binding and
maximum TTL apply. A context can restrict those keys but cannot add a key:
the selected public key must match in both sources. Repository-authored trust
files are refused. The evaluator exposes no custom trust-path argument and
never reads a private key. Production integration must keep signing keys and
eligibility credentials outside the coding agent's authority.

The signature payload is the UTF-8 domain
`agents-shipgate:human-review-decision:v1`, followed by a NUL byte and the
existing canonical JSON encoding of the statement. A valid signed-push proof
cannot serve as a review decision. The package builder accepts an externally
supplied proof; it does not sign. Dates require explicit timezones, are
normalized to UTC and must form an ordered, unexpired window within both the
host and integration TTL limits.

## Current evidence is reconstructed

The evaluator uses the existing live workspace observer and current-control
reader to capture the receipt, verification plan, report and verifier from
one validated artifact generation. It checks the actual engine requirement,
then uses the existing bounded receipt-snapshot reader to validate the larger
receipt closure, including files outside the pointer's selected artifact list.
That private temporary snapshot must carry the same captured receipt. It
rebuilds #536's full request and compares its canonical bytes against the
actual receipt-bound request artifact. The receipt's actual identity graph must agree
with the captured plan and verifier. A signer cannot substitute a claimed
receipt label for that observation.

It then compares the complete signed request, including repository, head/base
commits and trees, input set (including policy), engine, verification request,
decision, canonical full review set and postcondition. The trusted review
instance adds a separate binding against later-PR replay with identical trees.
After signature and eligibility validation, the evaluator re-reads host trust
and current verification; a change during evaluation invalidates the result.

Missing, malformed, expired, future, untrusted and partial-scope decisions fail
closed. So do changed workspace inputs, excluded finding classes, changed
policy, a missing author identity, a revoked key and an unknown reviewer.
The reason remains structured; signer prose is inert text and never an action.

## Storage and compatibility

The implementation writes no persistent output and preserves **every existing report-directory file byte**,
including the static set in #536, verifier, handoff, receipt and current
pointer. It neither adds an authorization overlay nor rewrites evidence as if
review had happened at scan time. The existing push overlay remains separate
and keeps its original push-only authority and signature domain.

Runtime contract 31 advertises the decision/evaluation schemas, both published
in [the standalone schema family](human-review-decision-schema.v1.json).
The minimum control contract remains 21. No existing persisted grammar, action
union, release verdict or artifact path changes. JSON Schema validates grammar;
the Pydantic models additionally verify content identities. Only the evaluator
checks signature, trust, scope and freshness. A schema-valid decision or saved
`applicable` result alone establishes none of those current facts.

The fixture in `tests/test_human_review_decision.py` demonstrates request →
externally signed decision → evaluation → exact existing continuation. It
also retains rejection/dispute history after a later commit and proves the old
decision cannot be reused. It does not claim that another human has completed
the GitHub product flow or that this class establishes demand for review tooling.
