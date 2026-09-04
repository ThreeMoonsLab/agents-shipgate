# Cut C — corpus round 2, 2026-09-03: the corrected guide

Same 48 cases, same two families and models, same answer-free host layout,
same sharding — the only thing changed is `LABELING.md`, carrying the owner's
sixth ruling (#520): *a binding is a capability you can name;
`insufficient_evidence` is a signal, not a step.* Packets rebuilt so the guide
inside each is that text, byte-identical.

## Result

```
47 complete pairs   agreement 0.872   Cohen's kappa = 0.8048   (floor 0.80)
round 1, same 47:   agreement 0.702   Cohen's kappa = 0.6036
```

| decision | claude (`sg`) | codex (`ft`) | round 1 (claude / codex) |
|---|---|---|---|
| `passed` | 13 | 15 | 14 / 14 |
| `review_required` | 23 | 19 | 18 / 12 |
| `insufficient_evidence` | **0** | **1** | 5 / 13 |
| `blocked` | 11 | 12 | 11 / 9 |

`insufficient_evidence` went from 18 labels to 1, and the one remaining is on a
case the inventory sourced as `insufficient_evidence`; claude called the same
case `review_required`. The predicted movement happened: the four cases both
raters had filed as `insufficient_evidence` in round 1 are now `review_required`
or `blocked` from both, naming the binding.

**Six disagreements remain**, down from fourteen, and they are no longer one
axis. Four are on cases the inventory sourced as `insufficient_evidence` —
which is what #520 predicted those slots would become once the label they were
sourced against stopped existing: the two raters now disagree about whether the
binding is a capability worth a human (`review_required`) or nothing changed
(`passed`). That is a genuine judgement call the owner adjudicates, not a guide
contradiction.

## The one case without a `security_governance` label

`coding_agent_trust_roots.review_required.2` was refused three times, from
three independent claude sessions at three fresh packet paths, for the same
reason each time: the final JSON carried `decision` and `rationale` and omitted
`evidence_references`. In one of the three attempts the citations appear inline
in the prose (`diff.patch:1-69`, `repo/skills/…/SKILL.md:13-17`); in the other
two there is no citation anywhere. The harness refused correctly: the contract
is exactly three keys, and the guide says every label carries at least one
reference, `passed` included.

It is not random (three of three on this case; three of forty-eight overall in
this round, the other two recovering on a second attempt) and it is not the
ruling (the decision each time was `passed`, on a change to instruction files
only — ruling 2's shape, where the rater seems to reason there is "nothing to
cite" and drops the array rather than citing the changed lines as the guide
says). Two things were deliberately **not** done: the parser was not loosened
to lift citations out of prose, which would be inventing evidence; and
`TASK.md` was not forked for one case, which would give one rater a different
instruction from the other forty-seven. The case is reported as it stands.

**κ on 47 is 0.8048.** The 48th pair could move it to either side of 0.80. That
is the honest state, and it is recorded rather than rounded.

## What is still open

- The 48th label above.
- The inventory and requirements have not yet been changed to match the ruling:
  28 → 21 cells, `minimum_insufficient_evidence_exact` removed, the twelve
  cold-start slots retired (the owner's ruling), and the twelve
  `insufficient_evidence`-sourced slots among the 48 re-targeted from what the
  raters established. That is one PR against `src/` schemas and
  `tests/test_strata_inventory.py`, and it follows this record rather than
  preceding it.
- Adjudication of the six, by the owner as third identity.

## Adjudication, and where the corpus now stands

The owner adjudicated all six as third identity on 2026-09-04, **disclosing a
personal walk on every one** (condition 4). Each upheld one of the two
primaries, so each frozen record carries that rater's citations — siding with a
rater is adopting their evidence, and nothing was invented for the schema's
`evidence_references` requirement.

| case | claude | codex | final | upheld |
|---|---|---|---|---|
| `coding_agent_trust_roots.insufficient_evidence.2` | `review_required` | `passed` | **`passed`** | codex |
| `langchain_crewai.review_required.2` | `passed` | `review_required` | **`passed`** | claude |
| `mcp_openapi_declared_binding.insufficient_evidence.1` | `review_required` | `passed` | **`review_required`** | claude |
| `mcp_openapi_declared_binding.insufficient_evidence.2` | `review_required` | `insufficient_evidence` | **`review_required`** | claude |
| `multi_agent_handoffs.blocked.1` | `review_required` | `blocked` | **`blocked`** | codex |
| `openai_agents_sdk.insufficient_evidence.2` | `review_required` | `passed` | **`review_required`** | claude |

**Not one adjudication landed on `insufficient_evidence`**, including the four
on slots the inventory had sourced as that. Forty-seven cases now carry a final
decision — 41 by agreement, 6 adjudicated — and none of them is
`insufficient_evidence`. The sixth ruling holds all the way through.

### Blocker 3 — the strata no longer balance

Removing the label redistributed the cases, and the distribution is not the one
the corpus needs. Against 21 cells at two cases each:

| profile | `passed` | `review_required` | `blocked` |
|---|---|---|---|
| `mcp_openapi_declared_binding` | 2 | 5 | 2 |
| `openai_agents_sdk` | 2 | 4 | **1** |
| `langchain_crewai` | 3 | 2 | **1** |
| `google_adk` | 2 | 4 | **1** |
| `n8n` | **1** | 2 | 4 |
| `multi_agent_handoffs` | **1** | 3 | 2 |
| `coding_agent_trust_roots` | 3 | **1** | **1** |

**14 of 21 cells reach two cases; seven do not.** Totals are not the problem —
47 finals against 42 needed — the shape is: `review_required` runs to 21 while
`blocked` falls to 12, and three profiles hold a single `blocked` case each.

This is a sourcing result, not a labeling one, and it could not have appeared
before the labels existed: every slot was sourced against a target decision,
and the blind raters put the cases somewhere else. The `insufficient_evidence`
slots that dissolved were carrying weight in cells that are now short.

It is the owner's to settle, and it is the last thing between here and a
freeze. Three directions, none of them a threshold change: source new
candidates into the seven short cells (Cut B work, in the profiles named
above); lower the per-cell count where the material genuinely does not exist,
and say so; or narrow the profile set. The requirements change this ruling
implies (28 → 21 cells) is deliberately **not** committed yet, because writing
two-per-cell into `pre_release_safety_requirements()` today would encode a
target the corpus is known not to meet.
