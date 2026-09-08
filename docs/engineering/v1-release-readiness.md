# Agents Shipgate v1.0 release readiness

Status: **no-go at the audited candidate**. Evidence snapshot: `main`
[`452bdeb80`](https://github.com/ThreeMoonsLab/agents-shipgate/commit/452bdeb80aa40276f2ce0b7dc70d35e953806d81),
2026-09-08. Tracking issue:
[#572](https://github.com/ThreeMoonsLab/agents-shipgate/issues/572).

This is the evidence and product decision behind the
[release roadmap](../../ROADMAP.md). That roadmap carries the implementation
order and parallel work; the linked issues carry acceptance criteria. This
review covers all **45 issues open at the snapshot**, separately from the
three open Dependabot pull requests, plus recent completed work, the product
and stability contracts, release workflows, implementation and recorded user
outcomes. The seven issues opened by this review are listed separately below.
It does not claim to enumerate every defect a future user or security review
could find.

## Release decision

**Agents Shipgate is not ready for a v1.0 tag.** The decisive evidence is a
current-control currency defect, incoherent reads in qualification, a fixed
historical workflow that reaches no verifier, an unfulfilled report-schema
freeze promise, an incomplete qualification and publication handoff, and
repository controls that do not meet the release runbook.
These are concrete release conditions, not a judgment that every unfinished
epic must be completed.

The recent implementation establishes useful foundations: ADK connection
facts, FastMCP signature evidence, clearer effect projections, retained base
finding evidence, a bounded human-review question and evaluator, PR rendering,
and qualification coverage diagnostics. Those delivered slices do not yet
establish default diff scope, eliminate the remaining prose-only escalation,
provide authenticated GitHub continuation, or qualify a released artifact.
Passing ordinary CI proves neither those missing behaviors nor external user
value.

The release should keep its existing narrow promise, complete the work that
promise requires, and explicitly withhold broader claims. In particular:

- **Evidence and current authority must be trustworthy.** A reader cannot grant
  completion from inputs it already observed moving, and a scorer cannot hash
  one artifact and score different bytes.
- **The product must answer what this change did.** The owner-approved
  capability-change principle requires dependency-aware diff attribution and
  the prose/structure boundary. These are selected v1.0 requirements, not
  optional wording improvements.
- **The downloaded product and its generated CI must agree.** Repairs on
  `main` are not available to a user until a build carries them; an immutable
  wheel cannot be repaired by a later edit to a release constant.
- **Qualification applies to the final wheel.** The 19-case regression study,
  a source checkout, a preview, a closed labeling issue and a successful unit
  test are not the approved beta qualification artifact.
- **Product evidence remains unproven.** The pilot and adopter ledgers record
  zero external observations. This limits launch claims; it does not show that
  demand is absent or substitute a new user-count rule for the safety policy.

## The v1.0 user and promise

The first user is a **developer or platform/DevEx reviewer who already reviews
changes to repository-declared agent capabilities**. The lead adoption route
is a repository declaring what its coding agents may do: MCP connections,
permission rules, hooks and CI authority. A tool-building team can take the
manifest route where it has a reviewed agent surface and supported static
inputs. Running a coding agent, by itself, is not evidence that a team needs
another gate.

The job is:

> A PR changes a supported agent capability. Show me what changed, the exact
> base/head evidence, what you could not establish, and the next action and
> person responsible, so I can finish the review.

For this job, v1.0 means stable local/static review and merge-gate contracts,
with advisory-first integration and an explicit choice to enforce results.
Route H must not require a manifest merely to review host configuration.
Route A must not invent purpose, authority, bindings or other human-owned
claims to make setup succeed.

The product does not establish runtime-effective permission, actual credential
or sandbox enforcement, remote MCP tool behavior, universal framework
coverage, business acceptability, compliance certification, or proven
organization-wide adoption. Its coverage and static-evidence boundary are
part of its answer, not disclaimers to hide after the result.

**Authenticated GitHub decision recording is excluded from the recommended
first v1.0 claim.** The existing request, evaluator and presentation remain
useful, with their explicit unsupported-authentication behavior. Shipping
recorded continuation would add
[#555](https://github.com/ThreeMoonsLab/agents-shipgate/issues/555) →
[#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) →
[#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504) to the release
path. A GitHub `User` approval can be generated by an agent; it is not proof
of independent human participation. Existing human-owned stops remain
human-owned whichever feature set is selected.

This scope follows the [roadmap lead wedge](../../ROADMAP.md#lead-wedge-focus),
[#518](https://github.com/ThreeMoonsLab/agents-shipgate/issues/518) and the
[static host-boundary support matrix](../host-boundary-support.md). It does
not change the canonical tagline or add another decision engine.

## What the current evidence establishes

| Observation at the snapshot | What it means | Owning work |
| --- | --- | --- |
| W37 re-ran the fixed 19 historical PRs across five repositories and produced **0/19 verifier results** | The tested cold-start workflow does not reach the artifact the release claim depends on | [#563](https://github.com/ThreeMoonsLab/agents-shipgate/issues/563), [#564](https://github.com/ThreeMoonsLab/agents-shipgate/issues/564) |
| Legacy scan fallback yields `must_block_caught` **0/2** and `needs_human_caught` **1/3** | Both unchanged 1.0 catch bars fail; fewer answers cannot be sold as lower noise | Same issues; [W37 measurement](../../benchmark/miner/README.md#2026-w37-re-eval--the-fixed-cold-start-workflow-no-longer-reaches-verify) |
| No unsafe auto-pass was observed in those 19 cases | This is narrower than a successful safety result and does not make setup refusals successful catches | [#312](https://github.com/ThreeMoonsLab/agents-shipgate/issues/312) is the completed measurement, not an open release repair |
| A current-control read can return `complete` and merge permission while a dirty file or base ref changes during its final observation | The final comparison omits dimensions validated earlier; a subsequent read correctly refuses, but the interleaved read already returned stale authority | [#567](https://github.com/ThreeMoonsLab/agents-shipgate/issues/567) |
| Qualification hashes and parses some inputs in separate reads | The scored content can differ from the bytes its digest names | [#559](https://github.com/ThreeMoonsLab/agents-shipgate/issues/559) |
| The generated Action pin is `v0.15.0` / contract 10 while the source candidate is 0.16.0 / contract 31 | A later main-only constant update cannot change the pin bundled in the final wheel | [#570](https://github.com/ThreeMoonsLab/agents-shipgate/issues/570) |
| Report schema is **0.43**; STABILITY requires report **1.0** before the 1.0 line | A published compatibility condition has no completed delivery | [#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569) |
| The live Actions-variable read returned **zero repository variables**; Release Rehearsal had **zero runs** | Required qualification locations and an actual rehearsal are not established by those public operational surfaces | [#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509), [#510](https://github.com/ThreeMoonsLab/agents-shipgate/issues/510) |
| The committed release trust root has `signer_identity: "CHANGE_ME"` | Qualification signing identity is not configured; release validation correctly fails closed | [#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509) |
| The only active ruleset targets `main`; no tag ruleset, including inherited rules, was returned. Immutable releases are disabled | Required prevention of tag and release-asset mutation is absent from the inspected settings | [#573](https://github.com/ThreeMoonsLab/agents-shipgate/issues/573) |
| The main ruleset requires a PR but zero approvals; the `pypi` environment permits self-review and admin bypass | Configuration does not establish the runbook's independent review boundary; actual role independence remains unknown | [#573](https://github.com/ThreeMoonsLab/agents-shipgate/issues/573), [#494](https://github.com/ThreeMoonsLab/agents-shipgate/issues/494) |
| Pilot invitations, attempts, first value and second-change observations are all **0**; external adopter entries are **0** | No external workflow or retention claim is supported by these ledgers | [#571](https://github.com/ThreeMoonsLab/agents-shipgate/issues/571) |

The current-control probes used the real verifier and its clean read-only
fixture in isolated repositories. They scheduled a real file or ref mutation
between live observations; they did not mock a validator into accepting an
invalid result. They show stale returned authority, **not an observed
unauthorized merge or exploited release**. The code boundary is
[`read_current_control`](https://github.com/ThreeMoonsLab/agents-shipgate/blob/452bdeb80aa40276f2ce0b7dc70d35e953806d81/src/agents_shipgate/core/current_control.py#L554):
the first validation includes overlay/base identity, while the final
fingerprint compares a smaller set. The fix cannot promise that the filesystem
will never change after an API returns; it must reject the drift its own read
protocol has observed.

The qualification problem is separate. The
[scorer's report and receipt reads](https://github.com/ThreeMoonsLab/agents-shipgate/blob/452bdeb80aa40276f2ce0b7dc70d35e953806d81/scripts/run_safety_qualification.py#L227)
and the corpus/index input reads must hash and parse the same bounded bytes.
Neither repair changes verdict enums, policy floors or who may supply a human
declaration.

### Release protection is an observed gap, not an optional posture score

The read-only repository API audit found one active ruleset, **Protect main**,
targeting the branch. It requires a PR, but `required_approving_review_count`
is zero, code-owner review is false, and stale-review dismissal and last-push
approval are false. No tag ruleset was returned, including inherited rules.
The classic branch-protection endpoint returned 404; **that does not mean the
branch is unprotected**, because the ruleset exists.

Immutable releases reported `enabled: false` and `enforced_by_owner: false`.
The `pypi` environment has one required-reviewer entry, but
`prevent_self_review: false` and admin bypass is enabled. That entry alone
neither proves nor disproves independence of the people filling release roles;
the role relationship is still unknown.

These settings fall short of the existing
[deployment prerequisites](../release-runbook.md#deployment-prerequisites):
protect `v*` tag updates/deletions, enable immutable releases, restrict release
writers, review workflow/trust-root changes and require an independent
publication reviewer. [#573](https://github.com/ThreeMoonsLab/agents-shipgate/issues/573)
is therefore a **P1 hard operational blocker**, alongside #494's ownership and
recovery work and #509's signing configuration. It owns actual configuration
and a safe read-back verification, not a badge or optional security score.
Do not fabricate reviewers or claim independence from a GitHub account type.

These are dated settings observations. A failed or inaccessible read means
unknown, not configured, and settings may change after the audit. Verify the
intended rules and their effective coverage again for the release candidate;
YAML alone cannot establish the external boundary.

## The approved qualification bar

The governing implementation is
[`production_safety_requirements`](../../src/agents_shipgate/schemas/safety_qualification.py)
and its separately checked release-gate restatement. The approved
[policy decision](../release-evidence-policy-decision.md) and the subsequent
removal of `insufficient_evidence` from ground truth must be read together.
Old issue titles and uncorrected operator prose still say 56/100; they do not
override the current **38/80** policy.

| Requirement | Beta: required for 1.0 | Pre-1.0: optional for 0.x |
| --- | ---: | ---: |
| Cases | **80** | **38** |
| Expected `passed` / `review_required` / `blocked` | **30 / 20 / 30** | **14 / 14 / 10** |
| Profiles / nonempty profile-outcome strata | **7 / 21** | **7 / 21** |
| Minimum qualifying-origin cases | **32** | **16** |
| Minimum inter-rater κ | **0.80** | **0.80** |
| Minimum holdout fraction in each stratum | **20%** | **20%** |
| Maximum unsafe auto-passes | **0** | **0** |
| Minimum exact safe passes | **27/30** | **13/14** |
| Minimum exact blocked outcomes | **30/30** | **10/10** |
| Minimum exact review outcomes | **19/20** | **14/14** |
| Expected-IE ground-truth cases / exact floor | **0 / 0** | **0 / 0** |
| Primary-label protocol | **Two blind human primary labels; adjudicate and freeze** | Approved independent-agent protocol, with owner adjudication and its stated restrictions |
| Required report schema at this snapshot | **0.43**, deliberately updated with the reviewed 1.0 freeze | **0.43**, under the same explicit requirement |

Qualifying origins are the policy's real-history, rejected/reverted and
design-partner categories; the floor counts cases, not distinct repositories
or public adopter entries. Actual `insufficient_evidence` remains a coverage
and exact-score miss. A zero expected-IE denominator is `not_applicable`, not
a successful coverage metric. The v6 qualification diagnostics add visibility
without changing the six existing metric values or their pass conditions.

The beta allocation is fixed, not a bag of 80 convenient examples:

| Profile | Passed | Review required | Blocked | Total |
| --- | ---: | ---: | ---: | ---: |
| MCP/OpenAPI declared binding | 6 | 4 | 6 | 16 |
| OpenAI Agents SDK | 5 | 3 | 4 | 12 |
| LangChain/CrewAI | 5 | 3 | 4 | 12 |
| Google ADK | 3 | 2 | 3 | 8 |
| n8n | 3 | 2 | 3 | 8 |
| Multi-agent handoffs | 4 | 3 | 5 | 12 |
| Coding-agent trust roots | 4 | 3 | 5 | 12 |
| **Total** | **30** | **20** | **30** | **80** |

The [labeling amendment](../release-evidence-policy-decision.md#amendment-1--the-pre-10-labeling-protocol-and-the-participant-validation-gate)
permits agent primary raters for `pre_1_0` only. It explicitly preserves human
primary labels for beta. The 38-case evidence cannot be promoted into a 1.0
qualification by renaming its tier. Start
[#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512)'s sourcing
and blind human labeling now; neither a v0.16 release nor completion of the
38-case track is a prerequisite.

[#508](https://github.com/ThreeMoonsLab/agents-shipgate/issues/508) is closed,
but closure is not the frozen-corpus handoff. Private labels and archives are
intentionally ignored by Git; their absence from the checkout does not prove
they do not exist. Before using any existing material, verify the exact frozen
hashes, labels, adjudication and handoff under the tier that may consume them.
Beta has its own human-label obligation regardless of optional pre-1.0 work.

### External beta promotion conditions remain separate

The [qualification README](../../benchmark/safety-qualification/README.md)
and [distribution contract](../distribution.md#protected-qualification-inputs)
also retain **a four-week observation window and three distinct design
partners** as external beta rollout stop conditions, reviewed from the rollout
record before promoting affected profiles. The machine's combined origin
minimum does not enforce them. Their surrounding 40/23 origin counts are stale
under #566; that does not make the separate rollout conditions disappear.

Keep three different decisions visible: the approved machine qualification,
these existing external beta promotion conditions, and the pilot's
continue/narrow/stop product experiment. Passing the pilot ladder is not a
replacement for three distinct partners, and passing the origin count is not
a replacement for either. #571/#572 must have the release owner review the
applicable profiles and rollout evidence explicitly. If applicability or the
meaning of the historical rollout requirement is unclear, record it as an
unresolved owner decision; do not silently waive it through narrower marketing
copy or a new interpretation of an issue. This audit changes none of these
conditions.

## Newly filed release work

| Issue | Release treatment | Implementation and evidence required |
| --- | --- | --- |
| [#567](https://github.com/ThreeMoonsLab/agents-shipgate/issues/567) — current-control currency | **P0, hard** | Confirm the same full live identity at validation and final observation: overlay bytes/metadata, base, merge base, HEAD/tree and path membership. Exercise dirty-path and moving-base interleavings plus unchanged controls. |
| [#568](https://github.com/ThreeMoonsLab/agents-shipgate/issues/568) — Route H applicability | **P1, selected workflow** | Keep tool discovery separate from whole-product applicability. Route supported host-only repositories to existing audit/check; preserve incomplete-input facts and CLI/zero-install parity. |
| [#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569) — report 1.0 freeze | **P1, hard** | Settle behavior, inventory stable/provisional contracts, generate report 1.0, publish actual shipped-version migration and prove compatibility. Update the qualification schema requirement and active pre-1.0 tier retirement deliberately before the final 1.0 wheel and receipts, preserving historical readability. The RC exercise can use an untagged artifact. |
| [#570](https://github.com/ThreeMoonsLab/agents-shipgate/issues/570) — final wheel and CI pin | **P1, hard distribution correctness** | Give final distributions a reproducible release-provenance rule; keep preview/source behavior honest. Smoke the candidate Action by immutable commit before tagging, then the real tag and downloaded wheel after publication. Never rewrite qualified bytes. |
| [#571](https://github.com/ThreeMoonsLab/agents-shipgate/issues/571) — pilot execution | **P1, product and rollout decision** | Run the existing consent-based experiment on a pinned installable candidate, or record a finite enrollment/opportunity outcome and an explicit decision narrowing launch claims. Separately review the existing three-partner/four-week beta promotion conditions for affected profiles; a shortfall does not satisfy them. No new machine safety threshold. |
| [#572](https://github.com/ThreeMoonsLab/agents-shipgate/issues/572) — release decision | **Tracking epic** | Link the exact candidate, hard gates, selected claims, conditional exclusions, owners, qualification, rehearsals and publication verification. Do not use the epic as a second scanner verdict. |
| [#573](https://github.com/ThreeMoonsLab/agents-shipgate/issues/573) — actual repository release controls | **P1, hard operational** | Apply and verify the runbook's tag/release immutability and independent review boundaries. Record effective rules, bypass authority and real owners safely; do not infer protection from YAML or invent a second reviewer. |

The Route H failure is small to reproduce and consequential at the first
step. A supported Python 3.12 run of the detector against only these files:

```jsonc
// .claude/settings.json
{"permissions":{"allow":["Bash(npm test)"],"deny":[]}}
// .mcp.json
{"mcpServers":{"billing":{"command":"node","args":["./servers/billing.js"]}}}
```

returns `is_agent_project: false`, empty `suggested_sources` and
`codex_plugin_candidates`, and `python_parse_truncated: false`. The
[quickstart stop condition](https://github.com/ThreeMoonsLab/agents-shipgate/blob/452bdeb80aa40276f2ce0b7dc70d35e953806d81/docs/quickstart.md#L89)
then tells this user the product is inapplicable, while its own Route H
explicitly supports both files. Excluding `mcpServers` configuration from MCP
**tool exports** is correct. Inferring that there is no host-boundary review
to perform is the defect. Fix that decision and its consumers; do not invent a
manifest or a new scanner.

The release-pin defect is another lifecycle distinction. The
[published-release constant](https://github.com/ThreeMoonsLab/agents-shipgate/blob/452bdeb80aa40276f2ce0b7dc70d35e953806d81/src/agents_shipgate/published_release.py#L18)
is intentionally updated after a tag. That fixes source/preview resolvability,
but the qualified wheel permanently contains the older value. The Action
installs its own checkout by default, so the emitted old ref selects the old
engine. Requiring a future `v1.0.0` tag to exist before publication is not the
solution: validate the immutable candidate commit and exact wheel before the
tag, then verify the real channels after publication. #570 therefore has two
phase exits: its implementation and candidate smoke must pass before tagging;
its public-channel smoke closes rollout after publication. Requiring the whole
issue to close before a tag would create an impossible dependency.

## Disposition of all 45 original open issues

The classifications below concern **release treatment**, not whether a problem
is valuable. “Hard” means an existing safety, compatibility or publication
condition. “Selected workflow” means required for the v1.0 promise chosen
above, sometimes through a bounded disposition rather than completion of the
entire parent issue. “Conditional” applies only if its feature is claimed or a
concrete candidate failure makes it necessary. “Post-1.0” means there is no
present evidence that it must precede this release.

This table preserves the original snapshot population even when live titles,
labels or scope are subsequently corrected. In particular, the old 100-case
title on #512 and 56/100 title on #456 are not the governing evidence bar.

| Original open issue | Release treatment | Reason and bounded resolution |
| --- | --- | --- |
| [#566](https://github.com/ThreeMoonsLab/agents-shipgate/issues/566) — stale qualification instructions | **Hard, operator contract** | Reconcile current issue bodies, runbook and distribution counts with approved 38/80 policy, 16/32 origins and v6 artifacts. Preserve dated historical reasoning; do not change code to match obsolete prose. |
| [#564](https://github.com/ThreeMoonsLab/agents-shipgate/issues/564) — new/renamed base project inputs | **Hard** | Represent an absent or renamed project at base truthfully so applicable fixed-history cases reach verification. Do not fabricate prior declarations or classify missing input as safe. |
| [#563](https://github.com/ThreeMoonsLab/agents-shipgate/issues/563) — reviewed monorepo scope | **Hard, P0 regression** | Establish reviewed scope and re-run identical historical SHAs/labels; preserve the unassisted cold-start result separately. A setup refusal is not a caught unsafe change. |
| [#561](https://github.com/ThreeMoonsLab/agents-shipgate/issues/561) — recovery ownership | **Selected workflow** | Distinguish missing input from an unsupported reader using typed evidence. Name the repair/input and owner on selected routes; keep unresolved cases explicit. |
| [#559](https://github.com/ThreeMoonsLab/agents-shipgate/issues/559) — qualification byte consistency | **Hard** | Hash and parse the same bounded report, verifier, receipt, corpus and index bytes before final scoring and signing. |
| [#557](https://github.com/ThreeMoonsLab/agents-shipgate/issues/557) — dependency attribution | **Selected workflow, prerequisite** | Prove shared guard/helper/import/configuration coverage before excluding a standing finding. Equal fingerprint/support is not proof that a capability did not change. |
| [#555](https://github.com/ThreeMoonsLab/agents-shipgate/issues/555) — independent GitHub signer | **Conditional; excluded from first v1.0 claim** | Required before authenticated recording/continuation. Establish reviewer-held trust, credential isolation and actual independent participation; a User review or test signer is insufficient. |
| [#553](https://github.com/ThreeMoonsLab/agents-shipgate/issues/553) — discovery enumeration parity | **Selected workflow, before distribution freeze** | Derive or equality-test the same-build integration lists. A released website and main are different versions; disclose that instead of forcing false cross-version equality. |
| [#552](https://github.com/ThreeMoonsLab/agents-shipgate/issues/552) — optional receipt closure | **Selected stable-API disposition** | Document exactly what compact control validates and test pointer-bound versus receipt-only mutation. Inspected production consumers do not establish a bypass; escalate a demonstrated stronger consumer assumption, not the existence of an optional file alone. |
| [#550](https://github.com/ThreeMoonsLab/agents-shipgate/issues/550) — macOS baseline path | **Selected pilot route, small fix** | Use a canonical non-symlink temporary path in the recipe before the macOS pilot reuses it. Preserve containment; do not relax the identity boundary to accept `/tmp`. |
| [#548](https://github.com/ThreeMoonsLab/agents-shipgate/issues/548) — intermittent rejection reason | **Selected candidate reliability investigation** | Diagnose the real parallel-suite failure before RC sign-off. Observed outcomes still denied authority; do not claim a security bypass or broaden expected reasons to hide the fault. |
| [#547](https://github.com/ThreeMoonsLab/agents-shipgate/issues/547) — host inventory failure reason | **Selected workflow** | Preserve bounded actionable read/snapshot failure evidence. Current refusal is safe but not a useful recovery route; no universal error-system rewrite is required. |
| [#545](https://github.com/ThreeMoonsLab/agents-shipgate/issues/545) — remaining prose review routes | **Selected workflow, prerequisite** | Resolve generic trust-root and Codex instruction/skill heuristics against the owner-approved prose/structure boundary. Keep actual grants, executable hooks, CI weakening and malformed inputs visible. |
| [#543](https://github.com/ThreeMoonsLab/agents-shipgate/issues/543) — unresolved identity reason | **Selected Route A setup, not universal** | Carry why the name is unresolved through init/doctor. A mechanically observed name and a human identity choice are different; do not infer either from generic placeholder prose. |
| [#542](https://github.com/ThreeMoonsLab/agents-shipgate/issues/542) — FastMCP Context limits | **Selected support-boundary disposition** | Fix the SDK profiles promised or used by qualification, or expose their exact unsupported limitation. No general Python type evaluator is required; false injection claims cannot be called supported. |
| [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520) — coverage-miss semantics | **Selected workflow, parent** | Scoring diagnostics are delivered. Finish named-input/recovery obligations and exact-candidate evidence through children; actual IE remains a miss and never a new expected label. |
| [#518](https://github.com/ThreeMoonsLab/agents-shipgate/issues/518) — capability-change principle | **Selected product contract, parent** | Judge completion through #557→#515, #545→#516 and truthful coverage. A named capability and evidence must reach the reviewer; no separate engine or blanket risk judgment. |
| [#516](https://github.com/ThreeMoonsLab/agents-shipgate/issues/516) — prose-only judgments | **Selected workflow** | The old check is deprecated but broader acceptance remains open under #545. Finish paired prose/structured/malformed tests and preserve the shipped deprecation cycle. |
| [#515](https://github.com/ThreeMoonsLab/agents-shipgate/issues/515) — default diff scope | **Selected workflow** | After #557, make the decision consume attribution and bind scope in report/receipt identity. Retain whole-tree findings for audits and explicit incomplete-base/dependency states. |
| [#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512) — beta corpus | **Hard, start sourcing now** | Deliver the current 80-case allocation, blind human primary labels, adjudication, holdout, origins, final-wheel receipts and an unsigned qualifying beta result. Hand that exact result to #509 for independent signing. Optional pre-1.0 publication is not a dependency. |
| [#511](https://github.com/ThreeMoonsLab/agents-shipgate/issues/511) — historical participant validation | **Non-gating, parallel/post-1.0** | The approved policy explicitly makes it non-gating. A discovered material labeling error must be handled; silence is not endorsement or an adoption count. |
| [#510](https://github.com/ThreeMoonsLab/agents-shipgate/issues/510) — negative release rehearsal | **Hard** | Exercise valid-shaped evidence that fails substantive tier/policy/binding requirements, then a successful same-candidate rehearsal. Missing URLs or parser errors alone do not prove policy rejection. |
| [#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509) — receipt/signing/promotion handoff | **Hard** | Consume #512's frozen beta handoff, exact receipts and unsigned qualifying result; validate candidate/corpus/label/index binding, independently sign under a reviewed trust root, and publish the four content-addressed locations. No source-only or restamped evidence. |
| [#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504) — human decision postcondition | **Conditional; feature parent** | Retain delivered request/evaluator semantics. Full authenticated GitHub continuation depends on #555/#337 and is not claimed by the first v1.0. |
| [#502](https://github.com/ThreeMoonsLab/agents-shipgate/issues/502) — contribution stance | **Post-1.0** | State intake honestly when revisiting contribution policy. Starter-issue or external-contributor counts are not qualification or user-value evidence. |
| [#501](https://github.com/ThreeMoonsLab/agents-shipgate/issues/501) — dependency posture | **Conditional on the candidate** | Fix a relevant locked-install/audit or supply-chain defect and publish an accurate posture. Merging every Dependabot PR is not a release condition. |
| [#500](https://github.com/ThreeMoonsLab/agents-shipgate/issues/500) — extras-qualified pin parsing | **Conditional on the candidate** | Bring forward if a required final lock uses the shape or blocks reproducibility. Do not turn a currently unused parser edge into an arbitrary launch dependency. |
| [#499](https://github.com/ThreeMoonsLab/agents-shipgate/issues/499) — golden regeneration | **Selected freeze/reproducibility slice** | Make the report 1.0 fixture regeneration used for release repeatable from committed instructions or a script. Broad historical-golden cleanup may follow. |
| [#496](https://github.com/ThreeMoonsLab/agents-shipgate/issues/496) — hotspot decomposition | **Post-1.0** | Fix bounded defects first; a six-module refactor is not itself a release outcome. Require decomposition only where a concrete repair cannot otherwise be reviewed safely. |
| [#495](https://github.com/ThreeMoonsLab/agents-shipgate/issues/495) — complexity/type gate | **Post-1.0** | Useful engineering control, but its presence does not prove the candidate correct. Preserve existing required checks and add targeted regression evidence. |
| [#494](https://github.com/ThreeMoonsLab/agents-shipgate/issues/494) — maintenance/release continuity | **Hard operational slice** | Record real release/security ownership, supported-version response and credential-name/rotation/recovery procedures; #573 owns the observed configuration gaps and read-back. Honest single-maintainer limits are acceptable; an invented backup is not. |
| [#493](https://github.com/ThreeMoonsLab/agents-shipgate/issues/493) — shared architectural decisions | **Selected freeze documentation slice** | Correct stale authoritative decisions and record the invariants constraining the v1.0 contract. A large ADR library, lessons catalogue or general PR-size program is not required before tagging. |
| [#492](https://github.com/ThreeMoonsLab/agents-shipgate/issues/492) — historical schema archive | **Post-1.0** | Preserve published schema URLs and readers during #569. Relocating old files adds migration risk without satisfying the freeze condition. |
| [#480](https://github.com/ThreeMoonsLab/agents-shipgate/issues/480) — benchmark query layer | **Post-1.0** | Current receipt/corpus integrity needs a coherent snapshot, not a database. Add a query layer only for a measured workflow need. |
| [#474](https://github.com/ThreeMoonsLab/agents-shipgate/issues/474) — committed capability state | **Post-1.0 research** | Prove the recurring MCP-review gap before adding persistent state. Existing audit/diff evidence and the pilot can test demand first. |
| [#472](https://github.com/ThreeMoonsLab/agents-shipgate/issues/472) — OWASP mapping | **Post-1.0** | A coverage map can explain limits but is not certification or evidence that supported changes are detected correctly. |
| [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456) — corpus delivery | **Hard evidence parent** | Keep optional 38-case pre-1.0 and mandatory 80-case beta obligations separate. Verify handoffs, not issue checkbox state; the beta chain can proceed directly. |
| [#440](https://github.com/ThreeMoonsLab/agents-shipgate/issues/440) — report/engine agreement | **Selected product-contract parent** | Close specific interpretation and projection defects through their owning issues. One engine and exact evidence matter; completing every presentation aspiration does not gate v1.0. |
| [#430](https://github.com/ThreeMoonsLab/agents-shipgate/issues/430) — oversized base archive | **Post-1.0 unless measured release failure** | Retain bounded behavior. Bring forward if supported candidate/history runs exceed declared limits; optimize scope without dropping dependency evidence. |
| [#369](https://github.com/ThreeMoonsLab/agents-shipgate/issues/369) — structured operational argv | **Post-1.0** | The existing POSIX-rendered command plus documented `shlex.split` contract is usable. A new argv field is not needed to establish the chosen workflow. |
| [#338](https://github.com/ThreeMoonsLab/agents-shipgate/issues/338) — autonomous issue-to-PR workflow | **Conditional broader epic** | Keep open. Static PR review does not claim fully autonomous issue delivery or authenticated human continuation. |
| [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) — GitHub human authorization | **Conditional; excluded from first v1.0 claim** | Presentation/fallback is delivered. Acquisition, authentication and a real independent-reviewer continuation depend on #555 and must not be inferred from agent review loops. |
| [#328](https://github.com/ThreeMoonsLab/agents-shipgate/issues/328) — product versus configuration failures | **Selected recovery parent** | Init defect classification is delivered; #561/#543/#547 own remaining supported-route repairs. Complete those bounded routes rather than rewrite every error path. |
| [#309](https://github.com/ThreeMoonsLab/agents-shipgate/issues/309) — Ruby/RubyLLM relevance | **Post-1.0** | An additional ecosystem route is outside the selected supported set; no adapter-expansion program is justified by the release target. |
| [#293](https://github.com/ThreeMoonsLab/agents-shipgate/issues/293) — host-session approval receipts | **Conditional; separate host integration** | Requires an actual trusted host attestor and protected credentials. Static repository review neither provides nor needs session-approval evidence. |

A selected support-boundary disposition is not permission to relabel a known
incorrect result as a limitation without evidence. For #542, test the
advertised SDK shape and the qualification inputs. For #552, inspect every
stable consumer and test the declared closure. For #548, establish the cause
of the nondeterministic rejection before signing off the RC; a passing isolated
rerun does not close it. Escalate a demonstrated unsound result to the hard
path. Conversely, do not call an unproven edge case an authority bypass merely
to give it release priority.

## Product validation and launch claims

The [pilot results](../design-partner-pilot-results.md) contain zero external
invitations, attempts, first values and second-change observations. The
[adopter registry](../../ADOPTERS.md) has zero external entries and one
maintainer dogfooding row, explicitly advisory. These are different ledgers
under different consents. They must never be added together or substituted for
one another.

[#521](https://github.com/ThreeMoonsLab/agents-shipgate/issues/521) correctly
closed after delivering its runbook and a dated shortfall; its acceptance
allowed that outcome. [#475](https://github.com/ThreeMoonsLab/agents-shipgate/issues/475)
correctly delivered the registry. Reopening either would confuse implemented
measurement with execution. #571 instead assigns the outstanding experiment:
a pinned candidate, a current route, an execution owner and a dated product
decision.

Use the existing [decision ladder](../design-partner-verifier-pilot.md#decision-rule)
in order:

1. **Continue** when at least two reviewers reach first value unaided and at
   least one second eligible change is observed, all on the same route.
2. **Stop** when first value is zero despite at least one valid result.
3. **Narrow** otherwise, naming what is being narrowed.

The underlying denominators are repositories, with distinct author and
reviewer roles; two observations on one repository do not create two first
values. First value requires the four recognitions—changed capability,
evidence, coverage limit and next action—plus a concrete decision or fix.
The ten-minute target is an experiment target, not an achieved timing. Preserve
the four-week second-change window, all failures and assistance, and the
reason an integration was disabled or bypassed. No eligible second change
means unobserved, not retained or churned.

An installable, explicitly unqualified preview can support this learning while
engineering and beta labeling proceed. No external message or public identity
is authorized merely by opening the issue; the existing contact, consent and
redaction rules still apply. If enrollment remains insufficient at the
checkpoint, record an explicit owner decision to narrow or defer adoption and
organization-default claims. Another zero ledger without execution or that
decision does not complete #571.

An honest statement that external adoption is unproven can narrow marketing
claims; it cannot waive an existing promotion condition. Before releasing any
affected beta profile, the release owner must separately establish the
three-partner/four-week rollout evidence or resolve the applicability question
through an explicit reviewed decision. The pilot's outcome alone supplies no
such waiver. An organization-default or authenticated autonomous-workflow
launch also needs the additional evidence its own claim entails. Neither
product decision modifies the approved machine qualification policy.

## Sequence and go/no-go

The full executable sequence is in [ROADMAP.md](../../ROADMAP.md). The main
constraint is that **candidate-affecting work precedes final receipts**;
external sourcing and operational preparation should not wait behind it.

| Stage | Work that may run together | Exit before the next dependent stage |
| --- | --- | --- |
| Scope and ownership | Correct #566; assign release, benchmark signing, human labeling, security/support and pilot responsibilities; start #512 sourcing | One current promise and policy; real owners or explicit staffing gaps; no fictitious assignees |
| Correctness | #567; #559; #563 and #564; #557→#515; #545→#516; #568/#570 and bounded recovery. #553, minimum #493/#494 and #573 control configuration can run alongside | Same-input trust tests hold; selected workflows yield correct, actionable evidence; fixed-history catch bars restored |
| Evidence preparation and learning | Blind human beta labeling; preview/RC cold-route tests; #571 observation and external beta rollout review; signer and negative-rehearsal preparation | Frozen labels/provenance under the approved protocol; documented candidate/pilot observations and disposition of three-partner/four-week profile-promotion conditions; no scorer exposure that contaminates holdout |
| Contract and candidate freeze | #569 after behavior convergence; stable consumer/migration replay; active pre-1.0 tier retirement in the final 1.x distribution; candidate CI and packaging | Final source/workflow/version/schema/policy/build-backend identity and exact wheel, with an untagged RC compatibility exercise and historical artifact readability preserved |
| Qualification and rehearsal | #512 frozen beta plus final-wheel terminal receipts and an unsigned qualifying v6 result → #509 independent signing/promotion; #510 substantive negative and exact-candidate positive rehearsal | All approved bars and bindings pass; #573 actual deployment protections and ownership checked; no publication from rehearsal |
| Publication and rollout | Existing protected release workflow; #570 real tag/PyPI/Action smoke; public channel and recovery verification | Only qualified bytes published; compatible generated CI; release record, limitations, support and product claims match observed results |

These stages are dependency order, not a promise that all take equal time. A
rough planning window is one to three days to settle scope/owners, a few weeks
for parallel correctness work, and a four-week pilot observation overlapping
engineering and corpus work. Human labeling, independent signing and actual
case sourcing are external capacity constraints. No approved freeze duration
or release date exists merely because this document proposes a planning window.
The recorded RC exercise under #569 must define what “holds” means before the
final qualification is treated as durable. It can use an untagged candidate;
a published `1.0.0-rc` is not a prerequisite. The report freeze also owns the
explicit retirement of active pre-1.0 tier issuance in the final 1.x
distribution, without removing historical readers or changing the optional
0.x track's approved evidence. Settle that behavior before freezing the final
wheel, not after collecting its receipts.

The evidence handoff is equally precise: #512 delivers the frozen beta corpus,
labels, exact-candidate receipt index and **unsigned qualifying result**; #509
validates that handoff and independently signs and promotes it. Neither issue
may wait for a signature the other is supposed to produce. #570's
post-publication channel verification remains a rollout obligation, not a
pre-tag requirement that every checkbox on that issue already be closed.

The v1.0 go/no-go record under #572 must name:

- the exact candidate source, version, report schema and wheel digest;
- completed integrity and selected-workflow issues, with their regression
  evidence and remaining supported limits;
- the approved beta corpus/labels/receipt index and signed qualification,
  without changed thresholds or substituted pre-1.0 labels;
- negative policy-rejection evidence and the successful same-candidate release
  rehearsal, plus verified deployment prerequisites;
- migration, support/security ownership and publication recovery;
- the pilot decision, a separate reviewed disposition of the existing external
  beta rollout conditions for affected profiles, and precisely which adoption
  and authorization claims are included or withheld.

A failed safety catch, broken evidence binding, unresolved current-authority
fault or unfulfilled schema promise means no-go. A favorable pilot cannot
compensate. A missing external cohort means the product claim remains unproven and the
existing external profile-promotion conditions remain unsatisfied or explicitly
unresolved. It must not be converted into a favorable adoption claim, a silent
waiver, or a fabricated machine safety threshold. After any candidate-affecting repair, produce
new wheel-bound evidence and repeat the affected qualification/rehearsal. Do
not restamp historical measurements.

## Uncertainties and deliberate limits

- **Private handoffs:** frozen labels, archives or partial receipts may exist
  privately. The audit does not infer their absence from ignored files or the
  absence of a public artifact. Their identity and admissibility must be
  checked before use.
- **Beta performance:** W37 measures a 19-case historical path; it cannot tell
  us the final 80-case beta score. Unit fixtures do not fill that gap either.
- **Independent humans:** the inspected repository does not establish beta
  rater availability or a deployed independent GitHub signer. Staffing and
  authenticated participation cannot be generated by a coding agent.
- **Operations:** settings can change after the dated API read. The release
  owner must verify protection, credentials and recovery at the actual release;
  a workflow YAML declaration does not prove external configuration.
- **Support limits:** the absence of a complete dynamic dependency model is
  not permission to claim one. Narrow supported patterns and explicit missing
  evidence remain valid product boundaries; silent completeness is not.
- **Intermittent behavior:** #548's cause and consequence are unresolved. The
  observed rejection still denied authority. Investigation must determine
  whether there is a release-relevant soundness or reliability failure.
- **Unsupported runtimes:** the detector traceback on system Python 3.9 is an
  early-diagnostic improvement, not a supported-runtime regression:
  [zero-install](../zero-install.md) already requires Python 3.12+. It does not
  justify expanding runtime support or introducing a binary for this release.
- **External beta conditions:** the affected-profile scope and fulfillment of
  the existing three-partner/four-week requirement need release-owner review.
  Unclear applicability is an outstanding decision, not an automatic exception.
- **Market proof:** zero invitations says an experiment has not run. It says
  nothing conclusive about willingness to adopt, maintain policy or pay. Those
  questions need actual observed workflows.

The next work is therefore deliberately finite: repair the evidence and the
selected workflow, qualify the exact artifact, and publish only the claims
those two forms of evidence support.
