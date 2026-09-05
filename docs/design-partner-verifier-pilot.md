# Design Partner Verifier Pilot

Use this runbook to run a small, consent-based cohort of external
repositories through an actual review workflow: someone introduces a
capability or permission change, someone else reviews it, and the next
eligible change tests whether the integration stayed useful.

Dated results, denominators and the standing continue/narrow/stop record live
in [`design-partner-pilot-results.md`](design-partner-pilot-results.md). This
page is how to observe; that page is what was observed.

## What this experiment measures

Two questions, in order:

1. **First value.** After the first result, could a reviewer name the changed
   capability, the evidence behind it, the coverage limit, and the next
   action — and did they record a concrete decision or fix?
2. **Repeat use.** On the *next eligible change*, did the team run it again?

A fixture completing, an advisory check returning zero, and a person saying
the report looks useful are all different from a reviewer acting on the
evidence. Only the second is first value.

The activation experiment this page used to describe — three partners, one PR
each, one first-run feedback note — is the *entry* to this one, not its exit.

## Routes under test

Record which surface each participant actually used. They have different
prerequisites, and mixing them in one denominator hides which one earned use.

| | **Route H — coding-agent host boundary** | **Route A — agent tool surface** |
|---|---|---|
| Who | Any team whose repo declares what its coding agents may do | Teams that build and declare a tool surface in-repo |
| Manifest | None | `shipgate.yaml` required |
| Inputs | `.mcp.json`, `.claude/settings.json`, `.codex/`, `.cursor/`, hooks, workflow scopes | MCP/OpenAPI exports, framework source, plus the host inputs |
| Per-change surface | `check`, and `audit --host --drift` against a committed baseline | `verify --base … --head …` |
| First result | `audit --host` | `verify --preview` |

Do not require a partner on Route H to maintain a manifest. `init --write`
on a repository with no tool surface writes a `CHANGE_ME` scaffold, and the
`verify` that follows exits non-zero on it — that is a setup dead end, not a
verdict, and it must be recorded as one if it happens.

Include tool-building teams on Route A only where a real partner already has
that workflow. Do not recruit for Route A to balance the cohort.

## Definition Of Running

A partner counts as **attempted** once they start the documented route in a
repository they control. A partner counts as having reached a **first valid
result** when all of these are true:

- The change under review touches a capability: tools, prompts, MCP/OpenAPI
  surfaces, permissions, hooks, policy, CI, `shipgate.yaml`, or another trust
  root.
- The route produced its artifact: on Route H a host-grant inventory and, for
  a change, a drift record against a committed baseline; on Route A
  `agent-handoff.json`, `verifier.json`, `pr-comment.md` and `report.json`.
- On Route A, `shipgate.yaml` has been reviewed and has no unresolved
  `CHANGE_ME` values, a reviewer read `agent-handoff.json` first, and
  `report.json.release_decision.decision` was used as the release gate.
- `agents-shipgate-reports/` is ignored and not committed.

A first valid result is a *machine* event. It is not first value. Record both
times separately; the gap between them is the number that matters.

## First value, defined

First value is reached when a reviewer who is **not** the person who made the
change can, from the artifacts alone:

1. name the capability the change added, widened, or removed;
2. name the evidence the result rests on;
3. name what the run did **not** read — the coverage limit; and
4. name the next action and who owns it,

and then records a concrete decision or fix. A process exit of zero is not
this event. "The report looks useful" is not this event. A maintainer
explaining the report to the reviewer means first value was **not** reached
unaided; record the assistance rather than dropping the row.

**Target: 10 minutes** from starting the documented route to first
reviewer-understood value. This is an experiment target used to size the
route, not a claim that any run has achieved it. Publish measured times
against it; never restate the target as a result.

## Denominators

Report these six counts separately in
[`design-partner-pilot-results.md`](design-partner-pilot-results.md). Every
row that enters a denominator stays in it.

| Denominator | Counts a repository once it has |
|---|---|
| `invited` | received a consent-based invitation |
| `attempted` | started the documented route |
| `first_valid_result` | produced the route's artifact per the definition above |
| `first_value` | met all four recognitions plus a recorded decision or fix |
| `second_change_eligible` | had another capability change within the window |
| `second_change_observed` | ran Agents Shipgate on that change |

Three rules keep these honest:

- **Failures stay in.** A repository that could not install, could not reach a
  valid result, or reached one and then abandoned it stays in `attempted`.
  Entry defects ([#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506),
  [#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485)) and
  review defects
  ([#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504),
  [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337)) are
  recorded against the row, never used to exclude it.
- **No second eligible change means not observed.** Such a repository is
  neither retained nor churned; it leaves `second_change_eligible` empty and
  is reported as unobserved. A one-time audit can still have been worth
  running; it does not evidence a recurring CI wedge.
- **Dogfooding is separate.** Runs against this repository, our own samples,
  or `verify-self` are reported in their own section and never enter these
  six counts.

## The second eligible change

Open a **four-week observation window** at first value. An eligible change is
any later change in the same repository that touches the same capability
class. Within the window, record:

- whether the team ran Agents Shipgate on it, unprompted;
- whether the integration is still enabled (CI present, not disabled, not
  bypassed);
- whether a **second** reviewer could act on the result without maintainer
  translation;
- if it was disabled, bypassed, or ignored — the reason, in the team's words.

If the window closes with no eligible change, close the row as
`unobserved: no second eligible change in window` and say so in the results.
Silence is not retention.

## Review-required outcomes

Track these on their own ledger, because a `review_required` result that
nobody can act on is a different failure from one a reviewer rejected:

| Outcome | Meaning |
|---|---|
| `delivered` | the named request reached the named reviewer |
| `accepted` / `rejected` / `disputed` | the reviewer decided |
| `continued` | the decision produced a recorded continuation |
| `unresolved` | the request stalled with no decision |

Until [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337)
lands there is no authenticated continuation, so a `review_required` result
cannot record `continued`. Report that missing continuation honestly rather
than scoring the row as a success or dropping it. After #337 lands, repeat an
eligible case and record the result again.

## Consent and redaction

Contact is consent-based and follows this repository's existing outreach
rules. This runbook is not permission for automated or unsolicited messages.

Three consents are **granted separately**, and none implies another:

1. **Public naming** — using the organization or repository name in public
   material.
2. **Source and PR links** — publishing a link to the change under review.
3. **Raw bundles** — publishing an unredacted artifact.

Without all three, a row is reported **aggregate-only**: counts, timings,
route, and redacted findings, with no name, link, or raw artifact. Aggregate
counts never require consent; anything identifying always does. A partner may
withdraw a consent later, and withdrawal applies to already-published
material.

Keep the working tracker in a private location — `.agents-private/` is
gitignored in this repository for exactly this. Only the aggregate ledger in
[`design-partner-pilot-results.md`](design-partner-pilot-results.md) is
public.

## Evidence to preserve

Preserve a real before/after artifact or a redacted decision note for **every
outcome reported as useful**. Use what already exists; the pilot adds no new
command.

Route A, where a `verifier.json` exists:

```bash
agents-shipgate feedback export \
  --from agents-shipgate-reports/verifier.json \
  --redact \
  --out shipgate-feedback.json
agents-shipgate feedback capture \
  --before before/verifier.json \
  --after agents-shipgate-reports/verifier.json \
  --human-decision merged \
  --out shipgate-scenario.json
```

Route H has no `verifier.json`, so `feedback export` and `feedback capture`
do not apply. Preserve the pair the route does produce, plus the note:

```bash
# on the base ref, once, committed
agents-shipgate audit --host --save-baseline
# on the change
agents-shipgate audit --host --drift --json --out shipgate-drift.json
```

The redacted decision note is three lines: what the reviewer decided, which
artifact field they decided from, and what they did next.

## Lower-friction first touch

If a partner hesitates at "bring a PR," start with the zero-config host
audit — one read-only command, no manifest, no CI:

```bash
agents-shipgate audit --host
```

It prints the repo's current coding-agent grants (MCP servers, permission
rules with wildcard flags, hooks, workflow write scopes). Reviewing that one
page together usually surfaces the first governance question. It is a
*snapshot*, not a review of a change: on its own it cannot reach first value,
because there is no changed capability to name. `--save-baseline` on the base
ref then `--drift` on the change is the step that can.

## Partner Fit

Use the general fit criteria in [`design-partners.md`](design-partners.md).
Prioritize teams that can share actionability feedback within one week.

Good first partners usually have:

- A developer or platform/DevEx reviewer who already meets capability and
  permission changes in pull requests.
- At least one refund, email, cancellation, deployment, record-modifying,
  sensitive-read, or other authority-bearing tool, or a coding agent holding
  broad host grants.
- A coding agent already used for PR work.
- Permission to run a non-blocking GitHub Action or equivalent local command
  during the pilot.
- A named reviewer who can judge whether the result and next action are
  useful.

Running a coding agent is not on its own a reason to want another gate. Avoid
first-wave partners that need hosted dashboards, runtime enforcement,
private-data upload, compliance certification, or non-GitHub CI as the primary
success path.

## Pilot Commands

Run these from the target repo root.

**Record what was installed before running anything else.** The build a
partner can actually install and the build this tree carries are not the
same, and the difference has decided every pilot attempt so far. The two
commands below are the first observation of the run, not preamble:

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate
agents-shipgate --version
agents-shipgate contract --json
```

A plain `pipx install` is a no-op when an older build is already installed,
so the `pipx upgrade` brings a stale copy current. If `pipx` is unavailable,
use `python -m pip install -U agents-shipgate`. Record `cli_version` and
`contract_version` from `contract --json` in the tracker; do not assume a
contract floor that the published build may not carry.

Route H — no manifest. Once, on the default branch:

```bash
agents-shipgate audit --host
agents-shipgate audit --host --save-baseline   # then commit .agents-shipgate/
```

Then, on each change under review:

```bash
agents-shipgate audit --host --drift --json --out shipgate-drift.json
agents-shipgate check --agent <codex|claude-code|cursor> \
  --base <base-ref> --head <change-ref>
```

The baseline is committed once and re-acknowledged only after a human has
reviewed the drift, so a change under review needs the second block alone.
Before pointing a partner at `check`, read § Standing decision in
[`design-partner-pilot-results.md`](design-partner-pilot-results.md): it
records which surfaces are invitable on a build a partner can install today,
and which are not.

Route A — manifest:

```bash
agents-shipgate verify --preview --json
agents-shipgate init --workspace . --write --ci --agent-instructions=default --json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

If the repo is not yet committed, omit `--base` and `--head` for the local
pre-commit run, then rerun with base/head refs after opening the PR. For
committed PR/CI refs, make `origin/main` and `HEAD` available before the final
`verify`.

If `init --write` emits `CHANGE_ME` for `tool_sources` because discovery found
no tool surface, stop: this repository is on Route H. Record the dead end and
switch routes rather than inventing a manifest.

## Read Order

Route A — read `agents-shipgate-reports/agent-handoff.json` first:

1. `control.state`
2. `gate.can_merge_without_human`
3. `gate.merge_verdict`
4. `next_action` / `fix_task`
5. `capability_review.top_changes`

Then read `agents-shipgate-reports/report.json.release_decision.decision`.
`merge_verdict` is the reviewer-facing projection; `release_decision.decision`
remains the release gate.

Route H — read the drift record first: the expansion signals, then the
per-surface added/removed rules, then the re-acknowledge next action. Then
read `check`'s `decision`, `risk_level`, and its coverage fields.

Do not self-resolve authority gaps. If `control.next_action.actor` or
`fix_task.actor` is `human`, the coding agent must surface that item for a
person rather than inventing action effect, action authority, approval,
confirmation, idempotency, broad-scope, prohibited-action, waiver, baseline,
suppression, or policy-weakening evidence.

## Partner Agent Prompt

Paste this into the partner's coding agent from the target repo root:

```text
Add Agents Shipgate as an advisory reviewer for this agent-capability change.

1. Install and record the build:
   pipx install agents-shipgate
   pipx upgrade agents-shipgate
   agents-shipgate --version
   agents-shipgate contract --json
   A plain pipx install is a no-op when an older build is already installed,
   so the follow-up pipx upgrade brings a stale copy current. If pipx is
   unavailable, use python -m pip install -U agents-shipgate. Report
   cli_version and contract_version before running anything else.
2. Decide the route. Run:
   agents-shipgate audit --host
   If this repository declares coding-agent host configuration and publishes
   no tool surface of its own, use route H and skip the manifest entirely.
3. Route H. Once, on the default branch, then commit .agents-shipgate/:
   agents-shipgate audit --host --save-baseline
   Per change under review:
   agents-shipgate audit --host --drift --json --out shipgate-drift.json
   agents-shipgate check --agent <codex|claude-code|cursor> \
     --base <base-ref> --head <change-ref>
   Lead with the drift expansion signals, the added/removed rules per
   surface, and check's decision, risk_level and coverage fields.
4. Route A, per change:
   agents-shipgate verify --preview --json
   agents-shipgate init --workspace . --write --ci \
     --agent-instructions=default --json
   Replace every CHANGE_ME value in shipgate.yaml using the agent's system
   prompt, README, main agent module, or owner-provided context. If discovery
   found no tool source at all, tool_sources is a scaffold and this repository
   belongs on route H — say so instead of guessing a type.
   Open or update the PR, make origin/main and HEAD available, then run:
   agents-shipgate verify --workspace . --config shipgate.yaml \
     --base origin/main --head HEAD --ci-mode advisory --format json
   Read agents-shipgate-reports/agent-handoff.json first. Lead with
   control.state, gate.merge_verdict, gate.can_merge_without_human,
   next_action, fix_task, and capability_review.top_changes. Then read
   agents-shipgate-reports/report.json.release_decision.decision.
5. Preserve evidence. Route A:
   agents-shipgate feedback export \
     --from agents-shipgate-reports/verifier.json \
     --redact \
     --out shipgate-feedback.json
   Route H: keep the committed baseline and shipgate-drift.json.
6. Ensure agents-shipgate-reports/ is ignored and not committed.

Report, in this order: which route you used, the build and contract you ran,
every command that failed and how, and what a reviewer could name from the
result. Do not enable strict CI, save a baseline to silence drift you have not
reviewed, suppress findings, weaken Shipgate policy, remove Shipgate CI, or
auto-assert action effect, action authority, approval, confirmation,
idempotency, broad-scope, prohibited-action, waiver, baseline, suppression, or
runtime-trace evidence.
```

If the partner wants the Codex skill bundle, use
`--agent-instructions=agents-md,codex-skill`. For Claude Code skill bundles,
use `--agent-instructions=agents-md,claude-command,claude-code-skill`.

## First Call Agenda

- Confirm the change source, changed capability, framework, and tool-source
  boundary — and from those, the route.
- Confirm the existing alternative: manual review, CODEOWNERS, a CI script,
  another tool, or doing nothing.
- Confirm GitHub Actions and PR comments are acceptable for the advisory pass.
- Agree who the reviewer is, and that they are not the change's author.
- Run or supervise the partner-agent prompt, recording every failed step.
- Decide which findings are mechanical fixes and which require human
  authority.
- Agree which of the three consents the partner grants.

## Success Tracker

Keep the working tracker private. Publish only the aggregate ledger.

Template: copy into a private tracker, one row per repository.

### Entry

| Field | Value |
| --- | --- |
| Partner |  |
| Repo / agent type |  |
| Route (H / A) |  |
| Entry point (how they arrived) |  |
| Build installed (`cli_version`) |  |
| Contract (`contract_version`) |  |
| Published or preview build |  |
| Environment (OS, Python, CI) |  |
| Installation attempted / succeeded |  |
| Setup steps performed |  |
| Maintainer assistance given |  |
| Failed commands and their errors |  |
| Time to first valid result |  |

### First value

| Field | Value |
| --- | --- |
| Reviewer (distinct from author) |  |
| Named the changed capability |  |
| Named the evidence |  |
| Named the coverage limit |  |
| Named the next action and owner |  |
| Concrete decision or fix recorded |  |
| Time to first value |  |
| Reached unaided / with translation |  |
| Existing alternative in place |  |
| Work Agents Shipgate saved |  |
| Work Agents Shipgate added |  |

### Result read

| Field | Value |
| --- | --- |
| `merge_verdict` (A) / drift + `decision` (H) |  |
| `can_merge_without_human` (A) |  |
| `control.next_action.actor` |  |
| `fix_task.actor` |  |
| `trust_root_touched` |  |
| `policy_weakened` |  |
| Top capability changes |  |
| Finding IDs |  |
| False positive or friction |  |

### Review-required ledger

| Field | Value |
| --- | --- |
| Named request delivered |  |
| Accepted / rejected / disputed |  |
| Recorded continuation |  |
| Unresolved, and why |  |

### Second eligible change

| Field | Value |
| --- | --- |
| Window opened (date) |  |
| Eligible change occurred |  |
| Ran again, unprompted |  |
| CI still enabled |  |
| Second reviewer acted without translation |  |
| Disabled / bypassed, and why |  |
| Outcome (observed / unobserved) |  |

### Disposition

| Field | Value |
| --- | --- |
| Consent: public naming |  |
| Consent: source / PR link |  |
| Consent: raw bundle |  |
| Evidence artifact preserved |  |
| Blockers reproduced → existing issue |  |
| Benchmark candidate decision |  |
| Follow-up date |  |

## Follow-Up Questions

Ask these after the first result lands:

- Did the coding agent discover and run the route without command-by-command
  coaching?
- Which of the four recognitions — capability, evidence, coverage limit, next
  action — did the reviewer get, and which did they not?
- Did the result match what the reviewer would have decided anyway? If it
  matched, what did it save? If it differed, who was right?
- Was `control.next_action` clear enough to route work to the right actor?
- Did `fix_task` draw the right boundary between mechanical fixes and human
  authority?
- Which finding was useful, noisy, confusing, or missing context?
- What were you doing about these changes before?
- Should this change become a benchmark scenario?

## Outreach Snippet

```text
We are looking for a few teams to try Agents Shipgate on one real
agent-capability change — a permission grant, an MCP server, a tool, a hook.
It is local-first: one command inventories what your coding agents are
granted, and a second shows what a change did to that. No agent execution,
LLM calls, MCP connections, hosted dashboard, or telemetry are required.

We ask for one thing back: whether a reviewer who did not write the change
could tell, from the output alone, what changed and what to do about it — and
whether you reached for it again on the next change. A "no" is a useful
answer; we are measuring whether this is worth your time, not looking for a
testimonial.
```

## Decision rule

Pre-registered so the terminal decision is not selected after the fact. Apply
it once the cohort closes, and record the outcome in
[`design-partner-pilot-results.md`](design-partner-pilot-results.md).

| Decision | Condition |
| --- | --- |
| **Continue** | ≥ 2 repositories reach first value unaided **and** ≥ 1 reaches `second_change_observed`, on the same route |
| **Narrow** | first value is reached, but only on one route, only with maintainer translation, or with no observed second change |
| **Stop** | no repository reaches first value, or every failure traces to the product rather than to enrollment |

These counts are read **against the reported denominators**, never against a
selected subset. Two repositories reaching first value out of three attempts
and two out of thirty are different results, and the decision has to name
which — that is the whole reason failures stay in `attempted`. Nothing here is
a retention target asked of a partner; it is a threshold this project holds
itself to before claiming a workflow works.

Whichever it is, name: which persona and workflow earned repeated use, which
blockers recurred, and whether any partner asked for blocking CI. No response
and an unobserved second change are not positive evidence for any of the
three.

## Exit Criteria

The experiment closes when all of these hold:

- Three external repository attempts are recorded with author and reviewer
  roles distinguished — including incomplete attempts and any maintainer
  assistance — **or** a dated report of the enrollment/opportunity shortfall
  is published in its place. A favourable adoption result is not required.
- Aggregate first-value timings and second-change outcomes are published with
  counts, observation windows and limitations.
- Every outcome reported as useful has a preserved before/after artifact or
  redacted decision note.
- The decision rule above has been applied and its outcome recorded.
- Every reproduced blocker has been routed to an existing issue. A
  participant saying a feature sounds useful is not grounds for a new one.
