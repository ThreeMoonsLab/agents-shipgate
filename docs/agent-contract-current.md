# Current Agent Contract

The single, current statement of what AI coding agents and CI integrations should read from Agents Shipgate output. When the contract changes, update [STABILITY.md](../STABILITY.md) first, then this file. Other agent-facing surfaces (`AGENTS.md`, `llms.txt`, `.well-known/agents-shipgate.json`, the slash command, the skill, the FAQ) link here instead of restating field lists.

## Current versions

Verify the installed CLI contract locally before relying on hard-coded docs:

```bash
agents-shipgate contract --json
```

Runtime contract v24 rolls the control envelope across the setup commands.
`detect --json`, `init --json`, and every `doctor --json` payload now carry a
`control` field holding the same `shipgate.agent_control/v1` object that
`verify`, `check`, and `agent control` emit, so one vocabulary answers "what may
I do next" for the whole adoption walk. Two things keep the setup family
distinguishable from the gate:

- **Setup names its own source.** These commands run before a release decision
  exists, so they report `decision_source: "setup"` and a `decision` from the
  closed setup vocabulary `setup_complete | setup_incomplete |
  setup_not_applicable`. The published schema enforces the pairing both ways: a
  setup source can only come from `detect`/`init`/`doctor`, and those operations
  can report no other source. `decision_source: "release_decision"` still means,
  and only means, `release_decision.decision`.
- **Setup authorizes nothing.** No setup command reads a diff, so every field of
  `permissions` is `false` on every setup envelope, no setup envelope binds an
  artifact or a `current_control_id`, and `control_state: "complete"` is
  unreachable for these operations in the schema itself. Setup routes; it never
  finishes a task.

`agent control` on a `scan` generation publishes **no** release verdict. `scan`
reaches one, but its pointer records no HEAD, no worktree overlay, and no input
set, so nothing about that verdict can be reconfirmed against the workspace as it
stands: editing the manifest, a `tools.json` it references, a policy pack, or a
baseline leaves the pointer reading cleanly. What the envelope carries instead is
`reason`, stating why there is no verdict — which is what keeps it
distinguishable from an envelope produced before any engine ran, and is the
ambiguity #323 set out to remove. Run `verify` for a verdict a reader can check.

The `AgentControl` union is **unchanged**, and `minimum_control_contract_version`
stays at `21`. That union is embedded by the verifier, the handoff, preflight,
the agent result, the boundary result, and verify-run, so widening it would widen
six durable published schemas under unchanged identifiers — and five of those
artifacts record no `contract_version`, so a consumer holding a stored payload
could not use the floor to tell which shape it has.

A setup step that needs a file changed is still *typed*, though: the envelope
publishes `next_action.kind: "edit"` with `path` and `expects`, as
`SetupEditAction` — declared on the envelope, which is stdout-only, and rejected
in both layers on any non-setup operation. Routing such a step as the command
that merely *checks* the edit was tried and is wrong: an envelope-only consumer
executing it re-ran `doctor` against an unchanged file forever.

What v24 widens is `shipgate.agent_control/v1` itself, which is emitted on
stdout and never written as an artifact: there are no stored envelopes to
disambiguate, and its new operations cannot appear in anything a v21 consumer
holds.

A human-owned manifest declaration is never published as a coding-agent edit.
When `shipgate.yaml` still holds an unresolved `declared_purpose`, policy, or
permission placeholder, the setup control state is `human_review_required` and
the action names the exact file, line, and field a person must fill in. The
command-specific `next_action` / `next_actions[]` fields are unchanged, and
remain supported.

Runtime contract v23 spells every emitted command for the invocation that
produced it. A run started with `python -m agents_shipgate` now proposes
`<sys.executable> -m agents_shipgate ...` instead of a console script its
environment may not have; a console-script run is unchanged. Actions with
`kind="command"` in `next_actions[]` also carry `executable[]` and `args[]` —
**the authoritative runnable form on every platform**; run them as
`[*executable, *args]` with no shell. The pair is computed from `command` and
cannot be supplied, so the two forms cannot disagree, and it is omitted (not
`null`) whenever the command has no faithful argv form. `command` itself is a
POSIX rendering for display and POSIX shells. When the rank-1 action is a
command, the legacy `next_action` string is that command verbatim. Set `AGENTS_SHIPGATE_CLI` to
name the entry point explicitly. Durable evidence artifacts (`report.json`,
`packet.*`) stay canonical: "same inputs, same report" outranks runnability
there, and process-entry spelling is not an input.

Runtime contract v22 publishes `shipgate.agent_control/v1`, the compact control
envelope. It is a **projection of the control state, not a second decision**:
every field is copied from a producer that already published it, and the
schema's validators only assert that the copies cannot contradict each other.
One object answers the whole routing question — tool execution status, the
release or boundary decision and which engine made it, the control state, the
six-way `permissions` vector, who acts next, the exact next action, and the
content-addressed path and hash of every artifact `current-control.json`
binds — not every file a run writes, and none at all from `check`, which
publishes no pointer. It is emitted by
`agents-shipgate verify --format control`, `agents-shipgate check --format
agent-control-json`, and `agents-shipgate agent control` (now its default
output; `--format pointer` returns the raw pointer). It publishes a size *budget*,
`agent_control_budget_bytes` (4096), that representative output meets and that
is pinned by tests; it is not a hard maximum, because a long required-reviewer
list or an unusually long exact command must never be truncated to hit a size
target. Free-text fields are capped at 400 UTF-8 bytes; commands, paths, hashes,
and reviewer names never are. It is never written to disk — the artifacts it
names stay where they are.

The envelope is a **discriminated union on `control_state`**, like the
`AgentControl` union it projects, so the published JSON Schema — not only
Pydantic — rejects a contradictory payload: `execution: "failed"` beside
`control_state: "complete"`, a coding-agent route on a stopping state, a
`review_publishable` that denies publication, or merge authority outside
`complete`.

Three separations are structural, not documentary. `execution` says whether the
tool ran; `decision` says what the gate decided; `permissions` says what the
agent may do. A failed run can never authorize completion, but a *succeeded* one
carries no implication at all.

Both entry points apply one currency test. `verify --format control` reads the
pointer it just published through the generation-safe protocol, validated
against the live workspace, and withholds authority when the workspace has moved
past what the run evaluated; the route comes from the verifier bytes captured
inside that read, so a pointer is never reported beside another generation's
decision. `artifacts[].path` is relative to the directory the command was
invoked from. `exit_code` reports the CI gate signal, which is
mode-dependent — in advisory mode a `blocked` decision still exits 0 — so
`permissions.merge` is the only field that answers "may I merge". The full
`verifier.json` remains the authoritative substrate and is unchanged;
`verify --json` still emits it.

Runtime contract v20 adds `agents-shipgate-reports/current-control.json`,
the one atomic entry point that says which control identity is current. It is a
pointer, not a second decision: it binds identities and hashes the receipt,
handoff, verifier, and report already published. Every run replaces it with a
non-terminal `unavailable` marker before touching any other artifact, and
publishes the terminal pointer atomically, last. Consumers must re-read it at
every boundary in `agent_refresh_triggers` — after any human or external-tool
action, after any worktree change, after any command returns, before enforcing a
cached `must_stop`, before commit/push/PR update, before merge, and before
declaring the task complete. A control state remembered from earlier in a
conversation never outranks the pointer, in either direction: it can neither
keep blocking after a newer complete run exists, nor authorize action after the
workspace moved.

Runtime contract v21 separates publish authority from merge authority.
`control.permissions` is a required object on every state with the exact
booleans `edit`, `commit`, `push`, `update_pr`, `merge`, `report_complete`,
fixed by the state and never set independently, and the new
`control.state: "review_publishable"` means "a human must approve the merge,
and the agent may still commit, push, and update the pull request to obtain
that review". `human_review_required` keeps its exact old meaning — nothing is
authorized — and is now reserved for results Shipgate cannot vouch for: a
`block` decision, a failed run, unreadable or unbindable diff input, an
undeclared surface with no discovery route, and preflight protected-surface
touches. `merge` and `report_complete` always equal `completion_allowed`, so
human review never becomes self-approvable.

Runtime contract v19 grades the LOCAL boundary stop: a `require_review`
violation set that is entirely low/medium risk projects
`control.state: "agent_action_required"` with the exact verify command, and
the review obligation is carried in the additive
`pending_review[]` field on the agent-boundary result instead of ending the
turn. Block actions, critical risk, gate-weakening rules
(`CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED`), unparseable content,
incomplete input, experimental surfaces, and every gate-governing trust-root
class (manifest, policy, ci_gate, shipgate_state) keep the human route; as of
v20 an evaluated `require_review` route is `review_publishable` and a `block`
route is `human_review_required`. PR-time `release_decision` semantics are
unchanged. It retains the v18 human-authorization overlay, the v17
content-addressed verification identity,
v16 typed policy-evidence, v15 host-neutral
boundary, v14 unambiguous `AgentControl`, and v13 root-reachable binding
contracts. v18 added a signed, externally rooted human-authorization overlay for
one exact post-review coding-agent action. Agents
switch on `control.state`; `decision` remains diagnostic and
`release_decision.decision` remains the release gate. Contract v14 requires
`completion_allowed == (state == "complete")` and
`must_stop == (state == "human_review_required")`. Report v0.34, packet v0.12,
verifier v0.6, verify-run v3, and handoff v6 bind their projections to the
same request and decision IDs. The terminal receipt hashes the complete
artifact set; see [Verification Identity and Reproduction](verification-reproducibility.md).
The runtime contract also exposes the local agent command spec:
`primary_commands{}`, `commands{}`, `default_paths{}`, `artifacts{}`,
`agent_read_order[]`, `verifier_read_order[]`, `merge_verdicts[]`,
`release_decisions[]`, `do_not_auto_assert[]`, `verifier_schema_version`,
`verify_run_schema_version`, `verification_plan_schema_version`,
`verification_unit_result_schema_version`,
`verification_artifact_manifest_schema_version`,
`verification_receipt_schema_version`,
`human_authorization_request_schema_version`,
`human_authorization_schema_version`,
`human_authorization_evaluation_schema_version`,
`human_authorization_trust_policy_schema_version`,
`human_authorization_trust_policy_default_path`,
`human_authorization_schema_path`, `agent_handoff_schema_version`,
`agent_handoff_schema_path`, `agent_handoff_artifact`,
`agent_boundary_result_schema_version`, the deprecated
`codex_boundary_result_schema_version`, `attestation_schema_version`,
`registry_schema_version`, `org_evidence_bundle_schema_version`,
`host_grants_inventory_schema_version`, `host_grants_baseline_schema_version`,
`host_grants_drift_schema_version`, `trigger_catalog_schema_version`,
`agent_interface_operations[]`,
`exit_code_policy`, `mcp_tools[]`, `minimum_control_contract_version`,
`agent_control_fields[]`, and `agent_control_states[]`. The legacy
`agent_result_*` fields are retained only for older protocol readers.
`primary_commands{}` is the prominent
entry surface and contains only `shipgate check`, `agents-shipgate verify`, and
`shipgate audit --host` flows; `commands{}` is compatibility/supporting metadata
and retains local verify commands for older consumers.
The short `shipgate verify` alias remains invokable for compatibility, but it is
not the promoted PR-gate spelling in `primary_commands{}`.
Contract v11 adds `action_effect` and `action_authority` to
`do_not_auto_assert[]`. They are reviewed human claims that can close semantic
evidence gaps; an agent may route the structured next action but must never
invent or auto-fill either declaration.
Downstream repos generated with
`init --agent-instructions=default` get the minimal local copy at
`.shipgate/agent-contract.json`.

- Latest release: `v0.15.0`
- In-tree runtime: `0.16.0b7` — see [pyproject.toml](../pyproject.toml)
- Runtime contract: `24` (minimum control contract: `21`)
- Current report schema: `0.34` — [`docs/report-schema.v0.34.json`](report-schema.v0.34.json)
- Current packet schema: `0.12` — [`docs/packet-schema.v0.12.json`](packet-schema.v0.12.json)
- Current shared agent result schema: `agent_result_v3` — [`docs/agent-result-schema.v3.json`](agent-result-schema.v3.json)
- Current verifier schema: `0.9` — [`docs/verifier-schema.v0.9.json`](verifier-schema.v0.9.json) (v0.8 and earlier stay frozen; `0.9` adds `capability_review.policy_weakening_proven`)
- Current verify-run schema: `shipgate.verify_run/v4` — [`docs/verify-run-schema.v4.json`](verify-run-schema.v4.json)
- Current verification identity schemas: [`plan v1`](verification-plan-schema.v1.json), [`unit result v1`](verification-unit-result-schema.v1.json), [`artifact manifest v1`](verification-artifact-manifest-schema.v1.json), and [`terminal receipt v1`](verification-receipt-schema.v1.json)
- Current control pointer schema: `shipgate.current_control/v1` — [`docs/current-control-schema.v1.json`](current-control-schema.v1.json)
- Current agent control envelope schema: `shipgate.agent_control/v1` — [`docs/agent-control-schema.v1.json`](agent-control-schema.v1.json)
- Current human-authorization schemas: request, signed grant, verifier evaluation, and external trust policy v1 — [`docs/human-authorization-schema.v1.json`](human-authorization-schema.v1.json)
- Current agent handoff schema: `shipgate.agent_handoff/v7` — [`docs/agent-handoff-schema.v7.json`](agent-handoff-schema.v7.json)
- Current agent boundary result schema: `shipgate.agent_boundary_result/v2` — [`docs/agent-boundary-result-schema.v2.json`](agent-boundary-result-schema.v2.json)
- Frozen deprecated Codex projection: `shipgate.codex_boundary_result/v2` — [`docs/codex-boundary-result-schema.v2.json`](codex-boundary-result-schema.v2.json)
- Current preflight schema: `0.4` — [`docs/preflight-schema.v0.4.json`](preflight-schema.v0.4.json)
- Current downstream local agent contract schema: `10`
- Current capability standard: `0.5` — [`docs/capability-standard.md`](capability-standard.md)
- Current capability lock schema: `0.6` — [`docs/capability-lock-schema.v0.6.json`](capability-lock-schema.v0.6.json)
- Current capability lock diff schema: `0.7` — [`docs/capability-lock-diff-schema.v0.7.json`](capability-lock-diff-schema.v0.7.json)
- Current attestation schema: `0.5` — [`docs/attestation-schema.v0.5.json`](attestation-schema.v0.5.json)
- Current registry schema: `0.4` — [`docs/registry-schema.v0.4.json`](registry-schema.v0.4.json)
- Current org evidence bundle schema: `shipgate.org_evidence_bundle/v2` — [`docs/org-evidence-bundle-schema.v2.json`](org-evidence-bundle-schema.v2.json)
- Current host-grants inventory, baseline, and drift schemas: `0.2` — [`inventory`](host-grants-inventory-schema.v0.2.json), [`baseline`](host-grants-baseline-schema.v0.2.json), [`drift`](host-grants-drift-schema.v0.2.json)
- Current trigger catalog schema: `0.3` — [`docs/triggers.json`](triggers.json)
- Current governance benchmark catalog schema: `0.2` — [`docs/governance-benchmark-catalog-schema.v0.2.json`](governance-benchmark-catalog-schema.v0.2.json)
- Current governance benchmark result schema: `0.2` — [`docs/governance-benchmark-result-schema.v0.2.json`](governance-benchmark-result-schema.v0.2.json)
- Frozen-reference report schemas: frozen [`v0.33`](report-schema.v0.33.json), frozen [`v0.32`](report-schema.v0.32.json), frozen [`v0.31`](report-schema.v0.31.json), frozen [`v0.30`](report-schema.v0.30.json), and older versions listed in [`docs/INDEX.md`](INDEX.md#reference)
- Frozen-reference packet schemas live in [`docs/INDEX.md`](INDEX.md#reference).
- Boundary v1, verifier v0.1–v0.5, verify-run v1/v2, handoff v1–v5, and preflight
  v0.1/v0.2 remain frozen references for legacy readers.
- Frozen experimental capability lock and governance benchmark result schemas live in [`docs/INDEX.md`](INDEX.md#reference).

## Two read entry points

Both start at `agents-shipgate-reports/current-control.json` (`agents-shipgate
agent control --workspace .`), which names the run that is current. Everything
below it describes *a* run; only the pointer says *which* run. A non-zero exit
from the reader means no control identity is current here and the caller holds
no authority — not that the previous answer still stands.

**The promoted read for a coding-agent control loop is one command.**
`agents-shipgate agent control --workspace .` runs the currency protocol and
returns the `shipgate.agent_control/v1` envelope, which already carries the
route the pointer deliberately omits. An agent that routes on `permissions` and
`next_action` from that one object never needs the artifact walk below; the walk
remains the contract for consumers that want the forensic detail, for CI, and
for anything reading the artifacts directly. A run can skip the second command
entirely with `agents-shipgate verify --format control`, which emits the same
envelope for the run it just performed.

Byte consistency is not generation consistency. A pointer whose artifacts all
still hash correctly can describe a workspace that one commit has moved past, so
the reader compares the bound `workspace_identity` against the live repository —
repository, HEAD commit, and HEAD tree — and refuses on any drift. Completion
authority is never returned without that comparison: a reader that cannot
resolve the workspace reports it as unverified rather than passing.

When the decision named a base, that base is compared too. A decision about
`base...HEAD` is a decision about that range, and advancing the base — a merge,
or a fetch moving `origin/main` — can empty the range without touching HEAD or
the working tree, leaving every HEAD-based check satisfied while the evidence
underneath has gone. The pointer therefore carries `base_ref`,
`base_commit_sha`, and `merge_base_sha`, and the reader resolves the ref live.

Uncommitted work is checked according to what the decision actually covered:

- A **worktree** decision (`snapshot_kind: "worktree_overlay"`) is re-checked
  two ways. Every path it covered must still hash to the overlay it committed
  to, and no path *outside* that set may differ from HEAD now — anything outside
  it was identical to HEAD when the decision was made, so a live change the plan
  never recorded is evidence the decision never saw. That second test is a
  subset test, not equality: `plan.inputs.changed_paths` is the union of
  `base...HEAD` and the worktree, not the uncommitted set, so requiring equality
  would refuse a clean workspace the moment the run that produced it finished.
- A **committed-tree** decision (`snapshot_kind: "committed_tree"`) stops at
  HEAD, so any uncommitted change appearing afterwards invalidates it — in both
  directions. A stale `complete` must not authorize work the decision never
  covered, and a stale `human_review_required` must not keep enforcing a
  pre-change stop. Re-running the same archived `--head` verification cannot
  clear that, so the refusal routes to a worktree verification instead.

An overlay row carries content *and* the two metadata axes Git itself tracks:
entry kind and the executable bit. Content alone is not the capability —
flipping a tool script from `100755` to `100644` changes no bytes, and swapping
a regular file for a symlink to an identical in-repo file changes no bytes
either. Full mode is deliberately not recorded: it varies with umask and would
make the identity depend on noise Git does not track.

Given a current pointer, there are two correct "read first" paths; which one
applies depends on who is reading. They are not two decisions — they are two
entry points into the same one decision engine.

- **PR / controller flow** — an autonomous coding agent deciding *continue,
  repair, or stop*. Prefer
  validate `agents-shipgate-reports/verification-receipt.json`, then read
  `agents-shipgate-reports/agent-handoff.json` for the compact
  `shipgate.agent_handoff/v7` view: lead with `control.state`, then read
  `control.next_action`, `gate.merge_verdict`, and `reproducibility.run_id` for the
  content-addressed verify identity. `verifier.json` remains the authoritative
  controller substrate and `verify-run.json` remains the detailed run
  projection; finally
  confirm `report.json.release_decision.decision` for the release gate.
  `.well-known/agents-shipgate.json` → `agent_read_order` is the
  machine-readable cross-artifact order. `verifier_read_order` remains the
  intra-`verifier.json` field order.
- **Gate / CI flow** — deciding pass/fail, or any raw `report.json` consumer.
  Read `agents-shipgate-reports/report.json` → `release_decision.decision` (the
  next section). `.well-known` → `gating_signal` names this signal.

`merge_verdict` is a deterministic projection of `release_decision.decision`, so
the two can never disagree.

### Command-scoped artifact lifecycle

Choose the read path from the command that just completed, not merely from
filenames already present in the output directory:

- After standalone `scan`, `report.json.release_decision.decision` is
  authoritative. `scan` writes report, advisory scaffold, and configured
  packet formats; it does not produce a verifier handoff or terminal receipt.
- After `verify`, validate `verification-receipt.json`, then read
  `agent-handoff.json` and the supporting verifier artifacts in the order
  above. The receipt and handoff retain the content-addressed identity of that
  exact verify run.

`current-control.json` records which of those two just happened in its
`operation` field, so the choice does not have to be inferred from filenames at
all. Only an `operation: "verify"` pointer can carry `control.state:
"complete"`, and only when it also binds a `verification_receipt` whose
`request_id` and `decision_id` are the ones the pointer records — the assembler
accepts any `--out` name under its artifacts root, so an older canonical receipt
must not be mistaken for the one a run just closed. A `scan` or `preview`
pointer is structurally incapable of authorizing completion or merge, and each
binds only the artifacts it actually wrote: a `scan --format markdown` after a
verify does not claim that verifier's `report.json`. While a run is in flight the pointer reads
`lifecycle_state: "in_progress"` with `control.state: "unavailable"`,
`must_stop: true`, so an interrupted or crashed run leaves a directory that
denies cached control rather than one that still authorizes it. Consumers built
before the pointer existed fall back through
`current_control_fallback_read_order`; the pointer's absence is evidence of an
older producer, never permission.

When standalone `scan` replaces a report set in the same output directory, it
removes the complete prior verifier route and its identity support:
`verifier.json`, `agent-handoff.json`, `pr-comment.md`, `verify-run.json`,
`verification-plan.json`, `verification-input.diff`,
`verification-base-report.json`, `verification-unit-result.json`,
`verification-artifacts.json`, `verification-receipt.json`, and
`human-authorization.json`. Their absence is intentional: an older controller
substrate, route, or receipt must never appear to authorize or describe the
newer scan. `verify` calls the same scan pipeline internally and writes a fresh,
mutually consistent verifier artifact set afterward. Supporting commands that
only need an in-memory report, including `baseline save`, scan into an isolated
temporary directory so they preserve the current report and verifier evidence.

## Primary vs supporting surfaces

Primary gates are intentionally narrow. CI gates on
`report.json.release_decision.decision`. Coding agents handling committed PRs
read `agent-handoff.json.control.state` first, with
`verifier.json.control`, `execution`, `applicability`, and `merge_verdict` as the
authoritative detailed substrate. Everything else in the
verifier/report/packet family is supporting review evidence or a convenience
projection.

Treat legacy `agent_result_v1` / `agent-result.json` compatibility surfaces,
runtime trace/evidence fields, the Release Evidence Packet, `reviewer_summary`,
`verifier_summary`, `capability_review`, non-gating capability diff
projections, and `agents-shipgate skill ...` review output as
supporting/provisional surfaces. They may be useful for routing and review, but
they do not replace the gate above and must not introduce a second verdict.

`agents-shipgate preflight --workspace . --plan - --json` remains a supporting
proactive routing surface for coding agents before edits. It accepts a single
`PreflightPlanV1` object with `changed_files[]`, optional `diff_text`,
`capability_requests[]`, `host_permission_requests[]`, and
`context.{agent,task}`. The emitted `PreflightResultV3` reports protected
surfaces, forbidden shortcut actions, required evidence for proposed high-risk
capabilities, host-grant drift when a host baseline is present, deterministic
`signals[]`, `control`, `requires_verify`, `verification_command`,
`allowed_next_commands[]`, and `plan_summary`. A concrete, resolvable diff that
only appends valid built-in `tool_sources` rows may mark that manifest touch
`requires_human_review=false` and route the coding agent to verification. This
authorizes proposal authorship only: existing rows and all other manifest
values must be unchanged, authority-bearing fields and custom adapters are
excluded, and the resulting trust-root diff still requires human review. It is
not a second gate; it must never be read as passed or mergeable. The release
gate remains `release_decision.decision`.

## Read these first for release gating

In `agents-shipgate-reports/report.json`:

- `release_decision.decision` — `"blocked"` / `"review_required"` / `"insufficient_evidence"` / `"passed"`. Baseline-aware. **This is the gating signal.** Precedence is `blocked` → `review_required` (active high/critical named concern) → `insufficient_evidence` → `review_required` (known review concern) → `passed`. Starting in v0.29, `passed` means every in-scope action has complete, conflict-free static surface, effect, and authority evidence, all applicable controls were evaluated, and no policy condition requires review. It does not prove runtime behavior or enforcement. Any required semantic dimension that is unknown, inferred-only, protocol-defaulted, partial, conflicting, invalid, or incomplete prevents `passed`, even when every other action is healthy. Existing extraction thresholds remain: low-confidence tools at least `max(1, ceil(tool_count × 0.5))` or more than three source-loader warnings also degrade evidence. `insufficient_evidence` means the scan cannot confidently gate release from the available static evidence; it does not prove the agent is unsafe. Switch on the enum with a `review_required` fallback for unknown future values.
- `release_decision.blockers[]` — items that block release on this run.
- `release_decision.review_items[]` — items the human reviewer should look at; includes baseline-matched accepted debt.
- `release_decision.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}` (v0.29+) — the machine-readable verdict boundary. Emitted values are `true`, `false`, and the canonical static-verdict disclaimer respectively. Packet §1 mirrors them exactly. Preserve these fields in agent summaries; `passed` must never be rewritten as runtime verification or safety proof.
- `release_decision.{blockers,review_items}[].capability_refs` (v0.24+) — stable capability IDs copied from the originating finding when a policy or policy-pack rule matched a `CapabilityFactV1`. Empty for findings that are not capability-policy matches. This is audit metadata only; `release_decision.decision` remains the gate.
- `release_decision.{blockers,review_items}[].capability_trace_refs` (v0.25+) — stable local trace-evidence IDs copied from the originating finding when an existing trace/evidence check used declared local trace artifacts. Empty when no local trace row is relevant. This is audit metadata only; `release_decision.decision` remains the gate.
- `release_decision.evidence_coverage.semantic_coverage` (v0.29+) — `{total_actions, pass_eligible_actions, gap_count, review_concern_count, reason_counts}`. A non-zero semantic `gap_count` prevents `passed`; a non-zero `review_concern_count` prevents an automatic pass and routes known unscoped/ambient authority to review. Semantic gaps are not Findings and cannot be suppressed, baselined, severity-overridden, waived by `--no-heuristics`, or satisfied by `human_ack`.
- `release_decision.evidence_coverage.policy_gap_count` and top-level `policy_evidence_gaps[]` (v0.33+) — policy applicability that is heuristic-only, mixed, unknown, or conflicting. These rows are outside Findings and cannot be suppressed, baselined, severity-overridden, acknowledged, or removed by `--no-heuristics`; any row prevents `passed`.
- `release_decision.evidence_coverage.identity_coverage` (v0.30+) — `{total_observations, canonical_tools, bound_tools, pass_eligible_tools, ambiguous_name_count, gap_count, reason_counts}`. Provider-scoped observations remain separate unless an exact reviewed `tool_identity.bindings[]` entry joins them. Any ambiguous selector, invalid binding, or conflicting identity prevents `passed`.
- `release_decision.evidence_coverage.evidence_gaps[]` (v0.26+; semantic kinds added v0.29) — one structured row per measurable gap: `{kind, subject, source_type, source_ref, why, next_action}`. In addition to `low_confidence_tool` and `source_warning`, v0.29 adds `incomplete_surface`, `missing_effect_evidence`, `inferred_effect_only`, `conflicting_effect_evidence`, `missing_authority_evidence`, `partial_authority_evidence`, `conflicting_authority_evidence`, and `invalid_semantic_annotation`. Semantic next actions use `declare_action_effect`, `declare_action_authority`, `declare_tool_inventory`, `provide_complete_inventory`, or `resolve_semantic_conflict`, include accepted values and exact source/manifest pointers, and are always human-routed. Their declaration placeholders carry `suggested_patch_kind="manual"`, `auto_apply=false`, and `requires_human_review=true`; they are not Patch objects. Work the rows in order instead of guessing; Agents Shipgate never auto-asserts effect or authority.
- `loaded_policy_packs[].{source,sha256,sha256_status,owner}` (v0.27+) — policy-pack distribution and ownership metadata for organization audit. `sha256_status` is `"verified"` only when the manifest pin matched; otherwise it is `"unpinned"`. This is report metadata; normal pack matching and release gating still come from deterministic rules and `release_decision.decision`.
- `findings[].support` (v0.33+) — typed predicate support with status, effective confidence, policy/block eligibility, claim IDs, evidence bases, predicate rows, and `support_hash`. Rule confidence and `block: true` are ceilings/requests; they cannot upgrade the support. Baseline matching for supported findings requires the same support hash.
- `findings[].policy_routing` (v0.28+) — optional policy-pack owner, reviewers, and approval-routing metadata. This is non-enforcing reviewer/audit metadata, not `Finding.evidence`; it does not affect fingerprints, suppressions, baselines, `blocks_release`, or `release_decision`.
- `release_decision.fail_policy.would_fail_ci` — `true`/`false`. Matches what
  the CI process will exit with. For a semantic evidence gap, strict mode emits
  the consistent tuple `decision="insufficient_evidence"`,
  `would_fail_ci=true`, `exit_code=20`; advisory mode keeps exit `0` while
  preserving the same non-pass decision.
- `release_decision.reason` — one-sentence explanation suitable for a PR comment.
- `release_decision.contribution_rules[]` (v0.17+) — deterministic per-finding audit explaining how each `report.findings` entry was classified. Exactly one row per finding (including suppressed). In v0.33, `unsupported_evidence` records a finding that cannot contribute because its typed support is not policy-eligible. Reading the contribution rule is sufficient to predict the gate outcome without re-deriving the decision logic.
- `privacy_audit` (v0.18+) — confirms the default redaction pass ran before public artifacts were written. Read `enabled`, `rules_version`, `sensitive_field_inventory_version`, `redacted_occurrence_count`, `redacted_paths[]`, and `output_surfaces[]`. `redacted_paths[]` contains structural paths and counts only, never raw values or raw hashes.
- `reviewer_summary` (v0.20+) — deterministic projection of the reviewer lens surfaces and audit envelopes; the reviewer-side parallel to `agent_summary`. Read this block first when triaging a scan for a human reviewer. Carries `verdict` (mirrors `release_decision.decision`), `headline` (≤200 chars, PR-comment-friendly), per-lens activity counts (`tool_surface_changes`, `capability_misalignments`, `action_surface_changes`, `evidence_matrix_gaps`), per-audit-envelope counts (`severity_overrides_applied`, `severity_overrides_tier_crossed`, `privacy_redactions`, `baseline_integrity_issues`), and `first_recommended_surface: ReviewerSurfacePointer | None` — a deterministic pointer naming which lens/audit to open first (`{kind, name, path, why}` where `kind` ∈ `{release_decision, lens, audit, evidence_matrix}` and `name` ∈ `{tool_surface_diff, capability_intent_diff, action_surface_diff, evidence_matrix, policy_audit, privacy_audit, baseline_integrity, release_decision}`). Same inputs always produce the same output; this block cannot disagree with the underlying lens/audit data.
- `heuristics_filter` (v0.21+) — top-level audit envelope describing the `--no-heuristics` CLI filter pass. Always present, even when the flag is unset (`enabled: False` with zero counts), so the report shape is stable. Carries `enabled: bool`, `excluded_provenance_kinds: list[str]` (`["keyword_heuristic", "regex_heuristic"]`), `filtered_finding_count: int`, and `filtered_by_kind: dict[str, int]` (per-kind breakdown). When `enabled: True`, findings whose `provenance_kind` is in the excluded list have been marked `suppressed=True` with `suppression_reason="filtered by --no-heuristics"` BEFORE the release decision was built — they remain in `findings[]` for transparency but no longer gate release. The filter never un-suppresses a finding; manifest-driven suppression reasons are preserved when they overlap with the filter. Useful for security/GRC reviewers who want declared-only findings.
- `verifier_summary` (v0.22+) — top-level **composition** for one-fetch controller consumption (the AI-coding-workflow verifier surface). It derives **no independent verdict**: `verdict` mirrors `release_decision.decision` exactly (Principle: one decision engine). Carries `by_severity: dict[str,int]` and `by_reason_code: dict[str,int]` (active-finding histograms — the complete per-code map), `capability_delta_summary: {added, removed, broadened, narrowed}` (equal by construction to the `capability_change` member-list lengths), `protected_surface_touched: bool`, `policy_weakened: bool`, `human_ack_required: bool`, `human_ack_satisfied: bool`, and `top_reason_codes: list[{reason_code, count}]` — the ranked top-five highlight (severity desc → count desc → code asc; the full set stays in `by_reason_code`). This block cannot introduce a finding-independent blocker.

In `findings[]`, v0.24 adds capability-native policy evidence for built-in
policy checks and policy packs:

- `capability_refs: list[str]` — stable `CapabilityFactV1.id` values that
  matched the rule. It is emitted as an empty list for findings that are not
  capability-policy matches.
- `capability_policy_evidence | null` — optional typed audit metadata with the
  matched capability identity, effect, authority, controls, semantic hashes,
  matched predicates, and source provenance. It is explanatory only and is not
  included in finding fingerprint inputs.
- `policy_routing | null` — optional policy-pack routing metadata with
  `owner`, `reviewers`, and `approval.{required,teams,min_approvals,enforced}`.
  `approval.enforced` is always `false`; Shipgate validates declared team names
  but does not verify external approval systems or make release decisions from
  these fields.

Deterministic match and gating `finding.evidence` keys remain stable for legacy
readers. Policy-pack routing keys that used to live in `Finding.evidence` now
live in `policy_routing`; old baseline fingerprints are still matched during
baseline comparison. Policy matching is capability-native internally, but
policy-pack behavior, suppressions, severity overrides, baselines, SARIF,
Markdown, and GitHub Action outputs remain compatible.

In `findings[]`, v0.25 adds opt-in trace/provenance references for existing
trace/evidence checks:

- `capability_trace_refs: list[str]` — stable IDs from the top-level
  `capability_runtime_evidence` block. It is emitted as an empty list for
  findings that are not linked to a local trace row.
- `provenance_kind: "runtime_trace"` — used only for findings derived from
  declared local trace artifacts. It is not filtered by `--no-heuristics`.

The top-level `capability_runtime_evidence` block is a deterministic audit
projection over local trace artifacts declared in `openai_api.trace_samples`,
`google_adk.trace_samples`, `validation.evidence.approval_traces`, and
`validation.evidence.agent_traces`. It carries summary counts, matched and
unmatched `CapabilityTraceEvidenceV1` rows, source provenance, and notes. Trace
normalization keeps only allowlisted scalar fields and discards prompts,
messages, tool arguments, tool outputs, and arbitrary payload bodies. The block
is empty when no trace inputs are declared. It is not part of capability locks,
fingerprints, baselines, run IDs, or release gating.

The remaining v0.22 verifier blocks are reviewer-facing projections / declared inputs — none gates independently (`release_decision.decision` stays the only gate). They populate with real values only under `verify` mode (a `VerificationContext` from `agents-shipgate verify` or an equivalent scan context); a plain `scan` emits their stable empty shape:

- `capability_change` (v0.22+, semantic metadata v0.23+) — the diff-derived capability delta, grouped into `{enabled, added, removed, broadened, narrowed}` member lists over `action_surface_diff` / `tool_surface_diff`. Each `CapabilityChangeMember` carries `{id, direction, subject_kind, tool, action, scope, before_scope, after_scope, before_capability_id, after_capability_id, changed_hashes, semantic_direction, semantic_changes, risk_tags, release_impact, provenance_kind, confidence, rationale, related_finding_ids}`. `broadened` = more effective capability (wider scope, escalated effect, removed control); `narrowed` = less (removed scope, added control). `semantic_direction` explains the proven capability-level movement (`added | removed | broadened | narrowed | mixed | unknown | evidence_only`), and `semantic_changes[]` gives field-level reasons when a base action snapshot is available. `enabled: false` when no base diff is available.
- `protected_surface_changes` (v0.22+) — list of touched release trust roots, each `{path, kind, glob, related_finding_ids}`. Derived from the active `SHIP-VERIFY-*` findings, so every row's `related_finding_ids` resolves to a real `findings[]` entry and the rollup can never disagree with the gate. A row means "a protected file was touched"; purely-semantic weakenings with no file path stay in `findings[]` and surface via `verifier_summary` flags.
- `effective_policy` (v0.22+) — normalized (not text-diff) snapshot of the release-policy surface for base-vs-head weakening comparison: `{ci_mode, fail_on[], suppressed_check_ids[], waiver_scopes[], severity_overrides{}, baseline_integrity_mode, baseline_fingerprints[], ci_gate_present}`. Every list/dict is sorted for byte-stable output; derived purely from the manifest (plus accepted-debt fingerprints). It describes the policy the repository **declares**, not the policy this invocation runs under: `--ci-mode` / `--fail-on` move `ci_mode` / `fail_on` at the top level of the report but never here, so two runs of the same tree produce the same snapshot and a base-vs-head comparison stays repository-vs-repository.
- `human_ack` (v0.22+) — declared human-acknowledgement state, `{required, satisfied, acks[], outstanding[]}`. Within the static boundary, acknowledgement is **declared evidence only — never inferred** (human authority cannot be synthesized). A trust-root weakening (`SHIP-VERIFY-POLICY-WEAKENED`, `-POLICY-BASE-ABSENT`, `-CI-GATE-REMOVED`, `-BASELINE-OR-WAIVER-EXPANDED`) makes a surface `required`; it is `satisfied` only by a matching `human_ack` entry in `shipgate.yaml` (owner + reason + affected surface, optional expiry). `required == (acks-covering-required) + outstanding`. The acknowledgement section lives in `shipgate.yaml` — itself a trust root — so a coding agent cannot add its own ack without tripping `SHIP-VERIFY-TRUST-ROOT-TOUCHED`.

New `SHIP-VERIFY-*` reason codes (v0.22+, category `verify` — suppression-immune and floor-protected; emit only under `verify` mode): `SHIP-VERIFY-POLICY-WEAKENED` (base-vs-head policy weakened), `SHIP-VERIFY-POLICY-BASE-ABSENT` (0.16+; a policy trust root changed with no base snapshot to compare against — split out of `-POLICY-WEAKENED` so a first adoption no longer reports a weakening that could not have happened; evidence `kind` is `manifest_introduced` or `base_snapshot_unavailable`, and only the former reports `policy_weakened: false`), `SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` (suppression/waiver/baseline broadened), `SHIP-VERIFY-CI-GATE-REMOVED` (Shipgate CI workflow deleted), `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED` (agent-instruction trust root changed; routed to human review), `SHIP-VERIFY-TRIGGER-CATALOG-DRIFT` (trigger catalog changed). They are ordinary `Finding`s routed through `release_decision` — never a second verdict.

The action exposes these as outputs `decision`, `blocker_count`, `review_item_count`, `ci_would_fail` (v0.8+).
For verifier-cycle PR workflows it also exposes additive outputs
`should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
`verifier_json`, `verify_run_json`, `run_id`, `merge_verdict`,
`can_merge_without_human`, `agent_control_state`, `agent_control_reason`,
`agent_controller_must_stop`,
`agent_controller_stop_reason`, `agent_controller_completion_allowed`,
`trust_root_touched`,
`policy_weakened`, `capability_changes_added`,
`capability_changes_modified`, and `capability_changes_removed`. These are
review and routing aids only. `trust_root_touched` and `policy_weakened`
mirror `verifier_summary`; the capability counts mirror
`capability_change` (`modified` is `broadened + narrowed`). Keep using
`decision` as the release-gating output and `agent_control_state` as the
coding-agent operational output.

When the action is asked to emit organization-governance artifacts, it also
exposes `attestation_json`, `org_evidence_bundle_json`, `host_grants_json`, and
`org_status_json` as artifact paths. These are ingestion and audit surfaces for
platform teams; they never create a second verdict.

For ongoing PR workflows, prefer:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

`verify` writes `verifier.json`, `verify-run.json`, `agent-handoff.json`, and
`pr-comment.md` alongside the head scan artifacts. `agent-handoff.json` is the
compact coding-agent projection over the verifier, verify-run, and report
artifacts; it does not gate independently. After a successful head scan it also writes the head static
capability lock to `agents-shipgate-reports/capabilities.lock.json`. When
`--base` is provided and the base scan can be materialized, verify writes
`agents-shipgate-reports/base.capabilities.lock.json`,
`agents-shipgate-reports/capability-lock-diff.json` and
`agents-shipgate-reports/capability-lock-diff.md`. The packet artifact is
intentionally `packet.json` only; use `scan` for manifest-driven packet
Markdown/HTML/PDF rendering. Read
`verifier.json.base_status` to understand whether base diff enrichment ran;
do not use it as a release verdict. The release gate is still
`report.json.release_decision.decision`. `verify` never fetches, so CI callers
must make the base ref available before invocation. Supplying `--head` makes
verify scan an isolated archive of that ref; omitting it scans the checked-out
workspace. If an explicit `--base` ref or PR diff cannot be inspected, verify
skips a head-only scan; `verifier.json.merge_verdict` is `unknown` and the
command exits 2.

`agents-shipgate verify --preview --json` is a lightweight relevance check — no
scan, no manifest required, exits 0 for every workspace it evaluates. A
`--workspace` that does not exist is not a workspace it evaluates: it is
refused as an invocation error (`config_error`, exit 2) before any directory
is created, on preview and on every other command that takes the option. It
emits a `verifier.json` with
`mode: "preview"`, `execution: "not_run"`,
`applicability: "not_evaluated"`, and
`control.state: "agent_action_required"`. `control.next_action` carries the
next recommended action: an exact
`init --workspace <workspace> --write --json`
command for unconfigured repos, or an exact `verify` command for configured
repos using the supplied workspace/config/base/head/out arguments. Use it as the
first touch before a full scan. To evaluate just the run/skip trigger, run
`agents-shipgate trigger --base origin/main --head HEAD --json`.

`agents-shipgate verify` and `verify --preview` also write
`agents-shipgate-reports/verify-run.json` whenever the output directory can be
created. It carries `schema_version: "shipgate.verify_run/v4"`, the exact
verification plan, executor, unit-result IDs, decision ID, outcome projection,
and artifact references. `request_id` is the content-addressed run identity;
the deprecated `run_id` remains for one compatibility cycle as its exact alias,
never as a separately derived identity. It has no wall-clock timestamp and is
not a second gate.

`agents-shipgate-reports/agent-handoff.json` carries
`schema_version: "shipgate.agent_handoff/v7"` and top-level sections
`gate`, `control`, `fix_task`, `blocked_by[]`,
`remediation_plan[]`, `capability_review`, `authorization`, `reproducibility`,
and `artifacts`.
`gate.decision` mirrors `release_decision.decision`; `gate.merge_verdict`
mirrors `verifier.json.merge_verdict`; and
`gate.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}`
mirrors the report/verifier static-only boundary. The values are locked to
`true`, `false`, and the canonical disclaimer. `control` is byte-identical to
the verifier/verify-run control object, and `can_merge_without_human` is true
only for a verified `passed` result or a completed deterministic
`not_applicable` skip. `authorization` is the byte-equivalent verifier
evaluation; the handoff cannot grant a command independently. Re-render it
from existing artifacts with:

```bash
agents-shipgate agent handoff --from agents-shipgate-reports/verifier.json --json
```

In `agents-shipgate-reports/verifier.json`, read the fields below (full
schema [`docs/verifier-schema.v0.9.json`](verifier-schema.v0.9.json)). **Lead
with `control.state`.** Every release and merge field below is a mirror or
deterministic projection of `report.json`; the authorization evaluation is an
operational overlay and cannot change those fields.
`release_decision.decision` remains the gate.

- `control` — the discriminated `complete | agent_action_required |
  review_publishable | human_review_required` operational projection. Its
  variant fixes `completion_allowed`, `must_stop`, `permissions`,
  `human_review`, and the actor/action shape; generated schemas enforce the
  variants with `oneOf`. Only a new verifier artifact can clear a pending
  control obligation.
- `execution` — `"not_run" | "succeeded" | "skipped" | "failed"`.
- `diff_status` — whether the compared change set was read at all.
  `completeness` is `"complete"` / `"partial"` / `"unavailable"`; `reason` is
  `null` only when complete, and otherwise `not_attempted`, `refs_missing`,
  `merge_base_missing` (shallow checkout — deepening restores the merge base),
  `unrelated_histories` (no common ancestor exists; no fetch can create one),
  `objects_missing`, `metadata_limit_exceeded`, `body_limit_exceeded`,
  `git_timeout`, or `git_failed`. `remediation` names
  the repair and `fetch_repairable` says whether fetching can perform it.
  **Only `"complete"` licenses reading a negative `trigger` result**; anything
  else means the diff was not read, which is never evidence that a PR is
  unrelated to agent capabilities. `null` means a pre-v0.7 artifact — unknown,
  not complete.
- `trigger` — the run/skip evaluation. Read `evaluation_status` first: when it
  is `"not_evaluated"`, `should_run` / `run_shipgate` / `skip` / `skip_reason`
  are `null` and `next_action.kind` is `"input_required"`. `skip_reason` is
  never `"no_match"` for inputs that were not fully read. `"evaluated"` on an
  incomplete `diff_status` is not a contradiction: only *skip* verdicts are
  withheld, so a `should_run: true` reached from evidence that did not depend
  on the missing bytes is authoritative and must not be overridden. Read
  `matched_rules` to see what carried it — a `force_run` match rests on the
  manifest, not on the diff.
- `merge_verdict` — `"mergeable"` / `"human_review_required"` /
  `"insufficient_evidence"` / `"blocked"` / `"unknown"`. Deterministic projection
  of `release_decision.decision` (`passed`→`mergeable`,
  `review_required`→`human_review_required`,
  `insufficient_evidence`→`insufficient_evidence`, `blocked`→`blocked`, missing
  decision→`unknown`). It cannot disagree with the gate; switch on the enum with
  an `unknown`/`human_review_required` fallback for future values.
- `static_analysis_only`, `runtime_behavior_verified`, and
  `static_verdict_disclaimer` — locked to `true`, `false`, and the canonical
  non-runtime disclaimer. When a release decision is embedded, construction
  rejects any disagreement between these top-level values and the decision.
- `applicability` — `"not_evaluated"` / `"verified"` /
  `"not_applicable"` / `"failed"`.
  Disambiguates a `mergeable` verdict: `"verified"` means Shipgate evaluated the
  change and produced a release decision; `"not_applicable"` means the head scan
  was skipped (nothing to gate — do **not** read this as "verified safe");
  `"failed"` means the scan could not complete. Orthogonal to `merge_verdict`;
  additive and locked to `"verified"` whenever a `release_decision` is present.
- `can_merge_without_human` — `bool`.
- `decision` — mirror of `release_decision.decision` (or `null` when no scan ran).
- `headline` — single-sentence, PR-comment-friendly summary (or `null`).
- `authorization` — the
  `shipgate.human_authorization_evaluation/v1` result. Only `accepted` can
  expose a command, and that command must exactly match both
  `control.next_action.command` and the sole entry in
  `control.allowed_next_commands`. `rejected`, `not_requested`, and
  `not_applicable` carry no command authority.
- `control.human_review` and `control.next_action` are the serialized route for
  the current verifier state; when authorization is accepted, the signed
  evaluation is the provenance for the exact coding-agent next action.
- `AgentController`, `VerifierNextAction`, and `VerifierHumanReview` remain
  importable only as deprecated v0.1/v0.2 reader models. Verifier v0.6 does not
  emit or invoke the retired `build_agent_controller` projector.
- `fix_task` — `{actor, safe_to_attempt, instructions[], allowed_repairs[],
  forbidden_repairs[], forbidden_shortcuts[], verification_command, patches[]}` or `null`.
  This is the deterministic repair boundary: `actor: coding_agent` with
  `safe_to_attempt: true` means the agent may attempt only the listed mechanical
  `allowed_repairs[]` and rerun `verification_command`; `actor: human` means the
  agent must not invent action effect, action authority, approval,
  idempotency, policy, waiver, baseline, or trust-root evidence to make the
  gate pass. `forbidden_repairs[]` explicitly
  lists reward-hacking moves such as suppressing findings, lowering severity,
  expanding baselines/waivers, weakening CI or policy, adding human ack, or
  inventing action-effect/action-authority/approval/idempotency evidence.
  `patches[]` (v0.13+) carries
  `{finding_id, check_id, patch}` rows with the
  machine-applicable suggested patches for the gating findings — populated
  only when verify ran with `--suggest-patches` and the task routes to the
  coding agent; repair aids, never gate inputs.
- `trust_root_touched` — `bool`; `true` when the PR changed a release-gate trust
  root (`shipgate.yaml`, the Shipgate CI workflow, `AGENTS.md`/`CLAUDE.md`,
  policy packs, prompts, baselines, waivers, etc.). Backed by the
  `SHIP-VERIFY-TRUST-ROOT-TOUCHED` check.
- `capability_review` — reviewer-facing projection of `capability_change` with
  `{trust_root_touched, policy_weakened, policy_weakening_proven,
  capability_changes_added, capability_changes_removed,
  capability_changes_modified, top_changes[]}`. Gate on `policy_weakened` (the
  fail-closed flag, raised even when no base policy existed to compare
  against); say the policy was weakened only when `policy_weakening_proven`
  (0.16+) is also true — that one means a base-vs-head comparison actually ran.
  `top_changes[]` carries the highest-signal capability deltas with
  `{id, change_type, change_bucket, subject_kind, subject, impact, rationale,
  source_path, source_start_line, related_finding_ids}`. `impact` mirrors the
  gate (`blocks_release`, `review_required`, `insufficient_evidence`, or
  informational values) and never introduces a finding-independent blocker.
- `mode` — `"advisory"` / `"strict"` / `"skipped"` / `"preview"`.

### Trusted human authorization for one exact command

Authorization changes operational routing, never the static release verdict.
The flow is deliberately two-pass:

1. Run `agents-shipgate verify --no-plugins` and validate the resulting
   terminal receipt. Authorization requires the plan's exact effective plugin
   mode to be false; the protected executor never loads third-party plugin or
   adapter entry points.
   `agents-shipgate authorization request --receipt <receipt>
   --artifacts-root <root> --destination-ref <full-ref>
   --expected-lease-oid <oid> --out <request>` constructs the unsigned
   `shipgate.human_authorization_request/v1` from that receipt's current
   request, subject, decision, source receipt/artifact-set/engine/executor and
   tree identities, the complete ordered
   review set, and one typed Git-push operation. This command creates a
   challenge, not authority.
2. The host authenticates the human and signs the canonical request with an
   Ed25519 key kept outside coding-agent reach. Agents Shipgate supplies no
   private key and no command that signs or approves a request. The v1 trust
   policy must be stored outside the evaluated workspace and protected from
   writes by the agent. On POSIX, Agents Shipgate reads it only from the OS
   account home's fixed path
   `~/.config/agents-shipgate/human-authorization-trust-policy.json`; `HOME`
   and `XDG_CONFIG_HOME` do not redirect that lookup.
3. Rerun `agents-shipgate verify --no-plugins --authorization
   <external-grant>`. The
   verifier recomputes the current identities and validates the signature,
   principal, repository scope, TTL, request, subject, trees, decision, full
   review set, and operation before publishing any command authority.

The only v1 operation is an exact force-with-lease Git push. It binds the exact
evaluated commit, a canonical credential-free HTTPS destination whose
repository identity equals the verified repository, a full destination
`refs/heads/...` ref, and the expected remote OID. A synthetic PR merge receipt
cannot authorize pushing a different parent commit. Authorization is eligible
only when execution
succeeded and the release decision is `review_required`. An accepted grant
changes `control.state` from `human_review_required` to
`agent_action_required` for that exact command, while all release facts remain
unchanged: `release_decision.decision="review_required"`,
`merge_verdict="human_review_required"`, `can_merge_without_human=false`, and
`completion_allowed=false`. The coding agent may perform only the serialized
guarded `agents-shipgate authorization execute` command. That consumer
revalidates the current receipt, trust root, clock, repository, and commit and
isolates Git configuration and hooks before issuing the internal typed push;
the raw Git command is never operational authority. The agent must rerun
verification afterward.

The signer must authenticate the source closure: content addressing is
integrity, not provenance. It must rerun verification in a trusted worker or
verify trusted-CI attestation over the bound source receipt/artifact-set IDs.
The request exposes the evaluated base commit and merge base, and the source
commit transitively binds its full parent graph. The signer must review that
complete ancestry and reachable history rather than relying only on the final
tree diff. Execution enforces a 512 MiB graph-pack ceiling and a 120-second
process timeout; the host broker should impose tighter deployment quotas. The
compressed pack ceiling does not bound expanded-object indexing memory or CPU,
so production brokers need cgroup, container, or equivalent host resource
limits.
Execution also requires a host-protected broker with a sanitized environment,
external trust policy, interpreter, entire virtual environment and
`site-packages` tree (including startup `.pth` files), dependencies,
credentials, and separately installed Agents Shipgate distribution. Same-UID
file permissions alone are insufficient, and an
editable install rooted in the authorized workspace is ineligible. If the host
cannot enforce those boundaries, authorization remains disabled. The guarded
executor is POSIX-only in v1 and authorization remains disabled on Windows. V1 is
push-only and does not authorize the coding agent to apply reviewed protected
patches.

Malformed, untrusted, expired, not-yet-valid, incomplete-review-set,
wrong-tree, wrong-request, wrong-ref, or wrong-lease grants fail closed with
zero allowed commands. Plain JSON in the repository, a PR comment, or
conversation-level approval is not equivalent to a signed grant. This release
defines the protocol and verifier consumer; it does not claim a current Codex,
Claude Code, or other coding-host UI signing integration. Such a host adapter
must be implemented separately. A grant replayed after the remote ref advances
cannot overwrite that ref: Git enforces the signed command's explicit expected
lease OID.

`verifier.json` also carries `trigger`, `base_status`, `head_status`, `base_ref`,
`head_ref`, `changed_files`, `base_notes`, the embedded `release_decision`, and an
`artifacts` map. When present, `artifacts.capability_lock_json`,
`artifacts.base_capability_lock_json`,
`artifacts.capability_lock_diff_json`, and
`artifacts.capability_lock_diff_markdown` are review artifacts only; they do not
change the gate. The matching GitHub Action outputs are `agent_control_state`,
`agent_control_reason`, `merge_verdict`, `can_merge_without_human`, and the
compatibility mirrors `agent_controller_must_stop`,
`agent_controller_stop_reason`, `agent_controller_completion_allowed`,
`trust_root_touched`, and
`capability_changes_{added,modified,removed}` (the original `decision`,
`blocker_count`, `review_item_count`, `ci_would_fail` outputs are preserved). See
[STABILITY.md §Verify Orchestrator](../STABILITY.md#verify-orchestrator) for the
authoritative contract.

The default Action PR comment style for the verifier-cycle minor is
`capability-review`: exactly two reviewer sections, a human summary and a
fenced JSON agent instruction block. The human summary leads with
`merge_verdict`, `can_merge_without_human`, capability delta, next actor, and
artifact links, including the semantic capability-lock diff summary when a base
lock is available. The agent block carries `control` and `fix_task` for
coding-agent routing. Existing adopters that need the v1
findings-oriented comment during migration can set `pr_comment_style: findings`
for one minor release cycle.

The GitHub Action emits source-backed GitHub Actions job annotations by default
for active blockers and review items. `check_annotations: "false"` disables the
projection; `check_annotation_limit` caps the number emitted. The helper also
writes `agents-shipgate-reports/check-annotations.json` for audit/debug.

`verify` writes non-gating capability artifacts when static extraction succeeds:
`agents-shipgate-reports/capabilities.lock.json` for head, and when a base ref
is available, `base.capabilities.lock.json` plus `capability-lock-diff.json`.
These artifacts are review/integration surfaces only and cannot introduce a
second verdict.

## Read this for local boundary control

`shipgate check --agent <codex|claude-code|cursor> --workspace . --format
agent-boundary-json` is the local static multi-host boundary command. The
`--agent` value is caller identity, never a coverage selector. The command emits
exactly one stdout JSON object using
`schema_version: "shipgate.agent_boundary_result/v2"` and the schema in
[`agent-boundary-result-schema.v2.json`](agent-boundary-result-schema.v2.json).
The old `codex-boundary-json` spelling remains a deprecated `0.16.x`
compatibility projection of the same assessment.

Read `input_coverage`, `host_coverage[]`, `affected_hosts[]`, `policies[]`,
`issues[]`, `pending_review[]`, and `excluded_scopes[]` before relying on the
result. `complete`
means complete only within the declared static input scope; it is not proof of
session grants, runtime enforcement, or tool behavior. `pending_review[]` is
non-empty only alongside `agent_action_required`: those are review obligations
the graded mapping carried forward instead of stopping the turn, and an agent
must name them when summarizing the change. The detailed matrix is
[`host-boundary-support.md`](host-boundary-support.md).

Coding agents switch on `control.state`, then follow `control.next_action` and
`control.allowed_next_commands`. `decision` is diagnostic only. A pending
verification obligation produces `agent_action_required`; it can never coexist
with `completion_allowed=true`. An evaluated human route produces
`review_publishable`, `must_stop=false`, a human next action, and
`permissions` that authorize publishing but not merging. An unsafe or
unbindable one produces `human_review_required`, `must_stop=true`, and
permissions that authorize nothing.
Do not derive control from Markdown, PR comments, natural language, or a
conversation-level acknowledgement. Only a new verifier artifact can clear
the obligation. Do not confuse this local boundary result with
`agents-shipgate verify`: verify writes
`agent-handoff.json`, `verifier.json`, and `verify-run.json`, and
`report.json` remains the full CI/reviewer substrate.

## Read these for release review

`agents-shipgate contract --json` exposes `manual_review_signals[]` as the
installed CLI's stable list of report/packet fields to inspect for human review
work. `findings[].provenance_kind` is included there as a filter/review signal
only; it never changes the release decision, severity, fingerprints, baselines,
or CI exit behavior.

The runtime contract also exposes stable non-gating integration fields:
`agent_handoff_schema_version`, `agent_handoff_schema_path`,
`agent_handoff_artifact`, `agent_interface_operations[]`, `exit_code_policy`,
`mcp_tools[]`,
`capability_lock_schema_version`, `capability_lock_diff_schema_version`,
`capability_standard_version`,
`governance_benchmark_catalog_schema_version`,
`governance_benchmark_result_schema_version`, and
`external_integration_surfaces[]`. These advertise capability lock/diff and
benchmark artifacts for integrations and research. They do not change the gate:
`release_decision.decision` remains the only release decision signal.

The capability/intent diff fields (v0.9+), used by reviewers to spot misalignment between declared agent intent and actual tool surface:

- `capability_facts[]` — every capability surfaced from the tool inventory. In v0.29 each newly emitted fact carries `semantic_assessment`, the normalized effect/authority claims, issues, conservative effect, and pass-eligibility state consumed by the gate.
- `declared_intentions[]` — what the manifest says the agent is supposed to do.
- `misalignments[]` — where capabilities exceed (or fall short of) declared intent.
- `release_consequence` — capability-aware roll-up of the release decision.
- `suggested_scenarios[]` — dynamic-validation scenarios derived from misalignments and findings.

The Action Surface Diff fields (v0.16+), reviewer-facing PR/release delta:

- `action_surface_facts.actions[]` — deterministic snapshot of the current agent action surface: action id, operation, effect, normalized risk tags, scopes, approval policy, safeguards, evidence, hashes, and (v0.29+) the same `semantic_assessment` projected onto the corresponding capability fact. `semantic_assessment.conservative_effect`, `action.effect`, and the capability effect must agree.
- `action_surface_diff.{enabled, base, summary, added, removed, modified, notes}` — what changed vs. a base report or v0.4 baseline. Policy findings generated from this diff can set `findings[].blocks_release=true` and appear in `release_decision.blockers`.
- `findings[].blocks_release` and `release_decision.{blockers,review_items}[].blocks_release` — explicit release-policy blockers from Action Surface Diff policies and policy-pack rules with `block: true`. Advisory CI may still exit 0; strict CI exits nonzero when an active unbaselined release blocker is present.

The tool-surface diff fields (v0.10+), lower-level explanatory data:

- `tool_surface_facts.{tools, scopes, controls, policies}` — current static facts about the tool surface.
- `tool_surface_diff.{enabled, base, summary, tools, high_risk_effects, scopes, controls, metadata_changes, policy_drift, finding_deltas, notes}` — what changed vs. a base ref. Disabled diffs render as `enabled: false` with a `notes` reason.

Source provenance fields on `findings[].source` (v0.11+), additive and optional:

- `path`, `start_line`, `end_line`, `start_column`, `pointer` — manifest-relative file path, 1-based line/column, and RFC 6901 JSON pointer for the offending tool. Populated for OpenAPI, MCP, OpenAI tool artifacts, and Anthropic tool artifacts when the source is YAML. JSON inputs carry `path` and `pointer` but no line in v0.11.

Per-finding `agent_action` enum (v0.12+), deterministic projection — read this **first** when deciding what to do with a finding so you don't have to synthesize an action from `patches`/`autofix_safe`/`requires_human_review`/`suggested_patch_kind`:

- `auto_apply` — `apply-patches --confidence high` will resolve cleanly. Every patch is non-manual and high-confidence.
- `propose_patch_for_review` — at least one non-manual patch is attached and machine-applicable, but the full patch set is not auto-safe. Two shapes land here: (a) every non-manual patch is medium- or low-confidence, and (b) a high-confidence non-manual patch sits alongside one or more `ManualPatch` siblings (the non-manual is safe to apply, but the manual instructions still need a human). In both cases the agent should ask the user before `--apply` and surface any manual instructions verbatim.
- `escalate_to_human` — no machine-applicable patch. Either every patch is `ManualPatch`, or `patches` is empty/absent and the check requires human review.
- `suppress_with_reason` — reserved for future check classes that explicitly mark themselves as suppressible. Not emitted by the v0.12 deterministic projection; the schema accepts it so callers can extend.
- `informational` — no action required (suppressed finding or non-actionable advisory).

Top-level `agent_summary` block (v0.12+), one-fetch summary shaped for direct agent consumption — read this when you want the headline numbers without traversing arrays:

- `verdict` — mirrors `release_decision.decision`.
- `headline` — single-sentence verdict + counts; suitable for a PR comment lead. The headline uses `needs_human_review` (action-driven) for "require human review" wording, so a `review_required` verdict with only auto-applicable findings reads honestly as "auto-applicable; none require human input" rather than falsely claiming N findings need review.
- `blocker_count` — mirrors `len(release_decision.blockers)`.
- `review_item_count` — mirrors `len(release_decision.review_items)`; **severity-driven** (medium-and-up severity findings that aren't blockers, plus baseline-matched accepted debt). Use this when reporting release-review debt to the human reviewer.
- `auto_appliable_patches` — number of active findings with `agent_action == "auto_apply"`.
- `needs_human_review` — **action-driven**: number of active findings with `agent_action ∈ {"escalate_to_human", "propose_patch_for_review"}`. Both kinds need explicit human attention before any change applies — full escalations have no machine path, and proposed patches ship at medium/low confidence and require an explicit `--apply` after the user confirms. Use this when reasoning about what work an agent must do.
- **`review_item_count` and `needs_human_review` track different populations and can diverge.** A medium-severity stale-suppression finding lands in `release_decision.review_items` (severity rule) but its `agent_action` is `auto_apply` (high-confidence patch attached), so it's counted in `review_item_count` and `auto_appliable_patches` but **not** in `needs_human_review`.
- `first_recommended_action` — `{kind, command|null, why}`; deterministic next step. `kind: "command"` carries an actual CLI invocation; `kind: "info"` is a "surface this to the user" hint with no command. The agent_summary block is a deterministic projection — same inputs, same output, no agent-side aggregation needed.
- **Evidence-gap actionability (v0.16+).** One **selected gap** feeds every short-form surface that names one: the first `release_decision.evidence_coverage.evidence_gaps[]` row that is *addressable*, falling back to the first row when none is. `Improve evidence:` (CLI and step summary, printed only on an `insufficient_evidence` verdict) always renders the selected gap's action.

  **A row is addressable when it names a visible target or carries a publishable command.** `next_action.path` and `next_action.command` are independently nullable, and either one alone is enough:
  - *Visible target* — the path contains at least one character that actually renders. Whitespace, control characters, and Unicode Default_Ignorable code points (U+200B ZWSP, U+200E/U+202E bidi marks, U+FE0F VS16, U+034F CGJ, …) render as nothing, so a path made only of those names no surface and is not addressable.
  - *Publishable command* — the command is safe to run **exactly as authored**. A command containing any control, bidi, or invisible code point, or any whitespace other than U+0020, is suppressed entirely rather than cleaned up: deleting a zero-width character from `r␣m -rf` would author a different program. Only leading/trailing U+0020 is trimmed, which cannot change `argv[0]`.

  Two guarantees, scoped to exactly what holds:
  - **Alignment, on `insufficient_evidence` with an addressable gap.** When the verdict is `insufficient_evidence` *and* at least one gap is addressable, `release_decision.reason`, `Improve evidence:`, and `first_recommended_action.why` name the **same** gap and the same target (or, for a command-only row, the same command). The reason leads with that gap and reports the source-warning / low-confidence counts as `Context:`.
  - **No false dead end, on every verdict.** The phrase *"no machine-applicable fix is available"* is never emitted in `first_recommended_action.why` while any gap is addressable. When you do see it, no gap names a surface to open or a command to run, and the next step really is a human gathering evidence.

  Outside that first case the three surfaces answer different questions, by design — do **not** read alignment into them:
  - `insufficient_evidence` with **no** addressable gap: `reason` keeps the `Evidence coverage below threshold (…)` wording and `first_recommended_action` routes to gathering deeper sources, while `Improve evidence:` still renders the first gap's `expects`. That line is a remediation hint, not a restatement of the reason.
  - `review_required`: `reason` is severity/findings-driven and never names a gap. Whether `first_recommended_action` names one depends on which branch of the action picker fires, and **auto-apply wins on sub-threshold evidence**: when the scan carries auto-applicable patches and evidence is *not* below the `insufficient_evidence` threshold, the action is the `apply-patches` command even if an addressable gap exists (the gap is still called out in the `why`). `first_recommended_action` names the selected gap on the evidence-first branches only — evidence below the threshold, or evidence-driven review with no findings to walk. Read `evidence_coverage.evidence_gaps[]` directly if you need the gap on this verdict; do not infer it from the action.

  **Rendering never rewrites what it renders.** Values reaching these one-line surfaces are repository-derived — a gap subject is a tool name, a policy pack authors `expects`, a semantic gap's `path` embeds a tool name — and they are made line-safe without being altered otherwise. Characters that could break a line or reorder it (controls, U+2028/U+2029, bidi marks) become a visible `<U+XXXX>` escape; **nothing is deleted**. Identity-bearing invisibles survive, so `agents/👩‍💻.yaml` and identifiers carrying ZWNJ are named as they actually are. Paths and commands are additionally never whitespace-folded — `configs/foo␣␣bar.yaml` keeps both spaces, and `python -c 'print("a␣␣b")'` stays the program that was written — while prose (subjects, `why`/`expects`, loader warnings) does fold whitespace, because there a stray newline is better read as a space. An affordance is published only when it exists: a suppressed or absent command produces no `Run:` line and a `null` repair command, and accepted values with nothing visible in them are dropped.

  **Authority comes from the action's own fields, never from its `kind` or its path.** Every evidence-gap row published today is `requires_human_review: true`, `auto_apply: false`, `suggested_patch_kind: "manual"` — including the `provide_source` row that regenerates a stale `--diff-from` comparison base, for which `verify` emits `fix_task.actor: "human"` and `safe_to_attempt: false`. A `command` on such a row tells a human exactly what to run; it does not make the row agent-owned. A coding agent acts mechanically only where `fix_task.actor == "coding_agent"` and `safe_to_attempt` is true, and then only within `allowed_repairs[]`. Separately, `first_recommended_action.kind` stays `"info"` on the evidence-first actions — a statement about the summary projection, not a claim that no gap row ever carries a command, and not a promise about every `review_required` action (the auto-apply branch returns `kind: "command"`).

Codex plugin surface block (v0.13+), explanatory only — never a release-gate
input by itself:

- `codex_plugin_surface.{plugins, marketplaces, skills, apps, mcp_server_stubs, hook_stubs, mcp_inventory_files, component_path_issues, warnings}` — local static plugin package and marketplace facts.
- Only explicit MCP inventory tools from `codex_plugins.mcp_tool_inventories` appear in `tool_inventory[]`; apps, hooks, skills, and MCP server declarations stay in `codex_plugin_surface`.

Per-finding `provenance_kind` enum (v0.15+), additive classification — read this when you want to filter findings by the kind of rule that fired, independent of `confidence` (sureness):

- `static_declaration` — declared metadata: manifest, MCP export, OpenAPI schema, ADK YAML agent config, LangChain/CrewAI inventory JSON. High-trust structural facts.
- `ast_extraction` — Tool parsed from user Python source by a framework extractor (LangChain function/structured tools, CrewAI function/class tools, ADK Python toolsets). Subject to extraction errors; agents that distrust AST quality may filter these as a class.
- `keyword_heuristic` — matched a keyword list (broad-scope tokens, read-only/approval prompt terms, free-text parameter names). Higher false-positive risk than declarative facts.
- `regex_heuristic` — matched a regex (secret-like values in descriptions, prompt-injection patterns). Highest false-positive risk; pair with the recommendation before acting.
- `policy_pack` — emitted by an external policy pack rule after its predicates have authoritative typed support. Rule confidence can lower, but never raise, evidence confidence.
- `runtime_trace` — derived from declared local trace artifacts. Audit evidence only; never filtered by `--no-heuristics`.

Provenance generally follows the rule's own trigger (e.g., a rule that checks for a declared manifest field is `static_declaration` even when the underlying Tool was AST-extracted). For framework checks that fire across both AST and declarative tool sources (ADK's per-tool checks against `google_adk_function` AND `google_adk_config` tools), the label tracks the underlying tool's source. Third-party plugin checks that don't yet set the field land at `static_declaration` by default — pre-v0.15 plugins continue to validate against the v0.15 wire schema. Use `findings[].source.type` for the precise underlying tool source.

To filter operationally, use:

```bash
agents-shipgate findings --from agents-shipgate-reports/report.json \
  --provenance-kind keyword_heuristic,regex_heuristic --json
```

The command reads active findings by default; add `--include-suppressed` when a
reviewer needs suppressed entries in the same provenance summary.

For reviewer-shaped output, also read the **Release Evidence Packet** at
`agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` when the
`[pdf]` extras are installed). The packet is a supporting/provisional reviewer
projection, not a second gate. Packet outputs are redacted by the same default
privacy layer as the report. The packet has fixed reviewer sections governed by
[`docs/packet-schema.v0.12.json`](packet-schema.v0.12.json) — see
[STABILITY.md §Release Evidence Packet](../STABILITY.md#release-evidence-packet-v012).
Packet schema `0.9` carries the report's evidence-backed semantic coverage and
gap remediation contract. Packet §1 also mirrors
`static_analysis_only=true`, `runtime_behavior_verified=false`, and
`static_verdict_disclaimer` from the report release decision. Frozen packet
schema `0.7` added capability-linked
trace summary and trace refs under `human_in_the_loop`; frozen schema `0.6`
preserved the v0.5
`action_surface_diff` section and added two independent additive extensions:

- `evidence_matrix` (PR #104) — a compact packet-only review aid
  derived from public `report.json` fields. The matrix never contributes
  to `release_decision`, CI exit behavior, severity, suppression,
  baseline matching, or `agent_summary`; its blocker and review-item
  cells are copied from `release_decision`.
- `ReleaseDecisionItem.source` and `ReleaseDecisionItem.policy_evidence_source`
  (PR #103) — packet §1 / §2 re-renders carry the same dual-source
  provenance that `Finding.source` / `Finding.policy_evidence_source`
  expose in the report.

It preserves every v0.5 field
(`human_in_the_loop.runtime_control_disclaimer`,
`human_in_the_loop.source_provenance[]`, `action_surface_diff`). The
`release_decision.verdict` label includes `INSUFFICIENT EVIDENCE` when
the report decision is insufficient evidence.

## Don't use for new gating

- `summary.status` — preserved for v0.7 callers, **baseline-blind**. A baseline-matched critical flips this to `release_blockers_detected` even though `release_decision.decision` correctly classifies it as `review_required`. New consumers should not gate on `summary.status`. See [STABILITY.md §`release_decision.decision` vs `summary.status`](../STABILITY.md#release_decisiondecision-vs-summarystatus).

## Per-finding contextual explanation (v0.12+)

For prose summaries of a single finding (PR comments, chat replies, commit messages), use:

```bash
agents-shipgate explain-finding <FINGERPRINT> \
    --from agents-shipgate-reports/report.json --json
```

The payload is the full `Finding` shape (every field on `findings[]` in `report.json`, including `source`, `patches`, `confidence`, `agent_id`, etc.) overlaid with three derived fields:

- `metadata` — full `CheckMetadata` for the check_id (rationale, fires_when, evidence_fields, docs_url, `mvp_tier`) when the check is in the catalog; null for unknown ids (third-party plugins, future checks). `mvp_tier` is display/triage metadata only and never affects gating.
- `explanation` — a deterministic 3–5 sentence prose summary suitable for direct quotation. Names the affected tool, the severity, the recommended fix, and an action-aware closing sentence keyed to `agent_action`. Same inputs always produce the same output.
- `source_report` — **absolute** path (always; relative `--from` values are resolved before serialization) to the report file the explanation was sourced from; round-trippable for caching and audit.

`explain-finding` requires `report_schema_version >= 0.12` because the action-aware explanation depends on per-finding `agent_action`. Pre-v0.12 reports are rejected with `input_parse_error` and a `next_action` pointing at the canonical scan command. The Pydantic `ReadinessReport` model is intentionally looser than this command's contract (so test fixtures can construct minimal findings); the version gate is what enforces v0.12 semantics on emitted reports.

Companion prompt: [`prompts/explain-finding-to-user.md`](../prompts/explain-finding-to-user.md). Use it when you need to translate a finding for a human who has never read the Shipgate docs. Keep `agents-shipgate explain <CHECK_ID>` for static catalog metadata (no specific finding); use `explain-finding` whenever you have a fingerprint and want the evidence-tied prose.

## Authoritative references

- [STABILITY.md](../STABILITY.md) — full alpha stability contract. Source of truth for everything above.
- [AGENTS.md](../AGENTS.md) — agent-facing instructions: install, run, single-turn flow, error semantics.
- [`docs/report-schema.v0.34.json`](report-schema.v0.34.json) — machine-validatable JSON Schema for the current report.
- [`docs/privacy.md`](privacy.md) and [`docs/report-sensitive-fields.json`](report-sensitive-fields.json) — default redaction behavior and sensitive-field inventory.
- [`docs/packet-schema.v0.12.json`](packet-schema.v0.12.json) — machine-validatable JSON Schema for the current packet.
- [`docs/checks.json`](checks.json) — check catalog, including `mvp_tier` for MVP/readiness triage.

## See also

- [`report-reading-for-agents.md`](report-reading-for-agents.md) — reader's primer that walks the JSON in the order a new consumer should read it; complements this field index.
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may assert mechanically vs. what must defer to a human reviewer when surfacing findings from `report.json`.
