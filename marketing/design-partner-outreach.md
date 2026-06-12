# Design Partner Outreach Kit

Status: operating doc, 2026-06-10. Companion to
`docs/design-partners.md` (the public-facing page) and
`docs/design-partner-verifier-pilot.md` (the pilot runbook). This file is the
internal sourcing, messaging, and tracking side.

Weekly operating target: 5–10 personalized messages, ≤30 min/day total.
Pipeline goal: 2 new active pilots per month, 5 completed pilots by day 90.

## Who to contact

The engineer who **reviews AI-generated agent PRs** — usually a tech lead,
staff engineer, or founding engineer. Not the security lead (first
conversation drifts to compliance we don't sell). Not the founder, unless
the founder is that engineer.

Qualification (all four):
1. Weekly real AI-generated agent PRs (Claude Code / Codex / Cursor).
2. Agent tools with blast radius: refund, email, cancel, deploy, record
   writes, sensitive reads.
3. Willing to share a sanitized diff or run
   `agents-shipgate feedback export --redact`.
4. GitHub CI (Python-first repos preferred for adapter coverage).

Log-but-defer (future commercial leads, not pilots): teams whose first ask
is a hosted dashboard, SSO, or compliance certification.

## Sourcing recipes (fill the 30-target list from these)

1. **GitHub evidence search** — repos that are both agent-shaped and
   coding-agent-authored:
   - Commit search: `Co-Authored-By: Claude` / `Co-authored-by: Cursor` on
     repos that also match code search for `mcpServers`, `shipgate.yaml`
     competitors' configs, `from openai_agents import`, `langgraph`,
     `crewai`, `tools=[`.
   - PR search: `"add tool"` / `"new tool"` in PR titles on agent-framework
     repos with recent activity.
   - The person who merged those PRs is the contact.
2. **MCP ecosystem** — authors of MCP servers with consequential actions
   (payment, email, CRM, infra). They feel the scope/approval problem from
   the supplier side and often also ship agents.
3. **Agent-framework community surface** — people asking "how do you review
   agent permissions/tool access" in LangChain/LangGraph, OpenAI dev, and
   Anthropic dev forums/Discords. Answer first, outreach second.
4. **Build-in-public founders on X** — anyone posting screenshots of agents
   that send email / move money / touch prod, especially if they also post
   about Claude Code or Codex throughput.
5. **Founder network / warm intros** — highest conversion; send the warm
   template below to anyone one hop away from a team shipping agents.
6. **Pilot-derived referrals** — every completed pilot ends with: "which
   other team do you know reviewing AI agent PRs by hand?"

Personalization bar for every cold message: one sentence that proves we saw
*their* repo/post/PR. No spray.

## Cold outreach (X DM / email, ≤110 words)

> Hi {name} — saw {specific: your post about {X} / {repo}'s PR #{N} adding
> {tool}}. Quick question: when Claude Code or Codex opens a PR that changes
> what your agent can do — adds a tool, widens a scope, touches approval
> policy — what does your review actually catch?
>
> I'm building Agents Shipgate, an open-source deterministic verifier for
> exactly that diff. Static, local-first, no LLM calls. I'm running pilots
> with teams shipping tool-using agents: you bring one real AI-generated PR,
> we run the verifier on it together (~30 min), you keep the verdict +
> report, I keep the feedback.
>
> Worth a look? 5-min demo first if you prefer:
> `uvx agents-shipgate fixture run ai_generated_refund_pr`

## Warm intro (for the introducer to forward)

> {Founder name} is building Agents Shipgate (open source, Apache-2.0) — a
> deterministic CI check that reads AI-generated PRs and tells you whether
> the agent's tool permissions changed and whether it's safe to merge
> without human review. No LLM calls, runs locally. He's looking for a few
> teams shipping tool-using agents to run one real PR through it as a design
> partner — ~30 minutes, you keep the analysis. Given how much {team} ships
> with {Claude Code/Codex}, thought you two should talk.

## Follow-up (one only, +5–7 days)

> One-liner follow-up: if a bot commented on your next agent PR "this diff
> grants refund authority, no approval policy declared" — useful or noise?
> Either answer helps me. If noise, I'd genuinely like to know why.

## Discovery call — question list (30 min)

Opening (2 min): we're validating, not selling; the product is free; we want
to know if the verdict is right or wrong on your real PR.

1. Last week, how many PRs were written by a coding agent? How many touched
   the agent's tools, scopes, or policies?
2. Who reviews those PRs? How long does one take? What do you actually look
   at?
3. Tell me about the last time you noticed *after* merging that the agent's
   capabilities had changed. (Or the nearest miss.)
4. What's the most dangerous action your agent can take in production right
   now? Where is that list written down? Who can recite it?
5. How are tools registered — static declarations, or factories/dynamic
   construction? (Direct probe for `insufficient_evidence` risk; sets
   expectations before the pilot run.)
6. What required checks run in your CI today? How did the most recently
   added one get adopted?
7. If a PR comment said "this diff grants refund authority, missing approval
   policy" — who would see it, and would it change the merge decision?
8. (Close) If this tool disappeared tomorrow, when would you notice?

Qualification scoring: Q1 ≥ weekly agent PRs, Q4 has a real answer, Q5
understood, Q7 = "yes it would change the decision" → schedule the pilot on
the call. Two or more misses → thank, log, move on.

## Pilot mechanics (per `docs/design-partner-verifier-pilot.md`)

Every pilot produces three artifacts, no exceptions:
1. Redacted feedback export (`feedback export --redact`).
2. Verdict-quality judgment: right / wrong / insufficient — and why.
3. One benchmark-candidate row for `benchmark/results/`.

Success signals, ascending: unprompted second run → Action installed
(advisory) in a real repo → verdict cited in a real review thread → custom
policy/check request → cross-repo rollup request (**commercial signal: log
in the tracker, do not build**).

Roadmap discipline: a new adapter request enters the roadmap only when it
arrives attached to a real PR.

## Pipeline tracker

Keep private operational notes in `.agents-private/` (gitignored); this
table tracks stage only. Stages:
`sourced → contacted → replied → discovery → pilot-scheduled → pilot-run →
artifacts-received → retained / closed`.

| # | Team / contact | Source bucket | Stage | Last touch | Agent framework | Blast-radius tools | Next step | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | | | sourced | | | | | |
| 2 | | | sourced | | | | | |
| 3 | | | sourced | | | | | |
| 4 | | | sourced | | | | | |
| 5 | | | sourced | | | | | |
| 6 | | | sourced | | | | | |
| 7 | | | sourced | | | | | |
| 8 | | | sourced | | | | | |
| 9 | | | sourced | | | | | |
| 10 | | | sourced | | | | | |
| … | (fill to 30 from the sourcing recipes) | | | | | | | |

Weekly review (15 min, same day each week): count by stage; if `contacted →
replied` < 15%, the personalization sentence is too weak — fix the message,
not the volume; if `discovery → pilot` < 50%, qualification is too loose.
