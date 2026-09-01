# The Capability Delta Attestation · `shipgate.capability_delta_attestation/v1`

**What can this agent do after the change — as a fact you can verify without
running Agents Shipgate.**

This page is written for *consumers*: a runtime policy engine, an agent
gateway, a governance dashboard, another CI system. If you are adopting
Agents Shipgate in your own repository, you want
[the quickstart](quickstart.md) instead; nothing here is something an adopter
has to configure.

| | |
|---|---|
| Predicate type | `https://threemoonslab.com/agents-shipgate/capability-delta/v1` (an identifier to switch on, not a fetch target — this page is the specification) |
| Predicate body version | `shipgate.capability_delta_attestation/v1` |
| Payload | [`shipgate.capability_payload/v1`](capability-payload.md), the `delta` view, **unchanged** |
| Statement envelope | [in-toto Statement v1](https://github.com/in-toto/attestation) |
| JSON Schema | [`capability-delta-attestation-schema.v1.json`](capability-delta-attestation-schema.v1.json) |
| Worked example | [`examples/capability-delta-attestation.v1.example.json`](examples/capability-delta-attestation.v1.example.json) |
| Reference verifier | [`tools/verify-capability-delta.py`](../tools/verify-capability-delta.py) |
| Written by | `agents-shipgate verify`, at `agents-shipgate-reports/capability-delta-attestation.json` |

---

## Why this exists

A merge-time verifier and a runtime policy engine are usually sold as
competitors. They are not, and this format is the reason. By the time a runtime
gateway sees an agent, the change is already merged: it can enforce a policy,
but it cannot tell you *what changed about the agent's authority in the review
that let it through*. The merge gate can, because it read both sides.

So this is the merge layer handing the runtime layer a reviewed baseline it
cannot compute for itself. It is deliberately **not a verdict**: it carries no
decision, no severity, and no per-subject release impact. Publishing an impact
in an interchange format invites a consumer to gate on it, which is a second
verdict by another name. `release_decision.decision` in `report.json` remains
the only release gate.

## Read one in 30 seconds

```bash
curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/verify-capability-delta.py \
  | python3 - agents-shipgate-reports/capability-delta-attestation.json
```

```
Capability delta attestation — shipgate.capability_delta_attestation/v1
  subject      github.com/acme/support-agent
  base tree    43d5c9a0ff90a5830479b9bee82a42fe7ee26e30
  head tree    4bd1a69f0454848b5bae2bb2120b04e7f4cf16dd
  head commit  80305fe2c1389a200e6964b403f259e2ec082e09
  receipt      bound · input_set_id sha256:b24e2dc8be9ea45559d65b6e9d04c9492f252c9cbf3958c512ff8ac54b7ad7bf

Analysed capability
  2 subject(s) changed (+1 added, ~1 modified, -0 removed) over 2 capability change(s)
  + stripe.create_refund [support_tools] — added
      added: stripe.create_refund (added)
  ~ support.search_kb [support_tools] — modified
      changed: support.search_kb (unknown)

Outside the analysed surface
  status complete (base complete, head complete)
  newly outside analysis: none
  no longer outside analysis: none

VALID — 20 rules checked. Unsigned: this proves self-consistency, not authorship.
```

The script is stdlib-only, one file, and imports nothing of ours. That is the
point of publishing a format at all: if consuming it needed our package, it
would not be an interchange format, it would be an API.

Exit `0` valid, `1` invalid (every failed rule is printed), `2` unreadable
input. `--json` gives the same result machine-readably; `--expect-tree`,
`--expect-commit` and `--require-receipt-binding` add the consumer-supplied checks described under [Consuming it](#consuming-it).

## The shape

```jsonc
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "github.com/acme/support-agent",
      "digest": {
        "gitTree":   "4bd1a69f0454848b5bae2bb2120b04e7f4cf16dd",
        "gitCommit": "80305fe2c1389a200e6964b403f259e2ec082e09"  // absent, never null, when uncomputed
      }
    }
  ],
  "predicateType": "https://threemoonslab.com/agents-shipgate/capability-delta/v1",
  "predicate": {
    "predicate_schema_version": "shipgate.capability_delta_attestation/v1",
    "capability_payload_schema_version": "shipgate.capability_payload/v1",
    "delta": { /* the frozen capability payload, view: "delta" */ },
    "verification": {
      "status": "bound",
      "input_set_id": "sha256:b24e2dc8…",
      "subject_id":   "sha256:90d775ae…"
    }
  }
}
```

Everything inside `predicate.delta` is specified by
[`capability-payload.md`](capability-payload.md) and nothing about it is
re-stated here. That is deliberate: the delta an attestation carries and the
delta a reviewer reads in the PR comment are **one projection of one
computation**, not two renderings of one value. This page owns only the
envelope.

### The subject is the reviewed tree

Exactly one subject, and its `digest.gitTree` is the git tree object id of the
head state the review evaluated. `gitCommit` is the reviewed commit when the
producer had one, and is context rather than identity: two commits with the
same tree reviewed the same content.

`digest` is in-toto's `DigestSet` and follows in-toto's rules rather than this
format's: it is a `map<string, string>`, so an algorithm the producer did not
compute is **absent**, never `null`. Everywhere else here absence is spelled as
a value; in this one object it is spelled as absence, because the type belongs
to in-toto. The worked example below has no `gitCommit` key at all.

**`predicate.delta.head.ref` equals that same `gitTree`, and the reference
verifier enforces it.** Without the join, a valid attestation for one commit
could be relabelled onto another by editing four characters of the subject. The
base state is named the same way, in `predicate.delta.base.ref`. The payload
spec calls `ref` an opaque caller label; this surface narrows it to a git
object id.

An attestation is written **only for a committed-tree subject**. A `verify` run
that evaluated a worktree snapshot scanned bytes that are in no tree object, so
publishing a tree id as "what was reviewed" would attest content nobody can
fetch. Such a run writes no attestation and leaves a note on
`verifier.base_notes[]`. In CI this does not arise: the GitHub Action always
passes an explicit `--head`.

The file moves as one lifecycle set with the receipt it chains into: a later
run into the same reports directory clears it before writing, so an attestation
you find beside a `verification-receipt.json` was produced by that run.

### The receipt binding is a value, never an omission

```jsonc
"verification": { "status": "bound" | "unbound", "input_set_id": …, "subject_id": … }
```

`bound` carries both identities; `unbound` carries neither, and the schema
refuses every other combination. A consumer that needs the chain back into
`verification-receipt.json` **checks `status`** rather than probing for absent
fields — the same fail-closed shape `analysis_coverage` uses, for the same
reason.

`verify` always emits `bound`. `unbound` is what a delta projected outside a
verification run says, and the worked example on this page is one.

Two identities and no more. `input_set_id` is the content address of *what was
reviewed* and `subject_id` is the resolved git subject; both are properties of
the inputs, so two runs of one review on two machines publish the same values.
`request_id`, `engine_requirement_id` and `decision_id` are deliberately not
published: they mix in the engine build, the Python version and the platform,
so an interchange format carrying them would emit different bytes for an
identical review and would leak the builder's machine. They stay in the
receipt, which `input_set_id` is the join key into.

### A tool that was added and never bound is still in here

The single most common way a capability change hides is a tool that arrives in
a catalog and is never wired to an agent: it produces no capability fact, so a
delta built from facts alone reports that nothing changed. `analysis_coverage`
is the separate axis that says so, it carries **both sides plus the transition
between them**, and it names the subjects rather than counting them:

```jsonc
"analysis_coverage": {
  "base":   { "status": "complete", "subjects_outside_analysis": [] },
  "head":   { "status": "complete", "subjects_outside_analysis": [ { "name": "delete_repository", … } ] },
  "status": "complete",
  "newly_outside_analysis":     [ { "name": "delete_repository", … } ],
  "no_longer_outside_analysis": []
}
```

**`status` is load-bearing, and neither `not_requested` nor `unavailable` means
zero.** A consumer that reads either as "nothing was left out" re-creates the
defect. `newly_outside_analysis` is the row a reviewer of *this* change must
act on; a subject that has been unbound since before the change is a different
fact, and the format keeps them apart.

Nothing here is joined to `subjects[]`. A tool that lost its binding is both
removed from analysed capability and newly outside analysis, and both
statements are true, so the two lists may overlap.

## What a passing verification establishes

Each rule has an id, and the reference verifier prints the id of anything that
fails. This table is the contract; it is pinned to the script's own `CHECKS`
table by the test suite, so the two cannot drift.

| Rule | The attestation … |
|---|---|
| `E1` | `_type` is the in-toto Statement type |
| `E2` | `predicateType` is the capability-delta predicate type |
| `E3` | names exactly one subject, with a name and a `gitTree` digest |
| `E4` | declares the published predicate and payload schema versions |
| `E5` | carries the `delta` view of the payload |
| `E6` | names `base.ref` and `head.ref` as git object ids |
| `E7` | attests the subject the delta describes (`gitTree` == `head.ref`) |
| `E8` | carries an empty delta whenever the two refs name one tree |
| `E9` | carries the identities its `verification.status` claims |
| `P1` | derives every `subject.key` from its own agent/provider/tool id |
| `P2` | states each `subject.key` and each `capability_id` once |
| `P3` | derives each `transition` from the presence pair, and bounds changes by presence |
| `P4` | has a `summary` that is the rollup of its own rows |
| `P5` | names exactly the per-dimension digests that differ |
| `P6` | publishes the direction and explanations the two records show |
| `P7` | derives coverage status and both directional lists from the two sides |
| `P8` | has coverage digests that describe the coverage it carries |
| `P9` | has state refs that reconcile with the membership rows |
| `P10` | backs an empty delta with two states whose capability digests agree |
| `P11` | is in the published sort order, so two builds of one input are byte-identical |

`P1`–`P11` are the payload's own **stage two** rules, listed in
[`capability-payload.md` § *Validating a payload: two stages*](capability-payload.md#validating-a-payload-two-stages),
restricted to the delta view. Recomputing them is what makes a tampered
attestation fail: you cannot raise a count, drop a row, relabel a direction, or
escalate an effect without every derived value disagreeing with the rows that
produced it.

### What it does not establish

**Authorship.** A `v1` statement is emitted **unsigned**. Everything above is
self-consistency: the file is internally coherent and describes the tree it
names. It is not evidence that Agents Shipgate produced it. Wrap the bytes in a
[DSSE](https://github.com/secure-systems-lab/dsse) envelope of your own, or
trust the transport that handed you the file. Signing is a separate increment,
deliberately out of `v1`.

**Stage one, unless you ask for it.** JSON Schema validation needs a validator
the reference script will not depend on. Pass
`--schema docs/capability-delta-attestation-schema.v1.json` and, if the
`jsonschema` package is importable, stage one runs too; otherwise the run
reports it as skipped. The structural checks the script's own rules need always
run, so a malformed document fails regardless.

**The two state digests.** `base`/`head` carry `capability_set_digest` and
`evidence_set_digest` for two *full state* payloads that a delta does not
carry, so those are taken on trust. The `analysis_coverage_digest` of each side
*is* recomputed, because the delta carries the coverage it is taken over. A
consumer holding the matching `view: "state"` payload can close the remaining
gap itself — which is exactly why both views share one schema.

## Consuming it

### As a gate input in your own system

```python
import json, subprocess, sys

attestation = json.load(open("agents-shipgate-reports/capability-delta-attestation.json"))
delta = attestation["predicate"]["delta"]

# 1. Verify before you read. Never branch on an unverified attestation.
check = subprocess.run(
    [sys.executable, "verify-capability-delta.py",
     "agents-shipgate-reports/capability-delta-attestation.json",
     "--expect-tree", my_reviewed_tree_sha,
     "--require-receipt-binding"],
    capture_output=True, text=True,
)
if check.returncode != 0:
    raise SystemExit(check.stderr)

# 2. Subjects, not changes. One tool that moved on two surfaces is one row.
for row in delta["subjects"]:
    subject = row["subject"]
    print(row["transition"], subject["name"], "from", subject["provider"])

# 3. Coverage is a separate axis, and its status is load-bearing.
coverage = delta["analysis_coverage"]
if coverage["status"] != "complete":
    raise SystemExit("this run did not establish what it left out — do not read 0 here")
for subject in coverage["newly_outside_analysis"]:
    print("arrived unanalysed:", subject["name"], subject["provider"])
```

### Three ways to read it wrong

- **Counting changes instead of subjects.** `summary.subjects` answers "how
  much did this change what the agent can do". `summary.capability_changes` is
  the finer number and inflates with however many dimensions each subject
  happened to touch. One added tool is `+1`.
- **Reading an empty `subjects_outside_analysis` as zero.** Only
  `status: "complete"` means the producer looked. `not_requested` and
  `unavailable` both carry an empty list and neither is a claim about what was
  left out.
- **Rendering `subject.name` as identity.** It is the adopter-facing spelling
  and two providers may ship the same one. Identity is `subject.key`; qualify
  any name you render with `provider`.

## Versioning

`shipgate.capability_delta_attestation/v1` is **closed**, exactly as its
payload is. Every object forbids extra properties and every vocabulary is a
closed enum, because a frozen interchange format whose point is that an
external tool can verify it must not accept content that tool cannot account
for. A closed schema cannot also be additive, so:

- **Any addition, removal, or change of meaning is a new version**, published
  as `…/capability-delta/v2` with a new schema file beside this one. There is
  no compatible in-place widening, and a consumer pinned to `v1` never has to
  guess.
- **`v1` stays published** after `v2` lands, and stays readable for at least
  one minor cycle — the deprecation rule in [`../STABILITY.md`](../STABILITY.md)
  applied to a whole version, because a whole version is the unit here.
- **The predicate type is the wire identity.** Switch on `predicateType` and
  nothing else. `predicate_schema_version` restates it inside the predicate so
  a body separated from its statement still says what it is.
- **The payload versions independently**, and
  `predicate.capability_payload_schema_version` is where you read it. It is
  restated rather than reached for inside `delta` so a consumer pinned to the
  payload schema does not have to descend.

The worked example shows `verification.status: "unbound"` and
`analysis_coverage.status: "not_requested"` because it is projected from the
shipped `samples/ai_generated_refund_pr` fixture rather than emitted by a
`verify` run — see the coverage section above for why `not_requested` is not a
claim that nothing was left out. Both the schema and the example are generated
by `scripts/generate_schemas.py` and CI fails on drift: a hand-written example
is a claim about a format that nothing checks.

## Related

- [`capability-payload.md`](capability-payload.md) — the payload this wraps,
  its identity rules, its canonical bytes, and both validation stages.
- [`capability-standard.md`](capability-standard.md) — the capability lock and
  lock diff, the internal-facing artifacts this projects from.
- [`agent-contract-current.md`](agent-contract-current.md) — where the contract
  advertises the predicate type and both schema versions.
- [`determinism-boundary.md`](determinism-boundary.md) — what a scan can prove
  per framework, which bounds what any delta can say.
