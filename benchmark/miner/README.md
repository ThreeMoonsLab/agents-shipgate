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
- Labeling for the accuracy corpus happens in a separate file next to the CSV
  (`<run>.labels.csv`: `pr_url,label,rationale`), two labels per case with
  adjudication — the mined row is evidence, the label is the ground truth.

| Run | Date | Repos | Rows | Notes |
|---|---|---|---|---|
| [`2026-W24-mined.csv`](results/2026-W24-mined.csv) | 2026-06-12 | stripe/ai, openai/openai-agents-python, crewAIInc/crewAI-examples | 121 (latest 40 merged PRs each + stripe/ai#232) | First real run; findings below. |

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
  **labeled-corpus candidates**; true per-PR receipts need the verify
  path (base-vs-head, new-findings-only) — that is miner v0.2.
- **Ground truth reproduced:** `stripe/ai#232` (the 2026-06-01 pilot's
  silent least-privilege removal) round-trips through the cold-start
  path via the monorepo retry (`retry_at:tools/python`) and lands on the
  pilot's exact verdict: `insufficient_evidence`, 5 review items,
  4 evidence gaps.
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
