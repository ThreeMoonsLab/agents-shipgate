# Design Partners

Three Moons Lab is looking for early design partners who ship tool-using AI
agents — often with coding agents like Claude Code, Codex, or Cursor — and want
a deterministic merge gate on every AI-generated agent-capability change before
production-like permissions are granted.

## Good Fit

You are likely a good fit if your team:

- Ships agents that call tools through MCP, OpenAPI, OpenAI Agents SDK,
  Anthropic Messages API, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API
  artifacts, Codex repo config, Codex plugin packages and marketplaces, n8n
  workflows, or Conductor OSS workflow JSON.
- Has tools that refund, email, cancel, deploy, modify records, read sensitive
  data, or change infrastructure.
- Wants advisory PR evidence before moving to stricter CI behavior.
- Can share sanitized findings, workflow constraints, or integration feedback
  with Three Moons Lab.

You are probably not a fit if you need a hosted policy engine, runtime gateway,
compliance certification, or private-data upload flow today. Agents Shipgate is
currently a local-first OSS verifier and GitHub Action.

## Verifier Pilot

The current pilot asks each design partner for one real agent-capability
change and one reviewer who did not write it. Which commands you run depends
on the repository: a repo that declares what its coding agents may do needs no
manifest, and a repo that publishes its own tool surface runs the full verifier
loop. Either way the pilot is measuring two things — whether the reviewer can
name the changed capability, its evidence, the coverage limit and the next
action, and whether the team reaches for it again on the next eligible change.

Use the [`Design Partner Verifier Pilot`](design-partner-verifier-pilot.md)
runbook for the routes, commands, artifact read order, tracker fields, consent
rules, and follow-up questions. Results so far — including the denominators
that are still zero — are published in
[`Design Partner Pilot: Results`](design-partner-pilot-results.md).

## What You Get

Design partners get:

- A capability-level review of one AI-generated agent PR or sanitized patch.
- `verifier.json` and `pr-comment.md` wired into the repo's advisory workflow.
- A map of what the coding agent may fix mechanically vs. what requires human
  authority.
- A trust-root review: whether the PR could weaken the gate that reviews it.
- Guidance from advisory verifier comments toward blocker-only or strict
  `can_merge_without_human` CI.

## What Three Moons Lab Asks For

Three Moons Lab asks for:

- A concrete PR link, sanitized patch, or representative diff from Claude Code,
  Codex, Cursor, or similar tooling.
- Feedback on whether the capability change, merge verdict, `fix_task`, and
  `control.next_action` are actionable for platform, security, and release
  reviewers.
- When possible, a redacted feedback artifact:

  ```bash
  agents-shipgate feedback export \
    --from agents-shipgate-reports/verifier.json \
    --redact \
    --out shipgate-feedback.json
  ```
- Permission to use anonymized lessons in docs or category writing, only when
  explicitly approved.

## Contact

The fastest way to start: bring us one AI-generated PR that changes what your
agent can do, and we'll turn it into a deterministic merge verdict together.

Email `help@threemoonslab.com` with the subject `Agents Shipgate design partner
review`.

Include the PR/diff, agent framework, tool-source types, whether the PR adds
tools or changes policy/CI, and what your reviewer needs to know before merge.
