# Design Partners

Three Moons Lab is looking for early design partners who are shipping
tool-using AI agents and want a repeatable release-readiness review before
production-like permissions are granted.

## Good Fit

You are likely a good fit if your team:

- Ships agents that call tools through MCP, OpenAPI, OpenAI Agents SDK,
  Anthropic Messages API, Google ADK, LangChain/LangGraph, CrewAI, or OpenAI
  Agents API artifacts.
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

Email `help@threemoonslab.com` with the subject `Agents Shipgate design partner
review`.

Include the agent framework, tool-source types, current CI system, and whether
you want a local CLI workflow, a GitHub Action workflow, or both.
