# Cut A — the pre-1.0 strata inventory

[`strata-inventory.csv`](strata-inventory.csv) maps the known candidate pool onto
the 28 profile × decision cells the `pre_1_0` policy requires, so Cut B could mine
the empty cells instead of re-finding the full ones. It is the first of the four
cuts in [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456).
**Sourcing is complete as of the [close-out](#cut-b--the-close-out-2026-09-02):
every slot is `pinned`, and the next step is the Cut C calibration round.**

**It is a sourcing plan, not evidence.** It contains no label, no verifier
verdict, and no receipt. Nothing in it can qualify a release, and nothing in it
is an input to the qualification runner.

## What the inventory is not

**Not a rater input.** [Amendment 1](../../docs/release-evidence-policy-decision.md#amendment-1--the-pre-10-labeling-protocol-and-the-participant-validation-gate)
condition 2 gives each rater session exactly three things: the pinned repository
state, the PR diff, and [`benchmark/miner/LABELING.md`](../miner/LABELING.md).
This file is none of them. It names a target decision for every slot, so a
session that has read it is contaminated and its labels are inadmissible. Keep it
out of rater packets.

**Not a label, and not convertible into one.** A `target_decision` is a
*hypothesis about which cell a candidate will land in*, recorded so mining can be
aimed. The corpus's `expected_decision` comes only from two blind primary labels
plus adjudication. When an adjudicated label disagrees with the hypothesis, the
label wins and the case moves cells — leaving the planned cell open again. That
is the normal case, not a defect, and it is why the register below keeps a
reserve.

**Not a fixture register.** #310 and #316 produce minimized in-tree fixtures,
`synthetic` by construction. The qualifying case for the same PR is the actual
repository at pinned base/head SHAs run through the external-PR recipe. One
source PR can feed both; neither substitutes for the other.

## Where a target decision may come from

`target_basis` is a closed vocabulary. No member is *taken from* verifier output
— the engine's decision on a candidate never selects that candidate's cell,
because a corpus assembled to match the verifier's own verdicts cannot measure
the verifier.

| `target_basis` | Meaning |
|---|---|
| `miner_label` | A committed miner label exists for this subject. `evidence_ref` names the CSV row. |
| `diff_substance` | Targeted from what the change does, recorded in the [candidate register](#candidate-register) below. |
| `sample_design` | A shipped sample built to exhibit this outcome. `evidence_ref` names the sample directory. |
| `constructed_design` | A Cut B construction built for the corpus to exhibit this outcome, under [`constructed/`](#constructions-are-changes-and-they-are-not-samples). `evidence_ref` names its `CASE.md`. |
| `unsourced` | A gap. No candidate; `mining_lead` says where to look. |

**`miner_label` is not verifier-independent, and must not be described as if it
were.** The labeling worksheet
(`benchmark/miner/results/*.labels.template.csv`) carries `head_decision`,
`verify_verdict`, `verify_can_merge` and `verify_trust_root_touched`, and
[`LABELING.md`](../miner/LABELING.md) tells the labeler the worksheet holds
"enough PR context (title, verdicts, capability counts) to label most rows
without opening the diff." So a `miner_label` row's *cell targeting* was made
with the engine's verdict in view. That biases which cell a candidate is aimed
at; it does not reach the corpus label, which Amendment 1 raters produce blind
and without this file. Every such row carries `miner_label` in its `exposure`
column so the bias is visible per row rather than argued in prose, and
`test_the_miner_label_basis_is_disclosed_as_verifier_exposed` fails if the
worksheet ever stops exposing verdicts — at which point this disclosure should
be revisited rather than left standing.

The miner's three-way vocabulary also does not distinguish `review_required`
from `insufficient_evidence` — `needs_human` covers both — so a `needs_human`
candidate may be targeted at either cell. **Drawing that line is work for the
Cut C calibration round**, not for this file; the labeling guide has to answer
it before 56 labels are produced against it.

## Exposure, and why it decides the split

The policy requires at least one holdout case per stratum, and holdout means
**evidence the engine was never tuned on**. Whether a candidate meets that bar
is a fact about this project's development history, not about where the
candidate's bytes live — so it is recorded per row, in `exposure`, and
`split_eligibility` is derived from it.

| `exposure` | Meaning | Blocks holdout? |
|---|---|---|
| `engine_tests` | The subject is named in this repository's `tests/` or `src/`. | **yes** |
| `maintainer_walk` | Walked during development; its findings drove engine changes. | **yes** |
| `shipped_sample` | An in-tree `samples/` path — the goldens under `samples/*/expected/` are what the engine is developed against. | **yes** |
| `benchmark_scored` | Appears in a committed miner sweep. Measured, not tuned on. | no |
| `miner_label` | A committed miner label exists, produced from a verifier-exposed worksheet. | no |
| `none` | No known exposure. | no |

A row may carry several, `;`-separated. `split_eligibility` is `tuning_only` if
any exposure blocks holdout, and `either` otherwise; a `tuning_only` slot can
never be its cell's holdout case, so **every cell must keep enough `either`
slots to meet the policy's own holdout floor**, `ceil(stratum × fraction)`,
recomputed from the policy rather than assumed to be one.

`engine_tests`, `shipped_sample`, `benchmark_scored` and `miner_label` are
detected mechanically from the tree and re-checked on every run; the detector is
a **floor**, so a row may declare more than it finds but never less.
`maintainer_walk` cannot be detected — `grafana/mcp-grafana#1080` appears
nowhere in this repository and still drove the `tool_sources[].binding` design —
which is exactly why exposure is recorded rather than inferred.

**`benchmark_scored` does not block holdout, and that is a judgment worth
re-examining.** Being *measured* is not being *tuned on*. The rule this file
applies is: a scored subject stays holdout-eligible unless an engine change was
made in response to that measurement, in which case it earns `engine_tests` or
`maintainer_walk` too. Two subjects already crossed that line —
`openai/openai-agents-python#3392` produced
`tests/test_capability_change_schema_hash_parity.py` and the fix behind it, and
`stripe/ai#232` has a committed fixture tree under `tests/fixtures/stripe_pr232`.
**If any of the W24–W26 scoring drove engine changes that are not traceable
this way, those subjects must be reclassified**; that is a question about this
project's own history, and only the owner can settle it.

**An origin is a fact about the PR, not a plan.** A merged PR is
`real_history` (or `design_partner`); a closed-unmerged or reverted one is
`rejected_or_reverted`. An **open** PR is neither, and cannot fill a slot: it
has no landed history for the counterfactual to be about, and Gate 2's
"reviewers outrank authors" rule presupposes a decision that resolved it. Every
external candidate's state is recorded in the register and checked against its
row's origin — and so is every candidate the [reserve](#reserve) holds, which
is where the next one comes from, and every gap whose `mining_lead` named a
specific PR while gaps existed.

## Columns

| Column | Meaning |
|---|---|
| `slot_id` | `<profile>.<target_decision>.<n>`, `n` counting from 1 within the cell. |
| `profile` | One of the seven profiles the policy stratifies by. |
| `target_decision` | The cell this slot is being sourced for. |
| `origin_class` | The `SafetyCaseOrigin` this slot is planned to carry. |
| `exposure` | `;`-separated, per the table above. `none` when there is none. |
| `split_eligibility` | Derived from `exposure`: `tuning_only` or `either`. |
| `status` | `pinned`, `unpinned`, or `gap` — see below. |
| `candidate_ref` | `github.com/<owner>/<repo>#<number>`, or an in-tree `samples/<name>` path. Blank for a gap. |
| `pinned_base` / `pinned_head` | Full 40-character SHAs. Blank unless `status: pinned` and the candidate is external. |
| `target_basis` | Where the target decision came from, per the table above. |
| `evidence_ref` | The in-tree file substantiating the row. Blank for a gap. |
| `mining_lead` | Where to look. Only on gaps. |
| `notes` | What the change does. No verdicts. |

**`status`.** `pinned` means the slot is ready to hand to a rater: an external
candidate with both SHAs recorded, or an in-tree sample, which this repository's
own history pins. `unpinned` means the candidate is identified but its base and
head are not yet resolved — **abbreviated SHAs are not pins**, so a candidate
known only by a short prefix stays `unpinned` until both full SHAs are recorded.
`gap` means no candidate exists yet.

### The pinning convention is unsettled, and the API will not settle it

Two conventions exist in-tree and they disagree. The miner sweeps record the
**merge commit on the default branch and its parent** — `stripe/ai#232` is
pinned `5af4bcd1…` → `cd8cee57…`. The GitHub API reports `baseRefOid` /
`headRefOid`, which for the same PR are `dd624f51…` → `4d201d8e…`: the base
branch's tip *today* and the PR branch's head, neither of which is the state
that PR landed against. The walk notes used the API form.

The sweep convention is the correct one for this corpus — it reproduces "the
repository immediately before and immediately after this change landed" — and
**the recorded pins must not be "corrected" from the API**, which is why
`test_a_pinned_external_candidate_matches_the_sweep_that_recorded_it` reads
every external pin back from the sweep that resolved it. It refuses a pin no
sweep can corroborate, a subject left `unpinned` after one did, and two
recordings of one subject that disagree — in two sweeps or twice in one, since
a contradictory duplicate would otherwise be read as agreement.

A candidate that cannot be mined — a private design-partner repository, say —
therefore fails that guard rather than passing on a hand-written pin. That is
the intended failure: the pin would be unverifiable, and whether to accept one
anyway is an owner's decision, not a silent exception.

The last three walk candidates were pinned this way in the
[close-out](#cut-b--the-close-out-2026-09-02), and `github-mcp-server#3076`
shows why the distinction is not academic: its walk note abbreviated the head
as `5ea9a0e8…`, which is `refs/pull/3076/head` and, after a squash merge, is
not an ancestor of the default branch at all. Resolving that abbreviation
would have pinned a commit no clone of the repository reaches. The merge
commit the convention asks for is `8ec62491…`, whose first parent is the
`bfb59bb7…` the same note recorded.

Settled for Cut B (2026-09-01), one form per origin, so a rater is always
handed "the repository the change was proposed against, and the repository the
change produced":

| Origin | `pinned_base` | `pinned_head` |
|---|---|---|
| `real_history`, `design_partner` (merged) | first parent of the merge commit on the default branch | the merge commit |
| `rejected_or_reverted`, closed without merge | `git merge-base <head> <base branch>` — the fork point the PR was proposed against | the PR branch's final commit (`refs/pull/<N>/head`) |
| `rejected_or_reverted`, merged then reverted | first parent of the *reverted* PR's merge commit | that PR's merge commit; the revert commit is named in the register as the evidence of rejection |

A closed-unmerged PR has no merge commit, so its base is a fork point rather
than a mainline parent; both SHAs still come from the clone, never from
`baseRefOid`, which moves with the base branch.

## Cut B — sourcing the gaps

Cut B is staffed as two sessions plus a Cut C preparation thread, split by the
kind of work rather than by profile, and they coordinate through the CSV.

| Session | Work | Slots |
|---|---|---|
| A | in-tree constructions, no network | every `synthetic` gap |
| B | real-repository re-mining | every `real_history` / `rejected_or_reverted` gap |
| C | Cut C preparation: the five calibration cases and the two rater harnesses | none — it must not read this file's targets into the rater packets |

**A session claims a slot by filling it, in one change:** `status` moves off
`gap`, `target_basis` moves off `unsourced` to the basis the evidence supports,
`evidence_ref` names the in-tree file that substantiates it, `mining_lead` is
cleared, `exposure` is declared, and the candidate is added to the
[register](#candidate-register). A row with a candidate and no evidence is not
a claim, and `test_a_gap_carries_a_lead_and_nothing_else` refuses it. Neither
session touches another session's rows; both recompute the summary tables in
[Where the plan stands](#where-the-plan-stands), and the merge resolves them.

**A sourcing change makes no engine change.** Nothing under `src/` and nothing
under `tests/` other than this inventory's own guard moves in a Cut B change.
The point of Cut B is holdout-eligible evidence, and a fix made in response to
a candidate is exactly what turns it into tuning material. A defect a session
finds while sourcing is reported, not fixed, and the candidate keeps its
exposure as recorded. The `benchmark/` tooling may change; the engine may not.

### Constructions are changes, and they are not samples

A Session A construction lives at
`benchmark/safety-qualification/constructed/<case>/`, never under `samples/`:
the goldens under `samples/*/expected/` are what the engine is developed
against, so a corpus-built synthetic committed there would be tuning material
the moment it landed. Each case is laid out as

```
constructed/<case>/
  CASE.md      the design record: the shape built, and why it exhibits the target
  base/        the repository before the change
  head/        the repository after it
```

and is checked by `test_a_constructed_case_is_a_change_with_its_design_record_beside_it`:
`base/` and `head/` both exist and differ — the corpus labels a *change*, so a
single tree has nothing to label; `CASE.md` sits beside the trees and never
inside one, so a rater packet built from the trees cannot carry the target
decision it names; neither tree holds engine output; and no construction
shares a name with a shipped sample. Its row is `origin_class: synthetic`,
`target_basis: constructed_design`, `evidence_ref: <case>/CASE.md`, and
`exposure: none` — which the detector re-checks by name, so a construction that
is later written into a test or the engine fails the floor and must be
re-declared `engine_tests`. The diff is a plausible pull request: the smallest
change that introduces the property, in the framework's own idiom, with no
comment that names the outcome. Running the engine on a construction to confirm
that the mechanism is present is fine; tuning the construction until the
verdict matches the target is not, because the target comes from the design.

### Real history is sourced through a sweep, then a miner label

A Session B candidate enters the way every existing `real_history` row did:
a committed sweep under `benchmark/miner/results/` records the measurement
(`benchmark_scored`), and a committed label file records the cell it is aimed at
(`miner_label`). That keeps the row holdout-eligible — being measured is not
being tuned on — at the cost the register already discloses: the worksheet
shows the labeler the engine's verdict, so the aim is verifier-exposed and the
row says so. The alternative, reading the diff and recording the reading here as
`diff_substance`, is refused for new candidates because `diff_substance` implies
`maintainer_walk` and would make every newly mined slot `tuning_only`, which is
the opposite of what Cut B is for.

Closed-unmerged and reverted candidates are enumerated outside the miner's
merged-only `mine` command and evaluated one at a time with `evaluate` at the
pins the convention above gives them; their rows carry a blank `merged_at`.

A closed-unmerged PR has no merge commit, so the W36 sweep pins it at the
**fork point and the PR head**: `base = git merge-base <head> origin/<base branch>`,
`head = refs/pull/<n>/head`. That reproduces "the mainline the change was
written against, and the change as it was rejected" — the closed-unmerged form
of the same convention. A reverted PR is pinned like any merged PR.

## Where the plan stands

51 slots over the 21 cells. The policy asks for 38 cases, so most cells carry a
reserve — `mcp_openapi_declared_binding × review_required` carries five slots
because its best candidates are engine-development inputs and can only ever be
tuning cases. The four `blocked` cells the policy sets to one case
(`openai_agents_sdk`, `langchain_crewai`, `google_adk`,
`coding_agent_trust_roots`) carry exactly one slot and no reserve: there is no
second candidate to reserve, which is why the policy asks for one.

| | Count |
|---|---|
| Slots with a candidate | 48 of 51 |
| Gaps to mine or construct | 3 |
| Slots planned as a qualifying origin | 36 (floor is 16) |
| …of those, already sourced | 33 |
| …of those, still to find | 3 |
| Slots planned as `synthetic` | 15 (ceiling is 22) |
| Slots that can be a cell's holdout case | 45 |
| Slots that are engine-development inputs | 6 |

Per profile:

| Profile | Sourced | Qualifying origin | Holdout-eligible | Gaps |
|---|---|---|---|---|
| `mcp_openapi_declared_binding` | 9 | 8 | 5 | 0 |
| `openai_agents_sdk` | 7 | 5 | 6 | 0 |
| `langchain_crewai` | 6 | 4 | 6 | 0 |
| `google_adk` | 7 | 5 | 6 | 0 |
| `n8n` | 7 | 3 | 8 | 1 |
| `multi_agent_handoffs` | 6 | 4 | 7 | 1 |
| `coding_agent_trust_roots` | 6 | 7 | 7 | 1 |

Per outcome:

| Outcome | Sourced | Qualifying origin | Holdout-eligible | Gaps |
|---|---|---|---|---|
| `passed` | 14 | 15 | 13 | 2 |
| `review_required` | 22 | 16 | 21 | 1 |
| `blocked` | 12 | 5 | 11 | 0 |

Every number on this page is recomputed from the CSV by
`tests/test_strata_inventory.py`, so the reading and the plan cannot drift
apart.

### Why four cells hold one case and not two

The policy asks for two cases per cell. Four `blocked` cells ask for one. The
shortfall is not a sourcing backlog to be worked off later — it is a claim
about the world, and it was measured rather than assumed.

The first corpus round labelled 48 cases blind, two raters each. Of the seven
profiles, only four produced a real-world `blocked` case at all, and each of
those four produced exactly one:

| Cell | What exists | Why there is no second |
|---|---|---|
| `openai_agents_sdk × blocked` | one real case | the sweep found one merged change two blind raters placed at `blocked`; the rest were `review_required` |
| `langchain_crewai × blocked` | one construction | its real candidate was sourced as `blocked` and both raters placed it at `review_required` |
| `google_adk × blocked` | one construction | same shape: the real candidate moved to `review_required` under blind labelling |
| `coding_agent_trust_roots × blocked` | one real case | the second slot was a shipped sample, and a sample is cold start, not a change |

Cut B recorded the cause before any of this, and the W36 sweep confirmed it:
**every** `blocked` slot with a qualifying origin came from a closed-unmerged or
reverted PR, never from merged history — because a change that should have been
stopped usually was. The material is thin because the world is thin here, and
the sourcing convention (`## Where a target decision may come from`) is what
makes that visible rather than hiding it behind a construction.

Which is the alternative, and why it was refused. A second case in each of
those cells is reachable today — by building four more constructions. A cell
filled entirely with constructions measures our imagination rather than the
world, and `mcp_openapi_declared_binding × blocked`, the one `blocked` cell
with two real candidates, is what the other four would be pretending to be.
One real case is worth more than two invented ones, so the count says one.

Three cells are *not* on that list even though they are short today, because
their shortfall has a fixable cause and they stay at two: `n8n × passed` and
`multi_agent_handoffs × passed` lost a slot when the cold-start samples were
retired, and `coding_agent_trust_roots × review_required` is short one blind
label, not one case. They are carried as `gap` rows with mining leads.

The floors move with the cells, and the *rates* do not:
`minimum_blocked_exact` falls from 14 to 10 because there are 10 `blocked`
cases, not because anything was allowed to be wrong more often.
`test_the_pre_1_0_policy_is_never_laxer_than_production_per_rate` holds that
line — it re-derives each floor as production's rate applied to this corpus,
and fails if a floor is one case laxer than that.

### What the shape says

**Every vendor MCP server this project has walked is disqualified from holdout
use.** `github-mcp-server#3020` and `#3076`, `awslabs/mcp#4489` and
`grafana/mcp-grafana#1080` are the four servers the adoption walks measured —
and each of them produced an issue, a fix, or a regression test. They are the
best-understood candidates in the pool and they can only ever be tuning cases.
Three `mcp_openapi_declared_binding` cells therefore carry a third slot whose
only job is to be holdout-eligible. **The best material and the admissible
material are close to disjoint**, and that is the single most expensive fact in
this plan.

**The origin floor was the binding constraint, not the case count.** 16 of 38
cases must be `real_history`, `rejected_or_reverted`, or `design_partner` —
23 of 56 when Cut A and Cut B ran, and the pool Cut A inventoried did not hold
them: clearing that floor took 18 further qualifying candidates, more than half
of it, which Cut B and the close-out mined. The floor fell with the corpus, not
with the share: it is 40% of the cases either way. The counts the plan holds now are in the tables above, which
are recomputed from the CSV; this paragraph is about where the cost fell.

**`blocked` is the scarce outcome.** (`insufficient_evidence` was scarcer
still, and it is why that outcome is no longer a target at all — see the
scarcity note below.) Before Cut B, 5 of 15 `blocked` slots had a candidate. Session A's constructions are all `synthetic`, so they moved
neither outcome's qualifying-origin count; the W36 sweep did, and it confirmed
the vein: every `blocked` slot with a qualifying origin was filled from
closed-unmerged or reverted PRs, not from merged history, because a change
that should have been stopped usually was.

**`n8n` was the profile most likely to fail this deliverable.** Before Cut B
one of its eight slots had a candidate and no n8n repository had ever been
mined; Session A's constructions fill five slots and hold two reserves, all
`synthetic`, and the W36 sweep below supplied its first two real-history
candidates from community workflow repositories — the profile is now fully
sourced, with the thinnest qualifying-origin margin of the seven.

**The pool was swept by a build that no longer exists.** The 2026-W24 … W26
sweeps ran before [#403](https://github.com/ThreeMoonsLab/agents-shipgate/issues/403),
when a trigger reported `no_match` as a confident negative and the MCP trigger
matched filename globs only. `langchain-ai/langgraph`, `modelcontextprotocol/servers`
and `pydantic/pydantic-ai` were each swept 40 PRs deep and triggered **zero**
times. Those zeroes are not evidence that the repositories hold no candidates,
and several gap rows depend on re-mining them. **Do not treat an old sweep's
silence as a closed cell.**

### Cut B — sourcing the gaps (session B, 2026-09-02)

The 2026-W36 sweep ([`benchmark/miner/results/2026-W36-cutb.csv`](../miner/results/2026-W36-cutb.csv))
is the first post-#403 run: 912 rows over 21 repositories, including every
n8n repository this project had never mined and eight unwalked MCP servers.
It carries three kinds of row the earlier sweeps did not: **closed-unmerged**
PRs (`mine --state closed`, pinned at the fork point and the PR head),
**reverted** merged PRs (`--state reverted`, pinned like any merged PR with the
revert recorded in `notes`), and **named** PRs (`--pr N`) mined outside the
latest-40 window. Every claim below was pinned by that sweep and labeled in
[`2026-W36-cutb.labels.csv`](../miner/results/2026-W36-cutb.labels.csv) from
the PR diff; those labels are one session's cell-targeting labels, not
adjudicated, and are `miner_label` exposure like every other sweep label.

**Real history is sourced through a sweep, then a miner label**, and a
closed-unmerged or reverted PR through the same path with its state read from
GitHub. A `reverted` candidate is landed history whose rejection came
afterwards: its register row names the revert PR and the revert's merge commit,
and the guard maps the `reverted` state to `rejected_or_reverted` alone.

Seventeen of the eighteen gaps this cut owned are claimed. The one left open is
`langchain_crewai.insufficient_evidence.1`, whose lead now records what the
re-mine found. Every `rejected_or_reverted` slot is filled — six of them, from
five different repositories — and `n8n` has its first two real-history
candidates. One claim moved a cell's holdout margin: `google/adk-python#6605`
turned out to be `engine_tests` exposure (it was reduced into a trigger fixture
before this cut read it), so `google_adk × insufficient_evidence` gained a third
slot, `github.com/google/adk-samples#1731`, to keep a holdout-eligible case.

### Cut B — the close-out (2026-09-02)

Two things stood between Cut B and Cut C: the last gap, and three candidates
that had a name but no pins. The
[`2026-W36-closeout`](../miner/results/2026-W36-closeout.csv) sweep — 45 rows,
three repositories — closes both, and **every slot is now `pinned`**.

**The last gap, `langchain_crewai × insufficient_evidence`.** Session B's lead
read: mine an *application* that calls `MultiServerMCPClient` or
`load_mcp_tools` at agent construction, not the adapter library. That is
`bytedance/deer-flow`, a LangGraph research application whose agent tools are
assembled at run time by `langchain-mcp-adapters` from an out-of-tree
extensions config. Its latest 40 merged PRs supplied nothing usable: only one
of the forty reaches a decision at all, for the reason below, and on a
repository this busy the forty span six days. So the claim is the named PR
`#4868`, mined with `--pr`: per-user credential injection for shared
MCP servers, where which credential a tool call carries is chosen at run time
from the caller's identity and a `$ENV_VAR` map. Neither the servers, the
tools, nor the credentials are in the tree.

**`init` refusing a monorepo is not a mining failure, and the miner cannot
tell.** 24 of the 40 rows are `init_skip` because a cold start at the
repository root returns `refused_unresolved_scope`: the workspace holds more
than one self-contained project that defines agents, and one manifest
describes one agent surface. The miner's fallback retries at the deepest common directory of the changed
files, which for any PR touching both `backend/` and `frontend/` is the root
again. Pointed at `backend/packages/harness`, `init --write` writes a manifest
and the scan reaches a decision. **A case rooted at a repository the miner
recorded as `init_skip` is not thereby unevaluable** — nine slots claimed
before this cut are in the same position — but Cut D has to root each of them
at the project the change is in, not at the clone.

**Three walk candidates, pinned.** `github-mcp-server#3020` and `#3076` and
`grafana/mcp-grafana#1080` were carried from adoption walks with abbreviated
or absent SHAs. Mining each by number resolves both ends under the sweep
convention and puts the pins somewhere a guard can re-read them; `#3076` is
the case that shows why re-reading matters (see
[the pinning convention](#the-pinning-convention-is-unsettled-and-the-api-will-not-settle-it)).
All three keep `diff_substance`: the walks are what targeted their cells, and
being swept afterwards adds `benchmark_scored` to their exposure without
changing what aimed them. All three were already `tuning_only` through
`maintainer_walk`, so nothing about the split moved.

## Candidate register

Every sourced candidate, and every gap whose lead names a specific PR. The
`Profile` and `State` columns are checked against the CSV; `State` is the
GitHub merge state as read on 2026-08-31 — 2026-09-02 for the candidates the
[close-out](#cut-b--the-close-out-2026-09-02) added — and it is what decides an
origin.

| Candidate | Profile | State | The change |
|---|---|---|---|
| `github.com/github/github-mcp-server#3020` | `mcp_openapi_declared_binding` | `merged` | Adds `find_duplicate`, a read-only tool gated behind a `duplicate_detection` feature flag. The repository checks in per-tool MCP schemas under `pkg/github/__toolsnaps__/`; the count goes 115 → 116. The flag is invisible in the schema — the filename encodes it, the JSON does not. |
| `github.com/github/github-mcp-server#3076` | `mcp_openapi_declared_binding` | `merged` | Adds a confirmed repository-deletion tool to the same 117-schema surface. Walk notes recorded base `bfb59bb7…` and head `5ea9a0e8…` as abbreviations; the base resolves, but `5ea9a0e8…` is the PR branch head and is unreachable from the default branch, so the pin is the merge commit `8ec62491…` the close-out sweep recorded. |
| `github.com/grafana/mcp-grafana#1080` | `mcp_openapi_declared_binding` | `merged` | Adds `update_incident` to a Go MCP server registering tools as `mcpgrafana.MustTool("…", …)`. The published surface goes 99 → 100. |
| `github.com/stripe/ai#232` | `mcp_openapi_declared_binding` | `merged` | Removes the client-side toolkit's action and permission least-privilege bounds entirely, delegating all tool authority to a server-side key through an async factory. |
| `github.com/openai/openai-agents-python#3392` | `openai_agents_sdk` | `merged` | Japanese documentation translation wording only; no code, tools, scopes or CI touched. |
| `github.com/openai/openai-agents-python#3451` | `openai_agents_sdk` | `merged` | Trace URL and credential sanitization, MCP HTTP redirect default `True` → `False`, and stops auto-propagating tracing keys. |
| `github.com/crewAIInc/crewAI-examples#184` | `langchain_crewai` | `merged` | Refactors the `markdown_validator` example to the standard crewAI `src/` layout; the sole agent tool stays a read-only local scanner. |
| `github.com/crewAIInc/crewAI-examples#169` | `langchain_crewai` | `merged` | Adds flow projects wiring new external write authority: Slack `chat_postMessage`, Trello card creation, and a Gmail draft tool attached to an agent. |
| `github.com/google/adk-samples#1977` | `google_adk` | `merged` | Directory rename only (`travel-panner` → `travel-planner`). |
| `github.com/google/adk-samples#1975` | `google_adk` | `merged` | Adds a travel agent with an `McpToolset` against the Google Maps MCP endpoint, exposing `search_places`, `lookup_weather` and `compute_routes`. |
| `github.com/google/adk-python#6605` | `google_adk` | `closed` | Adds `AgentHooksPlugin`, routing every ADK lifecycle callback (user message, model call, tool call, errors) through external `agent-hooks` interceptors whose deny/transform verdicts are decided at run time; the bundled sample's `delete_account` tool is gated only by such an interceptor. Closed without merge on 2026-08-13. Already reduced into `tests/test_public_surface_contract.py` as a trigger fixture, so it is an engine-development input and its cell carries a third slot. |
| `github.com/google/adk-samples#1731` | `google_adk` | `closed` | Adds a KYC/KYB sample whose only tool is an `MCPToolset` over `StreamableHTTPConnectionParams` to a third-party hosted server (`openregistry.sophymarine.com`, overridable by env) claiming live access to 27 national company registries; the tool list never appears in the tree. Closed without merge on 2026-08-12. |
| `github.com/google/adk-samples#2148` | `google_adk` | `closed` | Re-implements the auto-insurance sample under `core/`: a root `Agent` with `sub_agents=[membership_agent, roadside_agent, claims_agent, rewards_agent]`, each mounting an `ApiHubToolset` that registers members, files claims and dispatches roadside assistance against live Apigee-fronted APIs, with no approval step. Closed without merge on 2026-06-26. |
| `github.com/google/adk-samples#125` | `multi_agent_handoffs` | `merged` | The original auto-insurance sample: `root_agent` with the same four `sub_agents`, each an `Agent` whose tools come from an `ApiHubToolset` (membership registration, claims, roadside dispatch, rewards); financial and operational writes are delegated to sub-agents with no approval policy. Filed under `multi_agent_handoffs` because the authority sits behind the delegation. |
| `github.com/pydantic/pydantic-ai#3248` | `multi_agent_handoffs` | `merged` | A documented agent-delegation example: a `triage_agent` whose two tools call specialist and senior-doctor agents that return structured reports; no external tool, credential or write anywhere in the chain. |
| `github.com/pydantic/pydantic-ai#5120` | `multi_agent_handoffs` | `merged` | Makes the `XSearch` capability model-agnostic: when the main model lacks native X search, a `fallback_model` spins up a subagent on an xAI model to perform the search on the agent's behalf. |
| `github.com/openai/openai-agents-python#2932` | `openai_agents_sdk` | `closed` | An example agent connected over Streamable HTTP to a third-party remote MCP server (`mcp.hashlock.markets`) with a wallet-derived bearer token, exposing `create_rfq`/`respond_rfq` OTC crypto quote tools; only prompt text keeps settlement out of scope. Closed without merge on 2026-04-17. |
| `github.com/openai/openai-agents-python#3833` | `openai_agents_sdk` | `merged` | Adds `ProgrammaticToolCallingTool`: a hosted tool under which the model writes code that calls the agent's function tools, with per-tool caller permissions and a new run-loop execution path. |
| `github.com/openai/openai-agents-python#3788` | `openai_agents_sdk` | `merged` | Experimental hosted multi-agent support: server-hosted subagents run over WebSocket while the local `Runner` executes developer function tools on their behalf, with hosted-agent attribution on tool calls. |
| `github.com/langchain-ai/deepagents#5999` | `langchain_crewai` | `merged` | Reworks the Talon WhatsApp channel of LangChain's `deepagents`: the Node bridge gains local-ID compatibility, outbound delivery reporting changes, and the CI/release workflows add a Node setup step. |
| `github.com/bytedance/deer-flow#4868` | `langchain_crewai` | `merged` | Adds per-user credential injection for shared HTTP/SSE MCP servers to a LangGraph research application whose tools are assembled at run time by `langchain-mcp-adapters`. A `user_auth` block in the out-of-tree extensions config maps user ids to credential header values (`$ENV_VAR` references), and a built-in interceptor rewrites that header on every tool call from the run-time user, ordered after OAuth so the per-user value wins; the server entry's static headers then serve only startup tool discovery. Read the counter-argument with it: the default is fail-closed (`on_missing: deny`), so a rater may weigh the tightening rather than the surface. |
| `github.com/hashicorp/terraform-mcp-server#493` | `mcp_openapi_declared_binding` | `merged` | Moves the official go-sdk server's tools into `pkg/mcp-official/tools` and adds `list_terraform_orgs` there: read-only, but registered through a second runtime-assembled registry (`tools.RegisterTools` gated per toolset flag) that runs in parallel with the mark3labs one. |
| `github.com/hashicorp/terraform-mcp-server#461` | `mcp_openapi_declared_binding` | `merged` | Adds `grant_team_access`, a `ReadOnlyHint: false` tool granting a team read/plan/write/admin access to a workspace or project through the TFE API. |
| `github.com/cloudflare/mcp-server-cloudflare#433` | `mcp_openapi_declared_binding` | `merged` | Renames the stack-mcp search tool `search_docs` → `search_dev_stack` and rewrites both read-only tool descriptions; the surface stays two read-only documentation tools. |
| `github.com/elastic/mcp-server-elasticsearch#57` | `mcp_openapi_declared_binding` | `closed` | Adds `execute_es_api`, a tool forwarding an arbitrary method, path and body to any Elasticsearch API endpoint, plus ML job creation tools, on a previously read-only search server. Closed without merge on 2025-05-08. |
| `github.com/enescingoz/awesome-n8n-templates#134` | `n8n` | `merged` | Adds one exported workflow: a manual trigger, an HTTP Request POST to a local Ollama endpoint with a fixed prompt, and a Set node. No credentials, no external target. |
| `github.com/Zie619/n8n-workflows#87` | `n8n` | `merged` | Adds one exported workflow: a public Telegram trigger feeding an OpenAI chat node whose reply is sent back through a Telegram send node with a stored credential. |
| `github.com/aaif-goose/goose#9637` | `coding_agent_trust_roots` | `merged` | Developer eval tooling only: rewrites a `SKILL.md` doc and adds two analysis recipes that mount only the builtin developer extension. |
| `github.com/aaif-goose/goose#9684` | `coding_agent_trust_roots` | `merged` | Automated release chore: version bumps plus a regenerated model and provider catalog. |
| `github.com/aaif-goose/goose#9736` | `coding_agent_trust_roots` | `merged` | Adds `~/.agents/AGENTS.md` as a second global hints source loaded on every session; the instruction trust root now includes a user-home file nothing in the repository can enumerate. |
| `github.com/pydantic/pydantic-ai#4199` | `coding_agent_trust_roots` | `reverted` | Rewrites the `@claude` CI workflow: read permissions become `contents`/`pull-requests`/`issues` write, the pinned `allowed_tools` list is dropped, and a new `review.yml` plus `AGENTS.md`/`CLAUDE.md` rule files drive automatic AI review. Reverted by pydantic/pydantic-ai#4202 (revert merge `f74a093ae387b3bc92970c65eb6eef81e4be2b29`). |
| `github.com/stripe/ai#312` | `coding_agent_trust_roots` | `merged` | Rewires the agent-skill supply chain: the sync source moves from authenticated `mcp.stripe.com` to an unauthenticated fetch of `docs.stripe.com/.well-known/skills`, a daily cron is enabled, and the remote manifest dictates which files the sync writes. |
| `github.com/stripe/ai#338` | `coding_agent_trust_roots` | `merged` | Adds a skill instructing coding agents to install the Stripe CLI and run `stripe projects init`, which creates further local skills the agent is told to prefer. |
| `samples/clean_read_only_agent` | `mcp_openapi_declared_binding` | `in_tree` | Read-only tool surface with no risky actions, by design. |
| `samples/hitl_evidence_agent` | `mcp_openapi_declared_binding` | `in_tree` | Authority-bearing refund surface expecting human-in-the-loop evidence. |
| `samples/support_refund_agent` | `openai_agents_sdk` | `in_tree` | Refund tool missing both an approval policy and idempotency evidence. |
| `samples/simple_langchain_agent` | `langchain_crewai` | `in_tree` | Read-only LangChain agent, the benign baseline for this profile. |
| `samples/simple_crewai_agent` | `langchain_crewai` | `in_tree` | CrewAI agent whose file-read tool is broader than the task it serves. |
| `samples/google_adk_agent` | `google_adk` | `in_tree` | ADK agent whose function tools are all declared in a reviewed inventory. |
| `samples/google_adk_cold_start_agent` | `google_adk` | `in_tree` | ADK agent with no declarations at all. |
| `samples/n8n_workflow_agent` | `n8n` | `in_tree` | Workflow whose nodes are read-only and fully enumerable from the exported JSON. |
| `samples/multi_agent_workspace` | `multi_agent_handoffs` | `in_tree` | Two workspaces, each with its own manifest and no cross-workspace authority. |
| `samples/conductor_agent` | `multi_agent_handoffs` | `in_tree` | Conductor workflow whose task tool surface cannot be statically enumerated. |
| `samples/declaration_repair_agent` | `coding_agent_trust_roots` | `in_tree` | Manifest whose declarations do not cover the tool surface they claim. |
| `samples/agent_weakens_gate` | `coding_agent_trust_roots` | `in_tree` | The coding agent deletes the Shipgate CI gate so its own PR can self-merge. |
| `benchmark/safety-qualification/constructed/mcp_export_adds_undeclared_tool` | `mcp_openapi_declared_binding` | `in_tree` | Adds a fourth tool, zendesk.add_ticket_comment, to the committed MCP export while the manifest's complete root declaration still lists three. |
| `benchmark/safety-qualification/constructed/sdk_agent_adds_ticket_update_tool` | `openai_agents_sdk` | `in_tree` | Adds a zendesk.update_ticket function tool that PUTs to the Zendesk API, declared as a scoped write with no approval policy. |
| `benchmark/safety-qualification/constructed/sdk_agent_loads_tools_from_registry` | `openai_agents_sdk` | `in_tree` | Replaces the agent's literal tools list with tools=load_tools("triage"), a registry that imports names from a TOML profile at start-up. |
| `benchmark/safety-qualification/constructed/crewai_tools_from_factory` | `langchain_crewai` | `in_tree` | Builds the crew's tool list with build_tools(profile), a factory over a YAML profile, in place of the literal list. |
| `benchmark/safety-qualification/constructed/langchain_agent_adds_refund_tool` | `langchain_crewai` | `in_tree` | Adds an issue_refund @tool calling stripe.Refund.create, declared as a financial write with no approval policy and no idempotency safeguard. |
| `benchmark/safety-qualification/constructed/adk_agent_adds_calendar_toolset` | `google_adk` | `in_tree` | Adds a second McpToolset against a calendar MCP export filtered to calendar.create_event, declared as a scoped write with no approval policy. |
| `benchmark/safety-qualification/constructed/adk_billing_sub_agent_refund` | `google_adk` | `in_tree` | Adds a billing_agent sub-agent whose only tool calls stripe.Refund.create, declared as a financial write with no approval policy or idempotency safeguard. |
| `benchmark/safety-qualification/constructed/n8n_agent_adds_order_note_post` | `n8n` | `in_tree` | Attaches an HTTP Request Tool that POSTs an order note with a header-auth credential, declared as a scoped write with no approval step. |
| `benchmark/safety-qualification/constructed/n8n_code_tool_runtime_endpoint` | `n8n` | `in_tree` | Attaches a Code Tool whose JavaScript POSTs to a callback URL taken from a $fromAI() argument at run time. |
| `benchmark/safety-qualification/constructed/n8n_workflow_tool_expression_target` | `n8n` | `in_tree` | Attaches a Call Workflow Tool whose workflowId is an expression on the request body. |
| `benchmark/safety-qualification/constructed/n8n_webhook_delete_customer` | `n8n` | `in_tree` | Attaches a DELETE HTTP Request Tool to the agent behind an unauthenticated public webhook, declared destructive with no approval, confirmation or rollback. |
| `benchmark/safety-qualification/constructed/n8n_refund_approval_node_disabled` | `n8n` | `in_tree` | Sets disabled: true on the send-and-wait approval node between the agent's refund summary and the Stripe refund call; nothing else changes. |
| `benchmark/safety-qualification/constructed/handoff_billing_agent_account_note` | `multi_agent_handoffs` | `in_tree` | Gives the billing sub-agent an accounts.add_note write tool the root agent's own tools list does not carry, declared as a scoped write with no approval. |
| `benchmark/safety-qualification/constructed/handoff_specialists_from_routes` | `multi_agent_handoffs` | `in_tree` | Builds the root agent's handoffs from an environment-driven route list resolved by import at start-up. |
| `benchmark/safety-qualification/constructed/handoff_approvals_agent_decides_refunds` | `multi_agent_handoffs` | `in_tree` | Adds an approvals sub-agent, reachable by handoff from the root, whose tool approves the refund requests the root submits. |

`coding_agent_trust_roots` and `multi_agent_handoffs` are **scenario** profiles:
what puts a candidate in them is what the change does, not what source type it
declares. `samples/agent_weakens_gate` and `samples/declaration_repair_agent`
both declare `type: mcp` and belong to neither MCP cell. For the other five
profiles the sample's declared source type is checked against the profile; for
these two the register entry above is the justification, and there is nothing
mechanical to check it against.

### Reserve

Identified candidates not allocated to a slot. They exist so a relabel that
empties a cell does not require fresh mining.

Four former reserve candidates — `mongodb-js/mongodb-mcp-server#1417`,
`awslabs/mcp#4489`, `stripe/ai#353` and `openai/openai-agents-python#3518` —
are now the real-history calibration cases in
[`calibration.md`](calibration.md). A calibration case is chosen by someone who
has read this file, and the calibration round labels it before the guide is
final, so none of the four can ever become a corpus case; they are struck from
the reserve rather than held.

| Candidate | Origin | State | Note |
|---|---|---|---|
| `github.com/google/adk-samples#1745` | — | `open` | `SmartCloserAgent`: a root `LlmAgent` with `sub_agents=[salesforce_agent, sap_agent]` over a Salesforce/SAP quote-to-cash flow, with three financial writes reachable only through the sub-agents. **Still open**, so it is not history and cannot fill a slot; it is also `engine_tests` exposure through `test_declaration_questionnaire.py` and `test_detect.py`. Kept as the shape to match when mining `multi_agent_handoffs × blocked`. |
| `github.com/openai/openai-agents-python#3461` | `real_history` | `merged` | `safe_to_merge`: opt-in recovery for a missing function tool. |
| `github.com/aaif-goose/goose#9717`, `#9798` | `real_history` | `merged` | `safe_to_merge`: ACP search session in the desktop client; opt-in ACP last-message snippets. |
| `github.com/stripe/ai#332`, `#336`, `#400` | `real_history` | `merged` | `safe_to_merge` automated skill syncs. Three instances of one shape; prefer variety over volume. |
| `benchmark/safety-qualification/constructed/n8n_agent_adds_shipment_lookup` | `synthetic` | `in_tree` | `n8n` × `passed` alternate: attaches a second read-only, credential-free `GET` HTTP Request Tool with an inventory entry and a read/`none` action row. |
| `benchmark/safety-qualification/constructed/n8n_agent_adds_crm_mcp_client` | `synthetic` | `in_tree` | `n8n` × `review_required` alternate: attaches an MCP Client Tool with an explicit one-tool allowlist (`crm.update_contact`), declared as a scoped write with no approval step. |
| `benchmark/safety-qualification/constructed/handoff_billing_agent_receipt_lookup` | `synthetic` | `in_tree` | `multi_agent_handoffs` × `passed` alternate: gives the billing sub-agent a scoped read-only `orders.lookup_receipt` tool, declared and inventoried. |
| `github.com/hashicorp/terraform-mcp-server#469` | `real_history` | `merged` | `must_block`: adds `delete_team` (`DestructiveHint: true`), registered only when `ENABLE_TF_OPERATIONS` is set. The only merged destructive MCP tool the W36 sweep found; `mcp_openapi_declared_binding × blocked` has no real-history slot planned, so it waits here. |
| `github.com/hashicorp/terraform-mcp-server#451`, `#466` | `real_history` | `merged` | `safe_to_merge`: `get_team` and `whoami`, read-only literal `mcp.NewTool` registrations. `mcp_openapi_declared_binding × passed` alternates. |
| `github.com/hashicorp/terraform-mcp-server#422` | `rejected_or_reverted` | `closed` | `must_block`: the first `delete_team` submission, superseded by #469. |
| `github.com/elastic/mcp-server-elasticsearch#69` | `rejected_or_reverted` | `closed` | `must_block`: `document_manage`, one tool doing create/read/update/delete on documents. `mcp_openapi_declared_binding × blocked` alternate. |
| `github.com/cloudflare/mcp-server-cloudflare#414` | `real_history` | `merged` | `safe_to_merge`: a new Cloudflare Blog MCP server with four read-only tools. |
| `github.com/enescingoz/awesome-n8n-templates#161` | `real_history` | `merged` | `needs_human`: scheduled CoinPaprika poll sending a Telegram alert on a 5% move. `n8n × review_required` alternate. |
| `github.com/openai/openai-agents-python#4399` | `rejected_or_reverted` | `closed` | `needs_human`: `OsEnvValue` lets a sandbox manifest pull any named host environment variable into the sandbox. |
| `github.com/google/adk-samples#348` | `real_history` | `merged` | `needs_human`: the antom-payment sample, a single `LlmAgent` mounting an `MCPToolset` that creates payment sessions, cancels payments and creates refunds with merchant keys from `.env`. A `google_adk` financial-write shape with no real-history `blocked` slot to go to. |
| `github.com/modelcontextprotocol/servers#4739` | `real_history` | `merged` | `needs_human`: switches `readme-pr-check` to `pull_request_target` so fork PRs run with a write token. The W36 verify receipt on it is `blocked` — the sweep's only hard block on real history. `coding_agent_trust_roots` alternate. |
| `github.com/aaif-goose/goose#10825` | `real_history` | `merged` | `needs_human`: a fork-review boundary on the recipe security scanner; a CI trust-root change that tightens rather than weakens. |
| `github.com/bytedance/deer-flow#5010` | `real_history` | `merged` | `needs_human`: maps request-scoped secrets onto MCP HTTP/SSE headers in the same run-time-assembled toolkit as `#4868`. The `langchain_crewai × insufficient_evidence` alternate, and the closer of the two to a widening. |
| `github.com/langchain-ai/langchain-mcp-adapters#540` | `real_history` | `merged` | `safe_to_merge`: surfaces MCP tool execution errors as failed tool output in the adapter that builds LangChain tools from a server's tool list at run time. It was the nearest shape the W36 re-mine found for `langchain_crewai × insufficient_evidence` and it changes no authority — which is why the close-out went to an application repository instead. |
| `github.com/openai/openai-agents-python#3194`, `github.com/google/adk-samples#1665` | `rejected_or_reverted` | `reverted` | The only other reverts the W36 enumeration found in the target repositories: a kwargs guard fix and a region/lockfile pivot. Rejected history, but neither labels above `safe_to_merge`. |
| `samples/mcp_only_server`, `samples/openapi_only_agent`, `samples/hitl_evidence_covered_agent`, `samples/openai_agents_sdk_agent`, `samples/ai_generated_refund_pr`, `samples/simple_openai_api_agent`, `samples/large_multi_framework_agent`, `samples/baseline_workflow`, `samples/simple_anthropic_agent` | `synthetic` | `in_tree` | `tuning_only` if used — every one of them is engine-tuning material. |

## Maintaining it

`tests/test_strata_inventory.py` derives the 21 cells and the holdout floor from
`pre_release_safety_requirements()` rather than restating them, so moving the
policy fails the inventory instead of leaving it silently aimed at the wrong
shape — the failure mode
[`docs/release-evidence-policy-decision.md`](../../docs/release-evidence-policy-decision.md#where-the-bar-is-defined)
warns about for this directory. It also re-reads every cited miner label, every
pinned SHA, every declared exposure, and every candidate's profile and merge
state from the source that records it, so the inventory cannot drift from what
it cites.

Order of work: Cut B mined the gaps and the close-out pinned the last of them,
**both done**; next the calibration round runs on five non-corpus cases, then
labels, freeze, receipts, and the non-gating
[participant-validation gate](participant-validation.md).

**Nothing here needs mining before Cut C.** What this file cannot settle is
the part Cut C owns: the `review_required` / `insufficient_evidence` line the
miner's `needs_human` does not draw, and which of the two rater families takes
which role. And what Cut D inherits from the close-out is that a receipt is
rooted at a project, not at a clone — several candidates sit in repositories
where a cold start at the root correctly refuses to write one manifest for
several agent surfaces.
