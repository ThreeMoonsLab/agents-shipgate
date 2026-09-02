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

`target_basis` is a closed vocabulary. No member is *taken from* verifier output
— the engine's decision on a candidate never selects that candidate's cell,
because a corpus assembled to match the verifier's own verdicts cannot measure
the verifier.

| `target_basis` | Meaning |
|---|---|
| `miner_label` | A committed miner label exists for this subject. `evidence_ref` names the CSV row. |
| `diff_substance` | Targeted from what the change does, recorded in the [candidate register](#candidate-register) below. |
| `sample_design` | A shipped sample built to exhibit this outcome. `evidence_ref` names the sample directory. |
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
row's origin, including for a gap whose `mining_lead` names a specific PR.

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
`test_a_pinned_external_candidate_matches_the_sweep_that_recorded_it` reads them
back from the sweep. The unpinned walk candidates need pinning under the same
convention before Cut C, and the choice should be written down when it is made.

A closed-unmerged PR has no merge commit, so the W36 sweep pins it at the
**fork point and the PR head**: `base = git merge-base <head> origin/<base branch>`,
`head = refs/pull/<n>/head`. That reproduces "the mainline the change was
written against, and the change as it was rejected" — the closed-unmerged form
of the same convention. A reverted PR is pinned like any merged PR.

## Where the plan stands

60 slots over the 28 cells; three `mcp_openapi_declared_binding` cells and
`google_adk × insufficient_evidence` carry a third slot because their first two
are both engine-development inputs.

| | Count |
|---|---|
| Slots with a candidate | 44 of 60 |
| Gaps to mine or construct | 16 |
| Slots planned as a qualifying origin | 33 (floor is 23) |
| …of those, already sourced | 32 |
| …of those, still to find | 1 |
| Slots planned as `synthetic` | 27 (ceiling is 33) |
| Slots that can be a cell's holdout case | 42 |
| Slots that are engine-development inputs | 18 |

Per profile:

| Profile | Sourced | Qualifying origin | Holdout-eligible | Gaps |
|---|---|---|---|---|
| `mcp_openapi_declared_binding` | 10 | 8 | 5 | 1 |
| `coding_agent_trust_roots` | 8 | 6 | 6 | 0 |
| `langchain_crewai` | 5 | 4 | 6 | 3 |
| `google_adk` | 7 | 5 | 6 | 2 |
| `openai_agents_sdk` | 6 | 5 | 6 | 2 |
| `multi_agent_handoffs` | 5 | 3 | 6 | 3 |
| `n8n` | 3 | 2 | 7 | 5 |

Per outcome:

| Outcome | Sourced | Qualifying origin | Holdout-eligible | Gaps |
|---|---|---|---|---|
| `passed` | 15 | 10 | 8 | 0 |
| `review_required` | 11 | 9 | 12 | 4 |
| `blocked` | 10 | 8 | 11 | 5 |
| `insufficient_evidence` | 8 | 6 | 11 | 7 |

Every number on this page is recomputed from the CSV by
`tests/test_strata_inventory.py`, so the reading and the plan cannot drift
apart.

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

**The origin floor is the binding constraint, not the case count.** 23 of 56
cases must be `real_history`, `rejected_or_reverted`, or `design_partner`. The
plan reaches 32 only by committing to mine 18 further qualifying candidates —
more than half of them.

**`insufficient_evidence` then `blocked` are the scarce outcomes.** Three of
15 `insufficient_evidence` slots and 5 of 15 `blocked` slots have a candidate.
Merged history is the wrong place to look for `blocked`: a change that should
have been stopped usually was, so `rejected_or_reverted` is the vein.

**`n8n` is the profile that can fail this deliverable.** One of its eight slots
has a candidate, and no n8n repository has ever been mined at all.

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

## Candidate register

Every sourced candidate, and every gap whose lead names a specific PR. The
`Profile` and `State` columns are checked against the CSV; `State` is the
GitHub merge state as read on 2026-08-31, and it is what decides an origin.

| Candidate | Profile | State | The change |
|---|---|---|---|
| `github.com/github/github-mcp-server#3020` | `mcp_openapi_declared_binding` | `merged` | Adds `find_duplicate`, a read-only tool gated behind a `duplicate_detection` feature flag. The repository checks in per-tool MCP schemas under `pkg/github/__toolsnaps__/`; the count goes 115 → 116. The flag is invisible in the schema — the filename encodes it, the JSON does not. |
| `github.com/github/github-mcp-server#3076` | `mcp_openapi_declared_binding` | `merged` | Adds a confirmed repository-deletion tool to the same 117-schema surface. Walk notes record base `bfb59bb7…` and head `5ea9a0e8…` as abbreviations only; both must be resolved to full SHAs. |
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
| `github.com/langchain-ai/langchain-mcp-adapters#540` | `langchain_crewai` | `merged` | Surfaces MCP tool execution errors as failed tool output in the adapter that builds LangChain tools from a server's tool list at run time. Named as the nearest shape for `langchain_crewai.insufficient_evidence.1`; it changes no authority and labels `safe_to_merge`, which is why the slot stays a gap. |
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

| Candidate | Origin | State | Note |
|---|---|---|---|
| `github.com/awslabs/mcp#4489` | `real_history` | `merged` | Adds two literal FastMCP entrypoints, `budget-actions` and `budget-notifications`, to the billing-cost-management server, taking the budget tool surface from 1 to 3; both documented read-only, both requiring new `budgets:DescribeBudgetActionsFor*` IAM permissions. **Previously mis-registered here as the pre-existing `budgets` entrypoint and aimed at `insufficient_evidence`** — wrongly, since the names are static literals and are exactly what a grep-the-literal extraction reads. A `mcp_openapi_declared_binding × passed` alternate; `engine_tests` exposure via `tests/test_init_scaffold_disclosure.py`. |
| `github.com/google/adk-samples#1745` | — | `open` | `SmartCloserAgent`: a root `LlmAgent` with `sub_agents=[salesforce_agent, sap_agent]` over a Salesforce/SAP quote-to-cash flow, with three financial writes reachable only through the sub-agents. **Still open**, so it is not history and cannot fill a slot; it is also `engine_tests` exposure through `test_declaration_questionnaire.py` and `test_detect.py`. Kept as the shape to match when mining `multi_agent_handoffs × blocked`. |
| `github.com/mongodb-js/mongodb-mcp-server#1417` | `real_history` | `merged` | "request elicitation for aggregations"; walked repository, diff not read, placement undetermined. |
| `github.com/openai/openai-agents-python#3461`, `#3518` | `real_history` | `merged` | `safe_to_merge`: opt-in recovery for a missing function tool; typing tool-end hook results. |
| `github.com/aaif-goose/goose#9717`, `#9798` | `real_history` | `merged` | `safe_to_merge`: ACP search session in the desktop client; opt-in ACP last-message snippets. |
| `github.com/stripe/ai#332`, `#336`, `#353`, `#400` | `real_history` | `merged` | `safe_to_merge` automated skill syncs. Four instances of one shape; prefer variety over volume. |
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
| `github.com/openai/openai-agents-python#3194`, `github.com/google/adk-samples#1665` | `rejected_or_reverted` | `reverted` | The only other reverts the W36 enumeration found in the target repositories: a kwargs guard fix and a region/lockfile pivot. Rejected history, but neither labels above `safe_to_merge`. |
| `samples/mcp_only_server`, `samples/openapi_only_agent`, `samples/hitl_evidence_covered_agent`, `samples/openai_agents_sdk_agent`, `samples/ai_generated_refund_pr`, `samples/simple_openai_api_agent`, `samples/large_multi_framework_agent`, `samples/baseline_workflow`, `samples/simple_anthropic_agent` | `synthetic` | `in_tree` | `tuning_only` if used — every one of them is engine-tuning material. |

## Maintaining it

`tests/test_strata_inventory.py` derives the 28 cells and the holdout floor from
`pre_release_safety_requirements()` rather than restating them, so moving the
policy fails the inventory instead of leaving it silently aimed at the wrong
shape — the failure mode
[`docs/release-evidence-policy-decision.md`](../../docs/release-evidence-policy-decision.md#where-the-bar-is-defined)
warns about for this directory. It also re-reads every cited miner label, every
pinned SHA, every declared exposure, and every candidate's profile and merge
state from the source that records it, so the inventory cannot drift from what
it cites.

Order of work after this cut: Cut B mines the gaps, the calibration round runs on
five non-corpus cases, then labels, freeze, receipts, and the non-gating
[participant-validation gate](participant-validation.md).
