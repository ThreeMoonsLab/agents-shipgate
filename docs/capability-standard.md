# Agents Shipgate Capability Standard

The Agents Shipgate capability standard is the stable, static envelope for
describing what a tool-using agent can do. It is intended for external
integrators, reviewers, and researchers who need deterministic capability facts
without scraping `report.json` or the Markdown report.

The standard is non-gating. `release_decision.decision` remains the only
release gate. Capability locks and diffs are review and integration artifacts;
they do not create a second verdict.

## Current Versions

- Capability standard version: `0.2`
- Capability lock schema: [`capability-lock-schema.v0.3.json`](capability-lock-schema.v0.3.json)
- Capability lock diff schema: [`capability-lock-diff-schema.v0.4.json`](capability-lock-diff-schema.v0.4.json)
- Frozen lock reference: [`capability-lock-schema.v0.2.json`](capability-lock-schema.v0.2.json)
- Frozen lock-diff reference: [`capability-lock-diff-schema.v0.3.json`](capability-lock-diff-schema.v0.3.json)
- Frozen experimental lock reference: [`capability-lock-schema.v0.1.json`](capability-lock-schema.v0.1.json)

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
- `semantic_assessment` (v0.2) — normalized effect and authority claims,
  issues, conservative effect, and pass-eligibility state. Newly emitted v0.3
  locks populate it; it is optional only so older fact payloads remain
  readable.
- `risk_tags` — compatibility metadata for existing policy and review surfaces.
- `hashes` — separated semantic and evidence hashes for deterministic audit.

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
  and new facts by agent/provider/operation/tool.
- `evidence_changed` — only `evidence_hash` changed.

Changed rows carry `changed_hashes`, `semantic_direction`, and
`semantic_changes` so tools can explain whether the delta is broadened,
narrowed, mixed, unknown, or evidence-only.

See [`examples/capability-lock.v0.3.example.json`](examples/capability-lock.v0.3.example.json)
and [`examples/capability-lock-diff.v0.4.example.json`](examples/capability-lock-diff.v0.4.example.json).

## Provenance Boundaries

Capability locks are static-only. They are derived from local manifests and
declared tool sources through the same static extraction path as scans, but
they do not run agent code, call tools, connect to MCP servers, make network
requests, or collect live telemetry.

Opt-in runtime trace/provenance evidence belongs to `report.json` and release
evidence packets. It is reviewer/audit metadata and is not folded into the
static capability envelope.

## Compatibility

New exports use `capability_lock_schema_version: "0.3"` and
`experimental: false`. `agents-shipgate capability diff` continues to accept
old experimental `0.1` lock inputs and normalizes them before comparison.
Diff metadata reports the normalized current lock schema version for such
legacy inputs.

New diffs use `capability_lock_diff_schema_version: "0.4"` and
`experimental: false`. The v0.2 lock and v0.3 diff schemas remain frozen
references for archived artifacts; regenerate both sides with 0.16 before a
current semantic comparison. The older combined
[`capability-lock-schema.v0.1.json`](capability-lock-schema.v0.1.json)
remains a frozen reference.
