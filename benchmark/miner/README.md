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
- Labeling for the accuracy corpus happens in a separate adjudicated file next
  to the CSV (`<run>.labels.csv`: `pr_url,label,rationale`) — the mined row is
  evidence, the label is the ground truth. The rubric, two-labeler process, and
  metrics are in [`LABELING.md`](LABELING.md). Generate the turnkey worksheet
  with `python -m benchmark.miner labels` (a ready copy for the current run is
  committed as
  [`2026-W24-mined.labels.template.csv`](results/2026-W24-mined.labels.template.csv)),
  then `python -m benchmark.miner score --results <jsonl> --labels <csv>`
  prints the confusion matrix + headline accuracy metrics.

| Run | Date | Repos | Rows | Notes |
|---|---|---|---|---|
| [`2026-W24-mined.csv`](results/2026-W24-mined.csv) | 2026-06-12 | stripe/ai, openai/openai-agents-python, crewAIInc/crewAI-examples | 121 (latest 40 merged PRs each + stripe/ai#232) | Schema v0.2 (re-run with baseline-gated `verify_*` receipts; supersedes the v0.1 artifact in place). Findings below. |
| [`2026-W25-mined.csv`](results/2026-W25-mined.csv) | 2026-06-12 | google/adk-samples, langchain-ai/langgraph, modelcontextprotocol/servers | 120 (latest 40 merged PRs each) | Widen run over 3 new framework families. Schema v0.2. Findings below. |

### 2026-W25 findings — diminishing returns from framework-core breadth

- **The base rate of capability-changing merged PRs is low, and now quantified.**
  Across both runs — **6 repos / 241 merged PRs — 226 (93%) organically
  trigger-skip and only 9 are decided.** The trigger noise bound is strongly
  validated on real history; but real-history mining is an *inefficient* source
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
  *positives* by mining more framework cores. The labeled corpus should compose
  three strata — mined-real for the **negative** control (the 226 trigger-skips)
  and IE/coverage cases; **constructed-adversarial** for the `must_block`
  positives (already seeded: `samples/_anti_patterns`,
  `tests/fixtures/stripe_pr232`, `tests/test_verifier_scenarios.py`,
  `agent_weakens_gate`); and harness transcripts. Deeper-history mining of
  agent **application/example** repos is the only real-history source of more
  decided cases.

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
