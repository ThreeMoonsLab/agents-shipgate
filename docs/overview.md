# Agents Shipgate Overview

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes — a local-first, static Tool-Use Readiness review. It reads
`shipgate.yaml` plus declared local tool sources and writes a Tool-Use
Readiness Report as Markdown, JSON, and SARIF.

Use it when an agent can call tools that refund, email, cancel, deploy, modify
records, read sensitive data, or change infrastructure. The scanner runs before
promotion, usually in pull-request CI, so release owners can review the tool
surface before production-like permissions are granted.

## When to use it

- Before promoting a tool-using AI agent to staging, production-like, or production environments.
- When adding or changing MCP tools, OpenAPI operations, SDK tool functions, scopes, policies, or prompts.
- When platform, security, or release reviewers need deterministic tool-use evidence.
- When a coding agent is adding a release gate to an existing repository.

## When not to use it

- Do not use it as a replacement for LLM evals.
- Do not use it as a runtime guardrail or LLM gateway.
- Do not treat it as a security audit or compliance certification.
- Do not expect it to verify model behavior, prompt quality, latency, or actual runtime routing.

## Supported inputs

- MCP exports
- OpenAPI 3.x specs
- OpenAI Agents SDK Python entrypoints
- Anthropic Messages API artifacts
- Google ADK Python and YAML config
- LangChain/LangGraph Python entrypoints
- CrewAI Python entrypoints
- n8n workflow JSON and source-control stubs
- Conductor OSS workflow JSON
- OpenAI API artifacts
- Codex repo config
- Codex plugin packages and marketplaces

## Core references

- [Quickstart](quickstart.md)
- [Concepts](concepts.md)
- [Manifest v0.1](manifest-v0.1.md)
- [Check catalog](checks.md)
- [Report schema v0.31](report-schema.v0.31.json) (current; v0.30 frozen at [report-schema.v0.30.json](report-schema.v0.30.json), v0.29 frozen at [report-schema.v0.29.json](report-schema.v0.29.json))
- [Evidence-backed `passed` contract](passed-verdict-contract.md)
- [Trust model](trust-model.md)
- [Agent instructions](../AGENTS.md)
