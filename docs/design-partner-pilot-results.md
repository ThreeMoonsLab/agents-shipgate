# Design Partner Pilot: Results

The public, aggregate ledger for the experiment described in
[`design-partner-verifier-pilot.md`](design-partner-verifier-pilot.md). That
page is how to observe; this one is what was observed.

Nothing identifying appears here without the specific consent it requires.
Aggregate counts never require consent; names, source links and raw artifacts
always do.

**Status date: 2026-09-05.** No external repository has been enrolled. The
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

**Published build measured: `0.15.0`.** Preview measured:
`0.16.0+preview.20260903.gb61aca7`. Source tree: `0.16.0`, at the merge of
[#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506),
[#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485) and
[#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497). Every
finding below is a claim about those three builds on this date.

| | Released `v0.15.0` (`pipx install`) | Preview `0.16.0+preview.20260903` (`gh release download`) | Source tree |
| --- | --- | --- | --- |
| Runtime contract | 10 | 29 | 29 |
| Host-grant inventory schema | 0.1 | 0.2 | 0.2 |
| `check` on the fixture | `warn` / `none`, **0 violations** | `block` / `critical`, **4 violations** | `block` / `critical`, **4 violations** |
| Coverage limit visible (`host_coverage`, `excluded_scopes`) | no | yes | yes |
| `init --write --ci` Action pin | `@v0.15.0` — exists | `@v0.16.0+preview.20260903.gb61aca7` — **no such tag** (the release tag is `preview-`-prefixed) | `@v0.15.0` — exists |
| `init` then `verify` on this Route H repo | exit 3 | exit 2 | exit 2 |
| `audit --host --save-baseline` → `--drift` | works, all 4 expansion signals | works | works |
| Qualification | qualified release | **none** — no adjudicated corpus, nothing signed | not a distributed build |

Four things follow, and each one is a fact about a build rather than a
judgement about a partner.

1. **A build that shows the change is installable today — the preview.** It
   returns `block` / `critical`, names all three widened permission rules and
   the added MCP server, and carries the coverage surface, so it can deliver
   all four first-value recognitions. It also carries no qualification at all,
   which is a thing to say out loud to a partner rather than a footnote.
2. **The released build cannot show this change class.** `warn` / `none` with
   zero violations on a diff that grants `Bash(*)`, and no coverage surface at
   inventory schema 0.1. It reaches three of the four recognitions through the
   baseline/drift pair; it cannot reach the fourth.
3. **#506's repair has not reached any downloadable build.** The source tree
   now writes `@v0.15.0`, a tag that exists. The preview wheel a partner can
   download today was cut on 2026-09-03, before that fix, and still writes
   `@v0.16.0+preview.20260903.gb61aca7` — which is not the release tag
   (`preview-0.16.0+preview.20260903.gb61aca7`) and resolves to nothing. A
   preview cut from current main would not have this. Nothing to file: #506
   owns it and is fixed; the ledger records that the fix is not yet in a
   partner's hands.
4. **`init` then `verify` still dead-ends on Route H**, on every channel —
   exit 3 on the release, exit 2 on the preview and the tree. A repository
   with no tool surface gets a `CHANGE_ME` scaffold and the `verify` after it
   fails. That is why the runbook routes these partners away from a manifest
   rather than through one.

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
runbook's Route H and Route A command blocks against each build. The published
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
[#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) merged.

| Reproduced | Existing issue | Status |
| --- | --- | --- |
| `init --write --ci` pinned a workflow to a tag that does not exist | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | **Fixed on main** — the tree now writes `@v0.15.0`. Not yet in any downloadable build: the current preview predates the fix and still writes a ref that resolves to nothing |
| The released build's `check` returns `warn` / `none` on a host-boundary change the tree blocks; released and in-tree evaluators disagree | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) | **Parity registered on main.** The disagreement itself remains until a release carries the newer evaluator; the preview channel is the interim answer |
| The released build's `check --agent claude-code` reports "No **Codex** boundary rule fired" — the pre-multi-host evaluator | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497), [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) | Unchanged on the released channel; absent on the preview |
| The released build's host inventory carries no coverage or excluded-scopes surface, so a reviewer cannot see what was not read | [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520) | Open. Present on the preview at inventory schema 0.2 |
| A `review_required` result has no authenticated continuation | [#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504) → [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) | Open |
| `init` then `verify` dead-ends on a repository with no tool surface | [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) | Open on every channel. #498 owns it — "do not make policy authoring a universal prerequisite" — and this runbook mitigates it meanwhile by routing those partners to Route H |
| The runbook required a manifest on a route that does not need one | [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) | Fixed in this runbook |
| The runbook required a contract floor no published build carries | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) | Fixed in this runbook and, independently, by #497's channel table |

## Standing decision — 2026-09-05: **narrow**

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

## Limitations

- **n = 0 external.** Every count above is zero, and zero counts support no
  claim about adoption in either direction.
- **The dry run is one synthetic fixture on one machine**, by a maintainer who
  knows the answers. It shows what a command emits; it cannot show what a
  stranger understands.
- **Findings are build-dated.** They describe three builds as they stood on
  2026-09-05: the released `0.15.0`, the preview
  `0.16.0+preview.20260903.gb61aca7`, and this tree. A release or a new
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
