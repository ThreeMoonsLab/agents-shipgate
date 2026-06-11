# Your coding agent just gave your AI agent refund powers

Status: launch draft, 2026-06-10. Publish on the Three Moons Lab blog first,
then submit as Show HN (see `marketing/launch-kit.md` for the gating
checklist — do not publish before the checklist passes).

Target audience: engineers who review AI-generated PRs on repos that ship
tool-using agents. CTA: run the fixture; bring us one real PR.

---

Here is a pull request a coding agent opened on a support-agent repo. The
description says it implements the ticket: *"support agents should be able to
issue refunds for orders under the auto-approval threshold."*

```diff
 TOOLS = [
     lookup_order,
     summarize_ticket,
+    stripe.create_refund,
 ]
```

Four lines of context, one line of change. Tests pass. The linter is quiet.
Code review says LGTM — because as a *code change*, it is fine. There is no
bug in this diff.

What the diff doesn't say is that your support agent can now move money.

## Code review sees lines. It doesn't see capability.

Every review tool in the standard pipeline answers some version of "is this
code correct?" None of them answer the question this PR actually raises:

> What can the agent do after this merges that it could not do before — and
> did anyone with authority decide that's acceptable?

That question has a name in traditional release engineering: a permission
change. If this PR had added an IAM role with `payments:write`, your infra
review would have caught it, because infrastructure-as-code made permission
deltas reviewable artifacts.

Agent tool surfaces have no equivalent. The "permission" lives in an
argument list, an MCP config, an OpenAPI spec, or a toolkit factory — places
code review reads right past. And the volume problem is new: when Claude
Code, Codex, or Cursor writes most of the PRs, the capability delta arrives
faster than any human's attention for it.

Evals don't close this gap either. Evals measure behavior distributions —
*does the agent usually do the right thing?* A capability review asks a
different and prior question: *what is the agent allowed to do at all, and
under what policy?* You need both. Only one of them exists in most pipelines
today.

## What a deterministic capability review looks like

We built Agents Shipgate to review exactly this diff shape. It is an
open-source CLI and GitHub Action that reads the PR's capability delta from
static evidence — manifests, MCP and OpenAPI artifacts, SDK metadata — and
issues a merge verdict. On the PR above, the Action posts:

> ### Agents Shipgate result: block
>
> Decision: `block` · Risk: `critical` · Required reviewers: `agent-platform`, `security`
>
> | Impact | Change | Subject | Why |
> |---|---|---|---|
> | blocks release | action added | `stripe.create_refund` | Capability added. |
> | blocks release | action broadened | `stripe.create_refund` | high-risk effect financial_action added |
> | blocks release | scope broadened | `stripe.create_refund:stripe:*` | scope added |
>
> **Required before merge** — Actor: Human (human authority required — a
> coding agent must not self-resolve):
> 1. Declare an approval policy for `stripe.create_refund` or remove this
>    tool from the release.
> 2. Declare `approval.required`, `safeguards.audit_log`, and
>    `safeguards.idempotency` for this financial write action.
> 3. Replace wildcard/admin scopes with operation-specific scopes.

Three properties matter more than the verdict itself:

**It's deterministic.** Same diff, same verdict, every run. No LLM is in the
loop, which means the gate cannot be persuaded, prompted, or reward-hacked
out of its decision — including by the coding agent whose PR it is judging.
A verifier that can be argued with is a suggestion, not a gate.

**It's static and local.** No agent execution, no tool calls, no network
access, no telemetry. It reads files. That's the whole trust model, and it's
auditable in the test suite.

**It separates machine-fixable from human-required.** The verdict tells the
coding agent what it may mechanically fix (a missing idempotency declaration)
and what it must not self-resolve (the decision that a support agent should
hold refund authority at all). That line — between what an agent can fix and
what requires human authority — is the actual safety boundary in AI-assisted
development, and most pipelines don't draw it anywhere.

## What it is not

No overclaims: Shipgate is not a runtime guardrail, not an eval framework,
not an observability platform, and not a compliance certification. It reviews
static release evidence at PR time, before the agent gets production-like
permissions. If the capability is constructed in ways static analysis cannot
see, Shipgate says `insufficient_evidence` and tells you what evidence to
add — it does not guess.

## Try the exact PR above

The blocked-refund PR is a bundled fixture. One command, no install, no
setup; it builds a temporary git history, runs the verifier on the diff, and
writes the verdict, report, and PR comment you saw above:

```bash
uvx agents-shipgate fixture run ai_generated_refund_pr
```

Adding the gate to a repo is three lines of workflow YAML, advisory by
default:

```yaml
- uses: ThreeMoonsLab/agents-shipgate@v0.12.0
  with:
    config: shipgate.yaml
```

Apache-2.0. Static by default. GitHub: https://github.com/ThreeMoonsLab/agents-shipgate

## Bring us one PR

We're running design-partner pilots with teams that ship tool-using agents:
you bring one real AI-generated PR that changes what your agent can do, we
run the verifier on it together (~30 minutes), you keep the analysis, we keep
the feedback. The fastest email to send:
`help@threemoonslab.com`, subject "Agents Shipgate design partner review".
