---
description: Run the weekly GTM operating loop — metrics snapshot, channel hygiene, design-partner sourcing, pipeline review, launch-gate status. Drafts only; never publishes.
---

Arguments: `$ARGUMENTS`

You are running the weekly GTM operating loop for Agents Shipgate. The plan
of record is `marketing/gtm-strategy.md`; the launch gate is
`marketing/launch-kit.md`; the outreach kit and pipeline tracker are
`marketing/design-partner-outreach.md`. Read all three before acting.

**Hard rules (do not skip):**

- **Never publish or send anything.** No posts, no emails, no DMs, no PR/issue
  comments on external repos. Every outward-facing artifact you produce is a
  draft the founder sends manually.
- Distinguish vanity from validation metrics exactly as
  `marketing/gtm-strategy.md` § 9 defines them. Never describe stars or views
  as traction.
- New adapter requests found anywhere enter notes as "needs real PR attached"
  — never as roadmap recommendations.
- If `$ARGUMENTS` contains `metrics`, run only step 1. If it contains
  `sourcing`, run only step 3. If it contains `gate`, run only step 5.
  Otherwise run all steps in order.

## Step 1 — Metrics snapshot

Collect, then append ONE row to `marketing/metrics-log.csv` (keep the header
schema; never rewrite past rows):

```bash
gh api repos/ThreeMoonsLab/agents-shipgate -q '{stars: .stargazers_count, forks: .forks_count}'
gh api repos/ThreeMoonsLab/agents-shipgate/traffic/views -q '{views: .count, unique: .uniques}'
gh api repos/ThreeMoonsLab/agents-shipgate/traffic/clones -q '{clones: .count, unique: .uniques}'
curl -s https://pypistats.org/api/packages/agents-shipgate/recent
gh api repos/ThreeMoonsLab/agents-shipgate/releases/latest -q .tag_name
curl -s https://pypi.org/pypi/agents-shipgate/json | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
```

The validation columns (`active_pilots`, `pilots_completed`,
`real_pr_verdicts_7d`, `unprompted_org_requests_total`) cannot be fetched —
read the pipeline tracker for the current counts and ask the founder to
confirm them in your final report. Flag week-over-week deltas worth
attention (e.g., PyPI 7d downloads ±50%, first nonzero validation metric).

## Step 2 — Channel hygiene

Run the external-surface checks from `docs/distribution.md` (release
fan-out): PyPI version == latest git tag; GitHub Marketplace shows latest
tag; `https://threemoonslab.com/llms.txt` resolves and names the current
version; `/.well-known/agents-shipgate.json` resolves. Report PASS/FAIL per
surface. A FAIL here is a P0 — put it at the top of the report.

## Step 3 — Design-partner sourcing

Goal: propose 5–10 NEW qualified candidates for the pipeline tracker, using
the sourcing recipes in `marketing/design-partner-outreach.md`. Useful
searches (adapt freely):

```bash
gh search commits --committer-date=">$(date -v-14d +%Y-%m-%d)" "Co-Authored-By: Claude" --limit 30
gh search code "mcpServers" --language json --limit 30
gh search code "from agents import Agent" --language python --limit 30
gh search repos "ai agent tools" --updated ">$(date -v-30d +%Y-%m-%d)" --limit 20
```

For each candidate, record: repo/org, evidence of coding-agent authorship,
evidence of consequential tools (refund/email/deploy/data-write), and the
likely contact (who merges agent PRs). Score against the four qualification
criteria. Then draft (do not send) one personalized cold message per
qualified candidate using the kit's template — the personalization sentence
must cite the specific evidence you found.

Append the qualified candidates as new `sourced` rows in the tracker table
in `marketing/design-partner-outreach.md`.

## Step 4 — Pipeline review

Read the tracker. Flag: rows with no `Last touch` in 7+ days (overdue
follow-up — draft it), `contacted` rows older than 7 days with no reply
(one follow-up max, then close), `discovery` rows without a scheduled
pilot. Compute stage-conversion counts and compare against the kit's
thresholds (reply rate <15% → message problem; discovery→pilot <50% →
qualification problem).

## Step 5 — Launch-gate status

Read the go/no-go checklist in `marketing/launch-kit.md`. For each unchecked
box, state what concretely remains and whether evidence now exists to check
it (cite files/PRs/releases). Do NOT check boxes yourself unless the
evidence is in the repo (e.g., a merged PR); founder-judgment boxes
(outside-engineer cold-start runs) only get a status note.

## Step 6 — Report

End with one compact report in this order: (1) channel-hygiene failures if
any; (2) validation metrics + deltas; (3) pipeline state + overdue actions;
(4) new sourced candidates + drafted messages; (5) launch-gate distance;
(6) the single highest-leverage GTM action for the coming week, with your
reasoning in two sentences. Vanity metrics go last, one line.

If any step fails for missing credentials (e.g., `gh` traffic API needs
push access), report the gap and continue — never fake a number.
