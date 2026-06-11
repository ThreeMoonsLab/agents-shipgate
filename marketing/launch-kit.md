# Launch Kit — Show HN, X thread, and the go/no-go gate

Status: ready-to-ship drafts, 2026-06-10. Nothing in this file gets posted
until every box in the gating checklist is checked. HN first impressions are
non-renewable; the checklist exists so we spend them on a working funnel.

## Go / no-go gating checklist

Launch when ALL of these hold — launch on the gate, not the calendar:

- [ ] **Cold-start test passed by two engineers outside the project.** From
      zero to a meaningful verdict in under 10 minutes, friction log
      reviewed. (Internal dry run passed 2026-06-10:
      `marketing/cold-start-funnel-test-2026-06-10.md` — uvx → blocked
      verdict in ~5 seconds. The two outside-engineer runs are still
      required; we are blind to our own assumed knowledge.)
- [ ] **`insufficient_evidence` P1 fix landed** (config-bound removal /
      dynamic-toolkit-factory detection from the first pilot), so the most
      common real-repo shape doesn't dead-end on launch day.
- [ ] **Every CLI dead-end prints an executable next action** in human mode.
      (Shipped 2026-06-10: scan/doctor/verify config errors and the
      CHANGE_ME placeholder path now print `next:` hints.)
- [ ] **README moneyshot live**: PR-comment verdict above the fold. (Shipped
      2026-06-10.)
- [ ] **PyPI / Action tag freshness verified** for the current release
      (0.12.0 confirmed current on PyPI 2026-06-10; re-verify on launch day
      against the release fan-out checklist in `docs/distribution.md`).
- [ ] **Launch blog post published** on the site
      (`marketing/launch-blog-post.md`) so HN links to our page, not a bare
      repo.
- [ ] **Founder available for 6–8 hours** after submission to answer every
      comment. An unanswered HN thread is a wasted HN thread.

## Show HN submission

**Title** (≤80 chars, no hype, names the mechanism):

> Show HN: A deterministic merge gate for AI-generated agent capability changes

Fallback title if that reads too abstract on the day:

> Show HN: CI check that catches when a coding agent gives your AI agent new powers

**URL:** the launch blog post (preferred) or the GitHub repo.

**First comment (post immediately after submitting):**

> Author here. The problem this solves: coding agents (Claude Code, Codex,
> Cursor) now write a lot of agent code, and every so often a PR quietly
> changes what the *runtime* agent is allowed to do — adds a tool, widens a
> scope, touches an approval policy. As a code change it looks fine; as a
> permission change nobody reviewed it.
>
> Shipgate is an open-source CLI + GitHub Action that reads that diff
> statically and posts a merge verdict (`mergeable` /
> `human_review_required` / `insufficient_evidence` / `blocked`) as a PR
> comment. Design constraints that drove everything:
>
> - Deterministic: no LLM in the loop, so the gate can't be prompted or
>   reward-hacked out of its decision — including by the coding agent whose
>   PR it's judging.
> - Static and local: no agent execution, no network, no telemetry. It reads
>   files. The allowed exceptions are pinned in a test.
> - Honest about limits: if the tool surface is built dynamically and static
>   evidence is weak, it says `insufficient_evidence` and tells you what to
>   add — it doesn't guess. It's not a runtime guardrail and not a
>   compliance cert.
>
> Try the exact "coding agent adds stripe.create_refund" demo with zero
> setup: `uvx agents-shipgate fixture run ai_generated_refund_pr`
>
> Genuinely interested in: what's your current release gate for agent
> capability changes, if any?

**Prepared answers** (draft now, adapt in thread):

- *"Why not have an LLM review the diff?"* → A reviewer that can be
  persuaded is a suggestion, not a gate. The judging surface must be outside
  the model's influence loop, especially when the PR author IS a model.
  LLM review is complementary (semantics); the gate is deterministic
  (authority).
- *"Static analysis can't see dynamically-built toolkits."* → Correct, and
  that's the honest part: those repos get `insufficient_evidence` plus the
  exact evidence to add (manifest declaration, MCP export), not a fake
  green. Our first design-partner pilot hit exactly this; detection of
  config-bound dynamic factories is the current engineering focus.
- *"Isn't this just a linter?"* → A linter flags style/correctness inside
  the code. This reads the *capability delta* between base and head and
  applies release policy to it — closer to `terraform plan` for agent
  authority than to a linter.
- *"Who's behind it / how does it make money?"* → Apache-2.0, local-first
  forever for single repos. If teams want a cross-repo capability view
  later, that's the natural paid layer. Today we want design partners, not
  checkout pages.

## X / Twitter launch thread

Post 1:

> Claude Code opened a PR. It adds `stripe.create_refund` to a support
> agent's toolset. The diff is 4 lines. Tests pass. Review says LGTM.
>
> Nothing in the pipeline knows the agent just gained the power to move
> money.
>
> We built an open-source, deterministic merge gate for exactly this. 🧵

Post 2:

> Code review answers "is this code correct?"
> Evals answer "does the agent usually behave?"
>
> Neither answers: "what can the agent DO after this merges that it
> couldn't before — and who approved that?"
>
> That's a permission review. Agent tool surfaces just never had one.

Post 3 (attach: screenshot of the rendered PR-comment section from the
README — the real artifact, not a mockup):

> Shipgate reads the capability delta statically — no agent execution, no
> LLM calls, no network — and posts the verdict on the PR:
> blocked / human_review_required / insufficient_evidence / mergeable.
>
> Same diff, same verdict, every run. A gate you can't prompt-inject.

Post 4:

> Try the exact refund-PR demo, zero install:
>
> uvx agents-shipgate fixture run ai_generated_refund_pr
>
> 3 lines of YAML to run it advisory on every PR. Apache-2.0, no telemetry.
> https://github.com/ThreeMoonsLab/agents-shipgate

Post 5 (the ask):

> Shipping tool-using agents with real blast radius (refunds, emails,
> deploys)? We're running design-partner pilots: bring one AI-generated PR,
> we turn it into a deterministic merge verdict together, ~30 min.
> help@threemoonslab.com

## Reddit secondary (post 2–3 days after HN, not same-day)

Subreddits: r/LocalLLaMA, r/MachineLearning (weekend thread), agent-focused
subs. Lead with the question, not the product: "How do you review PRs where
a coding agent changed your agent's tool permissions?" — share the fixture
one-liner in the body, repo link once, answer everything.

## Day-of operating notes

- Submit Show HN 8–10am ET on a Tue/Wed/Thu; avoid US holidays.
- X thread goes out after the HN post has its first comment, linking the HN
  discussion ("discussion on HN: …") — don't split the audience early.
- Track in a plain text log: fixture runs (PyPI download delta), repo
  traffic, inbound emails, pilot leads. These numbers feed the day-90 gates
  in `marketing/gtm-strategy.md` § 11.
- Do not ship a release tag on launch day. Freeze the surface 48h before.
