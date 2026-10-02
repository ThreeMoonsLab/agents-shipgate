# Application review benchmark (Q1/Q2)

How many real pull requests that change an application agent's tools get a
correct, complete and useful answer from `diff --application`? This directory
makes that a number someone else can reproduce, on two corpora:

- **Development** — the 49 pull requests in [`development.json`](development.json).
  Reader issues are worked against these pull requests, so a count on them
  measures what was fixed. It does not predict how the next stranger's pull
  request will fare.
- **Holdout** — at least 30 pull requests chosen by the
  [rule below](#holdout-selection-rule), frozen in this directory before any
  of the reader changes it is meant to judge merged. Its members are scored
  only when a release is cut, and never used to debug a reader.

Both are development evidence for [#868](https://github.com/ThreeMoonsLab/agents-shipgate/issues/868),
not a success rate, a market estimate or a qualification claim. Neither may be
cited as one. The measurement contract is
[#908](https://github.com/ThreeMoonsLab/agents-shipgate/issues/908).

## What a run is

For each pull request: a full-object clone of the repository (no
`--filter`), the merge base and head pinned in the corpus file, and

```bash
agents-shipgate diff --application --base <merge_base> --head <head> --json
```

run in the clone's root with no `--scope`, so the scope is derived from the
change, as it is for a new user. Nothing is fetched while the diff runs.
[`run.py`](run.py) does exactly this and writes one JSON document per pull
request; [`summarize.py`](summarize.py) counts statuses and rows from those
documents. The counts are mechanical. The Q1/Q2 scores below are not.

## Scoring protocol

Each pull request is scored by hand against its source at the pinned refs.
The levels are [#868](https://github.com/ThreeMoonsLab/agents-shipgate/issues/868)'s,
verbatim:

- **Q0 (relevant):** the PR changes an SDK/ADK agent's tools, handoffs or sub-agents in application code.
- **Q1 (correct and complete):** every binding change on the affected agents appears, with no false rows, and `compared` is never reported while a binding is unobservable.
- **Q2 (useful):** Q1, the PR modifies an agent that exists at the base, and each changed row names what the tool reaches (#872) or names the unresolved hop.

**Any material omission or false row disqualifies the case.** A row the
source does not support, a binding change the source makes that no row shows,
or `compared` while a changed binding was unobservable, each fails Q1, and
therefore Q2, whatever else the output gets right. A pull request with no
row fails Q1: every corpus member changes a binding by construction.

**Disagreement.** Two scorers who disagree on a level write down the source
lines each relies on. The lower score stands until the disagreement is
resolved against those lines, and the ledger records the case as disputed,
with both readings. A level is never raised by majority, by the tool's own
output, or by what the pull request's description says it does.

**Worked examples** (2026-09-30 ledger):

- **Q2:** [jpka/attest#3](https://github.com/jpka/attest/pull/3) adds
  `remember_firm_finding` and `recall_firm_memory` to `attest_orchestrator`,
  an agent that exists at the base. The comparison is `compared` with exactly
  those two `added` rows, and each row names the hop it could not follow
  (`MemoryBank.from_env`, `bank.retrieve` / `bank.generate_memories` beyond
  `memory_bank.py:531`). Every binding change appears, none is false, and each
  row says what it reaches or where reading stopped.
- **Not Q1:** [Tiendat2703/MIS_TALENT#7](https://github.com/Tiendat2703/MIS_TALENT/pull/7)
  is `partial`. Its two rows on `Finance_Agent_Preflight` are right: `load_and_validate`
  `changed` and `load_service_catalog` `added`. But `load_and_validate` is also bound to
  `Finance_Agent` through `tools=[*FINANCE_TOOLS, *([prepare_finance_handoff] if want_handoff_tool else [])]`,
  which the reader does not resolve, so that agent's changed row is missing.
  One material omission, so it is not Q1, however useful the two rows are.

## Holdout selection rule

This rule was committed before any of the reader issues it judges —
[#874](https://github.com/ThreeMoonsLab/agents-shipgate/issues/874),
[#909](https://github.com/ThreeMoonsLab/agents-shipgate/issues/909),
[#910](https://github.com/ThreeMoonsLab/agents-shipgate/issues/910),
[#911](https://github.com/ThreeMoonsLab/agents-shipgate/issues/911),
[#912](https://github.com/ThreeMoonsLab/agents-shipgate/issues/912) and
[#913](https://github.com/ThreeMoonsLab/agents-shipgate/issues/913) — merged.
On that date no pull request in its window existed yet, so its pins could not
be. The owner chose (2026-10-01) to freeze the **rule** first rather than
hold the reader work for weeks: membership is then decided by the rule, not by
anyone who has seen how the new readers behave, which is the selection bias a
holdout exists to remove. The pins are generated from the rule once the window
holds enough pull requests, and committed in
[`holdout.json`](holdout.json) together with every skipped candidate and its
reason.

1. **Window.** Pull requests created on or after `2026-10-02T00:00:00Z`.
2. **Pool.** The public, non-fork, non-archived repositories that GitHub code
   search returns, on the day the pins are generated, for
   `"from agents import" language:Python`, `"import agents" language:Python`,
   `"from google.adk" language:Python` and `"import google.adk" language:Python`.
   The queries, that date and the resulting repository list are recorded in
   `holdout.json`, so the pool is whatever the index held, never a choice.
   Repositories in the development corpus are excluded: readers were tuned
   against their constructions.
3. **Candidates.** Every pull request in the window, in a pool repository,
   that is merged or open (not closed unmerged), whose patch edits a non-test
   `.py` file. Ordered by `createdAt` ascending, ties by URL.
4. **Relevance (Q0), judged from the diff alone.** Walking that order, a
   candidate is a member when its patch edits the `tools=`, `sub_agents=` or
   `handoffs=` argument of an SDK `Agent(...)` or ADK `Agent(...)` /
   `LlmAgent(...)` construction in non-test code, and that agent's
   construction exists at the merge base — the development corpus's criteria,
   with the framework read off the construction itself rather than off the
   repository: eight development members turned out to be LiveKit, ZeroRuntime,
   rustic-ai or hand-written agents (see the [2026-09-30 ledger](results/2026-09-30-6ced6f70.md)).
   The judgement is made before `diff --application` runs on the candidate,
   and recorded with its reason, member or not.
5. **Stop** at the first 30 members. A member whose clone or merge base
   cannot be obtained stays listed, scored as not Q1, with the reason.
6. **Pins.** Merge base of the PR's base and head commits, and the head commit
   as of the day the pins are generated. Both are full 40-character SHAs.

Nothing in steps 1–6 may be changed after this commit without starting a new
holdout from a later window.

## Every release

[`docs/release-runbook.md`](../../docs/release-runbook.md) § Cutting the
release runs both corpora with the release build, hand-scores the rows that
changed since the previous ledger, commits a new ledger under
[`results/`](results/), and records `Q2: n/49 development, m/≥30 holdout` with
the change since the previous release in `docs/changelog/<version>.md`. While
the holdout pins do not exist yet, that line says so instead of a number.

## Files

| File | What it is |
| --- | --- |
| [`development.json`](development.json) | The 49 development pull requests: repository, number, URL, state, created date, merge base, head, framework and the criteria they met. |
| [`holdout.json`](holdout.json) | The holdout rule's pins once generated; until then, the rule's commit and an empty member list. |
| [`run.py`](run.py) | Clone, hydrate and run one engine over a corpus. |
| [`summarize.py`](summarize.py) | Count statuses and rows from a run's outputs. |
| [`results/`](results/) | One ledger per measured build: counts, and per pull request its status, row counts, the Q levels and a one-line rationale. |
