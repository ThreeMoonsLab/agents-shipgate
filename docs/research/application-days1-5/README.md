# Application PR review: Days 1–5 execution record

Date: 2026-09-24. Program: #778 / #868. This is engineering evidence, not
external user validation or proof of ten useful PR reviews.

## Decision and ownership

The owner requested execution of Days 1–5 of the September 23 issue review.
Select application-agent comparison as the primary proof sprint; keep host
correctness in a bounded maintenance lane. Accountable product/acceptance owner:
Pengfei Hu (`pengfei-threemoonslab`); technical execution: Codex in this task.
No additional engineer staffing is assumed. Maintainer acceptance remains a
human decision. #830 and its outreach hold remain unchanged.

| Issue | Days 1–5 disposition | Next acceptance / dependency |
| --- | --- | --- |
| #778 | Record this selected sequence and supersede earlier recruitment-first scheduling | Preserve Oct 14 / Nov 13 / Dec 13 evidence checkpoints; do not promise releases |
| #868 | Rebaseline completed below; epic remains open | Ten individually source-checked, complete, useful application PR comparisons |
| #580 | Comparison input design recorded in `comparison-design.md` | Implement independently bound scopes and truthful absence |
| #655 | Advisory synthesis design reconciled with #580 | Implement automatic paired extraction; never count synthesized input as reviewed base |
| #867 | Selected after comparison input support | Project existing direct wiring per agent without guessing deployment root |
| #864 / #865 / #866 | Selected subsequent reader work | Local imports, bounded factories, exact ADK built-in identity; keep unresolved paths visible |
| #610 | Reproduced on release and main; remains open | Planning-only semantics, compatible schemas/projections and positive/negative consumer tests |
| #787 | Selected bounded reliability implementation | Safe-path installation in non-GitHub recipes and regression checks |
| #795 | Keep open for acceptance reconciliation | Delivered field presentation does not prove all same-version CLI/JSON/PR and reviewer criteria |
| #812 | Keep open for acceptance reconciliation | Delivered coverage slices do not prove unaided reviewer interpretation; #821 is closed, do not rebuild it |
| #780 | Keep open for exactly the hosted residuals | No-change update to existing comment; Not compared; hosted setup/execution failure |
| #369 | Deferred unless a concrete operational consumer needs argv | Existing POSIX command recovery is tested; setup argv does not satisfy this issue |

#795/#812 need a criterion-by-criterion same-version evidence join before closure,
not another broad renderer rewrite. #780's ten published 1.1.0 hosted runs remain
valid; #853 merged, removing its source-pin caveat. #854–#859 retain their own
observed defects. Hosted runs do not demonstrate external adoption (#571).
This phase selects and reconciles those residuals; it does not claim they shipped.

## Engines and method

- Release: PyPI `agents-shipgate==1.1.0`, contract 40, Python 3.13.11.
  Wheel SHA-256:
  `038bdb4650d45d9c81996f60d33781b5671bfb57f006233f80e74db8a7377d33`.
  Dependencies are recorded in `release-requirements.txt`.
- Source: main `d9a6d0ea5cca51b5b83eaf7743d8f1086bed1858`, version 1.1.0,
  contract 41. Same release interpreter/dependencies for the paired scans.
- `replay.csv`: 27 pinned PRs × two engines = 54 head scans. Reused identical
  provisional local-review config bytes on both engines (hash per row). These
  configs contain unresolved setup declarations; they are not reviewed policy.
  Clean exact-head trees were required; a dirty cached AI4ES tree was replaced
  by a clean clone. No application code or tools were executed.
- Per engine: 23/27 empty inventories; three inventories of two tools and one
  of three tools. 26/27 `insufficient_evidence`; one `review_required` with an
  empty inventory. These statuses and counts are not useful PR comparisons.
- The 27 cases are a selected historical corpus. The previous 310 count is
  screening only, not local runs. No population success rate follows.
- `fresh-adoption.json`: separate fresh init and exact-ref verify for four
  representative PRs, on both engines (eight init + eight verify operations).
  Base/head are pinned; the actual merge base equals the supplied base in all
  four cases. No manually supplied base report or semantic declaration.

| PR | Fresh verify on both engines | Source/binding limitation retained |
| --- | --- | --- |
| [attest#3](https://github.com/jpka/attest/pull/3) | `missing_manifest`, zero top changes | Three old local tools; new memory tools unresolved |
| [capstone_project#3](https://github.com/zendah21/capstone_project/pull/3) | `missing_manifest`, zero top changes | SQL tools read; `load_memory` unresolved |
| [visulate-for-oracle#526](https://github.com/visulate/visulate-for-oracle/pull/526) | Exit 2, unsupported Git tree binding | Gitlink `api-server/repos/test-proj-01`, mode 160000; separate factory gap remains |
| [scopeiq#2](https://github.com/rafliogun49/scopeiq/pull/2) | `missing_manifest`, zero top changes | Five direct edges already read; ambiguous deployment root, not a missing SDK parser |

Zero cases in these fresh replays meets the ten-case bar. `insufficient_evidence`
is retained as diagnostic evidence only. A host-grant row cannot substitute for
an application-agent capability change.

### Reproduction

Use each record's public PR repository and pinned refs from `replay.csv` / 
`fresh-adoption.json`. In a disposable clone, fetch the refs before analysis,
checkout the head, and verify `git merge-base BASE HEAD`. Then run, separately
with the release console script and the source checkout's `./shipgate`:

```sh
agents-shipgate init --workspace SCOPE --local-review --json
agents-shipgate verify --workspace REPO --config SCOPE/.agents-shipgate-local-review.yaml \
  --base BASE_SHA --head HEAD_SHA --ci-mode advisory --format json
```

Scopes: attest `agents/attest_orchestrator`; capstone `meal_planner_agent`;
Visulate `ai-agent/root_agent`; ScopeIQ `backend`. Set
`AGENTS_SHIPGATE_AGENT_MODE=1`, unset `PYTHONPATH`, and set the source launcher's
`AGENTS_SHIPGATE_PYTHON` to the same interpreter. Do not use a shallow/partial
clone or initialize submodules. Raw CLI reports remain local; curated data here
is a research ledger, not a receipt. The 27-case historical config hashes permit
identity checks but do not reconstruct those configs; fresh init above is the
portable four-case reproduction, not a byte-identical replay of all 27 configs.

## #610 reproduction and decision

In a disposable Git repository with a committed README and an unverified
working-tree file, feed `{"changed_files": []}` to
`agents-shipgate preflight --workspace REPO --plan PLAN.json --json`.
Both builds return `state=complete`, `completion_allowed=true`, and all six
permissions true, including merge/report_complete, without a current verifier
identity. A plan listing README instead returns `agent_action_required`, a
verify next action, and all permissions false. The defect is the empty-plan
route; the docs-only control is a useful negative control.

Select planning-only completion with no publication/merge authority. Implement
that deliberately across model, schema and consumers under #610; do not silently
change a shared `complete` union or frozen predecessor schema in this research
PR. Omitting edits cannot certify a workspace. This review does not fix #610.

## Phase exit

The baseline, owner, ordered backlog, scope/absence design, #610 diagnosis and
bounded reliability selection are recorded. Weeks 2–3 implementation remains
#580 → #655 → #867. Outreach and the ten-case value claim remain gated by their
actual evidence; neither is implied by closing the Days 1–5 planning phase.
