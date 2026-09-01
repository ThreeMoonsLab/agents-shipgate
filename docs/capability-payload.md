# The Capability Payload · `shipgate.capability_payload/v1`

**One payload. Two views. Frozen before either surface ships.**

Two planned public surfaces serialize the same internal truth:

- the **exported capability delta**, published as a standalone attestation an
  external tool can verify without running Agents Shipgate
  ([#470](https://github.com/ThreeMoonsLab/agents-shipgate/issues/470)), and
- the **committed capability state**, the lock-style file that says what the
  agent can do as of one commit
  ([#474](https://github.com/ThreeMoonsLab/agents-shipgate/issues/474)).

Both are projections of the capability-fact layer. If each defined its own
serialization we would ship two divergent schemas of one structure — the
"second implementation" class this repository has now hit for commands
([#322](https://github.com/ThreeMoonsLab/agents-shipgate/issues/322)) and lived
through for report schemas
([#232](https://github.com/ThreeMoonsLab/agents-shipgate/issues/232)). This page
and [`capability-payload-schema.v1.json`](capability-payload-schema.v1.json)
freeze the shared payload **before either surface exists**, so both consume it
and neither invents one.

This is the payload only. The attestation's predicate type and signing, and the
state file's location, name, and regeneration discipline, belong to those
surfaces. Nothing here gates: `release_decision.decision` remains the only
release gate, and neither view creates a second verdict.

Nothing emits this payload yet. It is published as a schema, a spec, and the
projection that fills it, so the two surfaces that will emit it have one shape
to agree on.

---

## Current version

| | |
|---|---|
| Schema version | `shipgate.capability_payload/v1` |
| JSON Schema | [`capability-payload-schema.v1.json`](capability-payload-schema.v1.json) |
| Worked state example | [`examples/capability-payload.v1.state.example.json`](examples/capability-payload.v1.state.example.json) |
| Worked delta example | [`examples/capability-payload.v1.delta.example.json`](examples/capability-payload.v1.delta.example.json) |
| Python models | `agents_shipgate.schemas.capability_payload` |
| The one projection | `agents_shipgate.core.capability_payload` |

Both examples are **generated** from the shipped
`samples/ai_generated_refund_pr` fixture by
`scripts/generate_schemas.py`, and CI fails on drift. A hand-written example is
a claim about the format that nothing checks.

They show `analysis_coverage.status: "not_requested"` because the projection was
called without one — see [the coverage
section](#the-subjects-outside-the-analysed-surface) for why that is not a claim
that nothing was left out.

## Shape

Every payload is one JSON object carrying
`capability_payload_schema_version`, `capability_standard_version`,
`analysis_coverage`, and a `view` discriminator.

```jsonc
{
  "capability_payload_schema_version": "shipgate.capability_payload/v1",
  "capability_standard_version": "0.5",
  "analysis_coverage": { "status": "not_requested", "subjects_outside_analysis": [] },
  "view": "state",            // or "delta"
  ...
}
```

**`view: "state"`** — what the agent can do at one point.

- `state` — a `CapabilityStateRef` describing this state (see below).
- `subjects[]` — one row per subject, each with the `capabilities[]` it holds.

**`view: "delta"`** — what changed between two states.

- `base`, `head` — a `CapabilityStateRef` for each side, including digests of
  states the delta itself does not carry in full.
- `summary` — subject counts, recomputed from the rows. They are counts of
  *subjects*, never of changes; `capability_changes` is the finer number and is
  a separate field.
- `subjects[]` — one row per **changed** subject, each with `present_in_base` /
  `present_in_head`, the `transition` those two imply, and the `changes[]` that
  moved it. An unchanged subject is absent, not listed as unchanged.

Both views are validated by the same schema file; a consumer switches on `view`.

## The subjects outside the analysed surface

`subjects[]` describes the **analysed** surface — what the binding graph proved
the agent can reach. A tool that is present but unbound is exactly the tool
missing from it, so a payload carrying only the rows would report no capability
change on a change whose entire content was one added tool
([#437](https://github.com/ThreeMoonsLab/agents-shipgate/issues/437)).

`analysis_coverage` is the separate axis that says so. **Its shape differs by
view**, because a delta has to answer a question a snapshot cannot.

A `state` carries one snapshot:

```jsonc
"analysis_coverage": {
  "status": "complete",                  // not_requested | unavailable | complete
  "subjects_outside_analysis": [ { "key": "capsubj_…", "name": "find_duplicate", … } ]
}
```

A `delta` carries both sides and the transition between them:

```jsonc
"analysis_coverage": {
  "base":   { "status": "complete", "subjects_outside_analysis": [] },
  "head":   { "status": "complete", "subjects_outside_analysis": [ … ] },
  "status": "complete",                        // the weaker of the two sides
  "newly_outside_analysis":      [ … ],        // in head, not in base — the #437 row
  "no_longer_outside_analysis":  [ … ]
}
```

The two directional lists are recomputed from `base` and `head` on parse, and
are empty unless both sides are `complete`. One snapshot could not distinguish
"a tool was added and is unbound" from "a tool has been unbound since before
this change", and only the first is something a reviewer of *this* diff must act
on.

Three rules:

- **`status` is load-bearing, and neither `not_requested` nor `unavailable`
  means zero.** A consumer that reads either as "nothing was left out"
  re-creates the defect. `unavailable` is the fail-open shape made visible: the
  comparison was asked for and could not run.
- **Naming requires having looked.** Any status other than `complete` must
  carry an empty list; the schema refuses to let "we did not look" be written in
  the same shape as "we looked and found none".
- **It is deliberately not joined to `subjects[]`.** A tool that lost its
  binding is both removed from analysed capability and newly outside analysis,
  and both statements are true — so the two lists may overlap, and a consumer
  must not treat one as a partition of the other.

Subjects are **named**, not only counted
([#433](https://github.com/ThreeMoonsLab/agents-shipgate/issues/433)): a count
tells a reviewer that something is missing without telling them what.

## Identity

### Subject — one subject, one row

A **subject** is the thing a reader recognizes: one tool, under one agent and
one provider. `subject.key` is the row identity for the whole payload, and it is
derived from three fields only:

```
key = "capsubj_" + sha256(canonical_json({
          "agent":    <subject.agent>,
          "provider": <subject.provider>,
          "tool_id":  <subject.tool_id>
      }))[:16]
```

where `canonical_json` is JSON with sorted keys and no insignificant
whitespace. A consumer can recompute the key from fields the payload already
carries.

The key is deliberately **not** derived from the subject kind. A tool and its
action are the same subject to a reader, and keying on the kind is exactly how
one added tool came to produce two rows and report `+2`
([#439](https://github.com/ThreeMoonsLab/agents-shipgate/issues/439)). In this
schema those two changes are two entries of one row.

Seven structural rules hold it there, enforced by the models and not only by the
producer:

1. `subject.key` is unique across `subjects[]`. A payload that states one
   subject twice is **rejected**.
2. `summary` is recomputed from the rows on parse. A payload whose counts
   disagree with its rows is **rejected**, not silently corrected — a tampered
   or hand-edited attestation must fail, not be repaired.
3. `subjects[].transition` is a statement about the **subject's own
   presence**, carried explicitly as `present_in_base` / `present_in_head` and
   recomputed from them on parse. `added` means the subject is not in base,
   `removed` means it is not in head, and everything else is `modified` — so a
   tool that keeps one operation and loses another is `modified`, because it is
   still there. It is deliberately *not* rolled up from the change kinds: a
   delta row carries only the capabilities that moved, so from its changes
   alone that tool is indistinguishable from one that went away, and calling it
   `removed` would tell a reviewer the agent lost a tool it still has.
   Presence also bounds the changes — a subject absent from base can only carry
   `added` ones, and one absent from head only `removed` ones.
4. A delta with **no** subject rows must name two states whose
   `capability_set_digest` and `evidence_set_digest` agree, and whose subject
   and capability counts are equal. "No analysed capability moved" is a claim
   about the two states, so it has to be one the payload's own digests support.
   `analysis_coverage_digest` is deliberately **excluded**: a change that only
   moves what could not be analysed has no subject rows by construction, and
   that coverage-only delta is precisely the #437 payload this schema exists to
   make expressible.
5. The two state refs are bound to the membership rows:
   `head.subject_count - base.subject_count` equals added minus removed
   subjects, and the same equation holds for capability counts over `added` and
   `removed` record transitions. Without it a head ref could claim any counts at
   all.
6. `capability_id` is unique across the **whole** payload, not only within a
   row. Provenance is keyed by it, so a repeat would quietly drop one
   capability from `evidence_set_digest`.
7. `semantic_direction` and `semantic_changes` are **derived** from the two
   records a change entry carries, not asserted about them — see below.

`subject.name` is the adopter-facing spelling and is **not** identity: two
providers may publish the same name. A consumer rendering names must qualify
with `provider` whenever two rows share one `name`.

### Capability — the join back to the fact

`capabilities[].capability_id` is the internal `CapabilityFactV1.id` verbatim.
Identity is taken at the adapter read boundary; this schema *records* that
identity and never re-derives one. The id is the join key back to the fact
layer, which is what makes the round-trip test in
`tests/test_capability_payload.py` able to compare published rows against the
facts that produced them.

`capability_id` is unique across the whole payload, not only within a row —
provenance is keyed by it, so a repeat would quietly drop one capability from
`evidence_set_digest`.

### State digests

`CapabilityStateRef` carries **three** digests, and between them they cover
every field a state publishes — a state whose ref matches another's is the same
published state, with nothing left unbound. Each preimage is stated exactly,
because a consumer has to be able to rebuild it:

| Digest | Preimage |
|---|---|
| `capability_set_digest` | the array of `subjects[]` rows, each as `{"subject": …, "capabilities": [ … ]}`, where every record has its `evidence` block removed and `digests.evidence_hash` removed from `digests` |
| `evidence_set_digest` | the object mapping each `capability_id` to `{"evidence": <that record's evidence block>, "evidence_hash": <that record's digests.evidence_hash>}` |
| `analysis_coverage_digest` | the state's whole `analysis_coverage` object |

Each is `sha256` over the canonical serialization below, as lowercase hex.

The first two draw the split the capability lock already draws, and keep two
questions separable: *did what the agent can do move*, or *only where we read it
from*. The third exists because coverage is a published claim too, and a state
that named a dangerous unanalysed subject must not share a ref with one that
did not.

A delta's `base`/`head` digests are computed over the full state payloads for
each side, so a delta and a state payload can be proven to describe the same
state. A delta additionally re-derives each side's `analysis_coverage_digest`
from the coverage it carries, so those two are checked rather than trusted.

### Canonical bytes

Every digest above, and the subject key, are `sha256` over the UTF-8 encoding of
this canonicalization. It is stated in full because the format exists for
consumers that are not this program:

1. **UTF-8, never escaped.** `café` is serialized as `café`, not `caf\u00e9`.
   The digest is taken over the UTF-8 bytes.
2. **Object keys sorted, compact separators**, no insignificant whitespace:
   `{"a":1,"b":[2,3]}`.
3. **Every object key in this payload is ASCII.** They are schema field names
   plus, in the evidence map, `capability_id` — which the schema constrains to
   `^cap_[0-9a-f]{16}$` for exactly this reason. Python sorts keys by code point
   and RFC 8785 sorts by UTF-16 code unit, and those orders **disagree above the
   BMP**: `"\ue000"` sorts before `"😀"` by code unit and after it by code point.
   Restricting the one dynamic key to ASCII removes the disagreement rather than
   documenting around it.
4. **Integers only, and inside the I-JSON safe range** (`|n| ≤ 9007199254740991`).
   No field is float-valued, `NaN`/`Infinity` are refused, and every published
   integer is bounded — `9007199254740993` reads back as `…92` in JavaScript, so
   an unbounded line number would digest differently for a JavaScript consumer.
5. **No fallback serialization.** A value the encoder cannot represent raises;
   it is never digested as its `str()`.

Within those constraints this agrees with RFC 8785 (JCS).

Cross-language vectors:

| Input | Canonical bytes | sha256 |
|---|---|---|
| `{"name": "café"}` | `{"name":"café"}` | `645fa443126a8954fc6d871912b8fc67bc2ee8feae417efe55546251962ca74d` |
| `{"b": 1, "a": [1, 2]}` | `{"a":[1,2],"b":1}` | `94a786c3662bc7beeb598efa7d8cb58d7bea25d6c275ea9785a0230ff1f8c2ba` |
| `{"agent": "agént", "provider": "p", "tool_id": "tool_✓"}` | `{"agent":"agént","provider":"p","tool_id":"tool_✓"}` | `capsubj_e7a364bcf95ee748` |

An implementation in another language that reproduces these three has the
canonicalization right. `test_the_published_canonicalization_vectors_hold` fails
if the implementation and this table ever disagree.

**A `state` payload verifies its own digests on parse.** It carries every row
they are taken over, so a state whose `state.*_digest` does not describe its
`subjects[]` is rejected — the promise that the digests are recomputable from
the payload alone is enforced, not just documented. A `delta`'s `base`/`head`
refs describe states the delta does not carry, so those are taken on trust; the
`state` payload for each side is what proves them.

`ref` is an opaque caller label — a commit sha, a path — supplied by the surface
that emits the payload. It is never a timestamp: this payload carries no wall
clock, so two exports of the same static inputs are byte-identical.

## Direction and explanations are derived, not asserted

`semantic_direction` and `semantic_changes` on a change entry are computed from
the two records the entry carries, and a payload that declares anything else is
rejected. That is deliberate: a direction a producer sets freely is one a
consumer cannot check without redoing the comparison, at which point publishing
it bought nothing.

It also means the direction is *the direction of what this payload publishes*,
which is narrower than the fact layer's classification and is the point. The
fact layer folds the whole semantic assessment into one digest while this
payload publishes a permission block derived from it — which is how a permission
expansion came to be labelled provenance-only.

- `evidence_only` is not a claim: it is exactly "the two records are equal apart
  from provenance", and nothing else can be called that.
- Each moved dimension yields one `CapabilityChangeFact` with its own
  `direction`. Set-valued dimensions (`scope`, `resource`, `authority.scopes`,
  `broad_scope`, `risk_tags`) widen on a gain and narrow on a loss; `effect` and
  `reversibility` move by rank; effect flags widen when set; a **control** widens
  when a proven one is lost; `permission` follows its classes, with a
  `measured` ↔ `unavailable` move given no direction at all, because losing a
  measurement is not a narrowing.
- Dimensions with no ordering — auth type, credential mode, evidence owner,
  operation — are reported as changes with direction `unknown`.
- The row's direction is the rollup: all one way is `broadened` or `narrowed`,
  both ways is `mixed`, and no directional movement is `unknown`. A change that
  moves only an opaque digest yields `unknown` with no explanations — the moved
  digests are already named in `changed_dimensions`.

## Validating a payload: two stages

`capability-payload-schema.v1.json` is **stage one**, and it is not sufficient
on its own. Pydantic's cross-field rules do not appear in a generated JSON
Schema, so anything requiring a *recomputation* is unexpressible there. Rather
than leave that gap implicit, it is named — in the schema's own `description`
and here — and everything JSON Schema *can* express has been pushed into the
published file.

**Stage one — the JSON Schema enforces:** the closed object shapes and
`additionalProperties: false`; every field of every object required; the closed
enums; the `view` discriminator; the `subject.key`, `capability_id` and digest
string patterns; counts and line numbers inside the I-JSON safe range; strict
scalar types, so `"2"` is not an integer and `"false"` is not a boolean;
non-empty `capabilities[]` and `changes[]`; `uniqueItems` on every list; the
transition ↔ sides ↔ direction coupling on a change entry, and that a membership
change carries no dimensions and no explanations; the presence ↔ transition
coupling on a subject row, that a subject is present on at least one side, and
that a subject absent from one side carries only the changes that side can have;
the permission shapes the classifier can produce; and that coverage may only
name subjects when its status is `complete`.

**Stage two — a consumer must also check**, because each needs a computation:

| Rule | What it takes |
|---|---|
| `subject.key` is the published derivation of its own agent/provider/tool id | one sha256 per row |
| `summary` equals the subject rows | a count |
| `subjects[].transition` equals the rollup of its presence pair | a comparison |
| `changed_dimensions` equals the digests that actually differ between the two records | a comparison |
| a `state`'s three digests describe its own rows and coverage | three sha256 |
| `subject.key` and `capability_id` are unique across the whole payload | two set walks |
| `analysis_coverage.newly_outside_analysis` / `no_longer_outside_analysis` follow from `base` and `head` | two set differences |
| `semantic_direction` and `semantic_changes` are what the two records show | a dimension-by-dimension comparison |
| the two state refs reconcile with the membership rows | two subtractions |
| an empty delta names two states whose capability and evidence digests agree | a comparison |
| a delta's `base`/`head` `analysis_coverage_digest` describes the coverage it carries | two sha256 |

`agents_shipgate.schemas.capability_payload` is the reference implementation of
both stages: parsing a payload with `CapabilityPayloadV1` runs every rule in
this section. A standalone verifier that does the same without depending on
this package is [#470](https://github.com/ThreeMoonsLab/agents-shipgate/issues/470)'s
deliverable.

## Required and optional

**Every field of every object is required.** Not "required in prose" —
required in the JSON Schema's `required` arrays, which is what a consumer's
validator actually reads. A field with a schema default is *absent* from
`required`, and a version field or a `view` discriminator that a consumer may
omit and have repaired is not a version field. Producers pass every value
explicitly, including the constants.

Absence and emptiness are therefore different from *null*. Fields are nullable
wherever the static evidence may not exist — an unauthed tool has
`authority.auth_type: null`, a declaration without a line number has
`evidence.source_start_line: null`. **Null means "not stated by the evidence",
never "false" and never "none".** A list may be empty (`subjects[]` on a state
with nothing analysed) but is never absent.

One default is deliberately fail-closed and is expressed as a value rather than
an omission: a producer that did not establish coverage publishes
`{"status": "not_requested", "subjects_outside_analysis": []}`, so a consumer
never has to decide what a missing coverage block would have meant.

`permission` is fail-closed in the same spirit: `status: "unavailable"` carries
`side_effect_unknown: true` and an empty `classes[]`, and the combination that
would read as "measured and harmless" is rejected. Unmeasured is unknown, not
read-only. A `measured` profile is checked against the shapes the lattice can
actually produce — `read` never pairs with a side-effecting class, `destructive`
always carries `write`, unknown side effects always carry the `unknown` class —
because a combination the classifier never emits is not a harmless oddity, it is
a claim with no meaning.

## What the payload does not publish

The published set is closed — every model forbids extra properties — and each
exclusion is recorded with its reason in
`agents_shipgate.core.capability_payload` (`UNPUBLISHED_FACT_FIELDS`,
`UNPUBLISHED_LOCK_FIELDS`). A test asserts those maps together cover every field
of `CapabilityFactV1`, so a new internal field cannot reach either surface — or
be silently dropped from both — without someone writing down which it is and
why.

From the capability fact:

| Not published | Why |
|---|---|
| `evidence.source_location` | A rendering of `source_path` plus `source_start_line`. Publishing both would put two spellings of one value on the wire. |
| `semantic_assessment` | The derivation, not the fact. Its conclusions are published as `effect`, `authority` and `permission`; the claim/issue tree underneath is the extractor's working, and freezing it here would freeze internals this schema has to be able to change. |

From the permission lattice
(`core.capability_lattice.CapabilityPermissionProfile`), whose semantic half the
payload publishes as `permission`:

| Not published | Why |
|---|---|
| `risk_score`, `risk_level` | A heuristic score tuned for the `mcp audit` surface. It is a ranking aid, not a capability fact, and an external consumer that gated on it would be gating on our tuning. |
| `reasons` | Internal claim-source identifiers. `evidence` carries the provenance a consumer can open. |

And one thing that is not an internal field at all: the payload carries **no
release impact, severity, or verdict** for a subject. `capability_change`
members carry a `release_impact`, and it is deliberately left behind here.
Publishing a per-subject impact in an interchange format invites a consumer to
gate on it, which is a second verdict by another name;
`release_decision.decision` remains the only gate, and a consumer that wants a
verdict should read the report rather than infer one from a capability fact.

From the existing capability **lock file**, the state artifact this payload
supersedes: `capability_lock_schema_version`, `experimental`, `cli_version`,
`source.*`, `summary.*`, and the lock-level `hashes.*`. The reasons are in
`UNPUBLISHED_LOCK_FIELDS`; the load-bearing one is `cli_version`, which would
change every consumer's digest on a release that changed nothing about what the
agent can do.

## Evolution policy

`shipgate.capability_payload/v1` is **closed, and that is the whole policy.**

This is a deliberate departure from the additive-within-a-version rule the
`report.json` schema follows, and the reason is that the two schemas make
opposite promises. `report.json` is open by construction and its consumers are
told to ignore what they do not know. This payload is closed by construction:
every object sets `additionalProperties: false` and every vocabulary is a closed
enum, because a *frozen interchange format* whose point is that an external tool
can verify it must not accept content that tool cannot account for. A closed
schema cannot also be additive — a `v1` validator rejects a new optional field
and a new enum value rather than ignoring them — so promising both would be
promising something the shipped validators do not do.

The rules, then:

- **Any addition is a new version.** A new field, a new enum value, a new
  vocabulary — `/v2`, published as a new schema file beside this one. There is
  no such thing as a compatible in-place widening here, and a consumer pinned to
  `v1` never has to guess.
- **So is any removal or change of meaning.** Same mechanism, same file.
- **Old versions stay published.** `v1` remains committed as a frozen reference
  after `v2` lands, and remains readable for at least one minor cycle — the
  deprecation rule in [`../STABILITY.md`](../STABILITY.md) applied to a whole
  version rather than to a field, because a whole version is the unit here.
- **Producers may emit more than one version.** Since versions do not widen, the
  migration path is to publish both for a cycle, not to bend `v1`.
- **`capability_standard_version` is separate and moves on its own.** It names
  the capability-fact standard that produced the rows. A consumer that only
  reads published fields does not need to branch on it; a consumer comparing two
  payloads across different standard versions must regenerate both sides rather
  than diff them.
- **Both views move together.** They are one payload; versioning them separately
  would reintroduce exactly the divergence this schema exists to prevent.
- **A change to the shape of a capability fact is caught before it ships.** A
  type-parity test compares every published field's type against the internal
  field it projects, so a widened `Literal` or a retyped field fails CI rather
  than reaching the wire — or raising on an adopter's machine. That is the point
  at which someone decides a `v2` is due.

## For the two consuming surfaces

Both surfaces reference this schema **by version** and define no second payload
shape:

- **#470, the delta attestation** wraps `view: "delta"` as its predicate
  payload. The predicate URI, the attested subject, and signing are that issue's
  surface; the bytes inside are this one.
- **#474, the committed state** writes `view: "state"`. The file's location,
  regeneration discipline, and staleness semantics are that issue's surface; the
  bytes inside are this one.

Because both come from `agents_shipgate.core.capability_payload`, the delta an
attestation carries and the delta a reviewer reads are one projection of one
computation, not two renderings of one value.

## Relationship to the capability lock

[`capability-standard.md`](capability-standard.md) describes the capability lock
and lock diff (`capabilities.lock.json`, `capability-lock-diff.json`), which
exist today and are unchanged by this schema. The lock is the internal-facing
artifact: it carries the full fact, including the derivation. This payload is
the external-facing one: closed, smaller, counted by subject, and frozen.

Where the two overlap, the lock is the source and this is the projection. They
are not two spellings of one artifact — a consumer that wants everything the
engine knows reads the lock; a consumer that wants a stable interchange fact
reads this.
