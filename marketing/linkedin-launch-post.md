# LinkedIn Launch Post — Agents Shipgate v0.12.0

**Title (if publishing as a LinkedIn Article, or as a bold first line):**
The Release Gate Your AI Agent Doesn't Have

---

## The Post

Your AI agent can refund $50,000.

Code review can't see that.
Eval suites can't see that.
Observability *will* see it — after it happens.

That's the gap Agents Shipgate fills.

And it's wider now than it was a year ago: when Claude Code, Codex, or Cursor writes the PR, the line that grants refund authority arrives faster than anyone's review attention for it.

Drop Shipgate into your CI as a GitHub Action and every PR that changes what your agent can do gets a deterministic merge verdict, posted as a PR comment: mergeable, human_review_required, insufficient_evidence, or blocked — with the capability delta spelled out (which tools, which scopes, which approval policies are missing).

No agent execution. No LLM calls. No network access. Same diff, same verdict, every run — a gate the PR's author can't talk its way past, even when the author is a coding agent.

Add it to your workflow in three lines:

```yaml
  - uses: ThreeMoonsLab/agents-shipgate@v0.12.0
    with:
      config: shipgate.yaml
```

v0.12.0 is live. Open source. Free. No telemetry, no account. Try the blocked-refund-PR demo with zero install: `uvx agents-shipgate fixture run ai_generated_refund_pr`

→ https://github.com/ThreeMoonsLab/agents-shipgate

#AIAgents #MLOps #DevTools #OpenSource #AISafety

---

## Suggested First Comment (drives algorithm + clicks)

If you'd rather see what it catches before wiring it up: the bundled demo replays a coding-agent PR that adds stripe.create_refund to a support agent, and shows the blocked verdict plus the exact "required before merge" list — missing approval policy, missing idempotency evidence, wildcard scopes:

uvx agents-shipgate fixture run ai_generated_refund_pr

https://github.com/ThreeMoonsLab/agents-shipgate

Would love to hear from anyone shipping production agents — what's your current release gate? (Comments, not DMs — others want to learn from this too.)

---

## Recommended visual

Posts with media get ~2x reach on LinkedIn. The two strongest options:

1. A screenshot of the **PR comment** the Action posts — the capability-change table plus the "Required before merge" list. Two honest sources, in preference order: (a) a real PR comment from any repo running the Action (even an internal one, redacted); (b) the rendered "What your PR sees" section now at the top of the README, which is the verbatim (abridged) fixture artifact — screenshot it rendered on GitHub, and caption it as the bundled demo PR.
2. A short clip / GIF of `uvx agents-shipgate fixture run ai_generated_refund_pr` going from empty terminal to `Merge verdict: blocked` in a few seconds — works as fallback and demos the zero-install path.

If you don't have a #1 yet, use #1(b) — it exists as of 2026-06-10. The GitHub Action pitch lands much harder when people can *see* the PR comment.

---

## Notes for the repost

When you repost and add the founder story, the natural beats are:
- The moment you realized code review + evals + observability still left a gap
- Why "static, manifest-first, no execution" was the design constraint (trust model)
- Whichever specific finding from the support_refund fixture made you go "yep, this is the one"

Keep the original post's structure — your repost adds the human layer the launch post intentionally leaves out.
