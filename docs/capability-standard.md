# Agents Shipgate Capability Standard

The Agents Shipgate capability standard is the stable, static envelope for
describing what a tool-using agent can do. It is intended for external
integrators, reviewers, and researchers who need deterministic capability facts
without scraping `report.json` or the Markdown report.

The standard is non-gating. `release_decision.decision` remains the only
release gate. Capability locks and diffs are review and integration artifacts;
they do not create a second verdict.

## Current Versions

- Capability standard version: `0.6`
- Capability lock schema: [`capability-lock-schema.v0.9.json`](capability-lock-schema.v0.9.json)
- Capability lock diff schema: [`capability-lock-diff-schema.v0.10.json`](capability-lock-diff-schema.v0.10.json)
- Frozen lock reference: [`capability-lock-schema.v0.2.json`](capability-lock-schema.v0.2.json)
- Frozen lock-diff reference: [`capability-lock-diff-schema.v0.3.json`](capability-lock-diff-schema.v0.3.json)
- Frozen experimental lock reference: [`capability-lock-schema.v0.1.json`](capability-lock-schema.v0.1.json)

## Relationship To The Capability Payload

[`capability-payload.md`](capability-payload.md) freezes
`shipgate.capability_payload/v1` — the single payload the planned exported
delta attestation and committed capability state both serialize. It is a
projection of the same capability facts described here, closed to a published
field set and counted by subject rather than by change.

The lock is the internal-facing artifact and carries the full fact, including
the derivation. The payload is the external-facing one. Where they overlap the
lock is the source and the payload is the projection; nothing on this page
changes, and no artifact emits the payload yet.

## CLI Workflow

```bash
agents-shipgate capability export -c shipgate.yaml
agents-shipgate capability export -c shipgate.yaml \
  --out agents-shipgate-reports/capabilities.lock.json \
  --no-report-copy
agents-shipgate capability diff \
  --base .agents-shipgate/capabilities.lock.json \
  --head agents-shipgate-reports/capabilities.lock.json \
  --out agents-shipgate-reports/capability-lock-diff.json --json
```

In PR workflows, `agents-shipgate verify` emits the review copy directly:

- `agents-shipgate-reports/capabilities.lock.json` after a successful head
  scan.
- `agents-shipgate-reports/capability-lock-diff.json` and
  `agents-shipgate-reports/capability-lock-diff.md` when `--base` is provided
  and the base scan can be materialized.
- If the base scan-derived lock is unavailable, verify falls back to the
  reviewed base lock at `.agents-shipgate/capabilities.lock.json`; if both are
  unavailable, it records a note and keeps the report-derived
  `capability_review.top_changes[]` PR surface.

Default export paths are unchanged:

- `.agents-shipgate/capabilities.lock.json` for the reviewed committed lock.
- `agents-shipgate-reports/capabilities.lock.json` for a byte-identical
  generated mirror.

`agents-shipgate verify` also writes PR-standard generated artifacts under
`agents-shipgate-reports/`: `capabilities.lock.json` for head, and when a base
ref is available, `base.capabilities.lock.json` plus
`capability-lock-diff.json`. These are review artifacts only; the release gate
remains `report.json.release_decision.decision`.

Repeated exports over the same manifest-relative static inputs are byte-stable.
No wall-clock timestamp is stored.

## CapabilityFactV1

`CapabilityFactV1` is the stable Python and JSON building block. External
Python consumers should import schema models from
`agents_shipgate.schemas.capabilities`; internal builders under
`agents_shipgate.core.*` are not the public API.

Each fact has:

- `id` — stable identity id derived from semantic identity only.
- `identity` — agent, provider, operation, tool, subject kind, resource, and
  normalized scope.
- `effect` — normalized side-effect facts such as write, financial,
  externally visible, code execution, reversibility, idempotency-known, and
  high-risk.
- `authority` — auth type, credential mode, authority source, exact scopes, and
  broad scopes.
- `controls` — approval, confirmation, safeguard, owner, and runbook signals.
- `evidence` — static source provenance for the declaration or extraction.
- `semantic_assessment` (v0.3; typed evidence basis in v0.5) — normalized
  identity, effect, and authority claims, issues, conservative effect, stable
  claim IDs, policy eligibility, and pass-eligibility state. Newly emitted v0.6
  locks populate it; it is optional only so older fact payloads remain
  readable.
- `risk_tags` — compatibility metadata for existing policy and review surfaces.
- `hashes` — separated semantic and evidence hashes for deterministic audit.
- `definition` (v0.6) — digests of the tool definition published to the model:
  `description_hash`, `output_schema_hash`, `annotations_hash`. `null` when the
  fact was built without its tool record or read from an older lock. See
  [Tool definitions](#tool-definitions).

See [`examples/capability-fact.v0.2.example.json`](examples/capability-fact.v0.2.example.json)
for a compact representative fact.

## Identity And Hashes

The capability `id` hashes only semantic identity:

- agent id
- provider
- operation
- tool id and tool name
- subject kind
- resource
- normalized scope

Source path, source pointer, and line numbers do not affect identity.

The lock keeps separate hashes:

- `identity_hash`
- `effect_hash`
- `authority_hash`
- `control_hash`
- `schema_hash`
- `risk_hash`
- `evidence_hash`

Lock-level hashes include:

- `semantic_capability_set_hash` for the semantic capability set.
- `evidence_set_hash` for provenance drift.
- `source_set_hash` for static source inventory metadata.
- `definition_set_hash` (v0.6) for every fact's `definition`, so a reviewed
  lock binds the tool definitions it was reviewed with.

Runtime trace evidence is intentionally excluded from capability locks and
semantic lock hashes.

The normalized semantic assessment is static evidence and contributes to the
fact's `evidence_hash`. It does not change capability identity. The legacy
effect and authority projections remain the stable semantic hash inputs.

## Diff Semantics

Capability lock diff matches facts by `CapabilityFactV1.id`.

- `added` / `removed` — id exists only in head/base.
- `changed` — same id, but one or more semantic hashes changed.
- `reidentified` — scope or resource changed and the engine can pair the old
  and new facts within the same canonical tool identity. Provider or source
  changes are added/removed unless a reviewed binding proves continuity.
- `evidence_changed` — only `evidence_hash` changed.

Changed rows carry `changed_hashes`, `semantic_direction`, and
`semantic_changes` so tools can explain whether the delta is broadened,
narrowed, mixed, unknown, or evidence-only.

- `definition_changed` (v0.6) — a paired fact (same id, or reidentified) whose
  tool definition digests differ. Independent of the buckets above: a fact can
  be `unchanged` semantically and still appear here. Each row names the
  `parts` that moved and both sides' digests, and carries no direction.

See [`examples/capability-lock.v0.9.example.json`](examples/capability-lock.v0.9.example.json)
and [`examples/capability-lock-diff.v0.10.example.json`](examples/capability-lock-diff.v0.10.example.json).

## Tool Definitions

A tool's input schema has always been part of the capability (`schema_hash`).
The rest of what a client hands the model about the tool was not: the
description, the output schema and the annotations. An MCP `tools/list` entry
whose description was rewritten, with its name and arguments unchanged,
produced an unchanged lock and an empty lock diff, while the PR's
`capability_review` already said `tool definition changed`. That is the shape
of a tool-poisoning or rug-pull change, and it is the change a reviewed lock
most needs to see when it is compared later (#1117).

Since v0.6 each fact built from a tool carries `definition`:

| Digest | Preimage |
|---|---|
| `description_hash` | the description with leading and trailing whitespace removed (`""` when absent) |
| `output_schema_hash` | the output schema as read (`{}` when absent) |
| `annotations_hash` | the annotations map as read, after the MCP reader removes reserved binding keys |

Each is the capability hash (`sha256` over canonical JSON, first 16 hex
characters), the same function as the other fact hashes. Only digests are
stored; the lock never carries a tool's text. Any source that publishes a
description gets one, so an OpenAPI operation's description and a function
tool's docstring are covered too.

A definition change is reported in `definition_changed`, never folded into
`changed`. It has no proven direction, so it neither broadens nor narrows the
semantic delta, and it does not change `release_decision.decision`. It is not
in `shipgate.capability_payload/v1` either: that payload is frozen, so the
capability-delta attestation does not show a definition-only change until a
payload version that carries `definition` is published.

## Provenance Boundaries

Capability locks are static-only. They are derived from local manifests and
declared tool sources through the same static extraction path as scans, but
they do not run agent code, call tools, connect to MCP servers, make network
requests, or collect live telemetry.

Opt-in runtime trace/provenance evidence belongs to `report.json` and release
evidence packets. It is reviewer/audit metadata and is not folded into the
static capability envelope.

## Compatibility

New exports use `capability_lock_schema_version: "0.9"` and
`experimental: false`. `agents-shipgate capability diff` continues to accept
old experimental `0.1` lock inputs and normalizes them before comparison.
Diff metadata reports the normalized current lock schema version for such
legacy inputs.

New diffs use `capability_lock_diff_schema_version: "0.10"` and
`experimental: false`. A `0.8` lock still loads, but it was written under
capability standard `0.5` and has no `definition`, so a diff against a `0.9`
lock is refused with the exact `capability export` command that re-exports it,
rather than reporting every definition as unknown. The v0.5 lock and v0.6 diff schemas remain frozen
references for archived artifacts; regenerate both sides with 0.16 before a
current identity-aware semantic comparison. The older combined
[`capability-lock-schema.v0.1.json`](capability-lock-schema.v0.1.json)
remains a frozen reference.
