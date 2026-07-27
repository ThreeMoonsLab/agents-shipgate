# Design: get first-adoption repos out of `insufficient_evidence`

Status: proposed. Owner: unassigned. Filed from the 2026-07-27 coding-agent UX
re-review, where this became the top remaining friction after contract v19
graded the local boundary stop.

Tracking issue: https://github.com/ThreeMoonsLab/agents-shipgate/issues/292

## The problem, stated as a user sees it

A developer adopts Shipgate on a small agent project, asks their coding agent
to add a comment to `CLAUDE.md`, and the turn ends with:

> A human must review this change before it can merge.

They ask for a README typo fix. Same ending. Nothing they do produces a
different outcome, and nothing they can act on is named.

Two mechanics combine to produce this:

1. **Verify always scans.** `verify` passes `user_requested=True`
   ([`cli/verify/orchestrator.py:155`](../../src/agents_shipgate/cli/verify/orchestrator.py)),
   so the capability scan runs on every invocation regardless of whether the
   diff touched a capability surface. This is deliberate — an adopted repo's
   force-run contract — but it means the scan's verdict is attached to every
   turn.
2. **Weak extraction abstains.** A repo whose tool surface is not statically
   enumerable trips `evidence_below_ie_threshold`
   ([`ci/release_decision.py`](../../src/agents_shipgate/ci/release_decision.py)),
   returning `insufficient_evidence` → `merge_verdict:
   human_review_required`. The abstention is correct as a *release* judgement.
   Attached to a docs-only turn, it reads as a non sequitur.

The cost is not just noise. `insufficient_evidence` is immune to baselines,
suppressions, severity overrides, and `human_ack` by explicit contract, and
every remedy the engine generates is `actor: human`. For the agent this is a
dead end, and dead ends are what make people uninstall a gate.

## What the engine already knows

The remediation content is not missing — it is precise and unreachable:

- `_insufficient_evidence_remedies` names the exact source and the exact fix
  (declare a local tool inventory, or replace a dynamic/config-bound toolkit
  with statically enumerable definitions).
- `EvidenceGapAction(kind="declare_tool_inventory")` even points at a generated
  skeleton next to `report.json` (`SUGGESTED_INVENTORY_FILENAME`).
- `declare_agent_root` gap actions name the missing manifest key
  (`shipgate.yaml#agent_bindings.root`) with accepted values.

Meanwhile `init` detects the framework with high confidence and writes a
manifest that does **not** declare `agent_bindings` — so the routed onboarding
path manufactures the very gap verify then abstains on.

## Proposal

Three changes, each independently shippable, ordered by value per unit of risk.

### 1. `init` scaffolds what it already detected

When detection identifies a framework and a root agent object with high
confidence, write `agent_bindings` into the generated manifest with the
detected root, marked with the same `CHANGE_ME`-style review affordance the
manifest already uses for unresolved fields. Where detection cannot identify a
root, write the key with an explicit placeholder plus the accepted values, so
the first `verify` reports "confirm this declaration" rather than "no root
agent matched the configured selector".

This does not assert authority the tool cannot see: a detected literal root
object is a structural fact, and the human still reviews the manifest before
committing it (the manifest is a trust root; PR-time verify reports the touch).

### 2. Scope the verdict to the change

Keep the force-run contract, but stop attaching a repo-wide evidence verdict to
a turn whose diff touches nothing capability-shaped. Two candidate shapes:

- **Preferred:** report `insufficient_evidence` only when the evaluated diff
  intersects the surfaces the gap is about; otherwise report the abstention as
  a standing repo-health note (`evidence_gaps` present, decision unchanged from
  what the diff itself warrants). The gate for *this* PR stays honest: a
  docs-only PR is not a capability change.
- **Fallback:** keep the verdict but make the agent-facing headline lead with
  the diff-scoped fact ("no capability change in this diff; the repo has N
  standing evidence gaps"), so the copy stops implying the current change is
  under suspicion.

The second is copy-only and cheap; the first is the honest fix and needs care
around `release_decision` semantics, which no change here may weaken.

### 3. Make one evidence declaration agent-proposable

Reuse the propose-and-ratify machinery that already exists for tool sources
(`assess_coverage_increasing_tool_source_proposal`, PR #282): let the agent
*write a proposal file* for the inventory/binding declaration the gap names,
never the manifest itself, and route `agent_action_required` with the exact
command. The human ratifies by moving the proposal into the manifest. The
invariant holds — the agent proposes, a human declares — and the dead end
becomes a two-step loop.

## Non-goals

- Weakening the `insufficient_evidence` verdict where the diff *is* a
  capability change. The abstention is the product working.
- Letting an agent author binding, effect, or authority declarations. Those
  stay human assertions.
- Auto-filling `agent_bindings` for a root the detector only guessed at.

## Acceptance

- A cold-start adopted repo (framework detected, one decorated tool) ends a
  docs-only turn with no human-review notice.
- The same repo's first capability-changing PR still routes to a human.
- A weak-extraction repo's `verify` names a concrete, agent-executable next
  step, and following it reaches `review_required` rather than another
  abstention.
- `fixture run ai_generated_refund_pr` is unchanged.

## Measurement

The 2026-W27 re-evaluation attributed most of `benign_escalation_rate` 0.286 to
a cold-start whole-repo-surface artifact. Re-run the labeled corpus after (1)
and (2); the expected movement is benign escalation down with
`must_block_caught` and `needs_human_caught` held at 1.0.
