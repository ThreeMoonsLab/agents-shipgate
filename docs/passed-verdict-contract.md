# Evidence-backed `passed` verdict

In the Agents Shipgate `0.16.0b7` runtime (contract v29, report schema v0.43),
`release_decision.decision: passed` means the configured root
agent and its complete reachable tool/handoff graph were statically proven,
and every reachable capability has complete, conflict-free static identity,
binding, effect, and authority evidence, all applicable controls were evaluated, and no
policy condition requires review. It does not prove runtime agent behavior or
runtime enforcement.

## Pass eligibility

An action is pass-eligible only when all of the following hold:

- its source surface is completely enumerable and extracted at high confidence;
- it is reachable from the unambiguously selected root through complete,
  static tool or handoff edges;
- every extracted observation has a complete canonical identity, and no
  selector or reviewed cross-source binding is ambiguous or conflicting;
- its effect is established by a reviewed manifest declaration or a structural
  source fact such as an OpenAPI method or an explicit MCP annotation;
- its authority is explicitly `none` or concretely scoped;
- no semantic claims conflict and all annotation values are valid; and
- controls required by the normalized effect are present; and
- every applicable policy predicate is supported by high-confidence reviewed,
  protocol-structural, typed-provider, or structural-scope evidence.

Names, descriptions, schema keywords, regular expressions, and protocol
defaults may raise the conservative risk bound, but they never establish
safety. A capability with only inferred, defaulted, partial, or missing
evidence is `insufficient_evidence`, regardless of how many fully described
capabilities are present alongside it.

Semantic and policy-applicability evidence are part of the release decision,
not Findings. They cannot be
suppressed, baseline-matched, waived through a severity override, or converted
to known evidence by `--no-heuristics` or `human_ack`.

Machine consumers should inspect
`release_decision.evidence_coverage.semantic_coverage`, `binding_coverage`,
`identity_coverage`, and `policy_gap_count`, then work `evidence_gaps[]` in
order. Current packet schema v0.18 mirrors this contract and binds the
verification request and decision, while capability standard
v0.5 carries the same normalized assessment and binding hash in capability
lock v0.6 and lock-diff v0.7 artifacts.

Policy severity, `block: true`, risk overrides, and rule-declared confidence
cannot upgrade underlying evidence. Heuristic-only, mixed, unknown, or
conflicting policy applicability creates a non-waivable evidence gap. The
conservative effect may still increase, but no hard finding is emitted until
the predicate is supported by authoritative static evidence.

Catalog membership never implies binding. `tool_catalog[]` contains every
canonical extracted declaration; `tool_inventory[]`, actions, checks, and
capability facts contain only tools proven reachable from the graph's entry
points. Reviewed closed-world declarations live under `agent_bindings` and,
since v0.42, under `tool_sources[].binding`; coding agents must not invent or
auto-apply either.

A graph has one or more entry points, published as
`binding_surface_facts.entry_point_agent_ids` (v0.42+) and empty exactly when
nothing rooted the graph. For an agent application that list is the single
selected root and `root_agent_id` names it. A repository that publishes tool
surfaces has **no agent object to select**: each `tool_sources[]` entry whose
published surface a human reviewed under `binding` is its own entry point, so
a repository publishing two servers has two — neither of which is the other's
root. Such a run carries `root_agent_id: null` with a non-empty
`entry_point_agent_ids`, and that state is a *deliberate* source-entry-point
graph, not an unresolved one; `root_agent_id: null` with an empty list is the
unresolved case. Every graph a release before v0.42 could produce has exactly
one entry point, equal to its `root_agent_id`.

`binding_surface_facts.agents[]` therefore holds nodes that are not runtime
agents, and `agents[].kind` (v0.42+, default `agent`) says which is which. Two
kinds exist today. `tool_source` is a reviewed published tool surface: it is
named by its configured `tool_sources[].id`, it is an entry point rather than
something an agent reaches, and it is deliberately invisible to root-selection
heuristics and to agent-name resolution, so declaring one cannot change how any
existing name resolves. The other is structural rather than declared: a fully
parsed, warning-free skill-only Codex plugin can prove a complete package root
with no callable tools or handoffs; the compatibility projection represents that
package root as an `agent` node, but it is not a runtime agent and does not
require a synthetic reviewed `agent_bindings` declaration. Apps, MCP servers,
hooks, MCP inventories, unknown manifest keys, skipped entries, component path
issues, or source warnings invalidate the zero-surface proof.

The release decision also carries an explicit machine boundary:

- `static_analysis_only: true`;
- `runtime_behavior_verified: false`; and
- `static_verdict_disclaimer`, the canonical statement that Agents Shipgate
  did not execute the agent or prove runtime behavior, tool routing,
  credential enforcement, or safety.

Packet §1 mirrors these three fields exactly. Consumers must preserve them in
summaries and must not translate `passed` into “runtime safe” or “runtime
verified.”

## Declaring missing semantics

Use `action_surface.actions` to close a reviewed evidence gap:

```yaml
action_surface:
  actions:
    - tool: process_order
      effect: write
      scopes: [orders:write]
      authority:
        mode: scoped
        auth_type: oauth2
        credential_mode: delegated
```

When two providers export the same display name, qualify the selector with
`tool_id`, `provider`, `source_type`, or `source_id`. A bare ambiguous name
applies nowhere. Use `tool_identity.bindings[]` only for reviewed equivalence
between observations; equal names never merge automatically.

Authority may also be declared once for a whole source, because every action a
source contributes normally runs with one credential:

```yaml
tool_sources:
  - id: crm
    type: mcp
    path: tools.json
    authority:
      mode: scoped
      auth_type: oauth2
      scopes: [crm.read]
```

An `action_surface.actions[]` row that declares its own `authority` overrides
it for that action. The resolver holds both spellings to the same rules — the
same mode co-requirements, the same refusal to weaken concrete published
evidence, and the same refusal to stand in for authority a source publishes
ambiguously — so writing the claim once is a convenience, never a weaker
statement.

Authority modes are:

- `none`: no authority is required; scopes and auth type must be empty.
- `scoped`: authority and concrete scopes are declared.
- `unscoped`: authenticated but not operation-scoped; a reason is required and
  human review remains mandatory.
- `ambient`: inherited process, user, or host authority; a reason is required
  and human review remains mandatory.

Agents Shipgate never auto-writes these declarations. They assert what an
agent can do and therefore require human review. Semantic next actions carry
`suggested_patch_kind: manual`, `auto_apply: false`, and
`requires_human_review: true`; a `declaration_template` is a placeholder for
human review, not an executable Patch.

A declaration may freely escalate past the evidence. Declaring an effect
**weaker** than one the scan inferred raises
`declaration_below_inferred_evidence` (v0.36+) and the action is not
pass-eligible until a reviewer accounts for the observation or acknowledges the
difference with `actions[].override` (`evidence` + `reason`). Accounting for it
is one edit, and the row names which: raise `effect` where one value covers
every uncovered reading, otherwise name the uncovered categories as reviewed
`actions[].risk_tags` — a declared tag is policy-eligible, so it accounts for
the reading *and* applies that category's controls. A tag **adds** its category
to the effect already declared; it does not replace it, and the obligations of
both stand. Raising `effect` instead answers the row with a single category and
drops whatever the previous value obliged, so the two edits are not
interchangeable: `effect: external_communication` plus a financial tag owes
confirmation as well as approval, audit, and idempotency, where
`effect: financial_write` alone does not owe confirmation. An acknowledged override is
accepted and the action is pass-eligible again, but it is counted as a semantic
review concern — like `unscoped` and `ambient` authority, it keeps human review
mandatory, so a run carrying one is never `passed`.

## CI behavior

Advisory mode continues to exit zero while reporting the non-pass verdict.
Strict mode exits 20 when semantic evidence is insufficient. Existing CI
files are not silently rewritten; repositories opt into blocking policy after
a human has reviewed the migrated surface.

## Migration from 0.15

There is no legacy `default_read` switch. Pin 0.15 temporarily if migration
cannot be completed immediately. Old reports and baselines remain readable,
but an old report without semantic evidence cannot prove a current action safe.
Regenerate base reports and capability locks with 0.16 before comparing them.
