# Design: get first-adoption repos out of `insufficient_evidence`

Status: proposed. Owner: unassigned. Filed from the 2026-07-27 coding-agent UX
re-review, where this became the top remaining friction after contract v19
graded the local boundary stop.

Tracking issue: https://github.com/ThreeMoonsLab/agents-shipgate/issues/292

## Update after implementation (2026-07-27)

Measuring the two real cold-start shapes disproved two premises below and
retired one proposal. What shipped, and why, is recorded here; the original
analysis is kept underneath because the problem statement still holds.

**The two shapes.** A repository with a literal `Agent(...)` resolves its root
and abstains on *semantic* gaps — an undeclared effect, undeclared authority,
an unenumerable surface. A pure-decorator repository (tools, no `Agent(...)`)
abstains on a single *binding* gap, `ambiguous_root_agent`, because nothing
says which agent reaches those tools. Both are IE; the remedies differ.

**Retired: scoping the verdict to the diff (proposal 2).** IE means the static
view of the agent surface is unreliable. A rule of the form "the diff touched
no capability surface, so do not abstain" reasons *from* that same view, which
is circular — and the failure is concrete, not theoretical: a config-bound or
dynamically constructed toolkit (the documented design-partner finding)
presents as a diff touching no recognized capability surface while changing
what the agent can do. Under a diff-scoped rule, precisely the case IE exists
for would escape it. The delta variant ("no new gaps versus base") fails
identically, because an unseeable capability addition yields an identical gap
set. **The verdict therefore stays exactly as it is.** What shipped instead is
attribution: verify compares the base and head gap sets and says whether this
diff introduced a gap or inherited it, so a docs-only turn stops reading as an
accusation about the current change while the gate is untouched.

**Corrected: `init` does not manufacture the binding gap (proposal 1).** The
claim below that omitting `agent_bindings` creates the gap is wrong for the
common case — a repository with one `Agent(...)` resolves its root through the
single-top-level-agent fallback and reports `binding_coverage.gap_count == 0`
without any `agent_bindings` key. Worse, the naive scaffold would *cause*
abstentions: every `CHANGE_ME` line becomes a source warning, and four
warnings crosses the tolerated-source-warnings threshold and trips IE on its
own. And the value `init` has — the `name=` literal — is the wrong selector
anyway: `agent_bindings.root.object` matches the *assignment target* for the
OpenAI Agents SDK, so a scaffold built from the detected display name would
silently never match. No `init` scaffold shipped.

**Shipped instead: make the one-time human declaration cheap.** The decision
engine already generates the exact manifest snippet each gap wants; those
snippets were reachable only by walking `report.json`, which made a
three-line task look like schema archaeology. `suggested-declarations.yaml` now
assembles them next to the report — merged per target, so two gaps on one tool
produce one pasteable row rather than two invalid ones — and every gap that
has a template names the file in its `expects`. Every human-owned value stays
`<REVIEW_REQUIRED>`, and the file says outright that a block still containing
a sentinel closes nothing.

**What surfacing the templates exposed.** Making them readable turned out to be
a correctness audit of the templates themselves, and three were wrong:

- The **binding** template carried a `declarations` row pre-filled with
  `complete: true`, `tools: []`, `handoffs: []` — an assertion that the agent
  definitively reaches *no* tools, offered in a file whose header promised it
  asserted nothing. It now scaffolds root selection only, and a guard test
  enumerates every shipped template and fails on any non-sentinel value in a
  human-owned field (negative-tested against the original block).
- The **selector** template offered a flat `{tool, tool_id, provider}` while
  pointing at `tool_identity`, whose schema accepts only `bindings` entries —
  unfillable anywhere. It emits no template now; inventing a `bindings` row
  would assert that separate observations are one capability, which is the
  reviewed claim the gap is asking for.
- The **authority** template is described below.

A second review then found the deeper version of the same problem: the scaffold
*said* a block still containing `<REVIEW_REQUIRED>` closes nothing, and nothing
made that true. The manifest only checked fields like `authority.auth_type` for
non-blankness, so a pasted-but-unfinished block loaded and was assessed as
reviewed evidence — moving a fixture from `insufficient_evidence` to
`review_required` on placeholders alone. The sentinel is now rejected by the
manifest wherever it appears, naming each unfilled path.

The general rules this produced:

- A template must ask and never answer, and must validate when filled for
  *every* accepted value, not just the convenient one.
- A template is offered only where it repairs the gap that carries it — the
  binding root block is useless for a declarations-level conflict, and
  meaningless in a repository with no agent object to name.
- A rendered selector must resolve exactly one row; a display name does not,
  because two canonical tools can share one.
- **A promise printed in an artifact must be enforced somewhere, or it is
  decoration.** Stating that placeholders close nothing was worth nothing until
  the loader refused them.

**Found by putting the templates in front of a human:** the authority template
offered `authority.mode` alone, but the manifest requires `auth_type` for every
mode except `none`, non-empty `scopes` for `scoped`, and `reason` for
`unscoped` and `ambient` — so a reviewer who filled it in exactly as written got
a config error for every mode they might pick. Nobody had hit it because nobody
could find the template. It now names all the co-required fields, and a
regression test validates the shipped shape against the manifest schema.

**Not closed by this work.** The issue's acceptance criteria asked for a
docs-only turn with no human-review notice and an agent-executable remediation.
Neither shipped, deliberately: the notice follows the verdict, which stays for
the circularity reason above, and these declarations are human-owned by the
`do_not_auto_assert` contract, so no agent-executable path to them exists. What
shipped removes the *dead end* — the remediation is now a concrete, cheap,
one-time human act instead of schema archaeology — and corrects the attribution
so the notice stops implicating the current change. The residual gap belongs to
the host-authenticated approval work, not here.

**Observed end to end.** On a representative cold-start repo the loop now
closes: the scaffold's effect and authority blocks, filled in and merged, clear
`inferred_effect_only` and `missing_authority_evidence`, and the verdict moves
off abstention — in the test repo to `blocked`, because the declared
`financial_write` tool has no approval policy. That is the intended outcome: a
substantive verdict the reviewer can act on instead of "the scan cannot tell."

**Also fixed:** the IE remedy told every framework to declare
`tool_inventories`, but only four frameworks have that key. `openai_agents_sdk`
— the quickstart framework and the most common cold-start case — has none, so
the advice sent readers looking for a key the schema rejects. The remedy now
names the real key when one exists and gives the supported alternative when it
does not.

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

### 1. `init` scaffolds the *question*, never the answer

Binding declarations are in `do_not_auto_assert` for a reason: a manifest that
declares a root agent is asserting what the agent can reach, and the tool must
never write that assertion on a human's behalf. So `init` must **not** emit a
populated `agent_bindings`.

What it can do is stop hiding the requirement. When detection identifies a
framework, `init` writes the `agent_bindings` key with an explicit unresolved
placeholder — the same `CHANGE_ME` affordance the manifest already uses for
`agent.name` — plus a comment naming the accepted values and the detected
candidate as a *suggestion*. The first `verify` then reports "confirm this
declaration, candidate: `support_agent` in `app/agent.py`" instead of "no root
agent matched the configured selector", and the human types the answer.

The distinction is load-bearing: writing `CHANGE_ME` asserts nothing and cannot
satisfy an evidence gap, so nothing downstream can mistake a scaffold for a
declaration.

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
