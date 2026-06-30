# Examples

This page points to runnable fixtures and CI recipes that demonstrate how
Agents Shipgate turns an agent tool surface into release-review evidence.

## Runnable sample agents

The [`samples/`](../samples/) directory contains local fixtures that can be
verified or scanned without network access. Start with the verify-native PR
demo:

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

It builds a temporary base/head git history, adds `stripe.create_refund`, and
writes `verifier.json`, `report.json`, and `pr-comment.md` with
`merge_verdict: blocked`.

For the lower-level static report path, run:

```bash
agents-shipgate fixture run support_refund_agent
```

Useful fixtures:

- [`ai_generated_refund_pr`](../samples/ai_generated_refund_pr/) — base/head verifier fixture for the blocked refund PR story.
- [`support_refund_agent`](../samples/support_refund_agent/) — production-like support/refund agent with MCP, OpenAPI, and SDK tool sources. Demonstrates critical approval and idempotency findings.
- [`clean_read_only_agent`](../samples/clean_read_only_agent/) — a low-risk read-only surface that should scan cleanly.
- [`simple_openai_api_agent`](../samples/simple_openai_api_agent/) — OpenAI API artifacts including prompts, tools, structured outputs, tests, and traces.
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

## Golden PR examples

- [Golden PR examples](../examples/golden-prs/) show the advisory loop a
  reviewer or coding agent should imitate: initial risky tool surface, commands
  run, release decision summary, top findings, safe patch boundary, and PR
  comment shape.

## Example output

The verify-native fixture writes:

- `agents-shipgate-reports/verifier.json`
- `agents-shipgate-reports/pr-comment.md`
- `agents-shipgate-reports/report.json`

The static scan fixtures write:

- `agents-shipgate-reports/report.md`
- `agents-shipgate-reports/report.json`
- `agents-shipgate-reports/report.sarif` when requested or when using the GitHub Action

The JSON output is the stable contract for tools and coding agents. See
[report-schema.v0.28.json](report-schema.v0.28.json) (current; emitted reports
carry `report_schema_version: "0.28"`, moving policy-pack routing metadata to
`findings[].policy_routing`; v0.27 is frozen at
[report-schema.v0.27.json](report-schema.v0.27.json)).
