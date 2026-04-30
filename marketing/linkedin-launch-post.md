# LinkedIn Launch Post — Agents Shipgate v0.5.1

**Title (if publishing as a LinkedIn Article, or as a bold first line):**
The Release Gate Your AI Agent Doesn't Have

---

## The Post

Your AI agent can refund $50,000.

Code review can't see that.
Eval suites can't see that.
Observability *will* see it — after it happens.

That's the gap Agents Shipgate fills.

Drop it into your CI as a GitHub Action and every PR gets a deterministic release-readiness report on the agent's tool surface — MCP, OpenAPI, OpenAI Agents SDK, Anthropic Messages API, Google ADK, LangChain/LangGraph, CrewAI.

No agent execution. No LLM calls. No network access. Just the question every release should answer:

For every action this agent can take in production, do we have explicit approval policies, scope coverage, and idempotency evidence?

Add it to your workflow in three lines:

```yaml
  - uses: ThreeMoonsLab/agents-shipgate@v0.5.1
    with:
      config: shipgate.yaml
```

v0.5.1 is live. Open source. Free. No telemetry, no account. Local CLI also available: `pipx install agents-shipgate`.

→ https://github.com/ThreeMoonsLab/agents-shipgate

#AIAgents #MLOps #DevTools #OpenSource #AISafety

---

## Suggested First Comment (drives algorithm + clicks)

If you'd rather see what it catches before wiring it up, this fixture demonstrates the kind of release risks it surfaces — missing approval policies on financial actions, wildcard tool exposure, idempotency gaps:

https://github.com/ThreeMoonsLab/agents-shipgate/tree/main/samples/support_refund_agent

Would love to hear from anyone shipping production agents — what's your current release gate? (Comments, not DMs — others want to learn from this too.)

---

## Recommended visual

Posts with media get ~2x reach on LinkedIn. The two strongest options:

1. A screenshot of the **PR comment** the Action posts — shows "Status: Release blockers detected · Critical: 2 · High: 14" with the top findings list. This is the moneyshot because it shows the product *in the developer's actual workflow*, which matches the "GitHub Action is the pitch" framing.
2. A short clip / GIF of `agents-shipgate scan` running locally and the report.md opening — works as fallback if no PR comment screenshot is handy.

If you don't have a #1 yet, I'd hold the post until you do. The GitHub Action pitch lands much harder when people can *see* the PR comment.

---

## Notes for the repost

When you repost and add the founder story, the natural beats are:
- The moment you realized code review + evals + observability still left a gap
- Why "static, manifest-first, no execution" was the design constraint (trust model)
- Whichever specific finding from the support_refund fixture made you go "yep, this is the one"

Keep the original post's structure — your repost adds the human layer the launch post intentionally leaves out.
