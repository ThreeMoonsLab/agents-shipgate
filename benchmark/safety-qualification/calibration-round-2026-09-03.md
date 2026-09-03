# Cut C — calibration round, 2026-09-03

[Amendment 1](../../docs/release-evidence-policy-decision.md#amendment-1--the-pre-10-labeling-protocol-and-the-participant-validation-gate)
condition 5 ran on the five non-corpus cases in [`calibration.md`](calibration.md)
before any corpus label exists. This is its record. It states what the round
found and what the guide needs as a result; **the decisions themselves are the
owner's**, because each one changes what 56 labels will mean.

The labels, transcripts and packets are working material and are **not
committed**, per `calibration.md`. They are on the owner's machine at
`/private/tmp/cal-round-2026-09-03/` — a volatile path; move it before it is
needed again.

## How it ran

| | |
|---|---|
| `security_governance` | `claude` — `claude-opus-5[1m]`, CLI 2.1.259 |
| `framework_tooling` | `openai` — `gpt-5.6-sol`, `codex-cli` 0.153.0 |
| Mode | `--home-mode shared` (both logins are OAuth) |
| Cases | `cal-1` … `cal-5`, 10 packets, 10 sessions, 10 admissible labels |

Every session was blind per condition 2 and every label carries a
content-addressed transcript per condition 3. The two families ran
concurrently, so `claim_family` was exercised for real: on each case whichever
role lost the race recorded `checked against <other> (<family>)` and the
winner recorded `unchecked`, which is the intended shape.

## The headline: κ = 0.44

Observed agreement 3/5 (`p_o` = 0.600), expected 0.280, **Cohen's κ =
0.4444**. The 0.80 floor is a corpus requirement and does not apply to
calibration — but a corpus labeled against *this* guide would be odds-on to
miss it, and "a κ failure discovered after 56 labels is a relabeling of 56
cases" is the sentence condition 5 exists for.

The disagreement is not noise. It has a direction:

| | `passed` | `review_required` | `insufficient_evidence` | `blocked` |
|---|---|---|---|---|
| `security_governance` (claude) | 3 | 1 | 0 | 1 |
| `framework_tooling` (codex) | 1 | 3 | 0 | 1 |

Both splits are the same way round — claude `passed`, codex `review_required`.
Two raters applying the same text to the same evidence and landing
consistently on opposite sides of one threshold is a property of the text.

## Finding 1 — the guide contradicts itself on a guard that is added but incomplete

**This is the round's most valuable result, and it is a textual contradiction,
not a judgement call.** Both raters established the same facts about `cal-1`
and cited the same lines. The guide told them different things:

- the `review_required` list contains *"a guard that is added but does not hold
  in every path the repository shows (**a confirmation the client may not
  support**, a check that is skipped under a flag)"* — which describes the case
  exactly, down to the parenthetical;
- refinement 2 says *"Pre-existing opacity that the change does not touch is
  not this change's problem"* — and the hole in the new guard is inherited, not
  introduced.

Each rater followed one of them. Nothing in the guide says which wins.

**The owner's decision.** Either:

- **(a) the added guard is judged on its own completeness** — a change that
  adds a guard which does not hold on every path the repository shows is
  `review_required`, whether or not the hole predates it; or
- **(b) the change is judged on the delta** — a strict tightening is `passed`
  even where the tightening is incomplete.

**Recommended: (a).** Refinement 2 is written about *opacity* and belongs to
`insufficient_evidence`; stretching it to cover guards makes the
`review_required` bullet's own parenthetical unreachable, because a partial
guard is almost always partial for reasons that predate the change. A reading
that turns an explicit bullet into dead text is the wrong reading, and for a
release gate it errs in the wrong direction.

## Finding 2 — whether an instruction's *direction* changes its label

On `cal-3` the guide's bullet is categorical: *"an instruction or skill file
that tells a coding agent to take actions (install software, run commands,
fetch and follow further instructions)"* → `review_required`, and the
illustration says the same. One rater applied an exception the guide does not
contain: the prose only *tightens* (prefer restricted keys, add IP allowlists,
keep keys out of logs), therefore `passed`.

**The owner's decision.** Either the rule is categorical, or the direction of
the advice matters.

**Recommended: categorical**, stated in one added sentence. Judging direction
asks the rater to assess the *merit* of the instruction, which is the judgement
the guide exists to route to a human. And the "tightening" prose here still
directs real action on credentials — set up a pre-commit hook, replace a secret
key, rotate and expire the old one.

## Finding 3 — the `review_required` / `insufficient_evidence` line was never exercised

**`insufficient_evidence` was chosen 0 times in 10 labels.** #508 names this
line as the thing the round must settle, and the round did not settle it: it
was never the deciding factor in either disagreement.

What that does and does not tell us: the negative result is real — `cal-4`
existed to catch a rater reaching for `insufficient_evidence` on a plain type
annotation, and neither did. The drafted rule that *distinguishes* the two,
however, remains untested by evidence.

**Recommended: one more constructed case and a second, smaller round.** A
change that introduces a runtime-assembled tool list forces the line to be
drawn rather than stepped around; two more sessions is a cheap price against
the alternative, which is 56 labels produced against an untested rule.

## Finding 5 — the two families were not blind in the same way

Running the round is what exposed this, and it is a harness defect rather than
a guide one. The Claude side has no shell: `--tools Read,Grep,Glob` removes it.
The codex side *is* a shell agent, and `--sandbox read-only` restricts **writes
only** — probed on 0.153.0, a session read a file outside its working root and
printed the contents, and that version offers no setting that narrows reads.
Nothing stopped a codex rater from reading the checkout, where the strata
inventory names a target decision for every slot.

**These five sessions did not.** Every command each one ran is in its archived
transcript, all of them are relative paths inside the packet, and all five pass
the new check. So the round's labels stand — but "did not" is not what
Amendment 1 asks for, which is blindness *mechanically enforced*.

`commands_that_reached_outside` now refuses a label when a recorded command
names the checkout, the round's output directory or a sibling packet, or walks
out with `..`. It is detection rather than confinement, and it is narrow on
purpose: flagging every absolute path would refuse a session for saying
`/usr/bin/grep`, and a guard that refuses real work is one an operator turns
off. A script that builds a path at run time still evades it; the compensating
control is the one condition 3 already requires, the archived transcript.

**This is worth the owner's attention before the corpus**, because the honest
options are to accept detection-plus-audit for the codex family, or to run it
under an OS-level sandbox or container so the read boundary is real.

## Finding 4 — which copy to cite when a change lands in N identical trees

`cal-3` ships the same file three times (the canonical skill and two provider
plugins). The two raters cited different copies of identical content. Harmless
here, but `evidence_references` are what an adjudicator re-opens, so the guide
should say: cite the canonical copy, and note the duplication rather than
citing each.

## The owner's rulings (2026-09-03) and where each landed

| # | Ruling | Landed in |
|---|---|---|
| 1 | **Judge the diff.** A weakness the change did not open or widen is not its finding. Wider than the guide: a `review_required` must name a capability the agent gained; *whether it is risky* is the human's call and is not what the gate establishes deterministically. | `LABELING.md` — "What you are deciding" rewritten; `review_required` redefined as "a capability you can name"; the incomplete-guard bullet removed, its case moved to `passed`. Engine: [#515](https://github.com/ThreeMoonsLab/agents-shipgate/issues/515) (`--scope diff` by default). |
| 2 | **Agent instruction files are out of scope.** Their effect is semantic; a static gate cannot judge it and would only produce false positives. Skip rather than escalate. | `LABELING.md` — instruction files labeled by what *else* the diff does, else `passed`; the bullet and the illustration removed. Engine: deprecation issue for `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED`. |
| 3 | **`insufficient_evidence` only when it names what would resolve it.** Otherwise it is not a finding. | `LABELING.md` — the requirement added to the definition and the line test; a second illustration contrasts a named gap with an unfinished label. Engine: scoring issue — an IE the gate cannot attach a missing input to is scored wrong. |
| 4 | **Identical copies: make it mechanical.** | `build_packet` groups byte-identical files in `MANIFEST.json` under `identical_files`; `run_rater` rewrites any copy's citation to the canonical path and records what was cited. Adjudicators never see the phantom disagreement. |
| 5 | **The codex read boundary: solve it as context management, not sandboxing.** Do not have the answer key where the rater can reach it. | `run_rater` refuses a shell-bearing rater on a host that carries `strata-inventory.csv`; `--working-material` proceeds for calibration and records that on the label. Corpus labels for the codex family are produced on a host without the checkout. The transcript audit stays as the second line. |

Rulings 1–3 change what 56 labels will mean, so the corrected guide is run
again on the five calibration cases before any corpus label — see the
re-run record below.

## What is not in scope of these corrections

No threshold moves. The κ floor, the four decisions and their meanings are
fixed by the base decision and Amendment 1; what changes is only where the
guide is silent or self-contradictory.
