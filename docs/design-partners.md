# Design Partners

Three Moons Lab is looking for early design partners who ship tool-using AI
agents — often with coding agents like Claude Code, Codex, or Cursor — and want
a deterministic merge gate on every AI-generated agent-capability change before
production-like permissions are granted.

## Good Fit

You are likely a good fit if your team:

- Ships agents that call tools through MCP, OpenAPI, OpenAI Agents SDK,
  Anthropic Messages API, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API
  artifacts, Codex plugin packages and marketplaces, or n8n workflows.
- Has tools that refund, email, cancel, deploy, modify records, read sensitive
  data, or change infrastructure.
- Wants advisory PR evidence before moving to stricter CI behavior.
- Can share sanitized findings, workflow constraints, or integration feedback
  with Three Moons Lab.

You are probably not a fit if you need a hosted policy engine, runtime gateway,
compliance certification, or private-data upload flow today. Agents Shipgate is
currently a local-first OSS scanner and GitHub Action.

## What You Get

Design partners get:

- Help mapping an existing agent repo to `shipgate.yaml`.
- A first Tool-Use Readiness Report for one agent or tool surface.
- Guidance on advisory CI, baselines, suppressions, and strict-mode rollout.
- Early influence on check semantics, report shape, framework adapters, and
  agent-facing workflows.

## What Three Moons Lab Asks For

Three Moons Lab asks for:

- A concrete agent/tool-surface use case.
- Feedback on whether the findings are actionable for platform, security, and
  release reviewers.
- Permission to use anonymized lessons in docs or category writing, only when
  explicitly approved.

## Contact

The fastest way to start: bring us one AI-generated PR that changes what your
agent can do, and we'll turn it into a deterministic merge verdict together.

Email `help@threemoonslab.com` with the subject `Agents Shipgate design partner
review`.

Include the agent framework, tool-source types, current CI system, and whether
you want a local CLI workflow, a GitHub Action workflow, or both.
