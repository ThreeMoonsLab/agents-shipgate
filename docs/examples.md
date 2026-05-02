# Examples

This page points to runnable fixtures and CI recipes that demonstrate how
Agents Shipgate turns an agent tool surface into release-review evidence.

## Runnable sample agents

The [`samples/`](../samples/) directory contains local fixtures that can be
scanned without network access. Start with:

```bash
agents-shipgate fixture run support_refund_agent
```

Useful fixtures:

- [`support_refund_agent`](../samples/support_refund_agent/) — production-like support/refund agent with MCP, OpenAPI, and SDK tool sources. Demonstrates critical approval and idempotency findings.
- [`clean_read_only_agent`](../samples/clean_read_only_agent/) — a low-risk read-only surface that should scan cleanly.
- [`simple_openai_api_agent`](../samples/simple_openai_api_agent/) — OpenAI Agents API artifacts including prompts, tools, structured outputs, tests, and traces.
- [`simple_anthropic_agent`](../samples/simple_anthropic_agent/) — Anthropic Messages API tool-use artifacts.
- [`google_adk_agent`](../samples/google_adk_agent/) — Google ADK Python and YAML config with eval references and explicit tool inventory.
- [`simple_langchain_agent`](../samples/simple_langchain_agent/) — static LangChain/LangGraph extraction.
- [`simple_crewai_agent`](../samples/simple_crewai_agent/) — static CrewAI extraction.
- [`multi_agent_workspace`](../samples/multi_agent_workspace/) — multiple manifests in one workspace.
- [`baseline_workflow`](../samples/baseline_workflow/) — adoption path from existing findings to strict mode.
- [`_anti_patterns`](../samples/_anti_patterns/) — intentionally invalid or unsafe shapes for testing errors and documentation.

## CI recipes

- [GitHub Actions recipes](../examples/github-actions/)
- [GitLab CI recipes](../examples/gitlab-ci/)
- [CircleCI recipes](../examples/circleci/)

## Example output

The canonical fixture writes:

- `agents-shipgate-reports/report.md`
- `agents-shipgate-reports/report.json`
- `agents-shipgate-reports/report.sarif` when requested or when using the GitHub Action

The JSON output is the stable contract for tools and coding agents. See
[report-schema.v0.7.json](report-schema.v0.7.json) (current; emitted reports
carry `report_schema_version: "0.7"`).
