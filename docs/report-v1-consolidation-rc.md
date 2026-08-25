# Report v1.0 Consolidation — RC Proposal

> Status: **proposal** (no behavior change in this document). Target:
> freeze a v1.0 report schema whose top-level surface stops growing.
> Written 2026-06; current runtime is `report_schema_version: "0.38"`.

## Problem

Since v0.17 the report has gained a top-level block roughly every minor:
`policy_audit` (v0.17), `privacy_audit` (v0.18), `reviewer_summary`
(v0.20), `heuristics_filter` (v0.21), the five verifier-cycle blocks
(v0.22), capability semantics/evidence additions (v0.23–v0.25), and
`evidence_gaps` (v0.26). Each is individually justified, but a 1.0
stability promise over the current ~30-field top level would lock in a
surface wider than the contract we actually want to maintain for years.

## Principle

The mental model (one engine, one verdict, everything else a projection
— see [`mental-model.md`](mental-model.md)) should be *visible in the
schema shape*. v1.0 groups top-level blocks by the reader they serve.

## Proposed v1.0 shape (additive regrouping, mechanical migration)

```
report.json (v1.0)
├─ schema/run identity          (report_schema_version, run_id, manifest_dir)
├─ subject                      ← project, agent, environment
├─ gate                         ← release_decision (UNCHANGED — the engine)
├─ findings[]                   (unchanged; the check domain)
├─ surfaces                     ← tool_surface*, action_surface*, api/anthropic/
│                                 frameworks/codex_plugin surfaces, tool_inventory
├─ change                       ← capability_change, tool_surface_diff,
│                                 action_surface_diff, protected_surface_changes
├─ policy                       ← effective_policy, policy_audit, loaded_policy_packs,
│                                 loaded_plugins, loaded_adapters
├─ review                       ← reviewer_summary, agent_summary, verifier_summary,
│                                 human_ack, misalignments, suggested_scenarios
└─ audit                        ← privacy_audit, heuristics_filter, contribution
                                  context, capability_runtime_evidence, source_warnings
```

Rules:

1. **`release_decision` does not move and does not change.** Every
   consumer contract that matters gates on it; v1.0 is the moment we
   promise it never moves again.
2. **No field is renamed or retyped** — blocks are *re-parented* only,
   so migration is a mechanical path rewrite
   (`reviewer_summary` → `review.reviewer_summary`).
3. **A `v0` compatibility envelope ships for one minor**: scan emits
   both shapes behind `--report-shape v0|v1` (default v0 until v1.0.0,
   then default v1 with `--report-shape v0` kept for one minor).
4. **Fingerprints, baselines, run IDs are shape-independent** — they
   hash semantic content, not the envelope (already true today; add a
   contract test that pins it across both shapes).

## What freezes at v1.0

- `gate.release_decision.*` — the truth-table grammar (STABILITY.md),
  `evidence_coverage` counts + `evidence_gaps` row shape, and the
  `static_analysis_only` / `runtime_behavior_verified` /
  `static_verdict_disclaimer` boundary.
- `findings[]` core fields (id/fingerprint/check_id/severity/category/
  blocks_release/baseline_status/recommendation/evidence/source).
- The verifier projection contract (`verifier.json` v0.x → 1.0
  simultaneously; field set as documented in agent-contract-current.md).
- Exit codes, suppression immunity, severity-floor semantics.

## What stays explicitly unstable after v1.0

- Members *inside* `audit.*` (diagnostic depth may grow/shrink).
- `review.suggested_scenarios`, misalignment heuristics.
- Markdown/packet rendering.

## Acceptance criteria for cutting the RC

1. A generated `docs/report-schema.v1.0-rc1.json` with the regrouped
   shape; goldens render from the same scan run in both shapes.
2. Contract test: every leaf field in v0.29 maps to exactly one v1 path
   (no drops, no renames) — a generated mapping table is committed as
   `docs/report-v1-field-map.json`.
3. Baseline round-trip: a v0.29 baseline matches identically against a
   v1-shaped scan.
4. Consumer dry-run: Action outputs, PR comment, packet, SARIF, agent
   result, and attestation all build from the v1 shape with zero output
   differences.
5. One design partner consumes the RC for two weeks without a breaking
   read.

## Out of scope

Any new semantics. v1.0 is a freeze, not a feature. New capability work
(host boundary, runtime evidence) lands as members of existing groups.
