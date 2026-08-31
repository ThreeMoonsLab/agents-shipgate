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

`analysis_coverage` is the separate axis that says so, on both views:

```jsonc
"analysis_coverage": {
  "status": "complete",                  // not_requested | unavailable | complete
  "subjects_outside_analysis": [ { "key": "capsubj_…", "name": "find_duplicate", … } ]
}
```

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

Five structural rules hold it there, enforced by the models and not only by the
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
4. A delta with **no** subject rows must name two states whose digests agree.
   "Nothing changed" is a claim about the two states, so it has to be one the
   payload's own digests support; a delta that says it while `base` and `head`
   differ is **rejected**.
5. `capability_id` is unique across the **whole** payload, not only within a
   row. Provenance is keyed by it, so a repeat would quietly drop one
   capability from `evidence_set_digest`.

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

`CapabilityStateRef` carries two digests over the **published** rows, so a
consumer can recompute both from the payload alone:

- `capability_set_digest` — over the semantic content of `subjects[]`, with the
  `evidence` block and `digests.evidence_hash` removed.
- `evidence_set_digest` — over the `capability_id → evidence` map.

The split is the one the capability lock already draws, and it keeps two
different questions separable: *did what the agent can do move*, or *only where
we read it from*. A delta's `base`/`head` digests are computed over the full
state payloads for each side, so a delta and a state payload can be proven to
describe the same state.

Both digests are computed with sorted keys and compact separators, over values
that are already JSON — the digest helper has no `str()` fallback, so it raises
rather than digesting a lossy rendering of something it cannot serialize.

**A `state` payload verifies its own digests on parse.** It carries every row
they are taken over, so a state whose `state.*_digest` does not describe its
`subjects[]` is rejected — the promise that the digests are recomputable from
the payload alone is enforced, not just documented. A `delta`'s `base`/`head`
refs describe states the delta does not carry, so those are taken on trust; the
`state` payload for each side is what proves them.

`ref` is an opaque caller label — a commit sha, a path — supplied by the surface
that emits the payload. It is never a timestamp: this payload carries no wall
clock, so two exports of the same static inputs are byte-identical.

## Required and optional

Required, always present: `capability_payload_schema_version`,
`capability_standard_version`, `analysis_coverage`, `view`, `subjects[]`
(possibly empty), and the view's own refs (`state`, or
`base`/`head`/`summary`).

Within a row, the identity fields (`subject.*`, `capability_id`, `operation`,
`subject_kind`) and the `effect`, `authority`, `controls`, `permission`,
`evidence`, and `digests` blocks are always present. Fields *inside* those
blocks are nullable wherever the static evidence may not exist — an unauthed
tool has `authority.auth_type: null`, a declaration without a line number has
`evidence.source_start_line: null`. **Null means "not stated by the evidence",
never "false" and never "none".**

One default is deliberately fail-closed: `permission.status: "unavailable"`
carries `side_effect_unknown: true` and an empty `classes[]`, and the schema
rejects the combination that would read as "measured and harmless". Unmeasured
is unknown, not read-only.

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

`shipgate.capability_payload/v1` is **frozen**. It follows the compatibility
rules in [`../STABILITY.md`](../STABILITY.md):

- **Additive within the version.** A new optional field with a default may be
  added to `v1`. Consumers must ignore fields they do not know, and must not
  treat an unknown enum value as invalid — widen or fall back.
- **Never silently narrowing.** Removing a field, making an optional field
  required, narrowing a type, or changing what an existing field means is a new
  version (`/v2`), published as a new schema file beside this one. The `v1`
  schema file stays committed as a frozen reference.
- **Deprecation over a minor cycle.** A field on its way out is documented as
  deprecated here and in `STABILITY.md` for at least one minor cycle before a
  version that drops it — it is never hard-removed.
- **`capability_standard_version` is separate and moves on its own.** It names
  the capability-fact standard that produced the rows. A consumer that only
  reads published fields does not need to branch on it; a consumer comparing two
  payloads across different standard versions must regenerate both sides rather
  than diff them.
- Both views move together. They are one payload; versioning them separately
  would reintroduce exactly the divergence this schema exists to prevent.
- A change to the **shape** of a capability fact — a field's type widening, a
  new value in a closed vocabulary — is caught by a type-parity test against
  the fact models rather than reaching the wire silently. That is the point at
  which someone decides whether `v1` can carry it or a `v2` is due; it is never
  decided by a `ValidationError` on an adopter's machine.

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
