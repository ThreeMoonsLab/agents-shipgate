# Samples

Runnable fixtures for Agents Shipgate. They are safe to inspect, verify, and
scan locally; the verifier does not run agents, call tools, invoke LLMs,
connect to MCP servers, or make network calls by default.

## Recommended first run

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

This builds a temporary base/head git history where the head commit adds
`stripe.create_refund`, then writes `verifier.json`, `report.json`, and
`pr-comment.md` with `merge_verdict: blocked`.

For the lower-level static report fixture:

```bash
agents-shipgate fixture run support_refund_agent
```

## Sample reports

These golden reports are committed so you can inspect the output shape without
running a scan first:

| Sample | Markdown | JSON |
| --- | --- | --- |
| [`support_refund_agent`](support_refund_agent/) | [`report.md`](support_refund_agent/expected/report.md) | [`report.json`](support_refund_agent/expected/report.json) |
| [`simple_openai_api_agent`](simple_openai_api_agent/) | [`report.md`](simple_openai_api_agent/expected/report.md) | [`report.json`](simple_openai_api_agent/expected/report.json) |
| [`simple_langchain_agent`](simple_langchain_agent/) | [`report.md`](simple_langchain_agent/expected/report.md) | [`report.json`](simple_langchain_agent/expected/report.json) |

The `support_refund_agent` fixture also includes the Release Evidence Packet at
[`packet.md`](support_refund_agent/expected/packet.md),
[`packet.json`](support_refund_agent/expected/packet.json), and
[`packet.html`](support_refund_agent/expected/packet.html).

## Fixtures

| Sample | Purpose |
| --- | --- |
| [`ai_generated_refund_pr`](ai_generated_refund_pr/) | Verify-native base/head PR fixture for the blocked refund capability story. |
| [`agent_weakens_gate`](agent_weakens_gate/) | Trust-root demo: the head commit deletes the Shipgate CI gate and the verifier blocks the merge. |
| [`support_refund_agent`](support_refund_agent/) | Production-like support/refund agent with MCP, OpenAPI, and SDK tools. |
| [`openai_agents_sdk_agent`](openai_agents_sdk_agent/) | OpenAI Agents SDK static extraction from a directory of Python tools. |
| [`clean_read_only_agent`](clean_read_only_agent/) | Low-risk read-only fixture for clean scans. |
| [`simple_openai_api_agent`](simple_openai_api_agent/) | OpenAI Agents API artifacts: prompts, tools, schemas, tests, traces. |
| [`simple_anthropic_agent`](simple_anthropic_agent/) | Anthropic Messages API tool-use artifacts. |
| [`google_adk_agent`](google_adk_agent/) | Google ADK Python and YAML config. |
| [`hitl_evidence_agent`](hitl_evidence_agent/) | HITL validation evidence gaps for limited auto-approval review posture. |
| [`hitl_evidence_covered_agent`](hitl_evidence_covered_agent/) | HITL validation evidence with local provenance for limited auto-approval review posture. |
| [`simple_langchain_agent`](simple_langchain_agent/) | LangChain/LangGraph static Python extraction. |
| [`simple_crewai_agent`](simple_crewai_agent/) | CrewAI static Python extraction. |
| [`multi_agent_workspace`](multi_agent_workspace/) | Multiple manifests in one workspace. |
| [`baseline_workflow`](baseline_workflow/) | Baseline adoption before strict CI. |
| [`large_multi_framework_agent`](large_multi_framework_agent/) | Production-shape retail-ops agent with ~65 tools across 5 sources. Exercises the pipeline at scale and pins the CI latency budget. No committed goldens — see the per-sample README. |
| [`_anti_patterns`](_anti_patterns/) | Intentionally unsafe or invalid examples for tests and docs. |

## Direct scans

```bash
agents-shipgate scan --config samples/support_refund_agent/shipgate.yaml
agents-shipgate scan --config samples/openai_agents_sdk_agent/shipgate.yaml
agents-shipgate scan --config samples/clean_read_only_agent/shipgate.yaml
agents-shipgate scan --config samples/simple_openai_api_agent/shipgate.yaml
```
