# Agents Shipgate — Go-to-Market Strategy

Status: plan of record, 2026-06-10
Owner: Three Moons Lab
Review cadence: every 30 days against the decision gates in § 11

The single organizing judgment: **the current GTM bottleneck is evidence, not
distribution.** The first real design-partner pilot returned
`insufficient_evidence`; the adoption benchmark CSV holds only a header row.
Amplifying traffic before the cold-start funnel reliably produces a meaningful
verdict would spend the one-time launch channels (Show HN, first impressions)
on a broken step 5. The first 90 days exist to convert "deterministic merge
gate" from a thesis into something that happened on five real teams' PRs.

## 1. Positioning

| Expression | Verdict | Use |
|---|---|---|
| The deterministic merge gate for AI-generated agent capability changes | **Primary** (canonical tagline) | tagline, repo, website |
| CI preflight check for AI agents | Explanatory phrase | first-touch explanations; not a category name |
| Tool-Use Readiness | Technical wedge name | docs, check catalog, artifact names |
| Agent Release Readiness | Category name — **soon** | category content after 3+ real cases |
| Agent deployment **safety** gate | **Avoid** | overclaims; `docs/category.md` "What It Is Not" is the copy compliance list |
| Agent healthcare infrastructure | **Not yet** | internal vision / fundraising narrative only |

Hook sentence (keep): *"Your coding agent changed what your AI agent can do —
Agents Shipgate tells you whether it can merge."*

Trust differentiators that must appear on every surface: static, local-first,
deterministic, no LLM calls, no network, no telemetry. This is the structural
contrast with every "AI reviews your AI" product: the gate itself cannot be
talked out of its decision.

## 2. ICP

**Primary (now):** teams that (a) ship PRs heavily authored by Claude Code /
Codex / Cursor AND (b) ship tool-using agents with consequential tools
(refund, email, deploy, record writes, sensitive reads). Typical shape: the
AI-product group inside a 10–50-person engineering org, or founding engineers
at an AI-native startup. GitHub + Python first (adapter coverage).

**Key persona:** the senior engineer / tech lead who reviews AI-generated
agent PRs. Pain: review fatigue plus the invisible capability delta — a
3-line diff that grants a production permission reads like any other diff.

**Secondary (sequenced later):** solo builders (reach, not revenue), platform
/ DevEx teams (org-level buyer entry point, *soon*), security & governance
reviewers (champions, *later* — they don't install CLIs; keep their fields in
the report), enterprise AI teams (*not yet* — their pull drags the roadmap to
SSO/dashboards prematurely).

Buyer/champion structure: today user = champion = the same engineer.
Commercialization moves the buyer up to platform/security leads. Sequence is
engineer-love first, lead-visibility second — never the reverse.

## 3. Motion

**Now: design-partner-led engine + open-source/developer-led surface.**

- Design partners are the only channel that produces truth: verdict quality
  on real repos, the real causes of `insufficient_evidence`, and the evidence
  library (case stories, benchmark rows). The pilot runbook
  (`docs/design-partner-verifier-pilot.md`) and `feedback export --redact`
  already exist; the missing input is people in the pipeline.
- The OSS surface (repo, README, fixture, Action) is credibility
  infrastructure for outreach, not yet a growth engine. Investment = funnel
  fixes, not stars.
- Content-led: *soon* — evidence-driven content after 2–3 pilot findings. One
  positioning post at launch is the exception.
- Enterprise-led: *not yet*; see § 8 triggers. Resist inbound gravity.

Why not pure OSS-led now: OSS-led assumes the tool self-explains, the pain is
ubiquitous, and first-run delights. The third assumption fails while
`insufficient_evidence` is common on real repos, and the category is new
enough that "capability delta is a thing you review" itself needs teaching.

## 4. Distribution (priority order)

| Channel | Priority | Action | Success metric |
|---|---|---|---|
| GitHub repo | P0 | PR-comment moneyshot above the fold; fixture one-liner above the fold (done 2026-06-10) | cold user → meaningful verdict < 10 min |
| Direct outreach + founder network | P0 | 5–10 targeted messages/week; "bring one PR" pilot offer | 2 new pilots/month |
| Package-channel hygiene | P0 | release fan-out checklist every tag; `uvx` first in docs | cold-install version == latest |
| Agent-native distribution (GEO) | P0–P1 | `llms.txt`, `/shipgate` skill, managed AGENTS.md blocks; "ask Claude Code to add Shipgate" as a first-class install path | agent-completed inits |
| GitHub Action Marketplace | P1 | listing copy + tag sync (hygiene only) | marketplace-sourced installs |
| X / Twitter | P1 | build-in-public, ≤2 originals/week, zero hype | 1–2 posts/month producing real inbound |
| MCP / agent dev communities | P1 | helpful presence, fixture one-liner when relevant; no ads | community-sourced pilot candidates |
| Hacker News (Show HN) | P1, **gated** | one shot; preconditions in `marketing/launch-kit.md` | ≥50 fixture runs day-of; ≥3 inbound pilot leads in 2 weeks |
| LinkedIn | P2 | existing draft after screenshot ready; buyer-side brand layer | none hard |
| Reddit / Dev.to / SEO | P2–P3 | secondary distribution of HN/launch content; category keyword squatting long-game | 6-month organic |

## 5. Funnel

discover → understand → install → first scan → **useful report** → Action →
share → design partner.

Step 5 is the critical step: steps 1–4 losses are marketing problems; step 5
loss is a product problem that poisons step 1 (reputation). North-star funnel
metric: **time-to-first-meaningful-verdict < 10 minutes**, where meaningful =
a non-`insufficient_evidence` verdict, or `insufficient_evidence` carrying a
concrete, executable next action.

Built-in viral surface: `pr-comment.md` lands in front of the whole team on
every PR. Its quality is a distribution investment, not a cosmetic one.

Cold-start QA discipline: before any amplification event, two engineers
outside the project walk the funnel from zero while we record friction.
(First internal run: `marketing/cold-start-funnel-test-2026-06-10.md`.)

## 6. Design partners

Target: 10–50-person teams, consequential tools, heavy coding-agent usage,
GitHub CI, Python-first. Contact the engineer who reviews agent PRs — not the
security lead (pulls toward compliance), not the founder (unless they are that
engineer).

Pitch = their own PR: "bring one AI-generated PR that changes what your agent
can do; we turn it into a deterministic merge verdict together" (existing
`docs/design-partners.md` language; ~30 minutes).

Qualification: weekly real AI-generated agent PRs; tools with blast radius;
will share sanitized diff or run `feedback export --redact`; GitHub CI.
Disqualify-for-now: "interested in agent safety" without a PR stream; first
ask is hosted dashboard / compliance certs (log as later-commercial lead).

Success signals, ascending: unprompted second run → Action installed in a
real repo (advisory) → verdict cited in a real review thread → asks for
custom policy/check → asks for cross-repo rollup (**commercial signal — log,
don't build**).

Per-pilot outputs (all three, every time): redacted feedback artifact;
verdict-quality judgment (right / wrong / insufficient + why); one benchmark
candidate row. Rule: **a new adapter request enters the roadmap only when it
arrives attached to a real PR** — GTM feedback must not reopen the
adapter-sprawl direction that strategy already closed.

Outreach copy and the discovery question list: `marketing/design-partner-outreach.md`.

## 7. Content

Principle: evidence before opinion. Pre-pilot, ship only the launch post and
one category-clarity piece; after pilots, switch to evidence-led content.

| Piece | Audience | When |
|---|---|---|
| "Your coding agent just gave your AI agent refund powers" (`marketing/launch-blog-post.md`) | primary ICP | launch |
| "Agent readiness is not the same as evals" | AI infra engineers | +2–4 weeks; SEO category anchor |
| "We ran a deterministic verifier on N real AI-generated agent PRs" | everyone | 60–90 days, post-pilots; the credibility piece |
| "A CI preflight check for tool-using agents" (how-to) | DevOps/CI owners | soon; SEO long-tail |
| "Why deterministic? The trust model" | deep-technical | soon; the moat narrative, public version |
| "From CI/CD to Agent Release Readiness" (category manifesto) | category/investor | later, only with evidence behind it |

GEO is the differentiated channel: target users ask their coding agent before
they ask Google. Keep `llms.txt`, `llms-full.txt`, `docs/ai-search-summary.md`,
glossary, and the checks catalog updated with every content release; every
entry must be a self-contained answer an agent can retrieve.

Copy red lines: never prevent/guarantee/secure/compliant; always
review/surface/evidence/verdict/deterministic.

## 8. Commercialization

Free forever (OSS): CLI, all checks, GitHub Action, full verifier/report
output, single-repo everything, custom policies. Charging for the gate kills
adoption and contradicts the local-first trust story.

Paid later, by willingness-to-pay likelihood: (1) org-level dashboard — every
agent repo's capability surface and verdict history in one view; (2) history
and trends (audit narrative entry); (3) curated policy packs (schema already
exists; maintained packs are a subscription-shaped service); (4) approval
workflow / audit-grade exports (enterprise pack, last).

WTP triggers all appear only in org context: >3 repos; security asks "show me
every agent capability across the org"; verdict-history retention requests.
Single-user/single-repo never paying is the design, not a failure.

Start commercialization only when ALL of: ≥3 teams producing weekly verdicts
on real repos for 4 consecutive weeks; ≥2 unprompted org-feature requests;
wedge validation green (§ 10). This matches the standing decision: commercial
held until the v0.9 "Merge Verifier" proof.

Fundraising: the 90-day evidence (pilot cases + benchmark rows + one "blocked
a real PR" story) is the seed narrative. Raising before that is the weakest
story at the worst terms.

## 9. Metrics

North star: **weekly verified real PRs** — real (non-fixture)
agent-capability PRs receiving a merge verdict.

| Layer | Metrics | Nature |
|---|---|---|
| Awareness | stars, site visits, post reach | vanity — channel-efficiency signal only, never reported as traction |
| Activation | cold-install success rate, fixture runs, time-to-first-meaningful-verdict, Actions installed in real repos | real |
| Engagement | repos with weekly repeat verifier runs, Actions alive ≥4 weeks, issues carrying real PRs | real |
| Validation | `insufficient_evidence` rate on real repos (quality metric, must trend down); pilots completing the loop; redacted feedback artifacts received; "verdict changed a merge decision" cases; unprompted org requests; real benchmark rows | decides the day-90 direction |

Discriminator: any number a user produces without committing their own real
repo/PR is vanity; any number that requires it is validation.

## 10. 30 / 60 / 90

**Days 1–30 — fix the funnel, start the pipeline.**
Engineering preconditions: `insufficient_evidence` P1 fix (config-bound
removal / dynamic-factory detection); every dead-end error carries an
executable next action (CLI hints shipped 2026-06-10); package-channel
freshness verified (PyPI 0.12.0 confirmed current 2026-06-10); first real
benchmark rows. GTM: README moneyshot (done); 30-team target list; 5–10
outreach messages/week → 2 active pilots; launch post finalized; LinkedIn
post out once screenshot exists; cold-start test with 2 outside engineers.
**Do not launch on the calendar; launch on the gate.**

**Days 31–60 — public launch + content cadence.**
Show HN (only after the gating checklist passes) + X thread + Reddit
secondary. Second content piece. Agent-native install path promoted on
website. 3–4 active pilots, each producing the three artifacts. MCP/agent
community presence begins.

**Days 61–90 — validation close-out + direction decision.**
Evidence content published. Pilot exit interviews: did a verdict change a
merge decision? when would you notice if it vanished? would you pay for the
org view? Score against § 11.

## 11. Day-90 decision gates

- **Scale:** ≥3 retained teams + ≥1 real blocked/changed-decision case + ≥2
  unprompted org requests → prototype org dashboard; open fundraising
  conversations.
- **Refine:** partial → narrow the wedge to whichever check class gets cited
  most; run 60 more days; do NOT widen the roadmap.
- **Rethink:** pilots completed but nobody retained → the wedge hypothesis is
  wrong; back to discovery. Do not mask a product signal with more channel
  spend.

## 12. Risks and anti-patterns

1. Amplifying a broken funnel (the #1 risk; HN is non-renewable).
2. Category language ahead of evidence ("agent healthcare" externally).
3. GTM feedback reopening adapter sprawl — real PR or no roadmap entry.
4. Security/compliance overclaim — invites red-team scrutiny and procurement
   processes we cannot serve.
5. Premature enterprise gravity — response template: "we're a local-first OSS
   verifier today, which is exactly why you can trust it; org features are on
   the roadmap — want to be a design partner?"
6. Stars counted as traction.
7. Founder time fragmentation — two active channels max (outreach + X);
   everything else is secondary distribution.
8. Pilots drifting into consulting — runbook commands only; extras go to the
   roadmap, not the call.
