# Architecture

A single-page summary of the `agents-shipgate` codebase for new
contributors and AI coding agents extending the project. Current as of
2026-06-08; auto-checked against `agents-shipgate contract --json`:
runtime contract `8`, report schema `v0.27`, packet schema `v0.7`.

For the per-field stability contract, see
[`../STABILITY.md`](../STABILITY.md). For the agent-facing field index,
see [`agent-contract-current.md`](agent-contract-current.md).

## Module map

```
src/agents_shipgate/
├── cli/                Typer entry points (scan, init, doctor, explain,
│                      apply-patches, bootstrap, evidence-packet,
│                      baseline {save, verify}, fixture, contract,
│                      explain-finding, scenario suggest, self-check).
│                      Major subcommands live in `cli/_register_<name>.py`
│                      (scan, init, doctor, explain, list-checks, contract,
│                      baseline {save,verify}). Leaf commands (detect,
│                      apply-patches, bootstrap, evidence-packet,
│                      explain-finding, self-check) register inline in
│                      `cli/main.py`. `fixture` and `scenario` are Typer
│                      subapps. `cli/main.py` is an ~90-line dispatcher.
├── inputs/             Adapters that read user artifacts into normalized
│                      tools. All adapters register a `ToolSourceAdapter`
│                      class with `inputs/protocol.py:REGISTRY`. No
│                      adapter may import/exec user code (lint enforced).
├── checks/             Pure functions `(ScanContext) -> list[Finding]`.
│                      Built-in callables listed in
│                      `checks/registry.py:BUILTIN_CHECKS`; built-in
│                      metadata in per-category YAML under
│                      `docs/checks/<category>.yaml`, loaded into
│                      `CHECK_METADATA` at import time by
│                      `checks/_metadata_loader.py`. External plugins
│                      discovered via the `agents_shipgate.checks`
│                      entry-point group and filtered through
│                      `checks/plugin_validation.py`.
├── core/               Domain logic: ScanContext, findings, baseline,
│                      severity_overrides, dynamic_defaults, privacy,
│                      risk_hints, heuristics, errors, and `lenses/`
│                      (reviewer-lens computation: tool_surface,
│                      action_surface, capability_intent). NOT wire
│                      types.
├── schemas/            (v0.11+) Wire-shape Pydantic models — `manifest`,
│                      `report`, `packet`, `baseline`, `contract`,
│                      `diagnostics`, `surfaces`, `policy_pack`,
│                      `checks`, `patches`, `disclaimers`, `detect`,
│                      `codex_plugin`, `adoption_scorecard`, `common`.
│                      Layer-isolated: schemas may NOT import core (lint
│                      enforced by `tests/test_schema_boundaries.py`).
├── ci/                 release_decision, exit_policy, github_summary.
├── report/             Output formatters only: markdown, json_report,
│                      sarif. Reviewer-lens *computation* lives in
│                      `core/lenses/`; renderers here consume the
│                      pre-built lens facts and diffs from the
│                      `ReadinessReport` Pydantic fields.
└── packet/             Release Evidence Packet builder + renderers
                       (markdown, json, html, pdf). Includes the
                       v0.6 `evidence_matrix` reviewer-lens projection.

harness/                (Not packaged.) Cold-agent adoption harness
                       (P0.2). 100-point rubric across 8 benchmark
                       repos. `smoke` subcommand is static replay (no
                       LLM calls); `run` may invoke live drivers under
                       a `--budget-usd` cap.
```

## Pipeline

```
config/loader.py                   loads & validates shipgate.yaml (Pydantic v2)
                                     ↓
inputs/protocol.py REGISTRY        dispatch in two passes:
                                     pass 1: per_source adapters (mcp, openapi,
                                            openai_agents_sdk) in declared
                                            order
                                     pass 2: per_scan adapters (google_adk,
                                            langchain, crewai, n8n, openai_api,
                                            anthropic_api, codex_plugin,
                                            validation) in canonical order
                                     ↓
inputs/<name>.py                   each adapter returns LoadedAdapterResult.
                                   Framework artifacts land in ArtifactBag.
                                     ↓
_flatten_and_deduplicate_tools     merge by stable id, source_priority break
                                     ↓
core/risk_hints.py                 enrich tools with risk tags (read_only,
                                   write, destructive, financial_action, …)
                                     ↓
_build_agent + ScanContext         (manifest, agent, tools, ArtifactBag,
                                    action_surface_facts, config_path)
                                     ↓
checks/registry.py run_checks      built-ins + validated plugins +
                                   manifest_consistency. Each check is a
                                   pure ScanContext → list[Finding].
                                     ↓
inputs/policy_packs.run            user-declared YAML rules emit findings
                                     ↓
core/lenses/action_surface         evaluate_action_surface_policies emits
                                   findings with blocks_release=True
                                     ↓
core/severity_overrides.resolve    floor enforcement, tier-crossing ack,
                                   expiry, dynamic-default aggregation
                                     ↓
core/findings.apply_*              severity overrides and manifest
                                   suppressions
                                     ↓
apply_no_heuristics_filter         v0.21: when --no-heuristics is set,
                                   mark heuristic-provenance findings
                                   suppressed BEFORE build_release_decision
                                   runs, so excluded findings cannot
                                   gate release. Runs after
                                   apply_suppressions so manifest
                                   intent wins on overlap. Always emits
                                   the heuristics_filter envelope
                                   (enabled=false when flag is unset).
                                     ↓
core/findings.patch/remediate      patch attachment (if --suggest-patches),
                                   v0.7 remediation annotation,
                                   v0.12 agent_action projection
                                     ↓
core/privacy piecemeal redaction    sanitize_model + redact_data on every
                                   public field; stats accumulate
                                     ↓
build_action_surface_facts +       reviewer-lens fact + diff blocks and
core/baseline.apply_baseline +     baseline classification, all run on
build_tool_surface_facts           sanitized public data
                                     ↓
build_privacy_audit                stats → privacy_audit
                                     ↓
core/findings.build_report         assemble ReadinessReport; internally
                                   calls build_release_decision
                                   ({blocked, insufficient_evidence,
                                    review_required, passed} +
                                   contribution_rules[] audit) over the
                                   post-filter active set; populates
                                   agent_summary + policy_audit +
                                   privacy_audit + heuristics_filter
                                     ↓
apply_capability_diff              mutate report from public tools
                                     ↓
build_reviewer_summary             populate v0.20 reviewer_summary from
                                   final lens/audit data (already
                                   post-filter)
                                     ↓
report/{markdown,json,sarif}       formatters write to agents-shipgate-reports/
packet/builder.build_packet        Release Evidence Packet (v0.7) including
                                   evidence_matrix and capability trace refs
                                     ↓
cli/scan/orchestrator.py:run_scan  entry-point orchestrator. Composed of
                                   nine sequential phase helpers
                                   (_prepare_scan → _load_inputs →
                                   _build_tools_and_agent →
                                   _load_diff_references →
                                   _run_checks_and_decide →
                                   _plan_outputs → _sanitize_for_output
                                   → _build_final_report →
                                   _write_outputs). Public signature,
                                   exit-code contract, and _run_id hash
                                   inputs are stable across the
                                   decomposition (PR #106).
```

## Schemas layer (v0.11+)

Wire-shape Pydantic models live under `src/agents_shipgate/schemas/`
(15 modules, see `Module map` above). `core/` holds processing logic — finding builders,
resolver, baseline manager, privacy sanitizer, etc. The two layers
are **AST-isolated**:

- `tests/test_schema_boundaries.py::REMOVED_SCHEMA_IMPORTS` rejects any
  code that imports from old monolithic locations (`core.models`,
  `config.schema`, `contract`, `packet.models`).
- `tests/test_schema_boundaries.py::FORBIDDEN_SCHEMA_LAYER_PREFIXES`
  rejects any `schemas/<x>.py` that imports from `agents_shipgate.core.*`.
  Schemas are pure wire data; they must not depend on processing logic.
- `test_representative_schema_payloads_keep_wire_fields()` pins the
  exact JSON field order for `ReadinessReport`, `EvidencePacket`,
  `BaselineFile`, and `ContractPayload`.

Adding a new wire field: edit the relevant `schemas/<name>.py`, run
`python scripts/generate_schemas.py` to regenerate the
`docs/*-schema.v0.N.json` artifact, and bump
`report_schema_version` / `packet_schema_version` if the addition is
public. The CI step `python scripts/generate_schemas.py --check`
fails if the committed JSON drifts from the live model.

## Typed domain types: `Scope`, `SideEffect`, `Action`

`core/domain.py` exposes three typed in-memory shapes alongside the
existing `Tool` / `Agent` / `LoadedToolSource` models:

- **`Scope`** — typed view of a permission scope string, with `raw`,
  `provider`, `resource`, `verb`. The parser (`Scope.parse`) is
  permissive (never raises) and handles `provider:resource:verb`
  (Stripe / AWS / K8s), `provider:resource` (GitHub), and
  `provider.resource.verb` (OpenAI). Wildcard slotting is
  **position-dependent**: 2-part `provider:*` puts the wildcard in
  the resource axis (2-part scopes have no canonical verb position),
  3+-part `provider:resource:*` puts it in the verb axis (AWS IAM
  convention — "all actions on the resource"). Both forms return
  `Scope.is_broad() == True` (delegated to
  `core.heuristics.is_broad_scope` so all broad-scope checks agree),
  and `is_read()` / `is_write()` always return False for a wildcard
  verb because `"*"` is not a canonical action verb. Frozen.
- **`SideEffect`** — typed side-effect profile. Single `effect` field
  (matches `schemas.surfaces.ActionEffect`) plus independently-derivable
  structural fields: `externally_visible`, `handles_sensitive_data`,
  `financial`, `code_execution`, `reversibility`, `idempotency_known`.
  `is_high_risk` is the canonical classifier. Frozen.
- **`Action`** — typed runtime representation of an action. Mirrors
  `schemas.surfaces.ActionFact` but with `scopes: list[Scope]` and
  `side_effect: SideEffect` instead of the wire-shape string-bag.

**Typed accessors in `core/risk_hints.py`:**
- `parse_scopes(tool: Tool) -> list[Scope]` — order-preserving wrapper
  over `tool.auth.scopes`.
- `tool_side_effect(tool: Tool) -> SideEffect` — derives the typed
  profile; its `effect` field matches `_infer_effect` byte-for-byte.
- `canonical_risk_tags(tool: Tool) -> list[str]` — risk-tag
  canonicalization through `CANONICAL_RISK_TAG_MAP` (single source of
  truth that `_normalized_risk_tags` in `core/lenses/action_surface.py`
  mirrors during the migration window).

**Wire-format invariant.** These types are **in-memory only**. The
canonical serialized shapes remain `ActionFact.required_scopes:
list[str]`, `AuthInfo.scopes: list[str]`, and `ActionFact.effect:
ActionEffect`. `core/lenses/action_surface.py` builds a typed `Action`
first (`build_action(...)`) and then serializes it to `ActionFact`
(`action_to_fact(...)`) — the legacy `_action_from_tool` is now a
thin wrapper around this pair. The output is byte-identical, so
`report_schema_version` and all finding fingerprints stay stable.

The existing string-based predicates in `core/risk_hints.py`
(`has_risk_tag`, `risk_tags`, `is_effectively_read_only`,
`is_high_risk_tool`, `is_write_tool`) are unchanged — they remain the
public API used by every check in `checks/`. New code should prefer
the typed accessors; legacy callers may migrate incrementally.

## Internal capability substrate: `CapabilityFactV1`

`core/capabilities.py` builds a durable capability vocabulary on top of
`Scope`, `SideEffect`, and `Action`. The public schema models live in
`agents_shipgate.schemas.capabilities`; internal builders remain under
`core.*`. The main type, `CapabilityFactV1`, groups stable semantic
identity, normalized effect, authority, controls, source evidence, risk
tags, and separate identity / effect / authority / control / schema /
risk / evidence hashes. The hashes use capability-specific canonical JSON
so they do not inherit the finding fingerprint exclusion list. It is the
substrate for stable capability locks, richer semantic diffs, policy
matching, and governance benchmark assertions.

**Boundary.** `CapabilityFactV1` records are emitted through capability
locks, not `report.json`, and do not gate release. The release decision
remains `release_decision.decision`; public report surfaces remain
projections of the scan pipeline. v0.23 uses the shared semantic delta
classifier to add explanatory metadata to `capability_change` members,
but the existing buckets and Action outputs stay compatible. Capability
facts are built from typed `Action` objects via `build_capability_facts(...)`
for locks, and from public `ActionFact` snapshots for report-comparable
semantic deltas.

## Capability-native policy matching

`core/capability_policy.py` builds an internal `CapabilityPolicySubject`
for each `CapabilityFactV1`. The subject pairs the durable capability fact
with the existing `ActionFact`, `Tool`, parameter schema, and effective
controls that built-in policy checks and policy packs already need.
Policy matching now runs through this substrate instead of raw `Tool`
field predicates, while legacy policy-pack syntax keeps the same behavior.

Reports expose only lightweight audit references:
`findings[].capability_refs`, optional
`findings[].capability_policy_evidence`, and mirrored
`release_decision.{blockers,review_items}[].capability_refs`. These fields
are not fingerprint inputs and do not create an independent verdict.
`release_decision.decision` remains the only gate. Packet schema `0.7`
adds report-derived capability trace evidence metadata, but runtime trace
evidence stays out of static capability locks.

## Capability standard and locks

`agents-shipgate capability export` builds a stable local capability lock
from the same static source-loading path used by scans, but stops after
enriched tools and typed `Action` objects are available. It does not run
findings, write `report.json`, invoke `verify`, or produce a release
decision. By default it writes the reviewed envelope to
`.agents-shipgate/capabilities.lock.json` and a byte-identical generated
copy to `agents-shipgate-reports/capabilities.lock.json`.

`agents-shipgate capability diff --base ... --head ...` compares two
lockfiles by `CapabilityFactV1.id`. Semantic hash drift on a stable id
(`effect`, non-scope `authority`, `control`, `schema`, or `risk`) is
reported as `changed` with `semantic_direction` and `semantic_changes`;
source-provenance-only drift is reported as `evidence_changed`.
Scope/resource changes intentionally re-identify a capability because
scope is part of durable identity, so the diff pairs same
agent/provider/operation/tool rows and reports them as `reidentified`
instead of unrelated add/remove churn. Added and removed capability facts
are listed separately.

The v0.2 lock is an enumerable-tools envelope. Dynamic toolkit scope
bounds parsed from factories are counted in `source.toolkit_bound_count`
but are not yet emitted as capability facts, so widening a dynamic
factory's authority bound is a known limitation until a later phase
adds non-enumerable authority facts. The current schema is
[`capability-lock-schema.v0.2.json`](capability-lock-schema.v0.2.json);
diff artifacts use
[`capability-lock-diff-schema.v0.3.json`](capability-lock-diff-schema.v0.3.json).
Both carry `experimental: false`. Old experimental v0.1 lock inputs
remain readable by `capability diff`, but new exports use v0.2.
Capability locks are not part of `report.json`, do not include runtime
trace evidence, and do not gate. The committed lock is deterministic for
the same manifest-relative inputs; `cli_version` is provenance and may
change on scanner upgrades. The release decision remains
`release_decision.decision`. The public spec is
[`capability-standard.md`](capability-standard.md).

## Governance benchmark substrate

`benchmark/agent-pr-governance/` is the executable eval substrate for the
capability model. Its v0.2 catalog distinguishes executable rows from
catalog-only and external-evidence backlog rows. Executable cases materialize
small base/head git repos, run the real verifier, export base/head capability
locks, compare them through the shared capability-lock diff engine, and assert
both gate behavior and `CapabilityFactV1` semantic deltas.

The internal runner is `python scripts/run_governance_benchmark.py --catalog
benchmark/agent-pr-governance/cases.yaml --json`. Benchmark orchestration lives
in the script layer, not in `src/agents_shipgate`, so the eval harness does not
ship in the scanner package or expand the audited scanner trust surface. Git
fixture materialization reuses the existing fixture helper rather than adding a
benchmark-specific subprocess call site. The runner emits deterministic
`governance_benchmark_result_schema_version: "0.2"` JSON with no wall-clock
timestamp and `experimental: false`. The benchmark is research infrastructure
only: it does not add public report fields, policy behavior, GitHub Action
outputs, or a second verdict. `report.json.release_decision.decision` remains
the only release gate. See [`governance-benchmark.md`](governance-benchmark.md).

## Reviewer surfaces: five lenses + three audit envelopes

The emitted `report.json` and the Release Evidence Packet expose
**five reviewer lenses** (each answering a different question) and
**three audit envelopes** (each tracing a separate trust event class).
None of the lenses gate the release decision by themselves; the
release decision is computed in `ci/release_decision.py` and surfaces
in `release_decision.{decision, blockers, review_items, …}`.

| Lens | Asks | Where in report.json | Where in report.md / packet |
|---|---|---|---|
| **Tool Surface Diff** (v0.10) | What inventory/schema/scope/metadata changed since the base? | `tool_surface_facts` + `tool_surface_diff` | `## Tool Surface Summary` / `## Tool Surface Diff` |
| **Capability/Intent Diff** (v0.9) | Does observed capability match declared purpose? | `capability_facts[]` + `declared_intentions[]` + `misalignments[]` + `release_consequence` + `suggested_scenarios[]` | `## Capability <-> Intent Diff` |
| **Action Surface Diff** (v0.16) | What can the agent do, under what controls? | `action_surface_facts` + `action_surface_diff` | `## Action Surface Diff` |
| **Policy Audit** (v0.17) | Who weakened the gate, and why? | `policy_audit.severity_overrides_applied[]` | `## Policy Audit` |
| **Evidence Matrix** (v0.6 packet) | Which release dimensions have coverage, gaps, or open review? | (packet) `evidence_matrix.rows[]` | Packet §1A (13 domain rows) |

Tool surface = *registry* (what exists / what changed). Capability/intent
= *governance* (does observed match declared). Action surface =
*authorization* (what can the agent do, under what controls). Policy
audit = *trust events on the gate itself*. Evidence matrix = *coverage
map across the 13 readiness dimensions*. Among these reviewer lenses,
only **Action Surface Diff** sets `Finding.blocks_release=True` — the
other four are inputs to `release_decision` or explanatory only.

Three additional release-blocking signal sources exist outside the
lens taxonomy and route through the same `blocks_release` flag:

- **Policy-pack rules** (`inputs/policy_packs.py`) emit findings with
  `blocks_release=rule.block` — user-declared YAML rules must
  explicitly set `block: true` to block the release (`block` defaults
  to `false`; see `schemas/policy_pack.py`).
- **Baseline integrity** (`checks/baseline_integrity.py`) sets
  `blocks_release=True` on `SHIP-BASELINE-INTEGRITY-MISMATCH`
  findings when `baseline.integrity_mode: strict` is declared in the
  manifest (the scan-time mismatch path). The standalone
  `baseline verify --strict` command uses a CLI flag and exits with
  code 6 instead of setting `blocks_release`.
- **Action-surface policies** declared in `manifest.action_surface.policies[]`
  emit `SHIP-ACTION-POLICY-VIOLATION` at the user-declared severity
  with `blocks_release` set by the lens.

Three audit envelopes record trust events:

| Audit | What it traces | Surface |
|---|---|---|
| **Policy Audit** (v0.17) | Every `checks.severity_overrides` applied: default → applied severity, tier-crossing flag, ack reason, expiry | `report.policy_audit.severity_overrides_applied[]` |
| **Privacy Audit** (v0.18) | Every secret-like value redacted before output | `report.privacy_audit.redacted_paths[]` |
| **Baseline Audit Log** (v0.11 / M2) | Every `baseline save` event: SHA-256 hash before/after, added/removed fingerprints | JSONL file at `<baseline>.parent/baseline-audit.log` |

For a coding agent reading the report, the one-fetch projection is
`agent_summary` (v0.12) for the action-driven view and
`release_decision.contribution_rules[]` (v0.17) for the per-finding
gate-classification audit.

For reviewer triage, `reviewer_summary` (v0.20) mirrors
`release_decision.decision` and projects lens/audit activity counts plus
`first_recommended_surface`.

For security/GRC reviewers who want declared-only findings,
`agents-shipgate scan --no-heuristics` (v0.21) marks
`keyword_heuristic` and `regex_heuristic` findings as suppressed
before the release decision is built. Filtered findings stay in
`findings[]` for audit but no longer gate release. The
`report.heuristics_filter` envelope records `enabled`,
`excluded_provenance_kinds`, `filtered_finding_count`, and a
per-kind breakdown — the audit pass for the filter. Earns the
contract weight of `Finding.provenance_kind` (shipped v0.15) by
giving it a first-class CLI consumer.

## Determinism

Two non-negotiable invariants:

1. **No network calls in core code paths.** Inputs are local files.
   Plugins can opt-in but are off by default.
2. **Same inputs → same report.** Findings appear in stable
   check-execution order; per-finding fingerprints are deterministic
   (excluding timestamps) and serve as the baseline key.

Coverage:

- **Schema roundtrip** ([`tests/test_schema_roundtrip.py`](../tests/test_schema_roundtrip.py))
  — `python scripts/generate_schemas.py --check` rejects any drift
  between live Pydantic models and committed `docs/*-schema.v0.N.json`.
- **Schema-layer isolation** ([`tests/test_schema_boundaries.py`](../tests/test_schema_boundaries.py))
  — AST scan rejects schemas importing core, or code importing the
  pre-refactor module locations.
- **Adapter contract** ([`tests/test_adapter_contracts.py`](../tests/test_adapter_contracts.py))
  — every tool-emitting adapter produces byte-identical
  `ActionSurfaceFacts` JSON across runs.
- **Public-surface drift** ([`tests/test_public_surface_contract.py`](../tests/test_public_surface_contract.py))
  — multiple parametrized surface sets: 10 `PUBLIC_SURFACES` (README,
  AGENTS.md, llms.txt, .well-known, skills, prompts, docs/faq, etc.)
  tested for naming canonicalization and positioning, plus a broader
  `ACTION_PIN_FILES` superset adding CI examples and docs with
  version-pin assertions.
- **Property-based loader tests** (Hypothesis) in
  [`tests/test_property_loaders.py`](../tests/test_property_loaders.py)
  fuzz the input adapters with generated manifests and tool-source
  shapes.
- **Fingerprint stability** in
  [`tests/test_findings.py`](../tests/test_findings.py) pins the
  report builder's deterministic fingerprint contract.

## Trust model — five enforcement axes

The public claim "the scanner does not execute or import user code"
is structurally enforced, not asserted. See
[`STABILITY.md` § Trust-model invariants](../STABILITY.md#trust-model-invariants)
for the full contract. Five axes:

1. **AST trust lint** ([`tests/test_adapter_static_only.py`](../tests/test_adapter_static_only.py))
   — every `.py` under `src/agents_shipgate/` is statically scanned
   for `exec`/`eval`/`__import__`/`compile`/subprocess/`importlib.*`
   surfaces. Four first-party meta-CLI uses are pinned per call site
   (line + `ast.unparse` snippet) in `ALLOWED_EXCEPTIONS`. Runs as a
   dedicated CI step *before* the main test suite. Companion live-load
   tests in [`test_fixture_no_import.py`](../tests/test_fixture_no_import.py)
   verify `sys.modules` snapshots stay clean.
2. **Plugin validation** ([`checks/plugin_validation.py`](../src/agents_shipgate/checks/plugin_validation.py))
   — six load-time gates (load, signature, metadata,
   `dynamic_default_not_supported`, id_collision, bad_floor) plus a
   runtime finding-id smuggling guard. Default lenient;
   `--strict-plugins` exits 4 on any failure.
3. **Severity-override floor** (M1) — `CheckMetadata.floor_severity`
   is a hard contract; `acknowledge_overrides` does NOT bypass it.
   Tier-crossing downgrades require an ack with a reason; expired
   acks fail manifest load with exit 2. Applied overrides land in
   `policy_audit.severity_overrides_applied[]`. See
   [`STABILITY.md` § Severity-override floor](../STABILITY.md#severity-override-floor).
4. **Baseline integrity** (M2) — every baseline finding carries a
   `provenance` block (`scanner_version`, `run_id`, `recorded_at`,
   optional `reason`/`expires`). Every `baseline save` appends to
   `<baseline>.parent/baseline-audit.log` with SHA-256 hash before/after.
   `agents-shipgate baseline verify` is a static integrity check
   (no scan needed) — exit 6 on `SHIP-BASELINE-INTEGRITY-MISMATCH`
   in strict mode. See
   [`STABILITY.md` § Baseline Integrity](../STABILITY.md#baseline-integrity-v05).
5. **Privacy redaction** — piecemeal `core/privacy` functions
   (`sanitize_model`, `redact_data`, `sanitize_findings`,
   `sanitize_tools`) run on every public field before any
   JSON/Markdown/SARIF/HTML/PDF/GitHub step-summary write. Eight
   pattern families plus sensitive-key forcing; stats accumulated into
   `build_privacy_audit` and recorded in `report.privacy_audit`.
   See [`STABILITY.md` § Privacy and redaction](../STABILITY.md#privacy-and-redaction).

Sub-invariants the lint enforces: no `subprocess.run` on user code
(four pinned meta-CLI uses only — bootstrap chains the CLI, discovery
runs `git ls-files`, triggers runs `git diff`, self-check `__import__`s
a curated module list); no `importlib.resources.<attr>(...)` calls
without a per-call-site allowlist with literal-anchor snippet;
files outside the manifest directory rejected (path-traversal
containment); files larger than 10 MB rejected.

## Release Evidence Packet (v0.7)

`scan` emits a reviewer-shaped artifact alongside `report.{md,json,sarif}`
whenever `output.packet.enabled` is true (default). The packet has its
own JSON contract ([`packet-schema.v0.7.json`](packet-schema.v0.7.json))
so the report schema stays minimal.

The packet is derived from the in-memory scan (manifest, tools,
findings, release decision, per-source policy artifacts) and persisted
as `packet.{md,json,html}`. PDF (`packet.pdf`) is opt-in via the
`[pdf]` extras. The standalone command
`agents-shipgate evidence-packet --from <input>` accepts either form:
`packet.json` re-renders the original full-fidelity packet, while
`report.json` rebuilds a degraded packet without the manifest's
declared coverage (per-source `policy_rules`, `permissions.scopes`).
§10 of every rebuilt packet carries an explicit note about the
degradation so reviewers are not misled.

Four rules govern the packet contract:

1. **Derived from JSON.** The packet is a deterministic function of
   the scan; nothing dynamic is invoked at packet-build time.
2. **Local artifact.** Output is files in `agents-shipgate-reports/`.
   No hosted UI, no SaaS, no telemetry.
3. **Explicit non-proofs.** §10 lists, on every emitted packet, the
   four things the packet does not prove: prompt robustness, runtime
   behavior, model correctness, adversarial resistance.
4. **Reviewer-readable.** All 13 sections are always present.
   §1A (the v0.6 **evidence matrix**, 13 reviewer-domain rows) gives a
   compact coverage view across Inventory, Schema, Auth, Approval,
   Confirmation, Idempotency, Side effects, Memory isolation, HITL,
   Prompt/scope, Retry/timeout, Baseline debt, and Action-surface
   policy.

The builder lives in `packet/builder.py`; renderers under the same
package keep the JSON model and rendered formats independent.

## Adding a new input adapter

Adapters live under `src/agents_shipgate/inputs/`. Every adapter
implements the `ToolSourceAdapter` protocol and registers with
`inputs/protocol.py:REGISTRY`. See
[`framework-adapter-checklist.md`](framework-adapter-checklist.md) for
the full checklist.

1. Create `src/agents_shipgate/inputs/<adapter>.py`. Define a class
   with `source_type: ClassVar[str]`, `scope: ClassVar[Literal["per_source", "per_scan"]]`,
   `artifact_class: ClassVar[type | None]`, and a `load()` method
   returning `LoadedAdapterResult`.
2. Reuse helpers from `inputs/common.py` (`load_structured_file`,
   `resolve_input_path`, `schema_to_parameters`, `stable_tool_id`).
3. Add the class to `_register_builtin_adapters()` in
   `inputs/protocol.py`.
4. Add the source type to `core/risk_hints.py:_KEYWORD_GATED_SOURCE_TYPES`
   so name-keyword classification fires.
5. Add a sample fixture under `samples/` and golden expected reports.
6. Add tests in `tests/test_<adapter>.py`. The contract test
   `test_tool_emitting_adapters_produce_normalized_tools_and_action_facts`
   will pick up the new adapter automatically and require
   byte-identical idempotency on the `ActionSurfaceFacts` projection.
7. **The AST trust lint will reject any `import`, `exec`, or
   subprocess call that touches user code.** Adapters parse with
   `ast.parse` / `yaml.safe_load` / `json.loads` only.
8. For framework adapters (Python-source extraction), follow
   [`framework-adapter-checklist.md`](framework-adapter-checklist.md).

## Adding a new check

1. Write the check function in `checks/<category>.py` with signature
   `(ScanContext) -> list[Finding]`. Use the `tool_finding()` /
   `agent_finding()` factories in `checks/base.py`; both require a
   `provenance_kind` kwarg. Register the callable in
   `checks/registry.py:BUILTIN_CHECKS` (canonical run order).
2. Declare the metadata in `docs/checks/<category>.yaml`. The
   filename **is** the check's `category` (the loader injects it,
   and rejects any per-row `category` that disagrees). `docs_url`
   is also loader-derived from the check id; do not set it in YAML.
   Every other `agents_shipgate.schemas.checks.CheckMetadata` field
   maps 1:1 onto a YAML key. For release-critical checks, declare
   `floor_severity` (severity below which a `severity_override`
   cannot apply, even with an acknowledgement). For checks whose
   emitted severity depends on user-declared manifest values
   (e.g. action-surface policies), declare `dynamic_default: true`
   AND add an overlay branch in
   `core/dynamic_defaults.py:dynamic_check_defaults` — the model
   validator requires the floor; the contract test
   `test_dynamic_default_aggregator_overlay_fires` requires the
   overlay. See [`STABILITY.md` § dynamic-severity check classes](../STABILITY.md#severity-override-floor).
3. Add a test in `tests/`.
4. Document in `docs/checks.md` (human-maintained prose) and
   regenerate the machine catalog with
   `python scripts/generate_schemas.py` (writes
   `docs/checks.json`).
5. **Do not change check IDs in published versions.** Always add
   new ones; legacy IDs may expand to new IDs via
   `core/check_ids.py:expands_to_check_id`.

The YAML-driven catalog is loaded once at registry import time by
`agents_shipgate.checks._metadata_loader.load_check_metadata()`.
Duplicate ids across files, mismatched per-row `category`, explicit
per-row `docs_url`, missing `id`, malformed top-level shape, and
Pydantic validation errors all raise `ValueError` with the file
path and offending check id — the catalog is a wire-stable
contract (`docs/checks.json`), so schema deviations fail at import
time, not at scan time.

## Adoption harness (developer-only)

`harness/adoption/` (not packaged in the wheel) drives realistic
cold-agent flows across 8 benchmark repos (OpenAI Agents SDK, MCP,
OpenAPI, LangChain, Google ADK, CrewAI, n8n, and a clean read-only
repo) plus a `non-agent-negative-control` harness context (9 entries
total). Success measured against a 100-point
rubric in [`agent-adoption-harness.md`](agent-adoption-harness.md):
correctly deciding relevance, installing the CLI, writing a valid
`shipgate.yaml`, reading `release_decision.decision`, wiring CI,
respecting the autofix-boundary, and not false-positiving on
docs-only repos.

The CLI is `python -m harness.adoption`. Five subcommands:

- **`smoke`** — mock-driver pipeline end-to-end. No live API calls.
  Used by PR CI for adoption-readiness regression.
- **`run --matrix <path.yaml> [--agent <names>] [--budget-usd <cap>]`**
  — execute the full pipeline against the matrix file (defaults to
  `benchmark/matrix.yaml`). May invoke real agent drivers depending on
  matrix configuration; `--budget-usd` (default `20.0`) caps cumulative
  cost and aborts on overrun. Per-cell scorecards land under
  `.agents-private/adoption-sprint/` and a CSV row at
  `benchmark/results/<run-id>.csv`.
- **`score`** — re-run detectors against a previous run's captured
  artifacts.
- **`report --results-csv <path>`** — print a human-readable summary
  of a results CSV without recomputing detectors.
- **`sync-fixtures`** — materialize `benchmark/repos/*` from in-repo
  sources.

For CI-safe quick checks, `python -m harness.adoption smoke` is the
right entry. For matrix-wide runs, populate `benchmark/matrix.yaml` and
invoke `run` directly.

## Stability contract

See [`../STABILITY.md`](../STABILITY.md) for the full per-field
contract. Headlines:

- **Manifest schema** stable across `0.x` (`version: "0.1"`).
- **Report JSON shape** is additive across the `0.x` line. Current
  `report_schema_version: "0.27"`; older schemas frozen as
  `docs/report-schema.v0.N.json`.
- **Packet JSON shape** is additive across the `0.x` line. Current
  `packet_schema_version: "0.7"`; older schemas frozen.
- **Exit codes**: `0` pass, `2` manifest config error, `3` input
  parse error, `4` other error, `6` baseline integrity failure (strict
  `baseline verify` only), `20` strict-mode gate failure.
- **Check IDs** never change in published versions; legacy aliases
  expand via `core/check_ids.py`.
- **Gating signal** is `release_decision.decision`. Do not gate on
  `summary.status` — it is preserved baseline-blind for v0.7 callers
  only.

## Related reading

- [`agent-contract-current.md`](agent-contract-current.md) — agent-facing
  field index; updates first when the contract bumps.
- [`STABILITY.md`](../STABILITY.md) — the per-field stability contract.
- [`concepts.md`](concepts.md) — Tool-Use Readiness in seven dimensions.
- [`category.md`](category.md) — what an "agent release gate" is.
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema reference.
- [`checks.md`](checks.md) — check catalog + per-check anchors.
- [`framework-adapter-checklist.md`](framework-adapter-checklist.md)
  — full per-adapter trust contract.
- [`agent-adoption-harness.md`](agent-adoption-harness.md) — adoption
  rubric + harness usage.
- [`AGENTS.md`](../AGENTS.md) — agent-facing instructions.
- [`ROADMAP.md`](../ROADMAP.md) — release planning.
