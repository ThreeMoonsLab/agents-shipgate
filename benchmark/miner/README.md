# Merged-PR history miner

Replays the merged-PR history of public agent repos through the real engine
and emits one metadata row per PR. One mining run produces three artifacts the
strategy needs at once:

1. **Demand receipts** — real merged PRs whose capability/authority changes
   shipped without any gate ("this diff merged unreviewed; here is the verdict
   that would have caught it"). The Stripe `stripe/ai` PR #232 pilot was this
   method at n=1.
2. **Labeled-corpus candidates** — rows are the candidate pool for the
   verdict-accuracy benchmark; humans label `safe_to_merge / needs_human /
   must_block` per row and the frozen corpus regression-tests every release.
3. **Extraction coverage, measured** — the `insufficient_evidence` rate on
   real repos is the headline extraction-coverage KPI; this measures it
   instead of assuming it.

## What one row records (schema v0.1)

Per PR (`base = merge_commit^1`, `head = merge_commit` — the PR's net
mainline change, correct for both squash and merge):

| Field group | Fields | Source |
|---|---|---|
| Identity | `repo, pr_number, pr_url, title, merged_at, base_sha, head_sha` | `gh pr list` |
| Trigger | `trigger_run, trigger_rationale, files_changed` | `agents_shipgate.triggers.evaluate` on the diff (organic `user_requested=False`) |
| Boundary gate | `check_decision, check_rule_ids` | `shipgate check --agent claude-code --diff …` (no manifest needed) |
| Release gate | `init_status, head_decision, head_blockers, head_review_items, evidence_gaps, tools_scanned` | `init --write` (cold-start) + `scan` on a head worktree |
| Authority delta | `cap_added, cap_removed, cap_changed, cap_broadened` | `capability export` on base+head worktrees (same manifest both sides) + `capability diff` |
| **Receipt (v0.2)** | `verify_verdict, verify_decision, verify_can_merge, verify_trust_root_touched, verify_policy_weakened, verify_cap_added/modified/removed` | Real `verify --base <base'> --head <head''>` with the cold-start manifest committed onto **both** sides; `head''` is re-parented onto `base'` (`commit-tree`) so the three-dot diff is exactly the PR's delta and the injected manifest cannot fire the trust-root signal. Diff-aware `SHIP-VERIFY-*` checks and new-findings gating apply — these columns are the per-PR verdict; the scan columns above remain the cold-start whole-surface state. |
| Lifecycle | `status` (`evaluated \| trigger_skip \| init_skip \| scan_failed \| error`), `notes` | — |

`evaluated` means the head scan produced a release decision. When the
repo-root cold start fails (monorepos), the evaluator retries once at the
deepest common directory of the changed files (`notes: retry_at:<dir>`) —
the stripe/ai PR #232 pattern, where the agent lives under `tools/python`.

**Privacy rule:** rows carry public PR metadata, verdicts, check IDs, and
counts only — never diff text, code excerpts, or report evidence. Same
convention as the adoption-harness CSVs.

## Run it

```bash
# Network steps (gh auth required): enumerate + clone, then evaluate locally.
python -m benchmark.miner mine \
  --repo stripe/agent-toolkit --repo crewAIInc/crewAI-examples \
  --limit 50 \
  --workdir .miner-work \
  --out benchmark/miner/results/$(date +%Y-W%V)-mined.csv \
  --jsonl benchmark/miner/results/$(date +%Y-W%V)-mined.jsonl

# Re-evaluate one PR offline (clone already in .miner-work):
python -m benchmark.miner evaluate \
  --repo-path .miner-work/stripe__agent-toolkit \
  --base <sha> --head <sha>
```

- `--force-run` evaluates trigger-skip PRs too (useful for noise/negative
  sampling when building the labeled corpus).
- Clones land in `--workdir` (gitignored); evaluation is offline after the
  clone, so reruns and one-off `evaluate` calls need no network.
- Engine calls go through `sys.executable -m agents_shipgate` (never bare
  `agents-shipgate` on PATH) so results always reflect this checkout/venv —
  not a stale shadow install.

## Results conventions

- Committed results live in [`results/`](results/) as
  `<YYYY>-W<NN>-mined[-suffix].csv` plus a row in the table below. Commit the
  CSV (and optional JSONL); never commit clones or report artifacts.
- A row with `status=evaluated` and `head_decision=insufficient_evidence`
  counts toward the IE-rate KPI. `trigger_skip` rows are the negative-control
  pool (the 0-noise-on-irrelevant-diffs property, on real history).
- The IE-threshold constants (`_LOW_CONFIDENCE_TOOL_RATIO`,
  `_MAX_TOLERATED_SOURCE_WARNINGS`) are examined and held — the calibration
  attempt, finding, and revisit conditions are in [`CALIBRATION.md`](CALIBRATION.md).
- Labeling for the accuracy corpus happens in a separate adjudicated file next
  to the CSV (`<run>.labels.csv`: `pr_url,label,rationale`) — the mined row is
  evidence, the label is the ground truth. The rubric, two-labeler process, and
  metrics are in [`LABELING.md`](LABELING.md). Generate the turnkey worksheet
  with `python -m benchmark.miner labels`; a ready blank copy is committed
  alongside each run's results as `<run>.labels.template.csv` (one per run in
  the table below — currently `2026-W24-…`, `2026-W25-…`, `2026-W26-…`,
  `2026-W36-cutb` and `2026-W36-closeout`). Label the run you mean to score,
  then `python -m benchmark.miner score --results <jsonl>
  --labels <csv>` prints the confusion matrix + headline accuracy metrics.

| Run | Date | Repos | Rows | Notes |
|---|---|---|---|---|
| [`2026-W24-mined.csv`](results/2026-W24-mined.csv) | 2026-06-12 | stripe/ai, openai/openai-agents-python, crewAIInc/crewAI-examples | 121 (latest 40 merged PRs each + stripe/ai#232) | Schema v0.2 (re-run with baseline-gated `verify_*` receipts; supersedes the v0.1 artifact in place). **Labeled + scored** (see W24–W25 section; first real `must_block` rows). Findings below. |
| [`2026-W25-mined.csv`](results/2026-W25-mined.csv) | 2026-06-12 | google/adk-samples, langchain-ai/langgraph, modelcontextprotocol/servers | 120 (latest 40 merged PRs each) | Widen run over 3 new framework families. Schema v0.2. **Labeled + scored** (see W24–W25 section). Findings below. |
| [`2026-W26-mined.csv`](results/2026-W26-mined.csv) | 2026-06-16 | stripe/agent-toolkit → **stripe/ai** (see note), block/goose, pydantic/pydantic-ai | 120 (latest 40 merged PRs each) | Deepen run over agent **apps/toolkits**. First run with `tools_scanned` captured (#223); decided rows are cold-start `head_decision=review_required` but `verify`-effective `insufficient_evidence`. Schema v0.2. Findings below. |
| [`2026-W27-reeval.csv`](results/2026-W27-reeval.csv) | 2026-07-08 | the 19 labeled PRs (stripe/ai, openai/openai-agents-python, crewAIInc/crewAI-examples, google/adk-samples, aaif-goose/goose — formerly block/goose) | 19 (re-eval at fixed SHAs, not a fresh mine) | **v0.15.0 delta on the labeled corpus.** Same PRs / same base→head SHAs as W24–W26, re-run on the released engine. Clears the 4 scan crashes; both `must_block` move abstain→review but `blocked_recall` stays 0.0. Off the `*-mined` glob by design. Findings below. |
| [`2026-W36-cutb.csv`](results/2026-W36-cutb.csv) | 2026-09-02 | n8n-io/n8n, n8n-io/self-hosted-ai-starter-kit, Zie619/n8n-workflows, enescingoz/awesome-n8n-templates, modelcontextprotocol/servers, microsoft/playwright-mcp, cloudflare/mcp-server-cloudflare, supabase-community/supabase-mcp, Azure/azure-mcp, hashicorp/terraform-mcp-server, elastic/mcp-server-elasticsearch, redis/mcp-redis, openai/openai-agents-python, google/adk-samples, google/adk-python, langchain-ai/langgraph, langchain-ai/langchain-mcp-adapters, langchain-ai/deepagents, crewAIInc/crewAI-examples, aaif-goose/goose, pydantic/pydantic-ai | 912 (latest 40 merged per repo, plus `--state closed` on openai-agents-python and adk-samples, `--state reverted` on the same plus goose and pydantic-ai, and `--pr` named candidates) | **The Cut B sourcing sweep for [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456), and the first post-#403 run.** Every n8n repository this project had never mined, eight unwalked MCP servers, and the rejected vein (closed-unmerged + reverted PRs, new in this run). Schema v0.2. **Labeled** in `2026-W36-cutb.labels.csv` — one session's Cut B cell-targeting labels from the PR diffs, *not adjudicated*; corpus labels come only from the Amendment 1 raters. Off the `*-mined` glob by design (see note). Findings below. |
| [`2026-W36-closeout.csv`](results/2026-W36-closeout.csv) | 2026-09-02 | github/github-mcp-server, grafana/mcp-grafana, bytedance/deer-flow | 45 (three `--pr` named walk candidates, the latest 40 merged on deer-flow, and two `--pr` named deer-flow candidates) | **The Cut B close-out for [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456).** Resolves the pins for the three walked MCP servers the inventory carried unpinned, and mines the LangChain application repository that closes its last cell. Schema v0.2. **Labeled** in `2026-W36-closeout.labels.csv` — one row, the claimed candidate, cell-targeting and *not adjudicated* like every other sourcing label here. Off the `*-mined` glob by design: every row is either a named PR or a repository chosen for one cell. Findings below. |

> **W26 repo note (data-integrity):** `gh pr list --repo stripe/agent-toolkit`
> follows GitHub's transfer redirect — `stripe/agent-toolkit` was folded into the
> `stripe/ai` monorepo (`gh api repos/stripe/agent-toolkit` → `full_name:
> stripe/ai`). So W26's "stripe/agent-toolkit" stratum is **stripe/ai**: its 6
> decided rows are the same stripe/ai skill-sync PRs W24 already covered (which is
> why they reuse W24's labels), and only `block/goose` + `pydantic/pydantic-ai`
> were genuinely new repos that week. The corpus therefore spans **8 distinct
> repos**, not 9; row counts (361 mined, 336 trigger-skip) are unaffected.

> **W36 glob note (data-integrity):** `2026-W36-cutb` is deliberately named
> off the `*-mined` glob. That glob feeds
> `test_cross_run_trigger_skip_rate_is_high_on_real_history`, which pins the
> "~93% of *unselected* merged PRs trigger-skip" noise bound. W36 is not an
> unselected sample: it aims at capability-dense repositories (MCP servers,
> n8n template collections, where 35 of 40 awesome-n8n-templates PRs add a
> workflow), and it mixes in closed-unmerged, reverted and named PRs. Its own
> trigger-skip rate is 648/912 (0.71), and folding it into the aggregate would
> either fail that guard or silently change what the guard measures. It is
> therefore a *sourcing* sweep for the safety-qualification corpus, not a
> noise-bound row; the CSV/JSONL pair still carries the v0.2 row schema and
> its template is still generated by `python -m benchmark.miner labels`.

### 2026-W36 findings — the Cut B sourcing sweep

Read this as a sourcing run, not an accuracy row: the labels are one
session's, made from the diffs to aim [#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456)
cells, and nothing here is scored.

- **The rejected vein exists and is minable.** `--state closed` and
  `--state reverted` are new in this run. Five `rejected_or_reverted`
  candidates from five repositories were placed: a closed remote-MCP OTC
  trading example (openai-agents-python#2932), a closed sub-agent
  financial-writes sample (adk-samples#2148), a closed "execute any
  Elasticsearch API" tool (mcp-server-elasticsearch#57), a closed runtime
  governance plugin (adk-python#6605), and a *reverted* CI change that gave a
  coding agent write permissions (pydantic-ai#4199, reverted by #4202).
  Merged history, as expected, contributed one destructive MCP tool in 912
  rows (terraform-mcp-server#469).
- **n8n is now mined.** Four repositories, 140 rows. Template collections
  trigger on nearly every PR (`n8n-nodes-base.` in the diff); the n8n core
  monorepo triggers on CI and node code, never on exported workflow JSON.
- **Engine surprises, recorded rather than fixed** (all reproduce from the
  committed pins with `python -m benchmark.miner evaluate`):
  - `init` writes the `CHANGE_ME` placeholder and the scan exits 2 on every
    TypeScript MCP server swept — playwright-mcp (`defineTool`), supabase-mcp
    (`tool({...})` via `@supabase/mcp-utils`), mcp-server-elasticsearch — so
    those repositories never reach a verdict (`scan_failed` ×24).
  - Go servers on mark3labs/mcp-go and the official go-sdk evaluate with
    `tools_scanned=0` (terraform-mcp-server ×15, every row
    `insufficient_evidence`): the `mcp.NewTool(` registration behind a runtime
    registry (`createDynamicTFETool`, `tools.RegisterTools`) is triggered on but
    not read.
  - `verify` exits 4 (unparseable) on 31 of 35 evaluated
    awesome-n8n-templates rows and exits 2 on all 7 evaluated goose rows; the
    cold-start `head_decision` survives, the receipt does not.
  - The scan crashes on duplicate tool names in test fixtures and in exported
    n8n workflows: langchain-mcp-adapters (`tests/test_tools.py` defines `add`
    twice) and Zie619/n8n-workflows (a node named by an `={{ $fromAI(...) }}`
    expression), `head_scan_failed_exit_3`.
  - openai/openai-agents-python now `init_skip`s every triggered PR (11 of
    40; W24 evaluated 4 of 40 on the same repository), and so do pydantic-ai,
    langgraph and deepagents. **Cause corrected by the close-out run below:**
    this is not the example trees going unseen. `init --write` at the root of
    openai-agents-python returns `refused_unresolved_scope` — the repository
    holds three self-contained projects that define agents, and one manifest
    describes one agent surface. The refusal is the monorepo behavior working;
    what the miner cannot do is act on it, because its only fallback is the
    deepest common directory of the changed files.
- **Trigger misses worth knowing.** goose#9736 (a new global `AGENTS.md`
  hints path), goose#11233 (two built-in skills) and pydantic-ai#3248 (an
  agent-delegation example) are `trigger_skip`; each was placed or reserved
  from the diff, and each is a shape the catalog does not see.

### 2026-W36 close-out findings — the pins and the last cell

A three-repository run with two jobs: resolve the pins the strata inventory
carried as abbreviations, and close
`langchain_crewai × insufficient_evidence`. Same reading rules as the Cut B
sweep — one session's cell-targeting labels, nothing scored.

- **`init_skip` on a monorepo is a refusal, not a failure, and the miner
  cannot tell them apart.** 24 of the 40 deer-flow rows are `init_skip`
  because a cold start at the repository root returns
  `refused_unresolved_scope`: two self-contained Python projects, and one
  manifest describes one agent surface. Pointed at
  `backend/packages/harness`, `init --write` writes a manifest and the scan
  reaches `insufficient_evidence`. The miner records `init_status: failed`
  for both cases, because `_run_init` reads only "did a manifest appear",
  so a refusal that names the right next step is indistinguishable in the
  CSV from a crash. Re-checking openai-agents-python at its own recorded
  pin gives the same refusal, which is the correction to the Cut B bullet
  above. **A repository that `init_skip`s is not thereby unevaluable** — it
  needs a scope, and the sweep does not carry one.
- **The walked-candidate pins had drifted, in the direction the inventory
  warned about.** `github-mcp-server#3076`'s walk note recorded the head as
  `5ea9a0e8…`, which is `refs/pull/3076/head`; after a squash merge that
  commit is not reachable from the default branch, so resolving the
  abbreviation would have pinned an object no clone reaches. The merge
  commit is `8ec62491…` and its first parent is the `bfb59bb7…` the same
  note recorded — half of the pin was right.
- **The three walked MCP servers all read `tools_scanned=0`.** Both
  github-mcp-server rows and the mcp-grafana row evaluate to
  `insufficient_evidence` with zero tools read, on two Go servers whose
  published surfaces are around a hundred tools each. That is the Go-server
  finding from the Cut B sweep reproducing on the two servers the adoption
  walks knew best, including the one whose walk drove
  `tool_sources[].binding`.
- **The dynamic-toolkit shape is in applications, not in the adapter.** The
  Cut B lead was to stop mining `langchain-mcp-adapters` and find a
  repository that *calls* it. deer-flow does: tools are assembled at run
  time from an out-of-tree extensions config, and the claimed candidate
  (#4868) makes which credential a call carries depend on the run-time
  user. Its own latest-40 window yielded one decided row and no candidate,
  so the claim came from `--pr` on a PR six weeks older — the window, not
  the repository, was the wrong unit.

## Constructed-adversarial accuracy — the blocked-recall proof

Real merged PRs rarely contain a `must_block` capability change — **2** of the
19 unique labeled PRs across W24–W26 (stripe/ai#232 and
crewAIInc/crewAI-examples#169; see the W24–W25 section), and the mining-era
gate **abstained** (`insufficient_evidence`) on both rather than blocking. So a
reliable **blocked-recall** measurement — the moat claim, that the gate blocks
what is known-unsafe — comes from the repo's bundled fixtures, each built to be
a specific case. The labels are each
fixture's **documented design intent** — external ground truth, not a post-hoc
opinion about the engine's output — so scoring the engine's verdict against
them is *non-circular*. This is the moat claim, measured: the gate blocks what
is known-unsafe and does not escalate what is known-safe.

Corpus: [`results/constructed.jsonl`](results/constructed.jsonl) +
[`results/constructed.labels.csv`](results/constructed.labels.csv). Regenerate
with `python -m benchmark.miner constructed --out … --labels-out …`; score with
`python -m benchmark.miner score`.

| label \ verdict | allow | review | insufficient_evidence | block |
|---|---|---|---|---|
| `safe_to_merge` | 2 | 0 | 0 | 0 |
| `needs_human` | 0 | 1 | 1 | 0 |
| `must_block` | 0 | 0 | 0 | 3 |

| Metric | Value | Reading |
|---|---|---|
| `blocked_recall` | **1.0** (3/3) | every known-unsafe fixture is blocked |
| `benign_escalation_rate` | **0.0** (0/2) | no known-safe fixture is escalated |
| `needs_human_caught` | **1.0** (2/2) | both review-needed cases are routed to a human (review / insufficient_evidence), never auto-passed |

The live engine is re-run against these fixtures in CI
(`tests/test_miner_constructed.py`), so a change that regresses a blocked
verdict fails there rather than silently in the data file. The mined runs below
supply the complementary halves — the **negative control** (the 336
trigger-skips) and the real-history **extraction-coverage** (`insufficient_evidence`) rate.

## 2026-W27 re-eval — the v0.15.0 delta on the labeled corpus

> **Required before `0.16` beta claims:** re-run these fixed-SHA cases with the
> `0.16.0b1` wheel and preserve their unaugmented cold-start inputs. The
> evidence-backed pass contract intentionally makes AST-only framework
> surfaces ineligible for `passed`; regenerated sample goldens and the
> synthetic cold-start fixture cannot measure the resulting real-history IE
> rate. Do not publish an accuracy or migration-burden claim for `0.16` until
> that re-evaluation (and the independently governed beta corpus) exists.

**What this is.** Not a fresh mine: the **same 19 labeled PRs**, the **same
`base→head` SHAs** from W24–W26, re-run through the released **v0.15.0** engine
(this checkout's `src/` is byte-identical to the `v0.15.0` tag). The only
variable is the engine version, so every delta below isolates a v0.15.0 change
(the `SHIP-CAP-CONFIG-BINDING-*` checks + the `action_id` crash-degrade of #256,
contract v10). Committed as
[`2026-W27-reeval.{jsonl,csv}`](results/2026-W27-reeval.jsonl); the headline is
pinned by `test_w27_reeval_*` in `tests/test_miner_corpus.py` (reads the
committed artifact — network-free). Reproduce with the maintainer driver over a
`.miner-work` clone of the five repos.

**Headline: abstention → human-review, but not → block. `blocked_recall` stays 0.0.**

| label \ verdict | allow | review | insufficient_evidence | block |
|---|---|---|---|---|
| `safe_to_merge` (14) | 0 | 4 | 10 | 0 |
| `needs_human` (3) | 0 | 0 | 3 | 0 |
| `must_block` (2) | 0 | 2 | 0 | 0 |

| Metric | mining-era | v0.15.0 | reading |
|---|---|---|---|
| `blocked_recall` | 0.0 (0/2) | **0.0** (0/2) | still no hard block on real history |
| `must_block_caught` | 1.0 | **1.0** (2/2) | neither unsafe PR auto-passed |
| `needs_human_caught` | 1.0 | **1.0** (3/3) | every authority-bearing PR held |
| `benign_escalation_rate` | 0.0 | **0.286** (4/14) | engaging costs precision (mostly apparatus — see #4) |
| `ie_rate_on_safe` | — | **0.714** (10/14) | abstention is still the dominant safe verdict |
| scan crashes | 4 | **0** | the #256 crash-degrade cleared all 4 |

Per-PR delta (9 of 19 moved):

| PR | label | mined | v0.15.0 |
|---|---|---|---|
| stripe/ai#232 | `must_block` | insufficient_evidence | **human_review_required** |
| crewAIInc/crewAI-examples#169 | `must_block` | insufficient_evidence | **review_required** |
| aaif-goose/goose#9637 | `safe` | scan_failed | insufficient_evidence |
| aaif-goose/goose#9684 | `safe` | scan_failed | insufficient_evidence |
| aaif-goose/goose#9717 | `safe` | scan_failed | **human_review_required** |
| aaif-goose/goose#9798 | `safe` | scan_failed | insufficient_evidence |
| crewAIInc/crewAI-examples#184 | `safe` | insufficient_evidence | **review_required** |
| openai/openai-agents-python#3461 | `safe` | insufficient_evidence | **human_review_required** |
| openai/openai-agents-python#3518 | `safe` | insufficient_evidence | **human_review_required** |

The other 10 (2 `needs_human` stripe skill PRs, adk#1975, and 7 `safe` stripe/openai/adk PRs) stay `insufficient_evidence` on both engines.

- **1. Scan crashes cleared (4/4).** The goose `Duplicate action_surface
  action_id` crash-degrade (#256) holds on real history: all four
  `aaif-goose/goose` `scan_failed` rows now evaluate (three → IE, one → human
  review). This is the clean, unambiguous win.
- **2. Both real `must_block` PRs move off abstention — to review, not block.**
  stripe/ai#232 goes IE → `human_review_required` on a **clean verify receipt**
  (`can_merge_without_human=false`); crewAIInc#169 goes IE → `review_required`
  via the **cold-start `head_decision`** (its per-PR verify receipt fails to
  build — `base_lock_failed`, a repo-specific apparatus limit that was present in
  the mining-era row too, so the comparison is apples-to-apples). **But neither
  is a hard `block`, so `blocked_recall` is still 0.0.** Reliable blocked-recall
  (1.0) remains the constructed stratum's to prove.
- **3. The #232 move is substantively right — but not from the new config-binding
  check.** The finding that routes #232 to review is `SHIP-SCOPE-TOOLKIT-UNBOUNDED`
  ("stripe toolkit mounted without a scope bound", ×3) — which **is** the
  least-privilege removal the PR introduced. The v0.15.0 `SHIP-CAP-CONFIG-BINDING-*`
  checks (#256, built for exactly this shape) do **not** fire here; the movement
  comes from improved extraction coverage surfacing the pre-existing scope check.
  Honest read: the engine now catches the right thing on #232, by a different
  mechanism than the feature added to catch it.
- **4. Engaging costs precision on large repos — and it's mostly apparatus.**
  `benign_escalation_rate` rose 0.0 → 0.286: four `safe` PRs now route to review.
  All four are the **cold-start-scans-the-whole-repo** artifact — the miner's
  synthetic `init --write` enumerates *every* tool in the repo, not the PR's
  delta: openai/openai-agents-python#3461/#3518 pull in **150+ test-fixture
  tools** (`tool_one`, `dangerous_tool`, `will_fail_on_bad_json`, …), and
  goose#9717 enumerates goose's **entire ~240-tool MCP API** (14 blockers, 240
  review items) even though the PR itself *removes* capability (`cap_removed=5`,
  −1158 lines). The per-PR verify receipt's baseline-gating narrows this but does
  not fully neutralize a very large standing surface
  (`SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` / `SHIP-BASELINE-ENTRY-STALE` still
  fire). A properly-scoped adopter manifest (source pointed at the actual agent,
  not the whole monorepo + tests) would not see most of these, so **0.286
  overstates** the escalation a real adopter would experience.
- **Reading.** v0.15.0 does what its fixes intended: no more hard crashes, and
  the gate now **engages** rather than abstains on more of the corpus — both
  `must_block` PRs and four additional (`safe`) rows — and on the #232 anchor the
  concrete finding is substantively correct. That is a real improvement in "never silently pass." It is
  **not** yet a *blocking* gate on real history (`blocked_recall` 0.0), and the
  added engagement escalates some safe PRs, dominated by the cold-start
  whole-surface measurement artifact rather than a product precision regression.
  The next real-history leg is severity (review → block on a true `must_block`)
  and a scoped-manifest re-eval that measures adopter-realistic escalation.

### 2026-W26 findings — deepen run over agent apps/toolkits

- **App/toolkit repos do yield more decided rows than framework cores — but
  thin, IE-effective, and never `must_block`.** `stripe/agent-toolkit` produced
  6 decided rows (15% of its 40 PRs) vs **0** from `block/goose` and
  `pydantic/pydantic-ai` (a framework core, the same library-internals-churn
  pattern as W25). Their cold-start `head_decision` is `review_required`, but
  the per-PR `verify` receipt — **the verdict the accuracy scorer uses**
  (`labels.effective_verdict` = `verify_verdict or head_decision`) — is
  `insufficient_evidence` for all 6 (the toolkit surface still isn't statically
  resolvable on the base→head diff). So the *scored* verdict mix stays
  IE-dominated; W26 adds **no** scored `review_required` cases. And the 6
  collapse to **one** distinct pattern (a repeated automated "sync skills from
  docs.stripe.com" bot PR), so decided *diversity* added is ≈1. Net: even the
  best app/toolkit repo's decided rows are effectively IE once verified.
- **`tools_scanned` capture validated on real data (#223).** Every decided row
  records the ratio denominator (`tools_scanned=2`); pinned by
  `test_w26_headline_numbers_reproduce_from_committed_data`. This is the first
  committed run where the IE-threshold ratio is computable from the data.
- **Engine-robustness bug found:** `block/goose`'s OpenAPI spec crashes `scan`
  with `Config error: Duplicate action_surface action_id` (4 `scan_failed`
  rows) — the OpenAPI action_id is built from method+path without the
  operationId, so two operations on `GET /sessions/{session_id}` collide. A
  third-party spec must never hard-crash a scan; chipped as a follow-up (same
  fail-soft class as #212/#214).
- **Confirms the W25 implication:** mining agent **application/toolkit** repos
  is the only real-history source of more decided rows, but it does **not**
  surface `must_block` positives — those still must come from the
  constructed-adversarial stratum.

### 2026-W26 labels + score — first adjudicated real-history accuracy row

The W26 worksheet (10 rows: 6 evaluated + 4 `scan_failed`) is now labeled and
committed as [`results/2026-W26-mined.labels.csv`](results/2026-W26-mined.labels.csv).

- **Method disclosure.** Two **independent AI labelers** (separate contexts,
  no coordination; each fetched the real PR diffs) filled the worksheet per
  [LABELING.md](LABELING.md); a third pass adjudicated.
  **Disagreement rate: 0/10.** Both labelers independently flagged the same
  two rows as `needs_human` with the same reasoning (stripe/ai#338: a new
  auto-synced skill directing agents to install the Stripe CLI and load
  further skills; stripe/ai#312: the skill-sync supply chain rewired to an
  unauthenticated source with a daily cron and a dropped API-key
  requirement). Treat these as AI-generated labels pending human spot-check —
  the run notes exist so that caveat travels with the numbers.
- **Ground truth:** 8 `safe_to_merge`, 2 `needs_human`, 0 `must_block`
  (consistent with the W24–W26 base-rate finding above).
- **Score** (`python -m benchmark.miner score`):
  `needs_human_caught` **1.0** (2/2 — both authority-bearing changes held for
  a human), `benign_escalation_rate` **0.0** (no safe PR was
  blocked/review-routed), `ie_rate_on_safe` **0.5** (4/8 safe PRs returned
  `insufficient_evidence`), and the remaining 4/8 safe PRs are `unscored`
  (the goose `Duplicate action_surface action_id` crash above).
  `must_block_caught`/`blocked_recall` are **null** on this corpus — real
  history contributes no `must_block` rows; that proof stays with the
  constructed-adversarial stratum.
- **Reading:** on real agent-toolkit history the gate currently **never
  wrongly passes** and **never cleanly passes** — every safe PR it engaged
  ended in abstention or a crash. The abstentions are the config-bound /
  dynamic-toolkit gap (`docs/engineering/config-bound-capability-detection.md`);
  the crashes are the chipped fail-soft bug. Both are the active fixes this
  row exists to measure against.

### 2026-W24–W25 labels + score — corpus grown to 19 unique labeled PRs, first real `must_block`

W24 and W25 are now labeled and committed
([`results/2026-W24-mined.labels.csv`](results/2026-W24-mined.labels.csv),
[`results/2026-W25-mined.labels.csv`](results/2026-W25-mined.labels.csv)),
taking the real-history labeled corpus from 10 (W26) to **19 unique
engine-engaged PRs** — of which **15 carry a scored verdict and 4 are
`scan_failed`/`unscored`** (the W26 goose `action_id` crash, since fixed).
W24 adds 7 new labeled rows (its 6 stripe/ai rows are the same PRs already
labeled in W26 and reuse those labels); W25 adds 2. Label distribution:
14 `safe_to_merge`, 3 `needs_human`, 2 `must_block`.

- **Method.** Same protocol as W26: two independent AI labelers fetched the
  real diffs, then adjudication. **Disagreement: 0/9** on the 9 new PRs — both
  labelers independently reached the same label, including the two `must_block`
  calls below. AI-generated labels, pending human spot-check.
- **Ground truth (9 new):** 6 `safe_to_merge`, 1 `needs_human`
  (google/adk-samples#1975 — a new unpinned external Google-Maps-MCP tool mount
  with no `tool_filter`), and **2 `must_block` — the first real-history
  `must_block` rows across W24–W26:**
  - **stripe/ai#232** ("Migrate from API to MCP") removes the client-side
    `StripeAgentToolkit` least-privilege `actions`/`permissions` bounds and
    delegates all tool authority to a server-side key — the documented
    dynamic-toolkit anchor.
  - **crewAIInc/crewAI-examples#169** wires new external-comms/write authority
    into example flows: Slack `chat_postMessage`, Trello card creation, and a
    Gmail draft tool.
- **Score on the mining-era engine.** The gate **did not auto-pass** either
  `must_block` PR (`must_block_caught` 2/2 — no unsafe merge) but **abstained**
  on both (`insufficient_evidence`), so `blocked_recall` is **0/2**: it caught
  them as "can't tell", not "block". Caveat: W24's two `needs_human` rows
  (stripe/ai#338, #312) are `scan_failed` here (the since-fixed `action_id`
  crash) so they are `unscored`, not a real miss — the same two PRs score as
  `needs_human_caught` in W26 where they evaluated cleanly.
- **v0.15.0 moves the needle on #232 — quantified corpus-wide in the W27
  re-eval below.** stripe/ai#232 returns **`human_review_required`** on v0.15.0
  where the mining-era engine returned `insufficient_evidence`: it now
  **engages** the change and routes it to a human (`can_merge_without_human=false`)
  instead of abstaining. It is **not** a full `block` (real-history
  `blocked_recall` stays 0.0), and the driving finding is
  `SHIP-SCOPE-TOOLKIT-UNBOUNDED` — the scope check, **not** the new
  `SHIP-CAP-CONFIG-BINDING-*` config-binding checks (#256), which do not fire
  here. The full W27 re-eval on v0.15.0 (same 19 PRs, same SHAs; clears the 4
  `scan_failed` crashes too) quantifies this across the corpus — see the
  [W27 section](#2026-w27-re-eval--the-v0150-delta-on-the-labeled-corpus).
- **Reading.** Growing the corpus did what a corpus is for: it surfaced that
  real merged history *does* contain unsafe (`must_block`) changes, and that
  the mining-era gate **abstained rather than blocked** them — the config-bound
  gap, now measured on real PRs instead of assumed. The constructed stratum
  stays where `blocked_recall = 1.0` is proven; real history is where the
  abstention rate is measured, and it is not yet where blocked-recall is.

### 2026-W25 findings — diminishing returns from framework-core breadth

- **The base rate of capability-changing merged PRs is low, and now quantified.**
  Across all three runs — **8 distinct repos / 361 mined rows — 336 (93%)
  organically trigger-skip and 15 carry a scored verdict** (4 more are labeled
  but `scan_failed`; one week's "stripe/agent-toolkit" stratum redirects into
  stripe/ai — see the W26 repo note above). Labeling those later found **2 `must_block`** among the 19
  labeled rows (see the W24–W25 labels section above) — this sentence's original
  "none unsafe" reading predated the labels. The trigger noise
  bound is strongly validated on real history; but real-history mining is an
  *inefficient* source
  of decided cases, especially from framework **cores**: `langgraph` and
  `modelcontextprotocol/servers` produced **zero** decided rows (library-
  internals churn and TS-MCP sources the static extractor doesn't resolve).
- **The engine is robust across 3 new families.** Zero crashes / error rows
  over ADK + LangGraph + MCP-servers — the three mining-found fixes (#212
  symlink, #214 init source-quality, #215 capability_change) hold.
- **The 2 decided rows are both the dynamic-toolkit/MCP `insufficient_evidence`
  pattern** (`adk-samples#1975`, a Travel agent wired to a Google Maps **MCP**
  toolset: `cap_added=0`, `evidence_gaps=246`). Consistent with the original #1
  real-world gap — extraction *coverage*, failing safe, not a wrong verdict.
- **Implication for the accuracy corpus (P3):** do not chase decided
  *positives* by mining more framework cores. The labeled corpus composes three
  strata — mined-real history for the **negative** control (the 336
  trigger-skips) and IE/coverage cases; **constructed-adversarial** for
  **reliable blocked-recall** positives (already seeded: `samples/_anti_patterns`,
  `tests/fixtures/stripe_pr232`, `tests/test_verifier_scenarios.py`,
  `agent_weakens_gate`); and harness transcripts for real workflow-evidence
  replay. Real history has since yielded 2 `must_block` PRs on labeling (see the
  W24–W25 section), but the mining-era gate **abstained** on both — so
  **reliable** `blocked_recall = 1.0` still comes from the constructed stratum,
  not real history. Deeper-history mining of agent **application/example** repos
  is the only real-history source of more decided cases.

### 2026-W24 findings (read this before quoting the numbers)

- **Trigger noise bound holds on real history:** 108/121 PRs organically
  trigger-skip — the catalog stays quiet on the overwhelming majority of
  merged PRs. One precision miss the other way: a docs-translation PR
  (`openai-agents-python#3392`) triggered via a broad `diff_contains`
  rule and scanned despite `cap_added=0`.
- **First real extraction-coverage number: IE on decided = 3/7 (43%).**
  This is the headline KPI the roadmap said to measure instead of assume.
- **`blocked` rows are cold-start gate state, not per-PR receipts.** The
  scan path evaluates the *whole* head surface under a fresh default
  manifest, so a `blocked` row means "the repo's standing tool surface
  would not pass the default gate at that merge point" — see
  `openai-agents-python#3392` (`blocked`, but it's a docs PR;
  `cap_added=0`; the blocker is pre-existing surface). Treat rows as
  **labeled-corpus candidates**. Schema v0.2 adds exactly the missing
  half: the `verify_*` columns are the per-PR receipt (base-vs-head,
  new-findings gating), while `head_decision` remains the cold-start
  whole-surface state — read the pair together.
- **Ground truth reproduced:** `stripe/ai#232` (the 2026-06-01 pilot's
  silent least-privilege removal) round-trips through the cold-start
  path via the monorepo retry (`retry_at:tools/python`) and lands on the
  pilot's exact verdict: `insufficient_evidence`, 5 review items,
  4 evidence gaps.
- **The blocked flip, demonstrated on real history (v0.2):** on the real
  PR #232 trees, `verify` with a **directory-scoped** SDK source
  (`path: examples/openai/customer_support`) returns
  `merge_verdict: blocked` with blocker
  `SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED` and
  `can_merge_without_human: false` — the silent least-privilege removal
  is caught and the merge is refused. The cold-start v0.2 receipt stops
  at `insufficient_evidence` + `can_merge_without_human: false`
  (fails safe, names no risk) for one reason: `init` scopes the SDK
  source to a single entrypoint **file** (`…/customer_support/main.py`)
  while the bounded `StripeAgentToolkit(configuration=…)` constructor
  lives in the sibling `support_agent.py`. Init source scoping
  (directory, not file) is the gap between "fails safe" and "blocks the
  attack" on this case — tracked with the init source-quality fix.
- **Engine bug found by the v0.2 receipts (caveat on `verify_cap_*`):**
  verify's report-level `capability_change` marks every capability
  "broadened (unknown direction; schema_hash changed)" even on a
  docs-only diff, while the lock-diff artifact from the same run
  correctly says `unchanged` — two engine paths disagree on identical
  input. Until that fix lands, treat `verify_cap_modified` as inflated
  on rows with many tools (e.g. 103 on `openai-agents-python#3392`,
  a docs PR); `verify_verdict`/`verify_decision` are baseline-gated and
  unaffected in their gate semantics. Also fixed while building v0.2:
  without a base-tree baseline the receipt included pre-existing
  blockers (a docs PR scored a `blocked` receipt from standing
  surface) — receipts are now `--baseline`-gated; and the injected
  manifest is committed re-parented (`head''` onto `base'`) so it
  cannot fire the trust-root signal.
- **Two product bugs found by one mining session:** (1) a symlink loop in
  stripe/ai crashed `detect`/`init` cold-start with a traceback — fixed
  with this run (see `cli/discovery/artifacts.py`); (2) `init` at the
  stripe/ai root auto-detects a Cursor plugin `mcp.json` (an
  `mcpServers` config, not a tools export) as an MCP source, writing a
  manifest `scan` rejects (exit 3) — all six stripe/ai `scan_failed`
  rows are this one bug; tracked as a follow-up fix.

## Trust-model note

This is a **maintainer benchmark tool**, not product surface: it uses the
network (`gh`, `git clone`) and subprocesses, which the scanner under
`src/agents_shipgate/` never may (`tests/test_adapter_static_only.py` scopes
that invariant to `src/`). It adds no CLI command, no schema, and is not part
of the wheel.
