# The report 1.0 contract

`report_schema_version` is frozen at **`1.0`**
([`report-schema.v1.0.json`](report-schema.v1.0.json)). This document is what
that number now means: which surfaces are stable, which are provisional, what
`1.x` may change, what needs `2.0`, and how a reader migrates from the contract
this project actually shipped.

Delivered by [#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569),
which exists because [`STABILITY.md`](../STABILITY.md) promised a `1.0` line
would not begin until the report schema reached `1.0` and held without a
breaking change.

---

## What the freeze is, and what it is not

**It is a promotion, not a break.** `1.0` is the `0.43` shape, renumbered.
Nothing was added, renamed, retyped, removed, or given a new meaning:
`report-schema.v1.0.json` and `report-schema.v0.43.json` are byte-identical
apart from `$id`, `title`, and the `report_schema_version` constant, and
`test_the_1_0_schema_is_a_promotion_of_the_last_pre_freeze_schema` holds that.
A consumer written against `0.43` reads a `1.0` report without a change.

This matters because [`STABILITY.md`](../STABILITY.md)'s own versioning rule is
"`report_schema_version` bumps minor on additive changes, **major on
breaking**". Read literally, `0.43 → 1.0` would announce a break. It does not.
The major moved because the *promise* attached to the shape changed, and there
was no other number to move it to.

**It is not the v1 consolidation.** [`report-v1-consolidation-rc.md`](report-v1-consolidation-rc.md)
proposed regrouping the top level into `subject`/`gate`/`findings`/`surfaces`/
`change`/`policy`/`review`/`audit`. That proposal is superseded and was not
adopted: it is a breaking re-parenting, and re-parenting the whole report is a
way to *do work*, not a way to *establish compatibility*. It stays in the tree
as the record of an option that was considered and rejected.

**The freeze is conditional and revocable.** It holds while no breaking change
lands. A breaking change restarts it and invalidates every qualification
receipt collected against the frozen schema — see
[Restarting the freeze](#restarting-the-freeze).

---

## The 1.x rules

1. **`1.x` is additive-only.** A minor may add a top-level block, add a member
   to an existing block, add an enum value to a field documented as open, or
   add a new check ID. It may not rename, retype, remove, or redefine anything.
2. **A change that cannot be expressed additively needs `2.0`.** There is no
   third option and no "small break". Retyping a field, narrowing an enum a
   consumer gates on, or changing what a value means is a major.
3. **Every published schema URL keeps its bytes forever.** `docs/report-schema.v<version>.json`
   is immutable once committed; new content goes to a new version. This is
   enforced by byte hash in `tests/test_effect_coverage.py`
   (`_PUBLISHED_SCHEMA_SHA256`), because "committed equals generated" — which
   is all `generate_schemas.py --check` proves — does not prove that a content
   change moved the version.
4. **A deprecation cycle is counted in shipped releases, never in elapsed
   time on unreleased `main`.** A surface deprecated in `1.n` may be removed no
   earlier than `2.0`, and only if `1.n` was actually published. Time a
   deprecation spent on a branch nobody could install is not a cycle; nobody
   was given the chance to migrate.
5. **A shipped check ID is deprecated, never hard-removed** — the existing
   [`STABILITY.md`](../STABILITY.md) rule, unchanged by the freeze.
6. **Projection reads accept additive minors; comparison and mutation fail closed.**
   `explain-finding`, `findings`, `scenario suggest`, and `evidence-packet`
   accept newer `1.x` reports while projecting the fields they understand.
   `scan --diff-from` and `apply-patches` refuse newer minors with an upgrade
   route, because this build cannot compare or apply evidence recorded under
   a contract it does not have.

---

## Stable and provisional

Two independent axes. Confusing them is how a diagnostic block ends up load-bearing.

| Axis | Values | Means |
| --- | --- | --- |
| Presence | `required` / `optional` | Whether [`report-schema.v1.0.json`](report-schema.v1.0.json) lists the field in `required`. A `required` field is present in every emitted `1.x` report. |
| Stability | `stable` / `provisional` | `stable`: the field keeps its name, its type and its meaning for all of `1.x`; members may be added inside it. `provisional`: its members may be added, removed or restructured within `1.x`, and the block itself may be removed after a deprecation cycle. |

A `provisional` block is never a gate input. If a decision depends on it, that
is a defect in the consumer, not a licence to freeze the block.

`tests/test_report_1_0_contract.py` checks this table against the runtime:
every `ReadinessReport` field appears exactly once, every row names a real
field, the presence column equals the published schema's `required` list, and
nothing [`STABILITY.md`](../STABILITY.md) already promises stable is marked
provisional here.

### `report.json` top level

| Field | Presence | Stability | Note |
| --- | --- | --- | --- |
| `schema_version` | required | stable | The `0.1` payload-contract marker, unchanged since v0.1. |
| `report_schema_version` | required | stable | `1.0`. The subject of this document. |
| `run_id` | required | stable | Deterministic run identity. |
| `request_id` | optional | stable | Verify-native content-addressed request; absent on a plain `scan`. |
| `subject_id` | optional | stable | Verify-native subject identity. |
| `input_set_id` | optional | stable | Verify-native input-set identity, taken at the adapter read boundary. |
| `engine_requirement_id` | optional | stable | Verify-native engine identity. |
| `decision_id` | optional | stable | Verify-native decision identity. |
| `manifest_dir` | optional | stable | Absolute manifest directory; `apply-patches` enforces containment against it. |
| `project` | required | stable | Subject metadata. |
| `agent` | required | stable | Subject metadata. |
| `environment` | required | stable | Subject metadata. |
| `summary` | required | stable | Legacy counts. Baseline-blind by construction; gate on `release_decision`. |
| `release_decision` | required | stable | **The gate.** Frozen hardest of anything here. |
| `capability_facts` | required | stable | Durable capability rows. |
| `declared_intentions` | required | stable | Manifest declarations as read. |
| `misalignments` | required | stable | The block is stable; the heuristics that populate it are not a contract. |
| `release_consequence` | required | stable | — |
| `suggested_scenarios` | required | stable | Present and typed; the suggestions themselves are advisory. |
| `tool_surface` | required | stable | — |
| `tool_surface_facts` | required | stable | Base-comparable tool-surface evidence. |
| `tool_surface_diff` | required | stable | — |
| `action_surface_facts` | required | stable | — |
| `action_surface_diff` | required | stable | — |
| `action_declaration_facts` | required | stable | — |
| `binding_surface_facts` | optional | stable | v0.31+; absent on older bases. |
| `binding_surface_diff` | optional | stable | Carries `base_report_schema_version`. |
| `capability_runtime_evidence` | optional | provisional | Runtime-evidence depth is still moving; never a gate input. |
| `api_surface` | optional | stable | — |
| `anthropic_surface` | optional | stable | — |
| `frameworks` | required | stable | — |
| `codex_plugin_surface` | required | stable | — |
| `baseline` | optional | stable | Baseline match state. |
| `findings` | required | stable | Core finding fields are frozen; see [`STABILITY.md`](../STABILITY.md). |
| `recommended_actions` | required | stable | — |
| `generated_reports` | required | stable | Paths this run wrote. |
| `loaded_policy_packs` | required | stable | — |
| `loaded_plugins` | required | stable | — |
| `loaded_adapters` | required | stable | Names the engine composition a verdict was produced by; reproducibility depends on it. |
| `tool_inventory` | required | stable | The proven reachable surface. |
| `tool_catalog` | optional | provisional | Diagnostics only. `tool_inventory` is the surface a decision may read. |
| `source_warnings` | required | provisional | The array is always present; its row shape is diagnostic. `evidence_coverage.source_warning_count` is the stable count that feeds the decision. |
| `policy_evidence_gaps` | optional | stable | Zero-tolerance policy-applicability gaps; not suppressible. |
| `agent_summary` | required | stable | Deterministic projection of `release_decision` + per-finding `agent_action`. |
| `policy_audit` | required | stable | — |
| `privacy_audit` | required | stable | — |
| `heuristics_filter` | required | stable | — |
| `reviewer_summary` | required | stable | — |
| `capability_change` | required | stable | — |
| `protected_surface_changes` | required | stable | — |
| `effective_policy` | required | stable | — |
| `human_ack` | required | stable | — |
| `verifier_summary` | required | stable | — |
| `surface_exclusions` | required | stable | Every subject a stage removed from the analysed surface. |

### CLI, exit codes and control

These are already stable across `0.x` under [`STABILITY.md`](../STABILITY.md);
the freeze does not widen them. Listed here so "what is provisional" has an
answer that is not "read six documents".

| Surface | Stability | Note |
| --- | --- | --- |
| Exit codes `0` / `2` / `3` / `4` / `6` / `20` | stable | Read `agents-shipgate contract --json` → `exit_code_policy` for the authoritative map. |
| `release_decision.decision` as the release gate | stable | The only release gate. Nothing else is. |
| `shipgate.agent_control/v1`, `agent_boundary_result/v2` | stable | `minimum_control_contract_version` is `21` and unchanged by the freeze. |
| `contract --json` field set | stable | Additive. |
| GitHub Action inputs / outputs | stable | The `shipgate_wheel` / `shipgate_wheel_sha256` pair is a local-wheel escape hatch, not a published channel. |
| `--format codex-boundary-json` | **deprecated** | Compatibility projection through `0.16.x`; use `agent-boundary-json`. Published in `contract --json` → `deprecated_surfaces`. |
| `preflight` result | provisional | A routing/projection surface. `release_decision.decision` remains the gate; preflight grants no authority. |
| Org governance, org evidence bundle, registry query | provisional | Ring-2 surfaces; not part of the `1.0` merge-gate promise. |
| MCP server transport | provisional | Every answer is produced by calling the CLI in-process; it has no contract of its own. |
| Markdown and packet **rendering** | provisional | The rendered text is for humans. Parse the JSON. |

Every published artifact schema keeps its own version and its own bytes; the
freeze applies to `report.json` and does not renumber packet, verifier,
receipt, capability-lock, attestation, preflight or host-grants schemas. That
was deliberate: renumbering every schema would have been work without
compatibility.

---

## Migrating from the shipped contract

The newest release a stranger can actually install is **`v0.15.0`**. It emits
`contract_version: 10` and `report_schema_version: 0.28`, and predates
`minimum_control_contract_version` entirely — its `contract --json` carries no
floor field at all. That is the real starting point for a migration, not
`0.43`, which only ever existed on `main`.

### What a `v0.15.0` consumer changes

| From `v0.15.0` | At `1.0` | Why |
| --- | --- | --- |
| `report_schema_version: "0.28"` | `"1.0"` | Everything between is additive. A `0.28` parser still reads a `1.0` report; it simply sees fields it ignores. |
| `contract_version: 10`, no floor field | `contract_version: 36`, `minimum_control_contract_version: 21` | Gate on `minimum_control_contract_version`, not on `contract_version`, and treat its absence as "older than 21". |
| Read `agent-result.json` | Read `verification-receipt.json`, then `agent-handoff.json`, `verifier.json`, `verify-run.json` | Removed in `0.14.0`; see that migration note. |
| `--format agent` | `--format json` | Removed in `0.14.0`. |
| `--format codex-boundary-json` | `--format agent-boundary-json` | Deprecated; the projection stays through `0.16.x`. |
| Gate on `summary.status` | Gate on `release_decision.decision` | `summary.status` is baseline-blind and always was. |

### Changed defaults

The freeze itself changes no default. The one behavior change it carries is the
refusal below, which narrows what is accepted as *input* without narrowing what
is emitted.

### Parser direction, and the refusal

The two directions are not symmetric, and saying so is the point of this
section.

**Reading our output** — additive, both ways within `1.x`. Validate against the
schema whose version the report declares. Every superseded schema stays
published, so an archived `0.10` report still validates against
[`report-schema.v0.10.json`](report-schema.v0.10.json).

**Feeding a report back to this engine** — requires `1.x`. Projection readers
accept newer additive minors; comparison and mutation also require a version
no newer than the running build. Every external report boundary goes through
`agents_shipgate.schemas.report_compatibility`:

- `agents-shipgate scan --diff-from <report.json>` (comparison)
- `agents-shipgate apply-patches --from <report.json>` (mutation)
- `agents-shipgate explain-finding`
- `agents-shipgate findings`
- `agents-shipgate scenario suggest`
- `agents-shipgate evidence-packet` given a `report.json`

The refusal is by name, with a route, and it carries a stable `reason_code`:

| `reason_code` | Cause | Route |
| --- | --- | --- |
| `report_schema_missing` | No `report_schema_version` | Not an Agents Shipgate report. |
| `report_schema_malformed` | Not a `MAJOR.MINOR` version this engine emits | Regenerate. |
| `report_schema_pre_freeze` | `0.x` — the pre-freeze line | Regenerate from the workspace it described; re-verify anything derived from it. |
| `report_schema_newer_than_engine` | `1.n` where `n` exceeds this build, for comparison or mutation | Upgrade the CLI, or regenerate with this build. |
| `report_schema_future_major` | `2.x` or later | Not convertible; upgrade the CLI. |

**What the gate covers, and what it does not.** These are the boundaries where
a *user* hands the engine a report path. Commands that read a `report.json`
from a run's own artifact bundle — `org status` and `attest`, which resolve it
from the `verifier.json` they were given — are deliberately not gated. Neither
is a release gate (`release_decision.decision` remains the only one), both are
reading a bundle whose engine identity the verifier already binds, and refusing
there would break reading an archived bundle for diagnostics, which this freeze
is required to preserve.

Why refuse rather than read it anyway: `ReadinessReport` is `extra="allow"` and
almost every field carries a default, so `model_validate` accepts a `0.9`
payload and returns an object whose newer blocks are filled with *this build's*
defaults. Nothing in that object distinguishes a value that was recorded from
one that was invented. Comparing a head scan against a base like that is not a
weaker comparison; it is a comparison against evidence that does not exist.

**There is no conversion.** A pre-freeze report is not upgraded, rewritten, or
re-labelled. Re-labelling one would assert evidence nothing measured.

### Re-verifying what was derived from an old report

| Artifact | What to do |
| --- | --- |
| Baseline (`.agents-shipgate/*.json`) | Regenerate the report, then `agents-shipgate baseline save`. A baseline is a snapshot of findings, so it inherits whatever the report it came from actually recorded. |
| `--diff-from` base report | Regenerate it from its own workspace. Base-scan caches are already bound to the effective engine identity ([#596](https://github.com/ThreeMoonsLab/agents-shipgate/issues/596)), so a cache produced by a different engine is invalid regardless. |
| Verification receipt | Re-run the verification that produced it. |
| Qualification artifact | Re-run `scripts/run_safety_qualification.py` against the current wheel. |

### An old receipt gains no authority

This is the property the freeze must not lose, and it holds from both sides:

- **This module converts nothing.** Refusal is the only outcome for an
  unsupported artifact, so there is no code path by which one written under an
  older contract acquires the standing of one written under the current engine.
- **The qualification gate compares for exact equality.**
  `required_report_schema_version` is `1.0` for the production `beta` policy.
  A receipt naming any other schema fails closed, and re-stamping its digest
  does not change the schema the receipt names.
- **Tier admission is decided by version, not by label.**
  `accepted_qualification_tiers` admits the production policy only for `1.0`
  and later, so a `pre_1_0` artifact cannot publish a `1.0` release however it
  is relabelled.

### Retirement of `pre_1_0` tier issuance

Issuance of the 38-case `pre_1_0` qualification tier
([#341](https://github.com/ThreeMoonsLab/agents-shipgate/issues/341)) is
retired with this freeze. `scripts/run_safety_qualification.py` will not
produce an artifact carrying it, whatever the wheel version, and
`--policy-tier pre-1.0` is refused by name rather than dropped from the choice
list — an operator running a pre-freeze runbook line gets the reason instead of
"invalid choice".

Retirement is about **issuance only**. Historical readability and diagnostics
are preserved intact: `pre_release_safety_requirements()` still exists, still
returns the same thresholds, and deliberately keeps its historical
`required_report_schema_version: "0.43"` pin. `tier_for_requirements` names a
policy by byte-equality, so re-pinning that constructor to a schema no
`pre_1_0` artifact ever carried would demote every existing one to the unnamed
`test` tier — replacing an accurate diagnosis with a misleading one. No scoring
floor moved, and no old artifact is re-read as `beta` qualification.

---

## The RC exercise

`STABILITY.md` asks that the frozen schema **hold**. The source tree asserting
that about itself is a different, weaker claim, so it was established against
an installed build. [#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569)
allows an untagged, unpublished candidate for exactly this, and that is what
was used.

**Candidate.** `agents_shipgate-0.16.0-py3-none-any.whl`
(`sha256:02a7a3fed745f0794beeb39fbd5bd913ecae51b1346aa76590b74ef6b1a5a1c3`),
built by the unqualified Release Engine Smoke workflow
([run 34726385422](https://github.com/ThreeMoonsLab/agents-shipgate/actions/runs/34726385422))
from committed source `8a14470d` with the locked `hatchling==1.32.0` backend
(`constraints/build-backend.txt`), then downloaded, installed into an isolated
virtualenv and driven as `python -I -m agents_shipgate` with `PYTHONPATH` cleared
— so what ran was the package, not the working tree. Re-established on
2026-09-12 after this branch was reconciled with `main`: the workflow's
automated `report_schema_exercise()` and a by-hand replay of all six rows below
agree. An earlier run of this record used a wheel built from `8e81672b` at
contract 34; its rows are unchanged, and its engine identity is superseded by
the one below.

**Engine identity observed:** `cli_version 0.16.0`, `contract_version 36`,
`report_schema_version 1.0`, `minimum_control_contract_version 21`.

| Replay | Input | Observed |
| --- | --- | --- |
| Emit | `scan` on `samples/support_refund_agent` | report declares `1.0`; equals what `contract --json` advertises |
| Projection, pre-freeze | that report, `report_schema_version` rewritten to `0.43` | `findings` exits `3`, `[report_schema_pre_freeze]`, names the regeneration route |
| Projection, newer minor | the same report rewritten to `1.99` | accepted — the additive promise holds forward for a reader that only projects |
| Comparison, pre-freeze | `scan --diff-from` that `0.43` base | scan completes, diff disabled, refusal recorded as a `source_warning`, verdict not `passed` |
| Comparison, newer minor | `scan --diff-from` the `1.99` base | refused as `report_schema_newer_than_engine`, diff disabled |
| Projection, current | the candidate's own report | accepted |

The relabelled input is the candidate's own report with **only** its version
field rewritten. That is a single-variable experiment — the only thing a
refusal can be about is the version — and it is precisely the move the freeze
forbids: had it been accepted, an old signed receipt would acquire current
authority through a one-field edit.

The exercise is automated as `report_schema_exercise()` in
`scripts/release_engine_smoke.py`, so the final candidate re-runs it on the
same disposable job that already proves the CLI and its generated Action agree,
rather than depending on anyone repeating the steps above by hand.

**What this is not.** The wheel was built by a disposable smoke run from an
unmerged branch commit. Its SHA-256 identifies that run's bytes, not a release,
and is not release provenance in
[#570](https://github.com/ThreeMoonsLab/agents-shipgate/issues/570)'s sense; the
run records `qualified: false` and `qualification_claim: "none: synthetic
distribution smoke only"` about itself. It is unsigned, unqualified, untagged and unpublished. It establishes
that the report contract holds through an installed build and a migration
replay — and nothing about qualification, adoption, or the final candidate,
which [#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512),
[#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509) and #570
own separately.

---

## Restarting the freeze

A breaking change to `report.json` restarts the freeze. Concretely:

1. The change ships as `2.0`, not as a `1.x` minor.
2. `STABILITY.md`'s freeze condition is no longer satisfied by `1.0`, so the
   `1.x` line is closed rather than continued.
3. Every qualification receipt collected against `1.0` is invalidated, because
   `required_report_schema_version` no longer names the schema the engine
   emits. They must be re-collected against the new candidate.
4. "Holds without a breaking change" is measured over **shipped** releases.
   A break discovered on unreleased `main` before any `1.x` tag exists costs
   the renumbering, not the cycle — nothing was published to break.

The freeze is therefore not a claim that the schema is finished. It is a claim
about what happens next, and about who pays for it.

---

## See also

- [`STABILITY.md`](../STABILITY.md) — the full per-field stability contract.
- [`report-schema.v1.0.json`](report-schema.v1.0.json) — the frozen schema.
- [`agent-contract-current.md`](agent-contract-current.md) — the current runtime contract.
- [`distribution-surfaces.md`](distribution-surfaces.md) — every surface this engine is published through.
- [`report-reading-for-agents.md`](report-reading-for-agents.md) — how to read a report.
