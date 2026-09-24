# Roadmap

> **Naming.** This project is **Agents Shipgate** (display name) / `agents-shipgate` (package, CLI, repo). See [`AGENTS.md` § Naming (canonical)](AGENTS.md#naming-canonical) for the full convention.

**Latest release: `v1.1.0`**
([release page](https://github.com/ThreeMoonsLab/agents-shipgate/releases/latest))
— a legibility and presentation-correctness release on the **advisory** channel
with no qualification claim. This line is checked against the
actual release tag by the `release-tag-consistency` job in
[`ci.yml`](.github/workflows/ci.yml) on every push to `main`.

## What Agents Shipgate is

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes. It turns a PR's change to what an agent can do into a
capability delta, evaluates protected release policy and returns machine-readable
control: **continue, repair, or stop for human authority**.

The release gate is one decision engine: `report.json.release_decision.decision`.
The current-control envelope supplies operational permissions and the next
action; verifier, handoff, PR and report surfaces project the same decision.
Execution success is not merge authority.

## Selected execution — 2026-09-24

The owner selected the **application-agent PR proof sprint** (#868) as primary,
with bounded host reliability maintenance. Accountable owner: Pengfei Hu
(`pengfei-threemoonslab`); current technical execution: Codex.
[Days 1–5 evidence and ordered backlog](docs/research/application-days1-5/README.md)
records released/main reproductions and the #580/#655 comparison design.
Weeks 2–3 implement paired inputs, then per-agent wiring; deeper readers follow
reproduced gaps. #610 remains a reproduced contract defect; #787 is the selected
recipe repair. #795/#812/#780/#369 retain their exact residual acceptance.

This selection supersedes the scheduling and recruitment instructions in the
September 14 historical plan below. #830 and external-outreach holds remain;
Oct 14 / Nov 13 / Dec 13 are evidence checkpoints, not release promises.
No ten-case value, external adoption or qualification claim is made.

## Historical lead wedge (September 14 focus)

Two surfaces share one engine: **(A)** tool-surface readiness for agent builders,
and **(B)** repository-declared host configuration, MCP bindings, permissions,
hooks and CI authority. **B remains the lead adoption hypothesis**, for developers
and platform/DevEx reviewers already encountering those changes in PRs. Running
a coding agent alone does not establish a need for another gate.

The job is **show what this PR changed, name evidence and coverage limits, and
let the reviewer decide**. The supported host route needs no manifest; agent
builders can use discovery/setup. Neither proves runtime-effective authority
or replaces judgment about business consequences.

Advisory first-value and repeat-use evidence precede mandatory checks.
Qualified behavior, tolerable noise and a usable human decision path precede
blocking CI. Organization-wide adoption must be demonstrated. New surface
follows the [non-goals](#explicit-non-goals) and
[`CONTRIBUTING.md`](CONTRIBUTING.md#surface-discipline).

## Historical post-1.0 adoption (2026-09-14; superseded above)

**Adoption > completeness.** `v1.0.0` is published on PyPI and GitHub from
`bace7c1871834e0b3eb98e6f60c0627725c53a59`, and #777 moved the pins to it.
`v1.1.0` followed on 2026-09-22 from
`e3c6cb0c7657d9c53d4e29b2061d04dcf99a4e9b` on the same channel, and the current
pins name it. The `v1.0.0` [advisory statement](https://github.com/ThreeMoonsLab/agents-shipgate/releases/download/v1.0.0/advisory-statement.json)
records advisory defaults, blocking opt-in and no qualification claim. That
publication supersedes the pre-release sequencing in the historical record
below; it does not satisfy the separate qualified-gate obligations in #572.

[#778](https://github.com/ThreeMoonsLab/agents-shipgate/issues/778) owns this execution sequence. The next product question is whether a
reviewer can act on the declared capability diff of a real PR, without a
maintainer translating it, and voluntarily use it on the next relevant change.
The primary user is a developer or platform/DevEx reviewer with such a PR, an
available base/head and another reviewer. Merely using a coding agent does not
establish the need for a new tool.

### Publication and evidence

| Record | What it establishes | Remaining work |
| --- | --- | --- |
| #648 / #644 | Published advisory package, installed current contract and host workflow; actual source/Action identity in the candidate smoke; #777 current pins | Publication delivery is complete. #570 retains the literal post-publication hosted Action replay. |
| #645 / #659 / #660 / #658 | Selected engineering workflow and fixed corpora: row precision 70/70, widening recall 55/65, benign zero-row 5/6, 41/50 comparable; cold start 26/30; 76 MCP findings, one false | Recall and benign rate remain below their original bars under the [owner retained-refusal decision](https://github.com/ThreeMoonsLab/agents-shipgate/issues/643#issuecomment-5657575213). They are not relabeled as passes. |
| #643 / #646 / #570 | The advisory product has shipped; the PyPI/local replay and generated immutable pin are recorded | Finish and link the actual published tag/Action observation before closing the remaining delivery record. It does not block supported, nonblocking user research. |
| #653 / #571 | Existing interview and pilot protocols | The [2026-09-14 ledger](docs/design-partner-pilot-results.md) records zero external invitations, attempts, first values and second-change observations. This is an experiment count, not a claim that no other users exist. |
| #572 | Qualified-gate evidence and historical release decisions | Preserve unmet human-label, independent qualification and authority obligations; no qualification or merge authority follows from this roadmap. |

The corpus measures candidate engineering behavior, not human comprehension or
adoption. The [support boundary](docs/host-boundary-support.md) also names
unread surfaces that produce no change row. Report precision, recall, coverage
and noise together; never treat refused or unobserved input as a quiet success.

### First value and repeated use

Lead with the existing manifest-free host `shipgate diff` on the user's PR.
[#779](https://github.com/ThreeMoonsLab/agents-shipgate/issues/779) makes that the clear current entry; [#780](https://github.com/ThreeMoonsLab/agents-shipgate/issues/780) carries it into optional,
nonblocking CI. [#781](https://github.com/ThreeMoonsLab/agents-shipgate/issues/781) repairs stale executable instructions already shipped
in the optional kit, without restoring #690's suspended legacy skill. These
repairs can proceed while #653 starts conversations; completing every entry,
reader or qualification issue is not a prerequisite for the local pilot.

Use the existing [pilot protocol](docs/design-partner-verifier-pilot.md) and
[aggregate ledger](docs/design-partner-pilot-results.md), not a new telemetry
platform. Record attempts, valid results, unaided versus assisted first value,
second-change eligibility and observed reuse separately. First value means a
non-author reviewer can name the capability change, evidence, coverage and
next action/owner, and records a concrete decision or fix from the artifacts
alone; an exit code, finding count or fixture pass is not that result.

Evaluate the existing ladder in order: **Continue** when at least two unaided
first values and one observed second change occur on the same route; **Stop**
when at least one valid result produces zero first values; **Narrow** otherwise.
Read those counts against their full denominators. These are exploratory
investment decisions, not a claim of product-market fit. No second opportunity
is neither a retained user nor a churned one. Observe second reviewers and
voluntary second-repository adoption only after repeat use, not instead of it.

### Milestones

Windows start with this plan on 2026-09-14. They assume two engineers and a
product/research owner; they are capacity estimates, not promises about other
people's replies. Record a dated shortfall when recruitment or observation is
incomplete. Reserve about 20% of engineering capacity for reproduced reliability
problems, initially triaging #577/#638/#575 rather than starting a broad rewrite.

| Window | Product/research work in parallel | Engineering work | Exit evidence |
| --- | --- | --- | --- |
| Now — days 0–7 | #571 records the current advisory channel decision; #653 selects people with recent relevant PRs and starts wave one | [#779](https://github.com/ThreeMoonsLab/agents-shipgate/issues/779) current docs and diff-first entry; [#780](https://github.com/ThreeMoonsLab/agents-shipgate/issues/780) host-only recipe and #570 hosted replay; [#781](https://github.com/ThreeMoonsLab/agents-shipgate/issues/781) installed-kit repair; prepare #771/#693 fixtures | A clear installed route and first research attempts; no invented recruitment or qualification record |
| Now — days 8–30 | #653 aims for two waves of five conversations and at least three live repository attempts; #571 begins the four-week second-change window | Merge #771 then #693 in their shared reader; #772 privacy/noise design and #714 loading-evidence semantics in parallel; fix the top observed entry/report failures under #328/#440 | Attempt → valid result → unaided value denominators, observed failure causes and a dated shortfall if needed |
| Next — days 31–60 | #571 observes the next eligible change, a second reviewer and whether CI stays enabled; apply Continue/Narrow/Stop | #699 interruption reduction and #698 measured latency where user evidence supports them; #714 → #702 and workflow-reader work → #701 only for bounded real cases | The selected route earns repeat use, or its persona/entry/scope is explicitly narrowed; no automatic feature expansion |
| Later — days 61–90 | Observe voluntary second-repository use, maintenance ownership and budget decisions; publish examples only with consent | #664's small App/onboarding MVP only if repeated value exists and workflow installation is an observed blocker; #666 consumer conversations before integration formats | An evidence-backed expansion decision, or an explicit decision not to expand |

### Order and concurrency

1. **Entry and research run together.** [#779](https://github.com/ThreeMoonsLab/agents-shipgate/issues/779), [#780](https://github.com/ThreeMoonsLab/agents-shipgate/issues/780) and [#781](https://github.com/ThreeMoonsLab/agents-shipgate/issues/781) are
   separate reviewable repairs. #653/#571 can use the existing local 1.0.0
   diff while they land. The current pilot-channel decision supersedes the
   old release-selection hold; qualified promotion requirements are unchanged.
2. **Repair silent omissions before adding host families.** #771 records step
   Action references and #693 named reusable-workflow secret mappings. Design
   and fixtures can run in parallel; merge changes to the shared workflow
   reader sequentially. This is an implementation order, not a functional
   dependency. A changed reference is not automatically a permission widening.
3. **Set semantics before deeper readers.** #772 decides how to expose an MCP
   URL-path change without exposing secret material; it cannot promise both
   every path change visible and every rotation quiet. #714 separates discovered
   hook files from declared/known loading, then #702 can follow bounded local
   scripts. #701 follows the workflow work and must account for consumed input
   defaults; unchanged `runs` text does not prove unchanged behavior.
4. **Reduce actual interruption and waiting.** #699 needs proof that an edit is
   semantically inert; #698 measures startup, tree and inventory costs before
   optimizing. Keep unknown coverage visible and existing no-change/dedup
   behavior intact. #328/#440 prioritize failures observed in real attempts.
5. **Expand only after the workflow earns it.** Three repositories running a
   command alone do not justify #664. Require observed repeat value and an
   installation obstacle. Keep its first scope to advisory onboarding; org
   policy, approval authentication and baseline governance remain separate.

Implementation PRs keep the established one-to-three independent review/address
loops, recorded in GitHub, with applicable checks before merge. Newly discovered
uncertainties or unrelated defects get bounded issues with evidence and a later
selection decision; they do not silently enlarge the current PR.

### Deferred scope

#690 remains suspended; user interruption is a cost, not an activation metric.
[#781](https://github.com/ThreeMoonsLab/agents-shipgate/issues/781) fixes existing shipped instructions only. #663's new host families need
a real attempt blocked by an absent surface before selection. #655/#656/#474's
tool-source base and manifest/lock-state proposals are not prerequisites for the
already manifest-free host route. #496's broad decomposition and #665's content
library remain deferred; demonstrated reliability failures can select a bounded
repair. #696's provider scorer repair precedes a provider experiment, not the
human-reviewer pilot. #666 starts with consumer conversations, not new formats.

#572, #504/#337/#555, and #456/#509/#510/#511/#512 retain their qualified or
human-authority obligations. An advisory PR comment does not authenticate an
approval, establish runtime-effective authority or authorize merge. Nothing in
this plan changes release gates, published machine contracts or declarations.

## Direction

The following record is **historical**, through the pre-publication 2026-09-14
checkpoint. Its no-go statements and future-tense release tasks describe the
commits named there, not the current published advisory product. The plan above
is current; unmet qualified obligations remain in #572.

<details>
<summary>Historical readiness and qualified-gate audit</summary>

**v1.0 is no-go at the audited `main` commit `452bdeb80` (2026-09-08).**
[#572](https://github.com/ThreeMoonsLab/agents-shipgate/issues/572) owns the
release decision; the [v1.0 milestone](https://github.com/ThreeMoonsLab/agents-shipgate/milestone/5)
tracks delivery. The [release-readiness audit](docs/engineering/v1-release-readiness.md)
records evidence and the disposition of all 45 original open issues. Closing
all those issues is not the release criterion.

**Delivery checkpoint — 2026-09-09.** The audit below remains the original
measurement. Subsequent repairs have passed their issue-specific GitHub
coding-agent review/address loops and applicable CI; they do not supply the
missing release qualification or human authority.

| Delivered work | Implementation evidence | Remaining obligation |
| --- | --- | --- |
| Live control currency and captured qualification bytes (#567/#559) | [#576](https://github.com/ThreeMoonsLab/agents-shipgate/pull/576), [#578](https://github.com/ThreeMoonsLab/agents-shipgate/pull/578) | Bind final evidence to the final candidate; ordinary tests are not beta qualification. |
| Discovery, identity and bounded reader/host recovery (#553/#561/#547/#543) | [#583](https://github.com/ThreeMoonsLab/agents-shipgate/pull/583), [#587](https://github.com/ThreeMoonsLab/agents-shipgate/pull/587), [#589](https://github.com/ThreeMoonsLab/agents-shipgate/pull/589), [#594](https://github.com/ThreeMoonsLab/agents-shipgate/pull/594) | Host-only discovery is addressed in #568; reviewed historical scope #563 and actual pilot adoption remain unresolved. |
| Current operator guidance and macOS pilot baseline recipe (#566/#550) | [#579](https://github.com/ThreeMoonsLab/agents-shipgate/pull/579), [#595](https://github.com/ThreeMoonsLab/agents-shipgate/pull/595) | No pilot participation or adoption counts were produced by these repairs. |
| Compact/full receipt validation contract and accepted decisions (#552/#493) | [#600](https://github.com/ThreeMoonsLab/agents-shipgate/pull/600), [#602](https://github.com/ThreeMoonsLab/agents-shipgate/pull/602) | Reconcile the final behavior before #569 freezes report 1.0. |
| Bounded FastMCP Context injection (#542) | [#603](https://github.com/ThreeMoonsLab/agents-shipgate/pull/603) | Whole-signature limits remain visible; broader framework/export provenance is deferred in #601. |
| Reproducible current sample goldens (#499) | [#605](https://github.com/ThreeMoonsLab/agents-shipgate/pull/605) | The 24 current artifacts have a checked recipe; #569 still owns the report 1.0 freeze and migration fixtures. |
| Host-only first discovery (#568) | Filename-only host candidates, no-write init/bootstrap handoff, CLI/zero-install parity and cold installed-wheel replay | Existing audit config-directory omission remains deferred in #613; synthetic tests do not demonstrate external adoption. |
| Maintenance and recovery documentation (#494) | [#619](https://github.com/ThreeMoonsLab/agents-shipgate/pull/619), [MAINTAINERS.md](MAINTAINERS.md) and the existing release runbook | Actual owner acceptance, response capacity, access/independence evidence and the responsible people's tabletop remain open. |
| Repository release immutability and tag protection (#573) | Immutability enabled for future releases; active `v*` update/deletion ruleset 22726019, with authenticated API read-back in the [runbook](docs/release-runbook.md#effective-configuration-observed--2026-09-09) | Restricted creators/writers, independent workflow/publication review, recovery ownership, remote refusal evidence and exercised candidate compatibility remain open. |

Three delivered slices leave larger release obligations open: [#582](https://github.com/ThreeMoonsLab/agents-shipgate/pull/582)
types unsupported historical base inputs but does not satisfy #563's catch bars;
[#591](https://github.com/ThreeMoonsLab/agents-shipgate/pull/591) improves #548's
prerequisite diagnostics without establishing its original intermittent cause;
[#599](https://github.com/ThreeMoonsLab/agents-shipgate/pull/599) binds imported
SDK guard evidence, and [#606](https://github.com/ThreeMoonsLab/agents-shipgate/pull/606)
compares a closed Boolean function and its literal source Agent membership.
Both keep capability dependency coverage incomplete and finding-exclusion
eligibility false. A return value is not an action effect or approval:
[#607](https://github.com/ThreeMoonsLab/agents-shipgate/issues/607), originally
deferred from #606's implementation pass, owns the isolated operation-to-policy-predicate
relationship. The subsequent engineering pass selects a bounded OpenAPI DELETE
profile: join actual source/capability/approval-predicate evidence, rebuild the
Git base, and report declared target changes separately from missing approval.
[The profile contract](docs/operation-attribution.md) preserves unresolved inputs,
unknown deployed reachability and false exclusion eligibility. #557 and #515 remain open; these source models do not satisfy
the TypeScript MongoDB acceptance case or historical safety bars.

The subsequent #515 prerequisite pass selects existing [#596](https://github.com/ThreeMoonsLab/agents-shipgate/issues/596)
for v1.0: a real same-version engine replay exposed reuse of a base report made
by an older reader. Base-cache keys now bind the effective engine requirement,
shared once per invocation with the verification plan. Source-checkout and
installed-wheel regressions exercise actual reader changes, old version-only
keys, warm reuse and corrupt-record recovery. This is a cache compatibility
repair before further #515 attribution/default work and #569 freeze. It does
not supply the TypeScript dependency/frame proof, finding-exclusion eligibility
or reviewed deployment declarations; OpenAPI operation attribution still
reconstructs its base from Git independently of cached report claims.

[#612](https://github.com/ThreeMoonsLab/agents-shipgate/pull/612) closes #545 and #516. It separates
supported instruction structure from prose across the final/local verifier,
preflight, host drift and exact edit-hook previews. Its successor schemas retain
raw byte freshness, unknown-structure review and legacy evidence migration;
prose edits cannot seed or consume hook approval memory. #516's deprecated
weakening ID remains available and non-emitting. These changes do not supply
reviewed deployment declarations, independent release signers, pilot observations
or qualification labels.

Host-only discovery #568 is delivered in merged
[#614](https://github.com/ThreeMoonsLab/agents-shipgate/pull/614). Its subsequent
review fixes preserve settled file-link and unreadable-census classifications,
published consumer values and CLI/stdlib parity. Final-wheel provenance #570 is
implemented in merged [#616](https://github.com/ThreeMoonsLab/agents-shipgate/pull/616),
with a candidate-only build path, immutable generated Action pin and a
hash-checked exact-wheel Action input. Both final PR heads passed applicable CI.

The first actual hosted [distribution smoke, run 34435280857](https://github.com/ThreeMoonsLab/agents-shipgate/actions/runs/34435280857),
passed on source `ed97340226dd85ba22693c333004be2d06255d23`. Downloaded artifacts
bind package 0.16.0, contract 33, that source/Action ref and the locked
hatchling 1.32.0 backend to wheel SHA-256
`42e86430c6a05a756f5e5fe1dcb9963a5186ffd808dfda657d9896258c93094d`.
The installed CLI and real composite Action agree on full engine identity and
the expected blocked refund result. This replaces the earlier dispatch 404;
the artifact explicitly remains synthetic and unqualified. Earlier local replay
bytes belong to their own commits and build environments, not this run.

Keep #570 open: the final frozen v1.0 candidate needs its own pre-publication
run and unchanged qualification/signing/publication bytes, followed by the real
published-tag/download observation. Do not wait for that post-publication
observation to implement #510's isolated negative rehearsal. Automatic approval
review still blocks #510's new protected workflow; current-main preflight
continues to route that concrete plan to a human. The rejected optional PR
trigger for #570 remains absent, but the existing manual workflow now runs.

Newly discovered issues #575/#577/#580/#581, #584–#586, #588/#590, #592/#593,
#597/#598/#601, #609–#611, #613/#615/#617/#618/#620 remain separately deferred; they are not silently
added to this implementation pass or counted as repairs. #609 documents a frozen
preflight schema URL/discriminator mismatch; the current successor uses a fresh
version without rewriting historical bytes. #610 owns the distinct compatibility
question of completed planning results exposing the shared permission vector;
preflight still supplies no verifier-bound current-control identity. #611 records
header-like hunk rows lost by the existing diff parser; incomplete comparisons
continue to require review until that separate repair lands.
The later findings cover directory-valued host configuration omitted by audit,
a corruption drill that rejects its filename before comparing wheel payloads,
fixture output overflowing the CI summary, manual-undraft guidance omitting
current asset/signature/tag checks, and the first-run cost of two discovery
inventories. #620 requires measurement before sharing walks with
different coverage and identity guarantees. None is counted as repaired:
#568's discovery routes the #613 defect to inspection and claims no audit
repair.

The subsequent #607 pass reproduced [#627](https://github.com/ThreeMoonsLab/agents-shipgate/issues/627):
a worktree control refresh can accept a changed declared input whose hash is
already in the plan but which is absent from the separately revalidated
dependency set. The selected #627 pass now reconfirms every recorded input blob
in both control observations, preserves live auxiliary origins before portable
copying, and validates frozen/generated copies beneath their bundle root.
Prepare/worker/assemble retain the same origin distinction; replay alone grants
no current authority. Missing, aliased, changed and over-budget inputs refuse.
This general currency repair precedes final candidate/freeze; it does not reopen
#299's delivered file-enumeration repair or count as a #567 review.

The pass separately reproduced [#630](https://github.com/ThreeMoonsLab/agents-shipgate/issues/630):
adding an ignored file under a directory-valued source can change a fresh tool
catalog without changing any recorded input blob. The selected #630 repair
binds reader-selected names and no-follow entry kinds in the same bounded input
session, across worktree/committed capture, both current observations, and worker
replay. Exact report exclusions preserve source siblings; incidental file and
negative-lookup parents are not new directory dependencies. Older plans without
capture need a fresh run. This repair precedes final candidate replay and #569
freeze; it does not remove static reader coverage limits, prove runtime
reachability, or supply the missing historical qualification evidence.

The #630 implementation review separately reproduced
[#633](https://github.com/ThreeMoonsLab/agents-shipgate/issues/633): a Codex plugin
component alias is resolved before capture, so retargeting it changes the next
skill surface while the previously resolved files and directory rows remain
valid. This is a lost lexical lookup, not missing membership of an already
recorded directory. The selected #633 repair retains the lexical component
lookup, uses shared no-follow file/directory capture, and records caught
failures as unconfirmable inputs. Worktree verification, current-control CLI,
committed missing-component capture and prepare/worker replay exercise the
refusal; committed symlinks keep their earlier archive rejection. Repairing a
component needs a fresh run. This does not establish a demonstrated merge
permission bypass or runtime behavior.

The #633 implementation pass separately reproduced
[#635](https://github.com/ThreeMoonsLab/agents-shipgate/issues/635): an implicit
`.app.json` can appear and change the next app surface while old full input
currency still passes. Its absent default was never selected for capture.
The #635 repair binds named absence/presence for implicit `skills`, `.app.json`
and `.mcp.json` before selection. An exact-name miss must also fail a no-follow
lookup before it counts as absence; a case alias or unreadable lookup remains
an unconfirmable dependency with a component diagnostic. Explicit paths do not
bind unused defaults, and unrelated root siblings stay outside the input set.
Actual verifier/current-control and prepare/worker regressions cover ignored
default insertion, including both control observations and committed/worktree
plans. This follows #633 and precedes final input-set qualification/#569 freeze;
it does not qualify all reader discovery or establish a merge-permission bypass.

The subsequent #515 pass projects the two shipped comparison profiles onto the
findings they support. `tool_surface_diff.finding_attributions[]` names, per
active finding, whether this change widened a bound, added or narrowed one
without resolving the finding, or left every modeled bound unchanged — the
three cases the existing `unchanged_findings` identity bucket reports
identically. Paired committed repositories pin all three plus a removed
approval declaration. [The projection contract](docs/finding-attribution.md)
records its refusals: only evidence naming a finding's own fingerprint
classifies, same-capability evidence can withdraw a negative claim but never
establish one, and unattributed findings are counted rather than dropped.
Dependency coverage stays `incomplete` and finding-exclusion eligibility stays
false, so no finding is excluded and no verdict changes. This is #515's
attribution step only: `--scope diff`/`--scope tree`, the receipt question,
decision consumption, #557's dependency closure and the TypeScript MongoDB
`cal-1` case all remain open, and no historical safety bar is satisfied.

The following sequence and approved release bars still apply. Contract
convergence, historical catch results, report freeze, actual human ownership,
blind labels, independent signing, rehearsals and external product evidence
remain prerequisites. The current release decision is still **no-go**.

- The [fixed W37 history](benchmark/miner/README.md#2026-w37-re-eval--the-fixed-cold-start-workflow-no-longer-reaches-verify)
  produced **0/19 verifier results** across five repositories. Legacy scan
  fallback catches are **0/2 must-block** and **1/3 needs-human**; both unchanged
  1.0 catch bars fail. No unsafe auto-pass was observed; fewer answers are not
  lower noise. #312 completed the measurement, not the repair.
- [#567](https://github.com/ThreeMoonsLab/agents-shipgate/issues/567) reproduces
  stale current-control authority during a live read; [#559](https://github.com/ThreeMoonsLab/agents-shipgate/issues/559)
  separates hashed from parsed qualification bytes. Both precede final
  evidence; neither demonstrates an exploited release.
- Report schema is frozen at **1.0**, meeting [STABILITY.md](STABILITY.md)'s
  report freeze condition; the freeze holds only while no breaking change
  lands, and a breaking change restarts it. Candidate-generated CI now selects the stamped
  source/version, as the hosted smoke above shows; ordinary source/preview
  builds retain the published fallback. The original release audit recorded
  **zero Actions variables**, signer **`CHANGE_ME`**, and **zero Release Rehearsal
  runs**. The distribution smoke supplies none of that qualification, signing
  or positive/negative rehearsal evidence.
- Repository release immutability and `v*` tag update/deletion protection are
  now enabled and read back, as recorded in
  the [runbook observation](docs/release-runbook.md#effective-configuration-observed--2026-09-09).
  The [later main-CI checkpoint](docs/release-runbook.md#main-ci-merge-requirements--2026-09-10-utc)
  adds nine required checks bound to the observed GitHub Actions App, with
  current-base testing and no bypass actors. Check configuration does not
  authenticate workflow contents or replace human approval.
  Current main/environment settings still do not establish independent
  workflow/publication review. [#573](https://github.com/ThreeMoonsLab/agents-shipgate/issues/573)
  retains restricted creators/writers, independent review, recovery ownership,
  remote refusal evidence and candidate compatibility; configured protection
  does not complete its release obligations.
- The [pilot ledger](docs/design-partner-pilot-results.md) records **zero**
  invitations, attempts, first values and second-change observations.
  [ADOPTERS.md](ADOPTERS.md) separately records no external adopters. An unrun
  experiment is not evidence of either demand or its absence.

### The v1.0 promise and owners

A developer or platform reviewer can inspect a **supported repository-declared
capability change**, open base/head evidence, understand coverage limits and
follow a truthful next action. Local/static operation and advisory-first CI
remain defaults; installed CLI, generated CI and machine contracts must agree.
Runtime-effective authority, universal coverage and organization-default
adoption are not launch claims.

The recommended v1.0 scope **withholds authenticated GitHub continuation**.
Request/evaluator/PR presentation does not prove independent human approval or
clear the gate. Human-owned stops remain; the broader autonomous loop is incomplete.

Assign actual owners before dependent work starts:

| Responsibility | Accountable role | Required result |
| --- | --- | --- |
| Scope and launch claims | Product owner | One supported workflow/claim set, pilot decision and disposition of external rollout conditions |
| Correctness and compatibility | Core engineering owner | Reproduced repairs, supported-boundary decisions, report/consumer migration evidence |
| Corpus and blind labeling | Benchmark owner and independent human raters | Current beta allocation, frozen labels/adjudication, holdout and qualifying origins |
| Qualification and publication | Release owner and independent signer/reviewer | Exact candidate handoff, reviewed trust, actual platform protection and rehearsals |
| Support and continuity | Maintenance/security owner | Honest response commitment, credential-name ownership and recovery procedure |
| Product observation | Pilot execution owner | Consented attempts, assistance and same-route first/repeat-value denominators |

Roles name obligations, not invented people; missing independent capacity is a
dependency. The 0–30 / 30–60 / 60–90-day windows are capacity hypotheses,
not deadlines or permission to skip an exit criterion.

### Now — 0–30 days: repair trust and entry; start evidence in parallel

Correct stale instructions through [#566](https://github.com/ThreeMoonsLab/agents-shipgate/issues/566),
name owners and start these independent workstreams. Coordinate shared files;
reader, control and publication work need not wait for each other.

| Workstream | Concrete order | Exit evidence |
| --- | --- | --- |
| Live authority | #567: reconfirm overlay contents/metadata and resolved base/merge base in the shared control reader | Same-path/base interleavings cannot return stale authority; unchanged reads and actual CLI consumers still work |
| Offline evidence | #559: capture bounded bytes once for hash validation and parsing across qualification inputs | Interleaved replacements cannot be scored as bound data; existing containment and identity joins remain enforced |
| Historical workflow | [#563](https://github.com/ThreeMoonsLab/agents-shipgate/issues/563) reviewed scope and [#564](https://github.com/ThreeMoonsLab/agents-shipgate/issues/564) new/renamed base inputs can start together | Same 19 SHAs/labels reach truthful verifier outcomes and restore the existing catch bars; no fabricated authority or scope |
| Change attribution | [#557](https://github.com/ThreeMoonsLab/agents-shipgate/issues/557) shared-helper/import/configuration evidence and [#607](https://github.com/ThreeMoonsLab/agents-shipgate/issues/607) operation/predicate attribution **before** [#515](https://github.com/ThreeMoonsLab/agents-shipgate/issues/515) default diff scope | #607's subsequent pass compares bounded OpenAPI declared targets and the actual approval predicate; broader dependency coverage, TypeScript `cal-1` and historical qualification remain pending before any default/gate change |
| Prose and structure | [#545](https://github.com/ThreeMoonsLab/agents-shipgate/issues/545) classification **before** completing [#516](https://github.com/ThreeMoonsLab/agents-shipgate/issues/516) | Final verifier, local check and preflight distinguish prose edits from structured grants/hooks/CI; malformed structure remains visible |
| Cold entry and CI | [#568](https://github.com/ThreeMoonsLab/agents-shipgate/issues/568) routes host-only repositories correctly; [#570](https://github.com/ThreeMoonsLab/agents-shipgate/issues/570) defines final-wheel/Action pin provenance | Supported Route H reaches a useful review without a placeholder manifest; local and generated CI name the intended engine/contract |

Recovery work is bounded to those supported paths:
[#561](https://github.com/ThreeMoonsLab/agents-shipgate/issues/561) distinguishes
reader repair from a missing input; relevant slices of
[#543](https://github.com/ThreeMoonsLab/agents-shipgate/issues/543) and
[#547](https://github.com/ThreeMoonsLab/agents-shipgate/issues/547) preserve
identity and host-read reasons. #543 is not automatically required for Route H;
a universal error taxonomy is outside scope.
[#553](https://github.com/ThreeMoonsLab/agents-shipgate/issues/553) brings
same-build discovery/runtime enumeration into parity; #550 repairs the existing
macOS temporary-path recipe where the pilot uses it.

**Start beta sourcing and blind human labeling now** under
[#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512).
The [separate beta source inventory](benchmark/safety-qualification/beta-strata-inventory.md)
records fixed PR revisions, exposure and per-cell gaps; it supplies no human
labels or qualifying evidence. Resolve #520's remaining rater output-contract
inconsistency and establish actual independent human raters before labeling.
Source collection can continue in parallel with those prerequisites.
Neither a v0.16 tag nor the optional 38-case pre-1.0 track is a prerequisite.
Prepare signing, negative rehearsal fixtures and #573's actual tag,
immutable-release and independent-review controls alongside engineering.
The release owner verifies effective settings; this plan does not change them.

Run [#571](https://github.com/ThreeMoonsLab/agents-shipgate/issues/571)'s existing
consent-based pilot on a pinned, explicitly unqualified installable preview
once its cold route works. Its four-week second-change window can overlap
engineering and labeling. No external outreach is authorized merely by opening
an issue. Record failures, assistance and lack of a second opportunity honestly.

**Exit:** reproducible integrity/cold-route repairs and historical remeasurement;
corpus, operational and pilot evidence work underway. An unmet exit extends the
workstream beyond its planning window.

### Next — 30–60 days: converge behavior, prove migration and freeze

Converge behavior in order **#557 (including #607) → #515** and **#545 → #516** before freezing
contracts. Equal fingerprints or changed-files-only scans cannot prove a
standing finding unrelated; preserve base/head evidence and explicit missing inputs.

Before freeze, diagnose [#548](https://github.com/ThreeMoonsLab/agents-shipgate/issues/548),
settle [#542](https://github.com/ThreeMoonsLab/agents-shipgate/issues/542)'s framework
support and [#552](https://github.com/ThreeMoonsLab/agents-shipgate/issues/552)'s
consumer/receipt-closure contract. Observed #548 outcomes denied authority;
#542 has a semantic mismatch; #552's review consumer already checks full closure.
None establishes a bypass. Repair a demonstrated support violation or state
its limit; unresolved safety consequences prevent sign-off.

[#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569) owns the
**report 0.43 → 1.0 freeze**, stable/provisional surface inventory and migration
from the actual shipped v0.15 contract, delivered in
[`docs/report-1-0-contract.md`](docs/report-1-0-contract.md). Preserve compatible controls and
historical readers; retire active pre-1.0 tier issuance in the final 1.x
implementation and update the qualification schema requirement before wheel
freeze. Old receipts gain no authority through conversion. An **unpublished RC**
can prove compatibility; no published prerelease is required. Record what
“holds” means; a breaking change restarts affected freeze evidence.

For #570, exercise the candidate Action at its immutable commit with the exact
candidate wheel in a disposable downstream repository **before publication**.
Real-tag/download smoke follows publication; not every #570 checkbox must close
before the tag. No post-signing wheel rewrite is permitted.

Complete the minimum accepted-decision corrections in
[#493](https://github.com/ThreeMoonsLab/agents-shipgate/issues/493), and real
release/security ownership and recovery in
[#494](https://github.com/ThreeMoonsLab/agents-shipgate/issues/494).
No six-ADR quota, broad refactor or invented maintainer is required. Independent
publication/signing still needs real people and effective #573 controls.

**Exit:** supported behavior and fixed-history catch bars hold; migration and
RC observations are recorded; final source, workflows, version, report schema,
policy, build inputs and wheel digest are frozen. Labels/holdout remain blind
and independently frozen. Candidate-affecting changes require new evidence.

### Later — 60–90 days: qualify, rehearse and release only on evidence

The final handoff is serial and has no signing circularity:

1. **#512:** assemble the approved 80-case beta corpus and human labels with
   terminal receipts from the exact final wheel; produce the **unsigned
   qualifying result**. Diagnose misses and repair their causes, rather than
   relabeling cases toward the implementation.
2. **[#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509):** validate
   that exact corpus/label/receipt/result handoff, independently sign against
   the reviewed trust root, and configure the four content-addressed release
   locations. Source-only results and restamped intermediate receipts do not qualify.
3. **[#510](https://github.com/ThreeMoonsLab/agents-shipgate/issues/510):** run
   substantive negative policy rehearsals and a positive rehearsal of the same
   final candidate. Negative fixtures can be prepared earlier. Rehearsal
   publishes nothing and does not require an existing release tag or weaker inputs.
4. **#572:** record pre-publication go/no-go with actual #573 controls, #494
   ownership/recovery, compatibility, signed evidence and the product decision.
   Publish only qualified bytes through the existing protected workflow.
5. **#570:** verify the real tag, PyPI package, generated Action and channel
   metadata; check the recovery path before declaring rollout complete.

The approved beta bar is **80 cases: 30 passed / 20 review-required / 30
blocked**, across seven profiles and 21 weighted strata. It requires at least
**32 qualifying-origin cases** (not 32 repositories), κ≥0.80, per-stratum
holdout≥20%, zero unsafe auto-passes, blocked exact **30/30**, safe **27/30** and
review **19/20**. Beta primary labels are human. Actual `insufficient_evidence`
is a coverage/exact-score miss, never a correct ground-truth outcome. The
[audit's policy table](docs/engineering/v1-release-readiness.md#the-approved-qualification-bar)
records the full allocation. Pre-1.0 evidence and historical catch measurements
cannot substitute for beta qualification.

Keep three product/evidence decisions separate. The #571 pilot ladder continues with **at least two unaided first values plus one second eligible
change on the same route**, stops at zero first value despite a valid result,
and otherwise narrows. The underlying denominators are repositories, not
repeated observations on one repository. Separately, existing external beta
promotion conditions require **three distinct design partners and four weeks**
for affected profiles. The release owner must establish their evidence or
resolve applicability through an explicit reviewed decision. A pilot shortfall
or narrower marketing claim is **not a waiver**. Neither product decision
replaces the machine safety bar. #511's participant validation remains non-gating.

**Exit:** the release record binds the candidate, signed beta qualification,
rehearsals, effective protections, support/migration and actual publication.
It names the pilot outcome, the separate external beta promotion disposition,
and withheld adoption/authorization claims. A failed catch, unbound evidence,
stale authority or an unmet schema promise remains no-go, whatever the date.

### Conditional work and completed foundations

Authenticated GitHub recording/continuation remains **#555 → #337 → #504**;
#293 separately requires a real host attestor. If that claim is added to v1.0,
prove independent signer credentials, author/bot/fork exclusion and replay
resistance before including it. Agent-created `User` reviews and fixture
signatures are not human approval. Keep #338 open for the larger autonomous
workflow. New organization state (#474) needs a demonstrated inventory problem;
a hosted control plane or broader adapters do not precede this release.

Twelve merged PRs remain delivered: reader/identity repairs (#540/#541/#544),
errors/prose-check retirement (#546/#549), request/evaluation/PR presentation
(#551/#554/#556), finding/effect evidence (#558/#560), qualification diagnostics
and history (#562/#565). **#328/#337/#515/#520 remain open** for residual
scopes; an epic's entire aspiration is not a new release prerequisite.

Build on existing manifest-free check, preflight, current control, capability
review, GitHub/PR/SARIF output, baselines, patches and opt-in feedback. #327/#325
adoption routing is complete. [Local attestations, policy packs, bundles and
registry](docs/organization.md) exist without authenticating GitHub review or
providing a hosted control plane. [Workflow evidence](docs/agent-workflow-evidence.md)
and replay machinery already exist; redacted bundles cannot rerun omitted source.

Implementation, preview availability and stable qualification remain distinct;
see the [distribution contract](docs/distribution.md) and latest tag above.

</details>

## Explicit non-goals

- **More framework adapters is not the roadmap.** The differentiation to prove
  is trustworthy capability review and resistance to gate weakening. New
  adapters (AutoGen, Semantic Kernel, LlamaIndex, additional language surfaces)
  are accepted only when a real workflow needs one — they do not advance the
  core thesis and are not a priority. The measured #484 gap is a bounded
  adoption repair, not permission for an adapter expansion program.
- **No second release verdict.** Release-decision surfaces project
  `release_decision.decision`. Setup routing and opt-in organization audits
  answer their own scoped questions and grant no independent release authority.
- **No agent execution, LLM calls, MCP connections, network access, or scanner
  telemetry** in the default static path. Runtime inventory stays an explicit,
  trust-gated, opt-in command — never part of default CI.
- **No speculative distribution or governance platform.** A single binary,
  Docker image, IDE extension, hosted scan or runtime certification is not
  scheduled without a measured workflow failure that existing entry points
  cannot resolve. Publication of pilot or adopter evidence remains opt-in.

## Release history

Releases `v0.2` through `v0.13.0` are complete. Highlights:

- **`v0.13.0` — Agent-native protocol.** `shipgate check`; the shared
  `agent_result_v1` contract for Codex, Claude Code, and Cursor; deterministic
  policy discovery; repair-loop routing; and the read-only `shipgate.check`
  MCP adapter.
- **`v0.12.0` — Verifier cycle.** `agents-shipgate verify`; `verifier.json`
  (`merge_verdict`, `can_merge_without_human`, `first_next_action`, `fix_task`,
  `capability_review`); `pr-comment.md`; diff-aware trust-root checks
  (`SHIP-VERIFY-*`: policy-weakened, baseline/waiver-expanded, CI-gate-removed,
  agent-instructions-weakened, capability-scope-broadened); `human_ack`;
  `capability_change`; and the agent-adoption harness with
  adversarial-obedience and verify-restraint scoring.
- **`v0.8.0` — Release decision engine.** `release_decision.decision` as the
  single, baseline-aware gating signal across CLI, JSON, Markdown, PR comments,
  and Action outputs.
- **`v0.6.0`–`v0.7.0` — Agent-friendly adoption.** `detect`, auto-detecting
  `init`, `--suggest-patches` / `apply-patches`, and per-check autofix metadata
  (`autofix_safe`, `requires_human_review`).
- **`v0.3.0`–`v0.5.0` — Static framework coverage.** Google ADK,
  LangChain/LangGraph, CrewAI, SARIF output, external policy packs, and baseline
  diff mode — static-by-default throughout.
- **`v0.2` — Onboarding and CI.** `init`, `doctor`, `self-check`, fixtures,
  baseline save/apply, SBOM generation, and release signing.

## Static-by-default principles

All adapters are read-only: local file parsing only; no agent run, model call,
tool call, MCP connection, or network access. Callbacks, plugins, and guardrail
declarations are static evidence, not proof of runtime enforcement. A known
dynamic or remote binding does not prove its downstream tool inventory or side
effects. Missing coverage remains explicit and never becomes a safe result by
omission. #520 records actual coverage misses without removing the shipped
`insufficient_evidence` verdict or reducing an applicable safety threshold.
Knowing a remote connection still does not establish its downstream effects
or deployed authority.
