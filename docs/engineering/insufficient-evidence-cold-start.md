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
a sentinel closes nothing. (That last sentence held until 2026-08-24 — see
*Third round* below, where the values the scan itself observed stopped being
blanks.)

**What surfacing the templates exposed.** Making them readable turned out to be
a correctness audit of the templates themselves, and three were wrong:

- The **binding** template carried a `declarations` row pre-filled with
  `complete: true`, `tools: []`, `handoffs: []` — an assertion that the agent
  definitively reaches *no* tools, offered in a file whose header promised it
  asserted nothing. It was cut back to root selection only, and a guard test
  enumerates every shipped template and fails on any non-sentinel value in a
  human-owned field (negative-tested against the original block). (A
  `declarations` template came back in 2026-08 — see *Second round* below —
  with the assertion removed rather than the block.)
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

**Follow-up (2026-07-28).** The adjacent half of the same cold-start experience
shipped separately: the *adoption* verdict itself. Verify used to greet a first
adoption with "This PR weakens the release policy that evaluates it" and no
`fix_task`, which is the same failure as an unreachable remedy — a correct
routing decision with nothing a reader can act on. Adoption is now proved from
git (the base carries no manifest under any name) and the copy says so, while
the verdict, severity, and check id are untouched. Same principle as the
scaffold: change what the tool *says*, never what it *allows*.

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

## Second round (2026-08-21): the scaffold reaches the scans that need it

Two follow-ups measured against the same walk, on
[google/adk-samples#1917](https://github.com/google/adk-samples/pull/1917) and
[#1745](https://github.com/google/adk-samples/pull/1745): issues
[#361](https://github.com/ThreeMoonsLab/agents-shipgate/issues/361) and
[#388](https://github.com/ThreeMoonsLab/agents-shipgate/issues/388).

**The scaffold existed only after the layer that needed it was already closed.**
It is written whenever a gap carries a template, and until now only *semantic*
gaps did — so the two scans where an adopter is most stuck emitted no scaffold
at all. In an ADK repository whose `tools=[...]` entries are all imported
symbols, stage 1 extracts nothing (six source warnings, each routed to
`review_warning`: no path, no command, nothing to open) and stage 2 reports a
binding gap carrying `declaration_template: null`. The reader hand-authored a
98-line inventory and an `agent_bindings` block, losing one round to an invalid
enum a template would have named.

Two templates close the two stages.

- **Stage 1 — the unenumerable source.** The names are not lost: the loader
  writes them into its own warning prose, and `core.source_warnings` owns both
  halves of that prose, so they are read back through the same exact,
  `repr`-delimited decoder `group_source_warnings` already relies on — never a
  regex over loader text. One `incomplete_surface` row per **source** carries
  the `tool_inventories` entry with `source_id` bound to the source it
  completes, and the skeleton is written with the symbol names pre-filled.
  Per source, not per symbol: six warnings are one mechanism restated six
  times, and hanging a repair off each row would have put raw loader prose back
  into the headline that the grouping work removed.

- **Stage 2 — the unbound catalog.** A resolved root, a populated catalog, and
  not one static edge between them now scaffolds the closed-world
  `declarations` row. The 2026-07 defect was not the block; it was the
  *assertion* inside it. What is pre-filled is only what was read off the
  surface — which agent, which catalog tools exist, which handoffs were
  observed — and `complete` and `reason`, the two values that are a judgement,
  stay `<REVIEW_REQUIRED>`. Merging it verbatim after answering those two
  closes `binding_coverage.gap_count` in one iteration.

  Past 50 tools the template is **withheld, not truncated**. `complete: true`
  claims the listed tools are all the agent can reach, so a silently cut list
  is false precisely where the reviewer cannot see it — the one failure mode
  this template must not have.

**The file users were told to edit was the file that did not name the
vocabulary.** `effect:` and `authority.mode:` were bare blanks while
`report.json` carried their nine and four accepted values *per gap*; completing
twelve tools meant roughly forty-eight values looked up somewhere else. Each
blank now carries a comment above it: the field's `accepted_values` where the
answer comes from a closed set, otherwise the shape it takes and which other
answers make it required.

The vocabulary is rendered **from the gap's own `accepted_values`**, never from
a second copy, so the two artifacts cannot drift. That is possible for exactly
two fields, and the reason is worth writing down: `accepted_values` is
*overloaded* on the wire. For most gap kinds it lists the manifest **keys** a
repair must set (`["agent", "complete:true", "tools", …]`); for
`declare_action_effect` and `declare_action_authority` it lists the legal
**values** of one field. Printing the first reading above a blank would tell a
reviewer that `agent` is a legal `effect`, so only the second is routed, and
the routing table is pinned to the action-kind enum by a test.

**The root block now offers what the scan already computed.** `agent_bindings.
root` handed out two blanks for a value the binding graph holds. It lists the
observed agent objects with their sources, as comments, for a human to confirm.
Note what that also fixes: `object` matches the agent's **declared** name, not
the Python variable it was assigned to — the issue's own worked example guessed
the variable — so the candidate list is the difference between confirming and
guessing wrong. Nothing is filled in; inferring the trust root from AST
evidence remains the self-declaration surface
[#268](https://github.com/ThreeMoonsLab/agents-shipgate/issues/268) closed.

**Two things a comment-carrying renderer has to get right.** PyYAML cannot
carry comments, so the block is emitted by hand — and a hand-rolled emitter is
where a second opinion about YAML style creeps in. A test asserts that
stripping the comment lines back out yields exactly what `safe_dump` writes for
the same template, so the two cannot diverge. And a *name* is repository data:
a tool name holding a newline renders as a multi-line scalar the emitter cannot
indent correctly on its own (it falls back to a JSON-encoded one-line scalar),
and an agent name holding one would close the `#` of a candidate comment and
write a filled-in root selector for a reader to paste — so those are escaped
with `display_literal`, injectively, and an adversarial test holds both shut.

**And the promise is enforced one layer earlier.** `declarations[].complete`
accepts only `true`, so pasting the scaffold unfinished failed with pydantic's
"Input should be True" — a true statement that says nothing about the scaffold
the reader pasted. The placeholder is now rejected from the raw input before
field validation, so one wording covers every field whatever its type. Same
rule as the round before: a promise printed in an artifact must be enforced
somewhere, and the enforcement has to reach the fields people actually paste
into.


### What review found: the prescribed route did not terminate

Two of the six findings on PR #401 were the same mistake seen from two sides,
and both are about **what "this source is repaired" means**.

The route was unreachable. Following it to the end — inventory, binding
declaration, effect and authority for every tool — still returned
`insufficient_evidence`, because the six unresolved-import warnings stayed on
the report and `evidence_below_ie_threshold` gates on their raw count. A
repository that did exactly what it was told had *nothing left to act on* and
still could not be gated. An advertised remedy that cannot terminate is the
same defect this whole document is about, one layer further in.

The first attempt at "repaired" was a catalog-wide tool-name subtraction, and
it was wrong in both directions:

- an unrelated source exposing a tool named `search` silently cleared the ADK
  source's unresolved `search` — a repair nobody had made; and
- an inventory that had correctly *split* a toolset symbol into the tools it
  exposes — which the skeleton's own instruction asks for — never matched the
  symbol, so that source was prescribed the same inventory forever.

The completion relationship is not a name. It is the reviewed
`tool_inventories[].source_id`, already desugared into
`LoadedToolSource.completes_source_id`, and it is *per source*. Withdrawal
happens in the one place holding both halves of it: the loaded sources know
which inventory completes which source, the ADK artifacts know which source
each agent came from. Only the **warning** is withdrawn — the loader's
`surface_gaps` entry stays, so nothing claims static analysis resolved what it
could not and extraction confidence is unmoved.

The obvious worry — silence the warnings by declaring an empty inventory — is
already answered by a different check: `SHIP-INVENTORY-NOT-ENUMERABLE` fires on
the empty surface and routes to `review_required`. The two are independent, and
a test pins that they stay so.

**The rule this adds:** *a remedy the tool prescribes must be able to reach a
verdict.* Whatever the tool asks for, satisfying it has to move the gate — or
the gate is asking for something else and should say so.

Three smaller findings from the same review, each a case of repository data
reaching a sink that could not take it:

- **`complete:` closes the world over handoffs too.** The hint spoke only about
  the tool list, while the scaffold pre-fills observed handoffs and the schema
  treats both as closed. A reviewer could ratify the tools and silently assert
  a downstream agent surface they never looked at. Both hints now name both
  lists.
- **A handoff has no source qualifier.** `handoffs:` is a bare list of names,
  so a target whose name two agents share resolves to neither — the block
  reports an unresolved binding instead of closing the gap it was offered for.
  The template is withheld entirely rather than dropping the handoff, because
  dropping it would understate a closed world the reviewer is about to assert.
- **Two robustness holes with the same shape.** The raw-input sentinel check
  recursed forever on a manifest carrying a recursive YAML alias, replacing a
  structured config error with a stack overflow; and `display_literal` passed
  Unicode noncharacters through, so an agent name containing U+FFFE made the
  generated scaffold unloadable by PyYAML. Both are fixed at the shared layer —
  cycle-safe traversal, and one more class in the escape predicate — rather
  than at the one call site that happened to surface them.

### Known gap: the mixed local/imported surface

Re-review of the fix above found a shape it does **not** close, and the reason
is worth recording because three separate defects sit behind it. An ADK agent
with `tools=[local_tool, remote_tool]` — one defined in the entrypoint, one
imported — follows the generated skeleton, binds the inventory to its source,
and still cannot reach a verdict:

1. **`incomplete_surface` prescribes an inventory for an unproven tool *set*.**
   `_evidence_gaps` already draws that distinction for the `low_confidence_tool`
   row about the same tool ("a tool inventory cannot close this: it describes
   tools, not which tools an agent has"), and `_semantic_gap` does not — so the
   two rows give opposite advice. Worse, after the inventory merges, the
   `source_id` the remediation prints is the *inventory's own* id, which the
   loader then rejects as self-referential.
2. **An agent whose list is not exhaustive still presents a "complete"
   structural set.** `structural_complete` is derived from edge completeness,
   and the one resolved edge is genuinely complete, so a reviewed closed-world
   declaration adding the imported tool — the repair the gap asks for — is
   rejected as "does not match the complete structural tool set".
3. **A framework partial is never superseded by a reviewed declaration.**
   `partials` is a bare `set[str]` with no agent attribution, so even an
   accepted declaration leaves the row standing.

Each was reproduced. None is fixed here, deliberately. (1) is a one-line
routing change, but on its own it points the reader at a target (2) then
rejects — replacing a wrong instruction with a right one the engine mishandles.
(2) and (3) are changes to shared binding-resolver semantics that the OpenAI
Agents SDK adapter also depends on, and the obvious version of (2) — marking
the agent's edges incomplete — drops the tools that *were* resolved out of
`reachable_tool_ids` entirely, so they stop being judged and the per-tool
`low_confidence_tool` rows #393 added disappear. The fix has to separate "this
edge is uncertain" from "this list is not exhaustive", and give `partials` an
agent, before any of it is safe.

**The rule this adds:** *when a remedy cannot be completed, say so rather than
shipping the half of it that fits.* A gate that misdirects is not better than
one that abstains, and a partial fix to shared resolver semantics is exactly
where a silent fail-open gets introduced.

## Third round (2026-08-24): ask only what the scanner cannot prove

`suggested-declarations.yaml` made the task *findable*. It did not make it
*small*, and it did not say when it ends. The fourth `adk-samples#1745` walk met
`0/12` pass-eligible actions and a report describing the work as "24 semantic
evidence gaps" — a symptom count with no order and no finish line — while the
same report already held a derived `financial_write` reading for the one tool
that moved money. Declaring 2 of 12 was enough to reach a verdict; nothing said
which 2.

Three changes, and one idea holding them up.

**Effects the scan observed are pre-filled.** Each effect question now prints
the readings behind it — the distinct effects the evidence supports, each with
the producers that support it — and, where they support one conservative
answer, offers it in the `effect:` line.

**The file is a numbered questionnaire.** `Question 3 of 5` banners, ordered by
how much answering can move the verdict; the same shared progress sentence in
the file header, the CLI, and the PR comment; and a counted question with no
blank to fill (a source conflict) still numbered and shown, so the numbering
never skips.

**A question is not a declaration.** The denominator counts only the `effect`
and `authority` of one `action_surface.actions` row, and `answered` is a
counterfactual: dimensions that gap when the same action is re-resolved
*without* its declaration. An action whose effect an OpenAPI method or an MCP
annotation established was never asked and never enters the total — a
repository cannot improve its progress bar by restating what the scan knew.

### The line this moves, and why it stays safe

The doc above says every human-owned value stays a sentinel. That is now false
for exactly one field, so the reason it is safe has to be mechanical rather
than editorial:

- **nothing consumes the file.** Only a reviewed edit to `shipgate.yaml`, the
  trust root, makes any of it operative, and `auto_apply=false` /
  `requires_human_review=true` are unchanged. The only readers of a
  `declaration_template` are the scaffold writer and a count in the verify
  prose;
- **the value comes from the closed `ActionEffect` vocabulary**, never from
  source content, so no repository can put a word of its choosing in front of a
  reviewer;
- **it is never weaker than any reading.** Confirming one without thinking
  *over*-declares — the safe direction, and one the monotone rule already keeps
  visible;
- **it is offered only where something was observed.** An unannotated MCP
  tool's protocol default keeps its blank: pre-filling `write` from an
  *absence* of evidence would arrive on 117 rows at once, which is the
  blanket-accept the blank was protecting against. A heuristic reading of
  `read` keeps its blank too — every other proposal over-declares, and `read`
  is the one direction where blanket acceptance loses safety.

### What surfacing the proposals exposed, again

The 2026-07 round found that making templates readable was a correctness audit
of the templates. The same held here, twice over.

- **A manifest was allowed to be the source that contradicts itself.** The
  read/side-effect conflict branch compared every authoritative claim that was
  not the declared `effect` — which includes the manifest's own `risk_tags`,
  `scopes`, and acknowledged `override`. Declaring
  `risk_tags: [code_execution]` on a tool published with `readOnlyHint: true`
  was reported as "high-confidence read and side-effect evidence conflict"
  attributed to `tool_source`, when the side-effect half was the reviewer's own
  line. That broke the `risk_tags` repair
  `declaration_below_inferred_evidence` publishes: it could not close the row
  it was printed on. Found by an exhaustive sweep only after the *structural*
  (policy-eligible) dimension was crossed into it — heuristic readings alone
  never reach that branch.
- **A guard stopped guarding.** The test that enumerates every shipped template
  and fails on a non-sentinel value still passed after pre-filling shipped,
  because its fixture built tools with no semantic assessment and never reached
  the proposal path. When an invariant genuinely changes, the test has to state
  the new truth *and* assert that it reaches the new path.

**The rule this adds:** *a progress counter must be measurable on both halves.*
Inventory and `agent_bindings` declarations are human answers too, and they are
deliberately outside the denominator — there is no counterfactual for them,
because removing an inventory does not re-run extraction. Counting what can
only be measured on one side is how a progress bar starts lying, and a lying
finish line is worse than none.

## Fourth round (2026-08-25): the order was ranking the wrong quantity

The questionnaire told the reader what its order meant — "ordered by how much
answering can move the verdict, so the first questions are the ones about
money, outward communication, and destruction" — and then delivered the inverse
of it.

The fifth `adk-samples#1745` walk asked 13 questions. Three already-drafted mail
tools sat at Q2–Q4. `create_sap_sales_order`, the one question that produces
both `critical` blockers the moment it is answered `financial_write`, sat at
**Q6**, behind three drafts a reader working top to bottom confirms first.

### One signal, two mechanisms, opposite directions

Each question was ranked by `effect_evidence_rank(conservative_effect)` — the
effect the scan had already inferred for the action. A pre-filled proposal is
offered *only where something was observed*, which is the right rule for a
proposal and was settled in the round above. But the two mechanisms then ran
off one signal, and nothing said so:

| action | evidence | old rank | proposal |
|---|---|---|---|
| `send_email` | `risk_hint:keyword` → external_communication | 6 | offered |
| `create_sap_sales_order` | none | 1 (the `write` fallback) | blank |

So the questions that arrived with a draft answer systematically outranked the
questions that arrived blank: the cheapest questions first, the most valuable
ones last, and a header sentence promising the reverse.

The ranking was faithful to observed risk. **Observed risk is not the quantity
the header names.** An action nothing was observed about is not a low-risk
action; it is an unmeasured one. Its answer can still turn out to be
`destructive`, and it is exactly where a human answer carries new information.

### Rank by the ceiling

A question is now ranked by the *ceiling* of what its answer can establish
rather than by the floor the scan already inferred. Where something already
bounds the effect, the answer cannot go below it and the old rank still
applies. Where nothing does, there is no bound at all, so the question sorts
above every bounded one (`UNBOUNDED_EFFECT_RANK`). On the same walk the
financial write moves Q6 → Q3, and all three drafts move to the end.

### "Bounded" is not "draftable", and the difference is a whole class of bug

The first draft of this used `effect_is_measured` — the gate
`propose_effect_declaration` already applies — on the reasoning that "measured"
and "could be drafted" are one fact. They are, and that is exactly why it was
the wrong predicate to rank with.

`effect_is_measured` is a **proposal-safety** rule. It returns `False` for every
read-only reading, however authoritative, because a pre-filled `effect: read`
is the one direction where a reviewer confirming without thinking loses safety.
An OpenAPI `GET`, an MCP `readOnlyHint: true` the manifest trusts, and a
reviewed `effect: read` are all *proven* reads — and all three read as "nothing
to propose".

Ranked with it, they went to the **ceiling**, and the name band then broke the
tie among them. A structural `GET` named `delete_account` led the questionnaire,
ahead of a genuinely unknown effect — this round's own defect inverted, with a
repository-chosen name ordering something the scan had established. Found in
review, not by the tests, because every fixture had been built from the walk,
where nothing is a proven read.

`effect_is_bounded` is the ordering question, and it is two clauses because the
resolver records the two cases differently:

- an effect status of `declared`, `structural`, or `conflicting` — the resolver
  established it (or established that its sources disagree, which is still a
  bound). This clause is what catches a reviewed `effect: read`, which leaves
  *no reading at all* behind: declaration claims are excluded from readings on
  purpose, because a row is not evidence about itself;
- otherwise, an observed side effect — the `inferred` action whose keyword hint
  reads `external_communication`.

A heuristic reading of `read` is deliberately in neither: this resolver refuses
to establish a read-only action from a heuristic, so such an action is exactly
as unproven as one with no reading at all. It is the one tier member that
*prints* a reading above its block, which is why the header describes the tier
as what nothing has pinned down rather than as what the scan "read nothing
about".

### A name may order what it may not judge

Within the unmeasured group nothing was observed, so alphabetical order is no
order at all — and that group is the whole questionnaire on a repository that
has just adopted. The tiebreaker is the shape of the action's *name*, in three
coarse bands (mutating, neutral, retrieving), read with the keyword vocabulary
the scanner already owns rather than a second one.

The scanner deliberately does **not** consult those keywords for evidence on
most source types: a Python function called `create_sap_sales_order` is not
proof that it writes anything, and `google_adk` is not in
`_KEYWORD_GATED_SOURCE_TYPES` for precisely that reason. Ordering needs no such
trust, and is given none:

- it is consulted only among actions the scan measured nothing about — never to
  reorder an action it did read;
- the band is inert (`0`) for every measured action, so it cannot cross the
  boundary even by accident;
- a source-level test pins the call sites, because the failure worth guarding
  against is a *future* one: the moment a name-shaped reading is consulted
  where a claim, an issue, or a verdict can see it, an unreviewed reading of a
  repository-chosen string has become evidence.

The `delete_account` GET above is what that second point is worth. The band was
correctly scoped to one tier the whole time; what put a name in charge of a
proven action was the *tier* being wrong, one predicate away. A guard on the
band alone would not have caught it.

Getting a band wrong costs a reader one place in a list they must finish
either way.

### What "never precedes" can and cannot mean

The obvious statement of the fix — *a question carrying a proposal never
precedes a blank one* — is not quite the property to enforce, and enforcing it
literally would break two things worth more.

An action's own `authority` question always arrives blank, and it follows that
action's `effect` question by design: they are one `action_surface.actions` row
and one block in the generated file. Sorting every blank first would separate
them, and the block that answers both would be announced as "Questions 1 and 7"
with no block numbered 7 anywhere in the flow.

The property that *is* enforced is the one the mechanism actually has: **every
question the scan could draft an answer for sorts after every question about an
action it measured nothing about.** Since drafting and measuring are one
condition, that is the same statement with the ambiguity removed — and it is
what the header now says.

**The rule this adds:** *rank by what an answer can still establish, not by what
you already inferred.* Where those two differ, the second one ranks a
questionnaire by how much it already knows, which is the reverse of what the
reader is there to do.

**And the one it adds twice:** *a safety rule is not a general-purpose
predicate.* `effect_is_measured` is exactly right for deciding what may be
pre-filled and exactly wrong for deciding what is known, and the two read as
the same sentence right up until an OpenAPI `GET` sorts above an unknown. When
a predicate's name describes its answer rather than its question, borrowing it
is how the borrower inherits a rule it never wanted.
