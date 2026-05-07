# Samples

Runnable fixtures for Agents Shipgate. They are safe to inspect and scan
locally; the scanner does not run agents, call tools, invoke LLMs, connect to
MCP servers, or make scanner network calls by default.

## Recommended first run

```bash
agents-shipgate fixture run support_refund_agent
```

This produces a Tool-Use Readiness Report with 18 findings, including critical
approval and idempotency findings on `stripe.create_refund`.

## Fixtures

| Sample | Purpose |
| --- | --- |
| [`support_refund_agent`](support_refund_agent/) | Production-like support/refund agent with MCP, OpenAPI, and SDK tools. |
| [`clean_read_only_agent`](clean_read_only_agent/) | Low-risk read-only fixture for clean scans. |
| [`simple_openai_api_agent`](simple_openai_api_agent/) | OpenAI Agents API artifacts: prompts, tools, schemas, tests, traces. |
| [`simple_anthropic_agent`](simple_anthropic_agent/) | Anthropic Messages API tool-use artifacts. |
| [`google_adk_agent`](google_adk_agent/) | Google ADK Python and YAML config. |
| [`hitl_evidence_agent`](hitl_evidence_agent/) | HITL validation evidence gaps for limited auto-approval review posture. |
| [`simple_langchain_agent`](simple_langchain_agent/) | LangChain/LangGraph static Python extraction. |
| [`simple_crewai_agent`](simple_crewai_agent/) | CrewAI static Python extraction. |
| [`multi_agent_workspace`](multi_agent_workspace/) | Multiple manifests in one workspace. |
| [`baseline_workflow`](baseline_workflow/) | Baseline adoption before strict CI. |
| [`_anti_patterns`](_anti_patterns/) | Intentionally unsafe or invalid examples for tests and docs. |

## Direct scans

```bash
agents-shipgate scan --config samples/support_refund_agent/shipgate.yaml
agents-shipgate scan --config samples/clean_read_only_agent/shipgate.yaml
agents-shipgate scan --config samples/simple_openai_api_agent/shipgate.yaml
```
