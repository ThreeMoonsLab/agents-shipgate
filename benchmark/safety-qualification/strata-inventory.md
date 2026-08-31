# Cut A — the pre-1.0 strata inventory

[`strata-inventory.csv`](strata-inventory.csv) maps the known candidate pool onto
the 28 profile × decision cells the `pre_1_0` policy requires, so Cut B mines the
empty cells instead of re-finding the full ones. It is the first of the four cuts
in [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456).

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

`target_basis` is a closed vocabulary, and **verifier output is not in it**. A
corpus assembled to match the verifier's own verdicts cannot measure the
verifier, so the engine's decision on a candidate never selects that candidate's
cell.

| `target_basis` | Meaning |
|---|---|
| `human_label` | A miner label exists for this subject. `evidence_ref` names the CSV row. |
| `diff_substance` | Targeted from what the change does, recorded in the [candidate register](#candidate-register) below. |
| `sample_design` | A shipped sample built to exhibit this outcome. `evidence_ref` names the sample directory. |
| `unsourced` | A gap. No candidate; `mining_lead` says where to look. |

The miner's three-way vocabulary does not distinguish `review_required` from
`insufficient_evidence` — `needs_human` covers both — so a `needs_human`
candidate may be targeted at either cell. **Drawing that line is work for the
Cut C calibration round**, not for this file; the labeling guide has to answer it
before 56 labels are produced against it.

## Columns

| Column | Meaning |
|---|---|
| `slot_id` | `<profile>.<target_decision>.<n>`, `n` counting from 1 within the cell. |
| `profile` | One of the seven profiles the policy stratifies by. |
| `target_decision` | The cell this slot is being sourced for. |
| `origin_class` | The `SafetyCaseOrigin` this slot is planned to carry. |
| `status` | `pinned`, `unpinned`, or `gap` — see below. |
| `split_eligibility` | `tuning_only` or `either` — see below. |
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

**`origin_class` and `split_eligibility` are both decided by what the candidate
is, not asserted about it.** An in-tree `samples/` path is `synthetic` — an
upstream PR is not — and both facts are checked against the path, because the
origin floor is otherwise satisfiable by renaming: calling the twelve sample
slots `real_history` would report 41 qualifying origins against a floor of 23
while adding no real evidence at all.

The policy also requires at least one holdout case per stratum, and holdout
means evidence the engine was never tuned on. Every `samples/*` path is
engine-tuning material — the goldens under `samples/*/expected/` are what the
engine is developed against — so a slot filled from `samples/` is `tuning_only`
and can never be the cell's holdout case. Every cell therefore needs at least
one `either` slot, which is checked. A synthetic *built for the corpus* is
`either`, provided it is not also committed as a shipped sample; do not move
corpus synthetics into `samples/`.

## Where the plan stands

56 slots, two per cell, all 28 cells present.

| | Count |
|---|---|
| Slots with a candidate | 28 of 56 |
| Gaps to mine or construct | 28 |
| Slots planned as a qualifying origin | 29 (floor is 23) |
| …of those, already sourced | 16 |
| …of those, still to find | 13 |
| Slots planned as `synthetic` | 27 (ceiling is 33) |
| Slots that can be a cell's holdout case | 44 |

Per profile, of 8 slots each:

| Profile | Sourced | Qualifying origin | Gaps |
|---|---|---|---|
| `mcp_openapi_declared_binding` | 7 | 5 | 1 |
| `coding_agent_trust_roots` | 6 | 6 | 2 |
| `langchain_crewai` | 4 | 4 | 4 |
| `google_adk` | 4 | 4 | 4 |
| `openai_agents_sdk` | 3 | 5 | 5 |
| `multi_agent_handoffs` | 3 | 3 | 5 |
| `n8n` | 1 | 2 | 7 |

Per outcome, of 14 slots each: `passed` 12 sourced, `blocked` 6,
`review_required` 6, `insufficient_evidence` 4.

### What the shape says

**The origin floor is the binding constraint, not the case count.** 23 of 56
cases must be `real_history`, `rejected_or_reverted`, or `design_partner`. The
labeled pool holds 19 PRs from five repositories and the walks add a handful
more; the plan reaches 29 only by committing to mine 13 further qualifying
candidates. Every synthetic added past 27 eats margin that is already thin.

**`insufficient_evidence` and `blocked` are the scarce outcomes, in that order.**
Only 4 of 14 `insufficient_evidence` slots and 6 of 14 `blocked` slots have a
candidate. Merged history is the wrong place to look for `blocked`: a change
that should have been stopped usually was, so `rejected_or_reverted` is the vein
— which is why four `blocked` and `insufficient_evidence` gaps name closed or
reverted PRs rather than merged ones.

**`n8n` is the profile that can fail this deliverable.** One of its eight slots
has a candidate, and no n8n repository has ever been mined at all. Seven
constructions or a fresh sweep stand between it and a complete corpus.

**The pool was swept by a build that no longer exists.** The 2026-W24 … W26
sweeps ran before [#403](https://github.com/ThreeMoonsLab/agents-shipgate/issues/403),
when a trigger reported `no_match` as a confident negative and the MCP trigger
matched filename globs only. `langchain-ai/langgraph`, `modelcontextprotocol/servers`
and `pydantic/pydantic-ai` were each swept 40 PRs deep and triggered **zero**
times. Those zeroes are not evidence that the repositories hold no candidates,
and several gap rows depend on re-mining them. **Do not treat an old sweep's
silence as a closed cell.**

## Candidate register

The in-tree evidence for candidates whose basis is `diff_substance`. These come
from adoption walks; each entry records what the change does and nothing about
what any build concluded about it.

| Candidate | Profile | The change |
|---|---|---|
| `github.com/github/github-mcp-server#3020` | `mcp_openapi_declared_binding` | Adds `find_duplicate`, a read-only tool gated behind a `duplicate_detection` feature flag. The repository checks in per-tool MCP schemas under `pkg/github/__toolsnaps__/`; the count goes 115 → 116. The flag is invisible in the schema — the filename encodes it, the JSON does not. |
| `github.com/github/github-mcp-server#3076` | `mcp_openapi_declared_binding` | Adds a confirmed repository-deletion tool to the same 117-schema surface. Walk notes record base `bfb59bb7…` and head `5ea9a0e8…` as abbreviations only; both must be resolved to full SHAs. |
| `github.com/grafana/mcp-grafana#1080` | `mcp_openapi_declared_binding` | Adds `update_incident` to a Go MCP server registering tools as `mcpgrafana.MustTool("…", …)`. The published surface goes 99 → 100. |
| `github.com/awslabs/mcp#4489` | `mcp_openapi_declared_binding` | Python/FastMCP monorepo registering tools as `@budget_server.tool(name='budgets', …)`. No checked-in export exists, so the surface has to be read from code. |
| `github.com/google/adk-samples#1745` | `multi_agent_handoffs` | `SmartCloserAgent`: a root `LlmAgent` with `sub_agents=[salesforce_agent, sap_agent]` over a Salesforce/SAP quote-to-cash flow. Three financial writes — `create_salesforce_quote`, `update_opportunity_status` → `Closed Won`, `create_sap_sales_order` — are reachable only through the sub-agents. |

### Reserve

Identified candidates not allocated to a slot. They exist so a relabel that
empties a cell does not require fresh mining. Adding one to the inventory means
giving it a `slot_id` in its cell; it does not require finding it again.

| Candidate | Origin | Miner label | Note |
|---|---|---|---|
| `github.com/openai/openai-agents-python#3461` | `real_history` | `safe_to_merge` | Opt-in recovery for a missing function tool. |
| `github.com/openai/openai-agents-python#3518` | `real_history` | `safe_to_merge` | Types tool-end hook results as an object. |
| `github.com/aaif-goose/goose#9717` | `real_history` | `safe_to_merge` | ACP search session in the desktop client. |
| `github.com/aaif-goose/goose#9798` | `real_history` | `safe_to_merge` | Opt-in ACP last-message snippets. |
| `github.com/stripe/ai#332`, `#336`, `#353`, `#400` | `real_history` | `safe_to_merge` | Automated skill syncs from `docs.stripe.com`. Four instances of one shape; prefer variety over volume when drawing on these. |
| `github.com/mongodb-js/mongodb-mcp-server#1417` | `real_history` | — | Walked repository, diff not read. Placement undetermined. |
| `github.com/google/adk-python#6605` | `real_history` | — | Identified during the #385 sub-agent work; diff not read. |
| `samples/mcp_only_server`, `samples/openapi_only_agent`, `samples/hitl_evidence_covered_agent`, `samples/openai_agents_sdk_agent`, `samples/ai_generated_refund_pr`, `samples/simple_openai_api_agent`, `samples/large_multi_framework_agent`, `samples/baseline_workflow`, `samples/simple_anthropic_agent` | `synthetic` | varies | `tuning_only` if used — every one of them is engine-tuning material. |

## Maintaining it

`tests/test_strata_inventory.py` derives the 28 cells from
`pre_release_safety_requirements()` rather than restating them, so moving the
policy fails the inventory instead of leaving it silently aimed at the wrong
shape — the failure mode
[`docs/release-evidence-policy-decision.md`](../../docs/release-evidence-policy-decision.md#where-the-bar-is-defined)
warns about for this directory. It also checks each `human_label` row against the
miner CSV it cites and each pinned external row against the SHAs recorded in
`benchmark/miner/results/2026-W27-reeval.csv`, so the inventory cannot drift
from its own sources.

Order of work after this cut: Cut B mines the gaps, the calibration round runs on
five non-corpus cases, then labels, freeze, receipts, and the non-gating
[participant-validation gate](participant-validation.md).
