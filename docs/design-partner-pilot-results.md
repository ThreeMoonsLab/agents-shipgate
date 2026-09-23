# Design Partner Pilot: Results

The public, aggregate ledger for the experiment described in
[`design-partner-verifier-pilot.md`](design-partner-verifier-pilot.md). That
page is how to observe; this one is what was observed.

Nothing identifying appears here without the specific consent it requires.
Aggregate counts never require consent; names, source links and raw artifacts
always do.

**Status date: 2026-09-14.** No external repository has been enrolled. The
sections below say why, in denominators rather than adjectives.

## Denominators

| Denominator | External | Note |
| --- | ---: | --- |
| `invited` | 0 | no consent-based invitation issued |
| `attempted` | 0 | |
| `first_valid_result` | 0 | |
| `first_value` | 0 | |
| `second_change_eligible` | 0 | |
| `second_change_observed` | 0 | |

Dogfooding is reported separately below and never enters these counts.

These six counts are this experiment's, not the project's adopter count.
Public, consenting adopters are listed and counted in
[`ADOPTERS.md`](../ADOPTERS.md). The two ledgers answer different questions
under different consents — a pilot row is an observation we made, an adopter
entry is a statement someone else chose to publish — so they are never added
together, and a pilot participant appears in the registry only by adding
themselves.

## Enrollment and opportunity shortfall — 2026-09-05

Zero invitations have gone out. The first version of this section, dated
2026-09-04, gave the cause as *no installable build could run the route to
first value*. **That was wrong, and the correction is the more useful
finding.** It surveyed two channels — the PyPI release and the source
checkout — and there are three. An unqualified preview wheel, published
2026-09-03, carries the full evaluator and is installable by anyone with `gh`.
The route was invitable the day the claim was written; what was missing was a
decision about whether to put an explicitly unqualified build in front of an
external partner, which is a different question with a different answer.

[#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) landing is
what surfaced it: its channel table in the runbook names all three. The
lesson for this ledger is not "re-measure after a release" — the standing
guard already forces that — but that a survey can be complete about the
channels it looked at and still be wrong about the world.

So the shortfall stands, with a narrower and honest cause: **choosing the
channel is a decision nobody had made.** The released build cannot show this
change class; the preview can but carries no qualification of any kind. That
choice is made below, and invitations follow it.

### Route readiness dry run (dogfooding)

Fixture: a synthetic Route H repository — `.claude/settings.json` plus
`.mcp.json`, no tool surface of its own. The change under review widens the
allow list from `Bash(npm test)` / `Read(src/**)` to `Bash(*)` / `Read(**)` /
`WebFetch(*)` and adds a remote MCP server `payments-remote`. That is the
capability-change class this pilot exists to observe.

**Published build measured: `1.1.0`.** Preview measured:
`0.16.0+preview.20260903.gb61aca7`. Source tree: `1.1.0`, runtime contract 41.
The released and source-tree columns were both rerun on 2026-09-22, after
`v1.1.0` was published: the released column from `pip install
agents-shipgate==1.1.0` in a clean virtualenv outside any checkout (the wheel
PyPI serves, sha256
`038bdb4650d45d9c81996f60d33781b5671bfb57f006233f80e74db8a7377d33`), the
source-tree column through `./shipgate`. The preview column retains its
2026-09-05 measurement and was not relabeled as a new run. The baseline was
recorded on the fixture base, to a file outside the repository, before the
permission change, so it was not itself under review.

Before #821, the published and source-tree runs returned identical cells,
version numbers included — runtime contract 40 and host-grant inventory schema
0.6: `check` blocking with four violations and visible coverage, the host-only
`init` handoff, manifest-free `verify` exiting 0 with six advisory rows, drift
naming all four expansion signals, and `diff` against the fixture base exiting
0 with `comparison_status: comparable` and the same six rows, four of them
widening — byte-identical `--json` apart from the workspace path and the
fixture's commit ids. That the published build and this tree answer alike on this change class
is a measurement, not an assumption carried over from the source column.

The previous release, `v1.0.0`, was rerun the same way on 2026-09-22, from
`pip install agents-shipgate==1.0.0` in its own clean virtualenv. It returned
the same cells apart from its version numbers — runtime contract 39, inventory
schema 0.5, capability diff `0.2`. Its six `diff` rows are `1.1.0`'s without
the `disposition` field, and it publishes no `review` or `coverage` block;
`1.1.0`'s `review.summary` counts 6 changes from 6 rows, 4 widening. On this
fixture `1.1.0` changes what a run says about the rows, not which rows it finds.

#821 then moved this tree's runtime contract to 41, and the source-tree column
was rerun on 2026-09-22 through `./shipgate` on the fixture rebuilt from the
description below, beside the `v1.1.0` release commit (`e3c6cb0c`, runtime
contract 40) run the same way. The two returned identical cells except the
runtime contract, 40 against 41: host-grant inventory schema 0.6, `check`
blocking with four violations and visible coverage, the host-only `init`
handoff with no manifest or workflow written, manifest-free `verify` exiting 0
with the same six advisory rows, drift naming all four expansion signals, and
`diff` against the fixture base exiting 0 with `comparison_status: comparable`
and the same six rows, four of them widening. The `diff` text is identical
apart from the fixture's commit ids. `diff --json` and `verifier.json` differ
only in their schema versions (capability diff 0.3 against 0.4, verifier 0.20
against 0.21) and in the members #821 adds to the coverage block: each item's
`candidate` is `null`, and `unread_candidates` is `examined` with none left
unexamined. This fixture changes only the two files the entry reads, so #821
names nothing on it and `read_sources_only` stays `true`.

#819 then published hook and MCP argument detail and moved the host-grant
inventory schema to 0.7 within the same contract. With both in the tree, the
source-tree column was rerun on 2026-09-22 from this tree's source, beside the
`v1.1.0` release commit (`e3c6cb0c`) exported and run the same way, on the
fixture rebuilt from the description below. The cells are the ones above:
`check` blocking with four violations and the same boundary result byte for
byte, the host-only `init` handoff with no file written, manifest-free `verify`
exiting 0 with six advisory rows, drift naming all four expansion signals, and
`diff` exiting 0, `comparable`, with the same six rows, four widening, its text
identical apart from the fixture's commit ids. The JSON differs only in schema
versions (capability diff 0.3 against 0.4, verifier 0.20 against 0.21,
host-grant inventory 0.6 against 0.7), in #821's coverage members as above, in
`init --json`'s contract version and input id, and in drift's added
`payments-remote` grant, which carries #819's `args: []` and `omitted_args: 0`.
This fixture has no hook, and `billing`'s arguments do not change.

An older release, `v0.15.0`, measured on 2026-09-05, did not. It reported
runtime contract 10 and inventory schema 0.1; `check` returned `warn` / `none`
with 0 violations and no coverage surface; `init --write --ci` pinned
`@v0.15.0`; `init` then `verify` exited 3; baseline/drift named all four
expansion signals. It shipped as a qualified release.

| | Released `v1.1.0` (`pip install`) | Preview `0.16.0+preview.20260903` (`gh release download`) | Source tree |
| --- | --- | --- | --- |
| Runtime contract | 40 | 29 | 41 |
| Host-grant inventory schema | 0.6 | 0.2 | 0.7 |
| `check` on the fixture | `block` / `critical`, **4 violations** | `block` / `critical`, **4 violations** | `block` / `critical`, **4 violations** |
| Coverage limit visible (`host_coverage`, `excluded_scopes`) | yes | yes | yes |
| `init --write --ci` Action pin | not applicable — host audit handoff, no workflow written | `@v0.16.0+preview.20260903.gb61aca7` — **no such tag** (the release tag is `preview-`-prefixed) | not applicable — host audit handoff, no workflow written |
| `init` then `verify` on this Route H repo | `init` exits 0 with audit handoff; manifest-free `verify` exits 0 and names six advisory change rows | exit 2 | `init` exits 0 with audit handoff; manifest-free `verify` exits 0 and names six advisory change rows |
| `audit --host --save-baseline` → `--drift` | works, all 4 expansion signals | works | works, all 4 expansion signals |
| `diff` against the fixture base (Git-backed Route H) | exit 0, `comparable`, 6 rows, 4 widening | not measured | exit 0, `comparable`, the same 6 rows |
| Qualification | **none** — advisory channel, no qualification claim | **none** — no adjudicated corpus, nothing signed | not a distributed build |

Every 2026-09-22 rerun reproduced four boundary violations (`block` /
`critical`) and visible coverage. `init --write --ci` writes no manifest or
workflow and routes the host-only fixture to the read-only audit route. Manifest-free `verify` now succeeds as an advisory
comparison: six rows name three added permissions, two removed narrower rules,
and the added MCP server. It publishes no application release decision or merge
authority. Baseline/drift
reproduced all four expansion signals with a canonical
non-symlink temporary path. The earlier run discovered the documented `/tmp/`
recovery-path problem on macOS; #550 was subsequently fixed by #595. This run
uses the canonical path and does not add a new recovery-path observation.
No reviewer, retention or qualification result is inferred from this rerun.

Four things follow, and each one is a fact about a build rather than a
judgement about a partner.

1. **The published release shows the change.** `v1.1.0`, installed with
   `pip install agents-shipgate`, returns `block` / `critical` with four
   violations and carries the coverage surface, and manifest-free `verify`
   names six advisory rows, so it can deliver all four first-value
   recognitions. It is advisory and makes no qualification claim, which is a
   thing to say out loud to a partner rather than a footnote.
2. **The previous release shows it too; an older one could not.**
   `v1.0.0` returns the same cells and the same six rows, without the
   dispositions and the `review` and `coverage` blocks. `v0.15.0` returned
   `warn` / `none` with zero violations on a diff that grants `Bash(*)`, with
   no coverage surface at inventory schema 0.1. A partner still on it reaches
   three of the four recognitions through the baseline/drift pair and cannot
   reach the fourth; `pipx upgrade agents-shipgate` closes that gap.
3. **#506's repair has reached a downloadable build.** The published
   `v1.1.0` writes no workflow for this host-only fixture, and where it does
   generate CI it pins the source commit it was released from — on
   2026-09-22, `init --write --ci` on a copy of `samples/openapi_only_agent`
   wrote `@e3c6cb0c7657d9c53d4e29b2061d04dcf99a4e9b` with
   `shipgate_version: "1.1.0"`. The preview wheel cut on 2026-09-03 still
   writes `@v0.16.0+preview.20260903.gb61aca7`, which resolves to nothing; for
   this route the published release supersedes it.
4. **The published release no longer dead-ends through manifest setup.**
   `init` exits 0 and hands the host-only fixture to audit, and manifest-free
   `verify` exits 0 with six advisory rows; the preview still exits 2. This is
   one synthetic fixture on a distributed build, not an external adoption
   observation.

An earlier version of this section reported findings 1 and 2 as a single
claim — that the two entry failures were "mutually exclusive by build", so no
partner could get both a working CI pin and an evaluator that sees the change.
The preview channel refutes it: that build has the evaluator, and after #506
reaches a preview it will have the pin too.

### Reproducing it

A claim about what a build does is worth only as much as the next person's
ability to disagree with it. The whole fixture is two files and one edit:

```jsonc
// .claude/settings.json  — base
{ "permissions": { "allow": ["Bash(npm test)", "Read(src/**)"], "deny": [] } }
// .claude/settings.json  — change
{ "permissions": { "allow": ["Bash(*)", "Read(**)", "WebFetch(*)"], "deny": [] } }

// .mcp.json  — base
{ "mcpServers": { "billing": { "command": "node", "args": ["./servers/billing.js"] } } }
// .mcp.json  — change, adding one entry alongside "billing" inside mcpServers
{ "mcpServers": {
    "billing": { "command": "node", "args": ["./servers/billing.js"] },
    "payments-remote": { "url": "https://payments.example.com/mcp" } } }
```

Commit the base as `main`, commit the change on a branch, then run the
runbook's command blocks against each build — `agents-shipgate diff` for
Git-backed Route H, the baseline Route H block, and Route A. The published
build goes into a clean virtualenv (`pip install agents-shipgate`); this tree
runs through `./shipgate`.

### What this does not establish

The dry run says a route can be run. It says nothing about whether a reviewer
who did not write the change can read the result, whether the finding is worth
the setup, or whether anyone runs it a second time. Those are the questions,
and they need external repositories.

## First-value timing

**No external first-value time has been measured.** The published target
remains **10 minutes** from starting the documented route to first
reviewer-understood value; it is an experiment target and no run has been
scored against it.

[#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) routes its
own quickstart timings here — "record observed timing and assistance in #521;
this is a target, not an adoption claim." Those are a cold *human reader* on a
committed sample, not an external repository, so they are reported in this
section beside the target and never in the external denominators.

Machine-step timings from the dry run are recorded only to show the target is
not obviously out of reach — `audit --host` completed in under a second, and
the baseline/drift pair in about the same again. Command latency is not first
value: the minutes the target budgets are a person reading the result, not a
process exiting.

## Second-change outcomes

**None observed.** No repository has reached `second_change_eligible`, so no
four-week observation window has opened, and there is nothing to report as
retained, churned, or bypassed.

## Review-required outcomes

**None observed.** Independently of enrollment, no `review_required` result
can currently record a continuation: the authenticated human decision step is
specified in
[#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504) and
delivered by [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337),
and neither has landed. When an eligible case is observed before #337, the
missing continuation is reported as missing; the case is repeated afterwards.

## Blockers reproduced, and where they belong

Every row below was reproduced in the dry run. None of them is a new feature
request, and none opened a new issue. Status is as of 2026-09-05, after
[#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506),
[#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485) and
[#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) merged,
rechecked on 2026-09-14 against the published `v1.0.0`, and again on
2026-09-22 against the published `v1.1.0`, which returned the same statuses.

| Reproduced | Existing issue | Status |
| --- | --- | --- |
| `init --write --ci` pinned a workflow to a tag that does not exist | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | **Fixed in `v1.0.0`** — generated CI pins the released source commit, and this host-only fixture writes no workflow. The 2026-09-03 preview predates the fix and still writes a ref that resolves to nothing |
| The released build's `check` returns `warn` / `none` on a host-boundary change the tree blocks; released and in-tree evaluators disagree | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) | **Resolved in `v1.0.0`** — the published `check` returns `block` / `critical` with 4 violations on this fixture, matching the tree |
| The released build's `check --agent claude-code` reports "No **Codex** boundary rule fired" — the pre-multi-host evaluator | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497), [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | Superseded in `v1.0.0` by the multi-host evaluator, which blocks this fixture; only `v0.15.0` carries the old evaluator |
| The released build's host inventory carries no coverage or excluded-scopes surface, so a reviewer cannot see what was not read | [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520) | Present since `v1.0.0` (inventory schema 0.5; 0.6 in `v1.1.0`), and on the preview at 0.2; #520 stays open for its wider scope |
| A `review_required` result has no authenticated continuation | [#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504) → [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) | Open |
| `init` then `verify` dead-ends on a repository with no tool surface | [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) | `v1.0.0` and `v1.1.0` route this host-only fixture to audit and manifest-free `verify` exits 0; the preview still dead-ends. #498 owns the manifest route — "do not make policy authoring a universal prerequisite" — and this runbook mitigates it meanwhile by routing those partners to Route H |
| The runbook required a manifest on a route that does not need one | [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) | Fixed in this runbook |
| The runbook required a contract floor no published build carries | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) | Fixed in this runbook and, independently, by #497's channel table |

## Standing decision — 2026-09-14: **narrow**

The successor to the 2026-09-05 checkpoint below, recorded in
[#571](https://github.com/ThreeMoonsLab/agents-shipgate/issues/571) after
`v1.0.0` was published and the owner accepted the post-release adoption plan
([#778](https://github.com/ThreeMoonsLab/agents-shipgate/issues/778)). It
changes the channel and the route. It does not change the pre-registered
[decision rule](design-partner-verifier-pilot.md#decision-rule), the
first-value definition, the consent rules or any denominator.

Applying the ladder to today's counts gives the same rung as before: every
denominator is 0, so rung 1 (`first_value` ≥ 2) and rung 2
(`first_valid_result` ≥ 1) do not match, and rung 3 takes it — **narrow**, now
to one entry route on the published channel.

- **Channel: the published `v1.0.0` advisory release** for non-blocking Route H
  review, installed with `pipx install agents-shipgate`. Record the exact
  installed version for each observation and any later patch upgrade.
  Qualification stays `none` unless separate evidence establishes otherwise,
  and the invitation says so. The preview-only invitation and the
  released-channel withhold below are superseded for this experiment; they are
  kept as recorded, not deleted, and no historical preview is reclassified.
- **Route: Git-backed Route H.** The partner compares a real change with
  `agents-shipgate diff` against the PR's base in Git history — no manifest,
  policy, previously committed baseline or legacy skill. The route's artifact
  mapping is frozen in the runbook's
  [Definition of running](design-partner-verifier-pilot.md#definition-of-running)
  before the first observation; results recorded under the earlier
  baseline-route definition keep it.
- **Persona and workflow to invite:** unchanged — the developer or
  platform/DevEx reviewer who already meets permission and MCP changes in pull
  requests, with base/head history and another reviewer.
- **Blocking CI:** no partner has asked for it, and nobody will be asked to
  enable it before first value and repeat use are evidenced. An advisory PR
  recipe is offered only after first value.

What the published build does on this route was measured, not assumed: on the
dry-run fixture, `v1.0.0` installed outside a checkout and this source tree
return the same six `diff` rows (see the matrix above). That is one synthetic
fixture, not an external observation.

**What would change this decision.** The terminal decision is taken when the
cohort closes, as the runbook's [decision rule](design-partner-verifier-pilot.md#decision-rule)
says; three attempted external repositories, with or without a favourable
result, make the next checkpoint due, and it stays interim while any
observation window is open. A published build that changes what `diff` reports
on this change class re-dates the dry run before it is cited again. Too few
recruits or eligible changes is recorded as a dated shortfall and a narrowing,
never as retention.

**Checkpoint — 2026-09-22.** This records facts; it does not change the
decision above, whose text stays with its owner to confirm. `v1.1.0` is
published on the advisory channel with no qualification claim, and `pipx
install agents-shipgate` now installs it rather than the `v1.0.0` the channel
line names. It changes what `diff` reports on this change class — the same six
rows, each now carrying its `disposition`, plus the `review` and `coverage`
blocks — so the dry run above was re-dated against it before being cited here.
Whether the channel line moves to `v1.1.0` is the owner's question, not this
ledger's. This checkpoint adds no observation, and no denominator moves.

## Standing decision — 2026-09-05: **narrow**

Superseded for the advisory Route H experiment by the 2026-09-14 decision
above, and kept here as it was recorded.

This is a checkpoint against the pre-registered
[decision rule](design-partner-verifier-pilot.md#decision-rule), not the
terminal decision. The terminal decision needs observations that do not exist
yet; recording "undecided" and moving on would leave the shortfall
unexplained, so the checkpoint says what is being done about it.

Applying the ladder to today's counts: rung 1 needs `first_value` ≥ 2 and it
is 0. Rung 2 needs `first_valid_result` ≥ 1 and it is also 0 — no repository
got a working result that a reviewer then failed to act on, so this is not a
demonstrated failure of the review. Rung 3 takes it: **narrow**, and what to
narrow to is now the channel rather than entry.

- **Persona and workflow that earned repeated use:** none. Nothing has been
  observed once, let alone twice, so no route, persona or workflow has earned
  anything yet. The rest of this decision is about where to spend the next
  invitation, not about what has been proven.
- **Persona and workflow to invite:** the developer or platform/DevEx reviewer
  who already meets permission and MCP changes in pull requests, on Route H.
- **Channel: the preview, with its status stated in the invitation.** It is
  the only channel that reaches all four first-value recognitions on this
  change class. It is also unqualified — no adjudicated corpus, no
  qualification artifact, nothing signed — and the invitation says so in those
  words. A partner who declines an unqualified build is a legitimate answer to
  record, not an objection to argue past.
- **Withhold:** the released channel for Route H per-change review. Inviting a
  partner to a `warn` / `none` on a `Bash(*)` grant spends an introduction to
  demonstrate a blind spot. The released build stays fine for the zero-config
  `audit --host` snapshot that opens a conversation.
- **Recurring blockers:** the publication drought behind #506 and the parity
  gap behind #497 both had fixes land today, and neither has reached a build a
  partner installs. That gap — repaired on main, absent from every channel —
  is what the next preview cut closes.
- **Blocking CI:** no partner has asked for it. Nobody will be asked to enable
  it before first value and repeat use are evidenced.

**What would change this decision.** A preview cut from current main removes
the last known entry defect from the invited channel and should be taken
before the first invitation. A qualified release whose `check` matches the
tree on the dry-run fixture moves the invitation off the preview entirely and
lifts the withhold. Three attempted external repositories, with or without a
favourable result, replace this checkpoint with the terminal decision. None of
those is a matter of writing more runbook.

**Checkpoint — 2026-09-14.** This records facts; it does not change the
decision above, which stays with its owner. `v1.0.0` is published on the
advisory channel, and on the dry-run fixture its `check` matches the tree:
`block` / `critical`, four violations, visible coverage. The preview is no
longer the only channel that reaches all four recognitions. The
pre-registered condition for lifting the withhold names a *qualified*
release; `v1.0.0` makes no qualification claim, so whether the advisory
release meets that condition is the owner's question, not this ledger's.
Every denominator is still 0.

## Limitations

- **n = 0 external.** Every count above is zero, and zero counts support no
  claim about adoption in either direction.
- **The dry run is one synthetic fixture on one machine**, by a maintainer who
  knows the answers. It shows what a command emits; it cannot show what a
  stranger understands.
- **Findings are build-dated.** They describe four builds as they stood on
  2026-09-22 for the released `1.1.0`, the previous release `1.0.0` and this
  source tree, and 2026-09-05 for the preview
  `0.16.0+preview.20260903.gb61aca7`. A release or a new
  preview invalidates the comparison, and the dry run must be re-run and
  re-dated before any row here is cited again. A standing guard fails the
  build when the newest published tag moves; **nothing fails the build when a
  new preview is cut or when main changes**, which is how the 2026-09-04
  version of this page went stale within a day.
- **A survey can be complete and still wrong.** The first version of the
  shortfall measured every channel it knew about and reached a false
  conclusion, because a third channel existed. Treat the channel list as
  something to re-derive from
  [the runbook's channel table](design-partner-verifier-pilot.md#which-build-this-runbook-is-for),
  not as something this page remembers.
- **The shortfall is a maintainer's judgement about when to spend
  introductions.** It is recorded here so it can be disagreed with, not to
  foreclose enrolling a partner who wants to try the route as it stands.
