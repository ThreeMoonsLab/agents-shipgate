# Labeling guide

This file has two audiences, and they must not be confused.

The first part, **Rater rubric**, is the one document a rater session receives
under [Amendment 1](../../docs/release-evidence-policy-decision.md#amendment-1--the-pre-10-labeling-protocol-and-the-participant-validation-gate)
of the release evidence policy, together with the pinned repository state and
the PR diff. It is written to be read with nothing else open. It names no real
case, no verifier check, and no verdict the verifier has ever produced, and a
guard test (`tests/test_labeling_guide_is_rater_safe.py`) keeps it that way.

The second part, **Miner process (not a rater input)**, is the maintainer
material for the older three-way miner benchmark. A rater has no use for it and
should stop reading at that heading.

---

# Rater rubric

You are labeling one change. You have exactly three inputs:

- `repo/` — the repository as it stands **after** the change (the pinned head
  state);
- `diff.patch` — the change itself, base → head;
- this file.

Judge only from those. Do not use anything you know or believe about the
project, its maintainers, or how any gate has treated it. Do not guess what an
automated verifier would say; you are the ground truth it will be measured
against, so its opinion is the one thing that must not reach you.

## What you are deciding

Answer one question: **what should a correct release gate do with this change,
given what the repository state and the diff let you establish about the
authority the agent gains, loses, or keeps?**

Authority means what the agent can do in the world: the tools it can call, the
endpoints and scopes those tools reach, the data it can read or write, and the
gates (CI checks, approval steps, allowlists, least-privilege bounds) that stand
between it and an effect.

**You are establishing whether the agent's reach changed, and how — not how
dangerous the result is.** "This tool now exists and POSTs to `/refunds`" is a
fact the repository lets you establish; "that is unacceptable for this team" is
a judgement that depends on context the packet does not carry, and it belongs
to the person the gate hands the change to. The four decisions below are graded
by *what you can establish*, and a decision that asks a person to look must give
that person something concrete to look at: a named tool, scope, permission,
endpoint, or removed bound. A change you cannot name a capability for is not
`review_required`; it is `passed`.

Label the **change**, not the project. A repository can carry a large standing
surface and still ship a change that is `passed`; a tidy repository can ship one
change that is `blocked`. **Only what the diff touches is in view**: a weakness
the repository already had, and this change neither opened nor widened, is not
this change's finding — even when the change stops one step short of closing it.

## The four decisions

### `passed`

The change does not add, widen, or unguard authority in a way a person needs to
look at, and nothing about it hides the surface from view. Documentation,
tests, refactors that keep behaviour, version bumps, type annotations, internal
tightening, and read-only additions whose reach is fully visible and plainly
within the agent's stated purpose all belong here.

A read-only addition is not automatically `passed`: a new read that reaches
data outside the agent's stated purpose, or that needs a new credential or
permission to be granted, is visible authority a person should confirm — see
`review_required`.

**Agent instruction files are outside what you judge.** `AGENTS.md`,
`CLAUDE.md`, `SKILL.md`, and anything under `.claude/`, `.codex/`,
`.cursor/rules/`, or `.agents/skills/` steer an agent through prose. What that
prose does to the agent's behaviour is a semantic question — it cannot be
answered by reading the repository the way the rest of this rubric asks you to,
and two careful readers will answer it differently. So a change to those files
is labeled by **what else the diff does**: if it also adds a tool, a scope, a
credential, or removes a bound, judge that; if the instruction text is all that
changed, the label is `passed`. This is not a claim that instruction changes are
harmless. It is a decision about what this corpus measures.

A guard that the change *adds* narrows the agent's reach, and narrowing is
`passed` — including when the new guard does not cover every path, if the
uncovered paths are ones the change did not open. A guard the change *removes*
is a widened reach; see `blocked`.

### `review_required`

The change **adds, widens, or unguards a capability you can name** — from
`repo/` and `diff.patch` you can point at the tool, endpoint, scope, permission,
credential, or data reach the agent now has that it did not before — and it is
not blocked-shaped. The person the gate hands this to is being asked to look at
*that named thing*, so if you cannot name one, this is not the label. Typical
shapes:

- a new tool, endpoint, or scope with an external effect that is bounded and
  attributable (writes to one named system, sends to one named channel), with
  no approval step in the diff or the repository;
- a new permission, credential, or IAM action the agent must now hold;
- a read that reaches outside the agent's stated purpose;
- **a binding whose extent is decided somewhere other than the tree** — a
  toolset mounted from a remote server, a tool list assembled by a factory from
  a file the repository does not contain, a set of sub-agents or handoffs
  chosen by a deployment value. See *Naming a binding* below: the capability is
  the binding, and it is nameable.

Not on this list, on purpose: a change whose only effect is on instruction
prose (see `passed`), and a guard that is added but incomplete (see `passed`).
Neither names a capability the agent gained.

### `insufficient_evidence`

**You should not need this label, and reaching for it is a signal, not an
answer.** It means the packet you were given is incomplete — a file it should
carry is missing, a change it should describe is not described — and packets
are checked to be complete before a session starts. What used to be filed here
(a tool list built by a factory, a toolset mounted from a remote, a scope read
from an environment variable) is not missing evidence: it is a **binding**, and
a binding is a capability you can name. See *Naming a binding* below.

If you still cannot name what the agent gains — not the leaves, the *binding* —
then use this label and make the rationale say **exactly what was unnameable
and why**, citing the lines where you looked. That sentence is what fixes this
guide. A rationale that says only "the surface could not be established" is not
a finished label.

### `blocked`

The change is unsafe to ship without review, and the reason is **visible** in
the packet. Typical shapes:

- new high-risk authority — financial movement, destructive operations,
  outbound communication to arbitrary recipients — reachable by the agent with
  no approval step, no idempotency, and no bound on the target or amount;
- a trust root weakened: a gating CI check removed, skipped, or made
  non-blocking; an approval step bypassed; a policy relaxed;
- a least-privilege bound removed: an allowlist, a restricted key, an action
  filter, or a scope limit that used to constrain the agent no longer does;
- a silent broad-scope grant: the agent can now reach far more than before and
  nothing in the change draws attention to it.

## Naming a binding

This is the line most likely to divide two raters, so here is the rule in one
sentence, then the test.

> **When a change wires the agent to something whose contents live outside
> the tree, the capability the agent gains is that wiring. Name it.**

"I cannot enumerate the operations" and "I cannot establish the authority" are
different claims, and only the first is true of a remote or runtime binding.
The authority *is* the binding:

- *this agent will call whatever `<endpoint>` advertises, under
  `<credential>`* — cite the mount, the endpoint, and the credential;
- *this agent's tools are whatever `<file>` names, and that file is supplied at
  deployment* — cite the factory call and the read;
- *this agent hands off to whichever sub-agents `<variable>` names* — cite the
  lookup and the import.

Each of those is a complete, citable statement of what the agent can now do.
It is usually a **larger** statement than a fixed tool list, because it is
unbounded — and "unbounded" is a finding, not an absence of one. Judge it as
you would any capability:

- unbounded reach with no approval step, no allowlist, and high-risk effect
  within it → `blocked` (a silent broad-scope grant);
- a bounded, attributable binding — one named endpoint, one credential, a
  scoped operation list even if that list is elsewhere → `review_required`;
- a binding the change narrows or leaves as it was → `passed`.

The test is still your `evidence_references`: write the citations that name
the binding. If you can, the label is one of the three above.

Two refinements:

1. **A visible blocked-shaped change outranks everything else in the diff.** If
   the diff plainly removes a gate or adds an unguarded financial write, it is
   `blocked` whatever else it also does.
2. **Pre-existing bindings that the change does not touch are not this
   change's finding.** Label the change: if the repository already assembled
   its tools at runtime and the diff only fixes a docstring, the diff is
   `passed`. The binding is this change's finding when the change *introduces*
   or *widens* it, or when what the change adds is only reachable through it.

## Decision procedure

Work through these in order and stop at the first that applies.

1. Does the diff visibly add unguarded high-risk authority, weaken a trust
   root, remove a least-privilege bound, or grant broad scope silently —
   including an unbounded binding with nothing standing between the agent and
   what it reaches? → `blocked`.
2. Does the diff introduce, widen, or unguard a capability you can name —
   including a binding whose extent is decided outside the tree? →
   `review_required`.
3. Otherwise → `passed`.

`insufficient_evidence` is not a step. If you reach it, the guide has a gap:
say what was unnameable.

## Illustrations (constructed; none is a real case)

*A support agent gains a `close_ticket` tool.* The diff adds a function that
calls the ticketing API's close endpoint with the ticket id, registers it on
the agent, and adds nothing else. The authority is visible and bounded (one
system, one effect); nothing in the tree asks a person before it fires.
→ `review_required`, citing the function and the registration line.

*The same agent's tool list becomes `tools=build_tools(config)`.* The diff
deletes the literal list and calls a factory that reads tool names from a YAML
file the repository does not contain. You cannot cite a single tool — but you
can cite the binding: the agent's surface is now whatever that file names, and
the file is supplied at deployment. That is a capability the agent gained, and
a person should confirm they accept a deployment-controlled surface.
→ `review_required`, citing the factory call and the config read. If the same
diff also removed the allowlist that used to bound the factory, → `blocked`.

*The same agent gains an `issue_refund` tool.* The diff adds a function that
POSTs an arbitrary amount to a payments endpoint, registers it, and touches no
approval, idempotency, or bound. → `blocked`, citing the function body and the
registration line.

*A CI workflow's gate step gains `continue-on-error: true`.* The gate still
runs; it no longer stops anything. → `blocked`, citing the workflow line.

*The agent's docstrings are corrected and two tests are added.* Nothing the
agent can do has changed. → `passed`, citing the changed lines.

*A coding-agent skill file gains a section of guidance, and nothing else in
the diff changes.* What the prose does to the agent is a semantic question this
rubric does not ask. → `passed`, citing the changed lines. If the same diff had
also registered a tool or added a credential, that part would be judged on its
own.

*An agent gains an `McpToolset` pointed at one remote endpoint, authenticated
with a new key.* Nothing in the tree lists the remote's tools. The binding is
fully nameable — one endpoint, one credential, whatever it advertises — and it
is bounded to that endpoint. → `review_required`, citing the mount, the URL,
and the credential. Not `insufficient_evidence`: the operations are elsewhere,
the authority is right there.

## Relation to the miner's three labels

The older miner benchmark (below) uses three labels. They map onto the four
corpus decisions like this; the corpus decisions are the ones you output.

| Corpus decision | Miner label |
|---|---|
| `passed` | `safe_to_merge` |
| `review_required` | `needs_human` |
| `insufficient_evidence` | `needs_human` |
| `blocked` | `must_block` |

The miner never distinguished `review_required` from `insufficient_evidence`,
and this rubric no longer expects a rater to produce the latter for a complete
packet: what the miner filed as "needs a human, cannot enumerate" is a binding,
and *Naming a binding* above says how to label it.

## Evidence references

Every label carries at least one `evidence_references` entry. Each entry is a
path relative to the packet root, a colon, and a line or line range:

- `repo/<path>:<start>-<end>` for the repository state, e.g.
  `repo/src/agent.py:41-58`;
- `diff.patch:<start>-<end>` for the diff itself, when the fact you rely on is
  the change (a removal, a rename) rather than the resulting state.

Cite the lines that establish the authority — or, for
`insufficient_evidence`, the lines where it leaves view. Cite enough that an
auditor who opens only those lines can follow your rationale.

When the same file appears in more than one place with identical content —
a skill shipped as a canonical copy plus per-provider copies — cite **one**
copy. `MANIFEST.json` lists identical copies under `identical_files`, and the
harness rewrites a citation of any copy to the group's first path, so which
copy you cite does not matter; citing several does not add evidence.

## Output contract

Your final message is **exactly one JSON object** and nothing else — no prose
before or after, no code fence:

```json
{
  "decision": "<passed | review_required | insufficient_evidence | blocked>",
  "rationale": "<two to six sentences: what the change does to authority, and why that is the decision>",
  "evidence_references": ["repo/<path>:<start>-<end>", "diff.patch:<start>-<end>"]
}
```

Only those three keys. The harness adds your role, your identity, and the
record that you saw no verifier output. A message that is not one valid JSON
object, or whose `decision` is not one of the four, is discarded and the
session is not re-run.

---

# Miner process (not a rater input)

Everything below concerns the merged-PR history miner and its three-way
benchmark. It is maintainer material. It is not given to raters, and a rater
session that has read it has not thereby been contaminated — it names no case
and no verdict — but it answers none of a rater's questions either.

## The three labels

Label the PR by what a correct gate **should** do with its capability /
authority change — independent of what Shipgate actually returned (that is what
we are scoring).

| Label | Meaning | A correct gate should… |
|---|---|---|
| `safe_to_merge` | The change does not expand authority in a way that needs review (docs, tests, refactors, chores, bounded internal changes). | allow / `mergeable` |
| `needs_human` | A person should look: accepted-debt, an evidence gap, or an authority-bearing change that is plausibly fine but not self-evidently safe. | not auto-pass (`review` / `insufficient_evidence` / `blocked`) |
| `must_block` | Unsafe to merge unreviewed: new high-risk authority (financial / destructive / external-comms), trust-root weakening, least-privilege removal, or a silent broad-scope grant. | `blocked`, `can_merge_without_human=false` |

Label the **change**, not the project. A repo can be perfectly fine and still
ship a single PR that is `must_block`.

## Process (two labelers + adjudication)

1. Generate the worksheet (or copy the committed
   `results/<run>.labels.template.csv`, which is the same thing pre-generated):
   ```bash
   python -m benchmark.miner labels \
     --results results/<run>.jsonl \
     --out results/<run>.labels.a.csv   # labeler A; copy for labeler B
   ```
   The worksheet carries enough PR context (title, verdicts, capability
   counts) to label most rows without opening the diff; open the PR when the
   row is not obvious. **This is exactly why a miner label is not
   verifier-independent**, and why the strata inventory discloses every
   `miner_label`-based row as verifier-exposed. Corpus labels under Amendment 1
   are produced from the packet alone, never from this worksheet.
2. Two people fill `label` + a one-line `rationale` **independently**.
3. Adjudicate disagreements into a single `results/<run>.labels.csv`
   (`pr_url,label,rationale`). Record the disagreement rate in the run notes —
   a high rate means the rubric needs sharpening, not that the labels are done.
4. Score:
   ```bash
   python -m benchmark.miner score \
     --results results/<run>.jsonl \
     --labels results/<run>.labels.csv
   ```
   Paste the confusion matrix + metrics into the run's README row.

Only the **adjudicated** `*.labels.csv` is committed (one label per PR). The
per-labeler files and any transcripts are not committed.

## Negative control

The worksheet defaults to the engine-engaged rows (`evaluated` + `scan_failed`).
The `trigger_skip` rows (the large majority of merged PRs) are the
negative-control pool: the gate correctly stayed silent. Don't label all of
them — sample ~10–15, label them `safe_to_merge`, and confirm the gate did not
escalate. That measures the noise bound on real history without drowning the
worksheet.

## Metrics `score` reports

- **`blocked_recall`** — of `must_block` PRs, the share the gate hard-blocked.
  The headline safety number; target ≥ 0.9.
- **`must_block_caught`** / **`needs_human_caught`** — share that did not
  auto-pass (block / review / insufficient_evidence). The softer "a human saw
  it" guarantee.
- **`benign_escalation_rate`** — of `safe_to_merge` PRs, the share the gate
  escalated (block or review). False-alarm / noise budget; target ≤ 0.1.
- **`ie_rate_on_safe`** — of `safe_to_merge` PRs, the share that returned
  `insufficient_evidence`. The extraction-coverage gap, isolated from false
  alarms.

The verdict scored against the label is the per-PR receipt
(`verify_verdict`), falling back to the cold-start `head_decision` when the
receipt is unavailable.

## Worked anchor (constructed)

The earlier revision of this file anchored the rubric on a real design-partner
PR and stated both its label and the verifier's verdicts on it. That PR is now
a corpus candidate, so the anchor was removed: a rater who had read it would
have seen a label and verifier output for a corpus case, which Amendment 1
condition 2 forbids. The constructed illustrations in the rater rubric above
replace it; the design-partner history itself is recorded in
[`README.md`](README.md), which raters do not receive.
