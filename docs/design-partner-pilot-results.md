# Design Partner Pilot: Results

The public, aggregate ledger for the experiment described in
[`design-partner-verifier-pilot.md`](design-partner-verifier-pilot.md). That
page is how to observe; this one is what was observed.

Nothing identifying appears here without the specific consent it requires.
Aggregate counts never require consent; names, source links and raw artifacts
always do.

**Status date: 2026-09-04.** No external repository has been enrolled. The
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

## Enrollment and opportunity shortfall — 2026-09-04

Zero invitations have gone out. The cause is not enrollment effort: it is that
the route an invitation would point at could not be run to first value on a
build a partner can install. Inviting a cohort onto it would have burned the
introductions this experiment exists to make.

The dry run below is what established that. It is dogfooding — a route
readiness check against a synthetic repository, run by a maintainer — so it
proves nothing about adoption and fills no external denominator. It answers
one question only: *is this route invitable today?*

### Route readiness dry run (dogfooding)

Fixture: a synthetic Route H repository — `.claude/settings.json` plus
`.mcp.json`, no tool surface of its own. The change under review widens the
allow list from `Bash(npm test)` / `Read(src/**)` to `Bash(*)` / `Read(**)` /
`WebFetch(*)` and adds a remote MCP server `payments-remote`. That is the
capability-change class this pilot exists to observe.

**Published build measured: `0.15.0`.** In-tree build: `0.16.0`. Every
finding below is a claim about those two builds on this date.

Two builds, same fixture, same commands:

| Step | `agents-shipgate` 0.15.0 — newest on PyPI, what `pipx install` gets | This tree, 0.16.0, unpublished |
| --- | --- | --- |
| `contract --json` | `contract_version` **10** | `contract_version` 29 |
| `check --agent claude-code --base … --head …` | `decision: warn`, `risk_level: none`, no violations; summary reads *"No **Codex** boundary rule fired, but the diff changes a tool/capability surface (.mcp.json) that shipgate.yaml does not declare, so verify cannot gate it yet."* The three widened permission rules are not mentioned. | `decision: block`, `risk_level: critical`, 4 violations — three `HOST-PERMISSION-WILDCARD-ALLOW` and one `HOST-MCP-SERVER-ADDED` — plus `host_coverage` and `excluded_scopes` |
| `init --workspace . --write --ci` | workflow pins `@v0.15.0` (the tag exists); manifest guesses `type: openapi`, `path: CHANGE_ME.yaml` | workflow pins `@v0.16.0` (**no such tag**); manifest is honest: `type: CHANGE_ME` |
| `verify --base … --head …` after that `init` | exit 3, `Input file not found: …/CHANGE_ME.yaml` | exit 2, `No adapter registered for source type 'CHANGE_ME'` |
| `audit --host` | works; names 3 wildcard allows and 2 MCP servers; **no coverage or excluded-scopes section** (inventory schema 0.1) | works; adds a per-host coverage table and excluded scopes (inventory schema 0.2) |
| `audit --host --save-baseline`, then `--drift` on the change | **works**: `mcp_server_added: …payments-remote` and three `wildcard_allow_added`, the two narrowed rules it replaced, and a re-acknowledge next action; `--fail-on-drift` exits 20; `--json --out` writes the artifact | same, at inventory schema 0.2 |
| `feedback export` / `feedback capture` | both present; both read `verifier.json`, which Route H never produces | same |

Four things follow, and each one is a fact about a build rather than a
judgement about a partner.

1. **The runbook's own precondition was unsatisfiable.** It required "runtime
   contract 14" and installed it with `pipx install agents-shipgate`. The
   newest published build carries contract **10**, and this tree carries 29 —
   so contract 14 was first reached somewhere on the unpublished 0.16 line, and
   nothing on that line is installable. The next-newest published build, 0.8.0,
   has no `contract` command at all. The requirement and the commands printed
   beside it contradicted each other, and the runbook has been corrected to
   record the installed build rather than assert a floor.
2. **The two entry failures are mutually exclusive by build.** The published
   build writes a CI pin that resolves and runs an evaluator that returns
   `warn` / `none` on the exact change class under study. This tree runs the
   evaluator that names all four boundary changes and writes a CI pin to a tag
   that does not exist
   ([#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506)).
   There is no combination that gives a partner both.
3. **The documented order dead-ends on Route H.** `init --write` then `verify`
   is what the runbook told every partner to run. On a repository with no tool
   surface — which is most of Route H — it writes a `CHANGE_ME` scaffold and
   the `verify` after it exits non-zero. The issue this experiment answers to
   says not to require a manifest on this route; the runbook required one.
4. **One route is invitable today.** `audit --host --save-baseline` on the
   base ref, then `audit --host --drift` on the change, is zero-manifest, runs
   on the installable build, names the added server and the widened rules, and
   leaves a committed before/after JSON pair to preserve. On the published
   build it delivers three of the four first-value recognitions: capability,
   evidence, and next action. It cannot deliver the fourth — the coverage
   limit — because inventory schema 0.1 carries no coverage or excluded-scopes
   surface.

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
request, and none opened a new issue.

| Reproduced | Existing issue |
| --- | --- |
| `init --write --ci` pins a workflow to a tag that does not exist | [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) |
| The installable build's `check` returns `warn` / `none` on a host-boundary change this tree blocks; the published and in-tree evaluators disagree | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) |
| The installable build's `check --agent claude-code` reports "No **Codex** boundary rule fired" — the pre-multi-host evaluator, still the newest one a partner can install | [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497), [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) |
| The installable build's host inventory carries no coverage or excluded-scopes surface, so a reviewer cannot see what was not read | [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520) |
| A `review_required` result has no authenticated continuation | [#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504) → [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) |
| The runbook required a manifest on a route that does not need one | fixed in this runbook |
| The runbook required a contract floor no published build carries | fixed in this runbook |

## Standing decision — 2026-09-04: **narrow**

This is a checkpoint against the pre-registered
[decision rule](design-partner-verifier-pilot.md#decision-rule), not the
terminal decision. The terminal decision needs observations that do not exist
yet; recording "undecided" and moving on would leave the shortfall
unexplained, so the checkpoint says what is being done about it.

Applying the ladder to today's counts: rung 1 needs `first_value` ≥ 2 and it
is 0. Rung 2 needs `first_valid_result` ≥ 1 and it is also 0 — no repository
got a working result that a reviewer then failed to act on, so this is not a
demonstrated failure of the review. Rung 3 takes it: **narrow**, and what to
narrow to is entry.

- **Persona and workflow that earned repeated use:** none. Nothing has been
  observed once, let alone twice, so no route, persona or workflow has earned
  anything yet. The rest of this decision is about where to spend the next
  invitation, not about what has been proven.
- **Persona and workflow to invite:** the developer or platform/DevEx reviewer
  who already meets permission and MCP changes in pull requests, on Route H,
  using the baseline/drift pair on the published build.
- **Withhold:** invitations onto `check` and `verify` as the per-change
  surface, until a published build's evaluator matches this tree's. Inviting a
  partner to a `warn` / `none` on a `Bash(*)` grant spends an introduction to
  demonstrate a blind spot.
- **Recurring blockers:** the publication drought behind
  [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) and the
  distribution parity gap behind
  [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) are
  upstream of every other row in the table above.
- **Blocking CI:** no partner has asked for it. Nobody will be asked to enable
  it before first value and repeat use are evidenced.

**What would change this decision.** A published build whose `check` matches
this tree's on the dry-run fixture lifts the withhold on Route A and on the
per-change `check` surface. Three attempted external repositories, with or
without a favourable result, replace this checkpoint with the terminal
decision. Neither is a matter of writing more runbook.

## Limitations

- **n = 0 external.** Every count above is zero, and zero counts support no
  claim about adoption in either direction.
- **The dry run is one synthetic fixture on one machine**, by a maintainer who
  knows the answers. It shows what a command emits; it cannot show what a
  stranger understands.
- **Findings are build-dated.** They describe `agents-shipgate` 0.15.0 as
  published on 2026-09-04 and this tree at 0.16.0. A release invalidates the
  comparison, and the route readiness dry run must be re-run and re-dated
  before any row here is cited again.
- **The shortfall is a maintainer's judgement about when to spend
  introductions.** It is recorded here so it can be disagreed with, not to
  foreclose enrolling a partner who wants to try the route as it stands.
