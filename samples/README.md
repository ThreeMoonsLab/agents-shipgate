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

## Replayable incident shapes

Three PR-shaped demos map public incident shapes to fresh verifier output:

```bash
./shipgate fixture run agent_weakens_gate
./shipgate fixture run governed_edits_governance
./shipgate fixture run prompt_change_rides_release
```

Those commands run from this checkout, and two of the three need it: the
newest published release, `v0.15.0`, bundles `agent_weakens_gate` and not the
other two. Once a release carries all three, pin that release —
`uvx agents-shipgate@<version> fixture run <name>` — rather than naming a
version the index does not have.

The second command is an explicit expected-fail for the unshipped
`.github/agents/**` governance surface. It prints both the desired and observed
verdict and links the owning RFC; it is never silently skipped. See the
[`docs/incidents/` suite](../docs/incidents/README.md) for the public sources,
one-page write-ups, and incident-response article template.

## What the support-refund fixture demonstrates

`support_refund_agent` is the static-report fixture, and it fails on purpose.
The committed golden at
[`support_refund_agent/expected/report.md`](support_refund_agent/expected/report.md)
is the authoritative output — it is regenerated with the engine, so it never
drifts from what a run prints. The release risks it is built to surface:

- `stripe.create_refund` lacks a declared approval policy, so a financial action could ship without an explicit human review gate.
- `stripe.create_refund.amount` lacks a maximum bound, weakening blast-radius control.
- `stripe.create_refund` lacks idempotency evidence while retry behavior is known, risking duplicate refunds.
- `wildcard_mcp_tools.*` exposes a wildcard tool surface, making review incomplete.
- `gmail.send_customer_email` overlaps a prohibited external-communication action without a matching confirmation policy.

Human-facing output groups by *subject* — the tool you would open — with
severity as an attribute of each row. `report.json` keeps the flat per-finding
record that automation consumes.

## Sample reports

These golden reports are committed so you can inspect the output shape without
running a scan first:

| Sample | Markdown | JSON |
| --- | --- | --- |
| [`support_refund_agent`](support_refund_agent/) | [`report.md`](support_refund_agent/expected/report.md) | [`report.json`](support_refund_agent/expected/report.json) |
| [`simple_openai_api_agent`](simple_openai_api_agent/) | [`report.md`](simple_openai_api_agent/expected/report.md) | [`report.json`](simple_openai_api_agent/expected/report.json) |
| [`simple_langchain_agent`](simple_langchain_agent/) | [`report.md`](simple_langchain_agent/expected/report.md) | [`report.json`](simple_langchain_agent/expected/report.json) |
| [`conductor_agent`](conductor_agent/) | [`report.md`](conductor_agent/expected/report.md) | [`report.json`](conductor_agent/expected/report.json) |
| [`google_adk_cold_start_agent`](google_adk_cold_start_agent/) | [`report.md`](google_adk_cold_start_agent/expected/report.md) ([cold first contact](google_adk_cold_start_agent/expected/cold-report.md)) | [`report.json`](google_adk_cold_start_agent/expected/report.json) |
| [`declaration_repair_agent`](declaration_repair_agent/) | [`report.md`](declaration_repair_agent/expected/report.md) | [`report.json`](declaration_repair_agent/expected/report.json) |

Two samples stop with **open declaration questions**, so they are the two that
ship `suggested-declarations.yaml` — the questionnaire an adopter is told to
edit — as a byte-compared golden. They render the two halves of that file:

- [`google_adk_cold_start_agent`](google_adk_cold_start_agent/expected/suggested-declarations.yaml)
  is the cold start: mostly **blanks**, plus one pre-filled proposal and one
  challenged authority row printed as a note. The fixture exists to pin the
  order the questions are asked in. Read the diff before regenerating it: a
  change in block *order* is a change in which question this tool asks a human
  first. Its own [README](google_adk_cold_start_agent/README.md) documents the
  [cold-report regeneration procedure](google_adk_cold_start_agent/README.md#regenerating-the-cold-report).
- [`declaration_repair_agent`](declaration_repair_agent/expected/suggested-declarations.yaml)
  is the step after, and adds the one shape the cold start has no example of: a
  declaration challenged in the **effect** dimension, whose repair is a
  `risk_tags:` block. Read the diff on those values — that value is the remedy
  this tool hands an adopter, and pasting both blocks back must take the sample
  from `insufficient_evidence` to `passed` (#424).

Each sample's own README says what its actions are there to pin.

The `support_refund_agent` fixture also includes the Release Evidence Packet at
[`packet.md`](support_refund_agent/expected/packet.md),
[`packet.json`](support_refund_agent/expected/packet.json), and
[`packet.html`](support_refund_agent/expected/packet.html).

`conductor_agent` also ships the control pointer a scan publishes,
[`current-control.json`](conductor_agent/expected/current-control.json). It
hash-binds the report artifacts beside it, which constrains the order in which
the goldens are regenerated: run the scan, apply the `<REPO>` substitution that
replaces the generating checkout's path in `report.json`, and only *then*
rebind the pointer. Hashing before the substitution records a file that is
never committed and a `size_bytes` that moves with the contributor's path
length, so `agents-shipgate` refuses its own sample with `artifact_mismatch`.
Because `current_control_id` covers the artifact refs, rebinding means
recomputing that identity too — never editing a digest in place.
`tests/test_reports.py::test_sample_current_control_pointers_bind_the_committed_artifacts`
enforces this, and also runs `read_current_control()` over each fixture so a
sample cannot satisfy the digests while the product still refuses it.

Because those digests describe exact bytes, `.gitattributes` pins everything
under `samples/*/expected/` with `-text`. Without it a checkout that translates
line endings — the Git for Windows default — rewrites `report.json` and breaks
the pointer for the reader, not merely for the test.

## Fixtures

| Sample | Purpose |
| --- | --- |
| [`ai_generated_refund_pr`](ai_generated_refund_pr/) | Verify-native base/head PR fixture for the blocked refund capability story. |
| [`agent_weakens_gate`](agent_weakens_gate/) | Trust-root demo: the head commit deletes the Shipgate CI gate and the verifier blocks the merge. |
| [`support_refund_agent`](support_refund_agent/) | Production-like support/refund agent with MCP, OpenAPI, and SDK tools. |
| [`openai_agents_sdk_agent`](openai_agents_sdk_agent/) | OpenAI Agents SDK static extraction paired with a reviewed bound-tool inventory. |
| [`clean_read_only_agent`](clean_read_only_agent/) | Low-risk read-only fixture for clean scans. |
| [`simple_openai_api_agent`](simple_openai_api_agent/) | OpenAI Agents API artifacts: prompts, tools, schemas, tests, traces. |
| [`conductor_agent`](conductor_agent/) | Conductor OSS workflow JSON with static and dynamic MCP call surfaces. |
| [`simple_anthropic_agent`](simple_anthropic_agent/) | Anthropic Messages API tool-use artifacts. |
| [`google_adk_agent`](google_adk_agent/) | Google ADK Python and YAML config. |
| [`google_adk_cold_start_agent`](google_adk_cold_start_agent/) | The cold-start ADK sibling: stops at `insufficient_evidence` with ten open declaration questions, and ships the generated questionnaire as a golden. |
| [`declaration_repair_agent`](declaration_repair_agent/) | The repair sibling: everything declared and controlled, held at `insufficient_evidence` by two challenged effect declarations. Pasting the two published blocks back reaches `passed` (#424). |
| [`hitl_evidence_agent`](hitl_evidence_agent/) | HITL validation evidence gaps for limited auto-approval review posture. |
| [`hitl_evidence_covered_agent`](hitl_evidence_covered_agent/) | HITL validation evidence with local provenance for limited auto-approval review posture. |
| [`simple_langchain_agent`](simple_langchain_agent/) | LangChain/LangGraph static Python extraction plus a reviewed tool inventory. |
| [`simple_crewai_agent`](simple_crewai_agent/) | CrewAI static Python extraction plus a reviewed inventory; ambient `FileReadTool` authority remains a review concern. |
| [`multi_agent_workspace`](multi_agent_workspace/) | Multiple manifests in one workspace. |
| [`baseline_workflow`](baseline_workflow/) | Baseline adoption before strict CI. |
| [`large_multi_framework_agent`](large_multi_framework_agent/) | Production-shape retail-ops agent with ~65 unique tools across 6 declared sources, including a reviewed SDK inventory. Exercises the pipeline at scale and pins the CI latency budget. No committed goldens — see the per-sample README. |
| [`mcp_only_server`](mcp_only_server/) | An MCP server that commits its surface as a `tools/list` export. |
| [`mcp_source_only_server`](mcp_source_only_server/) | The same server with no export: its tools exist only as TypeScript registration sites, which is the normal state of a vendor MCP server. Detected identically by the CLI and the zero-install script (#485). |
| [`_anti_patterns`](_anti_patterns/) | Intentionally unsafe or invalid examples for tests and docs. |

## Direct scans

```bash
agents-shipgate scan --config samples/support_refund_agent/shipgate.yaml
agents-shipgate scan --config samples/openai_agents_sdk_agent/shipgate.yaml
agents-shipgate scan --config samples/clean_read_only_agent/shipgate.yaml
agents-shipgate scan --config samples/simple_openai_api_agent/shipgate.yaml
```
