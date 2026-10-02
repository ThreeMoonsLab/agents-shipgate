# Application review benchmark (Q1/Q2)

How many real pull requests that change an application agent's tools get a
correct, complete and useful answer from `diff --application`? This directory
makes that a number someone else can reproduce, on two corpora:

- **Development**: the 49 pull requests in [`development.json`](development.json).
  Reader issues are worked against these pull requests, so a count on them
  measures what was fixed. It does not predict how the next stranger's pull
  request will fare.
- **Holdout**: at least 30 pull requests the [rule below](#holdout-selection-rule)
  selects from a later window. The rule, and the repository pool it draws
  from, were committed before any of the reader changes it is meant to judge
  merged; the pins follow once the window holds enough pull requests. Members
  are scored only when a release is cut, and never used to debug a reader.

Both are development evidence for [#868](https://github.com/ThreeMoonsLab/agents-shipgate/issues/868),
not a success rate, a market estimate or a qualification claim. Neither may be
cited as one. The measurement contract is
[#908](https://github.com/ThreeMoonsLab/agents-shipgate/issues/908).

## What a run is

For each pull request: a full-object clone of its repository, the merge base
and head pinned in the corpus file, and

```bash
agents-shipgate diff --application --base <merge_base> --head <head> --json
```

run in the clone's root with no `--scope`, so the scope is derived from the
change, as it is for a new user. Nothing is fetched while the diff runs.
[`run.py`](run.py) does exactly this: with `--fetch` it clones and fetches the
pins, and it records every member's outcome (`ok`, `refused`, `timeout` or
`unavailable`, with the reason) in `runs.json`. [`summarize.py`](summarize.py)
counts statuses and rows from a run directory. The counts are mechanical. The
Q1/Q2 scores below are not.

```bash
python benchmark/application-q2/run.py --engine <launcher or console script> \
  --corpus benchmark/application-q2/development.json \
  --clones ~/.cache/shipgate-q2/repos --out ~/.cache/shipgate-q2/out-<build> --fetch
python benchmark/application-q2/summarize.py --corpus benchmark/application-q2/development.json \
  --out ~/.cache/shipgate-q2/out-<build> --scores benchmark/application-q2/results/<ledger>.scores.json
```

Each invocation first removes the corpus members' previous outputs and the
previous `runs.json`, before it prepares any clone. A member that times out, is
unavailable or is refused this time therefore has no answer, never an earlier
build's, and a run that stops early leaves no `runs.json`, which `summarize.py`
refuses to read. The engine's stderr tail is kept in `<slug>.err` when it ran;
every member's outcome and reason is in `runs.json`. The runner exits non-zero
unless every member answered `ok`.

## Scoring protocol

Each pull request is scored by hand against its source at the pinned refs.
The levels are [#868](https://github.com/ThreeMoonsLab/agents-shipgate/issues/868)'s,
verbatim:

- **Q0 (relevant):** the PR changes an SDK/ADK agent's tools, handoffs or sub-agents in application code.
- **Q1 (correct and complete):** every binding change on the affected agents appears, with no false rows, and `compared` is never reported while a binding is unobservable.
- **Q2 (useful):** Q1, the PR modifies an agent that exists at the base, and each changed row names what the tool reaches (#872) or names the unresolved hop.

**Any material omission or false row disqualifies the case.**

The terms in these levels are read as [Definitions](#definitions) states them.

**Disagreement.** Two scorers who disagree on a level write down the source
lines each relies on. The lower score stands until the disagreement is
resolved against those lines, and the ledger records the case as disputed,
with both readings. A level is never raised by majority, by the tool's own
output, or by what the pull request's description says it does.

**Worked examples** (2026-09-30 ledger):

- **Q2:** [jpka/attest#3](https://github.com/jpka/attest/pull/3) adds
  `remember_firm_finding` and `recall_firm_memory` to `attest_orchestrator`,
  an agent that exists at the base. The comparison is `compared` with exactly
  those two `added` rows. Each row names where reading stopped:
  `remember_firm_finding` at `MemoryBank.from_env` (`memory_bank.py:485`) and
  `bank.generate_memories` (`:493`); `recall_firm_memory` at
  `MemoryBank.from_env` (`:531`) and `bank.retrieve` (`:545`).
- **Not Q1:** [Tiendat2703/MIS_TALENT#7](https://github.com/Tiendat2703/MIS_TALENT/pull/7)
  is `partial`. Its two rows on `Finance_Agent_Preflight` are right:
  `load_and_validate` `changed` and `load_service_catalog` `added`. But
  `load_and_validate` is also bound to `Finance_Agent` through
  `tools=[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]`,
  which the reader does not resolve, so that agent's `changed` row is
  missing. One material omission, so it is not Q1, however useful the two
  rows are.

## Definitions

Two terms are read the way the engine defines them, so a scorer and the engine
answer the same question. They are frozen with the holdout rule below.

- **A binding change** is a tool, handoff or sub-agent added to or removed from
  an agent, or a change to the definition of a tool an agent binds: the
  function's own code, which is what a row's implementation digest covers. A
  change only to code a tool calls is not one; a row's `reach` shows that
  code, and it does not make a row on its own.
- **A row "appears"** when it states the change: `added`, `removed` or
  `changed`. A `not_established` row names a candidate the engine could not
  establish. It is never a false row, but it does not count as the change
  appearing.

Q0's "changes an SDK/ADK agent's tools, handoffs or sub-agents" is a binding
change, so a relevant pull request whose answer has no `added`, `removed` or
`changed` row fails Q1.

## Holdout selection rule

This rule, the definitions above and [`pool.json`](pool.json) were committed
before any of the reader issues they judge merged:
[#874](https://github.com/ThreeMoonsLab/agents-shipgate/issues/874),
[#909](https://github.com/ThreeMoonsLab/agents-shipgate/issues/909),
[#910](https://github.com/ThreeMoonsLab/agents-shipgate/issues/910),
[#911](https://github.com/ThreeMoonsLab/agents-shipgate/issues/911),
[#912](https://github.com/ThreeMoonsLab/agents-shipgate/issues/912) and
[#913](https://github.com/ThreeMoonsLab/agents-shipgate/issues/913).
The window opened the day they were committed, so it could not yet hold 30
qualifying pull requests. The owner chose (2026-10-01) to freeze the rule
first rather than hold the reader work for weeks. Membership is then decided
by the rule and a pool recorded in advance, not by anyone who has seen how
the new readers behave, which is the selection bias a holdout exists to
remove. The pins are generated from the rule once the window holds enough
pull requests, and committed in [`holdout.json`](holdout.json) together with
every candidate walked and its reason.

1. **Window.** Pull requests created on or after `2026-10-02T00:00:00Z`.
2. **Pool.** The repositories in [`pool.json`](pool.json): what GitHub code
   search returned on its `generated_at` date for the queries and file-size
   shards it records, at most 1,000 results each. It is a recorded sample,
   never regenerated. Excluded: the repositories of the development corpus,
   whose constructions readers were tuned against, and every repository whose
   merge base holds the OpenAI Agents SDK's own package
   (`src/agents/function_schema.py`) or Google ADK's
   (`src/google/adk/runners.py`): the frameworks' repositories, their forks
   and their copies.
3. **Candidates.** Every pull request created in the window in a pool
   repository that is merged or still open, not closed unmerged, and whose
   patch edits a non-test `.py` file. Ordered by `createdAt` ascending, ties
   by URL.
4. **Relevance (Q0), judged from source, never from the engine.** Walking
   that order, a candidate is a member when, read at its merge base and head
   (its patch, the files it touches, and the modules those import or are
   imported by), it makes a binding change, as defined above, to an OpenAI
   Agents SDK or Google ADK agent that exists at the merge base, in
   application code. The list may be built anywhere: in the
   construction, a list variable, a builder's argument or an agent's copy.
   A tool's own definition counts too. The framework is read off the agent's
   construction, not off the repository. Code that implements an agent
   framework or library is not application code; test code is never
   application code. Each judgement is made before `diff --application` runs
   on the candidate, and recorded with its reason, member or not.
5. **Stop** at the first 30 members. A member whose clone or pins cannot be
   obtained stays listed, with `merge_base` and `head` set to `null` and the
   reason in `unavailable_reason`, and is scored not Q1.
6. **Pins.** The merge base of the PR's base and head commits, and the head
   commit as of the day the pins are generated. Both are full 40-character
   SHAs.

Nothing in the definitions or in steps 1–6 may change without starting a new
holdout from a later window. [`holdout.json`](holdout.json) records the sha256
of the text from `## Definitions` up to `## Every release`, and of
[`pool.json`](pool.json); the benchmark's tests hold each to its digest.

## Every release

[`docs/release-runbook.md`](../../docs/release-runbook.md) runs both corpora
with the release build, hand-scores every member whose answer changed since
the previous ledger (a score names the `answer_id` it judged: the digest of
the answer without the engine's own version, platform and build, so
`summarize.py` lists a score whose answer moved as stale and does not count
it), commits a new
ledger under [`results/`](results/), and records
`Q2: n/49 development, m/≥30 holdout` with the change since the previous
release in `docs/changelog/<version>.md`. While the holdout has no pins, that
line says so instead of a number.

## Files

| File | What it is |
| --- | --- |
| [`development.json`](development.json) | The 49 development pull requests: repository, number, URL, title, state, created date, merge base, head, the `frameworks` the engine read and the search that admitted them. |
| [`pool.json`](pool.json) | The holdout's recorded repository pool, with every code-search request that produced it. |
| [`holdout.json`](holdout.json) | The holdout's record: the digests of the frozen definitions and rule and of the pool and, once generated, its members and every skipped candidate with the reason. |
| [`pool.py`](pool.py) | How `pool.json` was recorded. Re-running it does not reproduce it. |
| [`run.py`](run.py) | Clone, fetch and run one engine over a corpus. |
| [`summarize.py`](summarize.py) | Count a run's statuses and rows, and join the hand scores that judged its answers. |
| [`results/`](results/) | One ledger per measured build: `<date>-<build>.md` with the counts and a row per member, and `<date>-<build>.scores.json` with each member's levels, rationale and the `answer_id` they judged. |
