# Large multi-framework agent

A production-shape retail-ops AI assistant for exercising Agents Shipgate at
real scale. Most other samples are deliberately small (5–15 tools) so the
golden reports stay scannable. This sample is the opposite: it ships ~65 tools
across six declared tool sources to exercise the pipeline's merge, scope-coverage,
risk-enrichment, and release-decision paths under realistic load.

## What it scans

Six tool sources, all loaded statically. The reviewed inventory overlaps the
five SDK functions intentionally: it supplies reviewed interface and semantic
facts while the Python source exercises conservative AST extraction. Reviewed
tool declarations do not establish the application's constructor identity or
prove which tools its agent can reach.

| Source                                   | Adapter              | Tools | Risk shape                                                    |
| ---------------------------------------- | -------------------- | ----- | -------------------------------------------------------------- |
| [`specs/payments.openapi.yaml`](specs/payments.openapi.yaml)         | `openapi`            | 20    | Financial reads/writes; one catastrophic admin op (`terminate_account`). |
| [`specs/fulfillment.openapi.yaml`](specs/fulfillment.openapi.yaml)   | `openapi`            | 15    | Shipment reads/writes; reversible holds + destructive cancels. |
| [`mcp/crm-tools.json`](mcp/crm-tools.json)                           | `mcp`                | 15    | Customer comms (email/sms/in-app) + GDPR compliance ops.       |
| [`mcp/internal-tools.json`](mcp/internal-tools.json)                 | `mcp`                | 10    | Warehouse inventory reads/writes + admin (`drain_warehouse`).  |
| [`agents/ops_assistant.py`](agents/ops_assistant.py)                 | `openai_agents_sdk`  |  5    | SDK function tools: previews, computations, escalation.        |
| [`inventories/ops-sdk-tools.json`](inventories/ops-sdk-tools.json)  | `mcp`                |  5    | Reviewed declarations for the same SDK tool candidates; binding still requires source evidence. |

## What it intentionally exercises

The manifest declares partial governance coverage. It includes approval,
confirmation and idempotency policies, permission scopes, severity/risk
overrides, and suppressions so the pipeline can inspect these declarations
alongside the catalog and binding evidence.

The current Python read cannot establish constructor-namespace ownership
through the anonymous generator at `agents/ops_assistant.py:40`.
The graph contains **five possible SDK tools and zero established reachable
tools**. The reviewed inventory cannot clear that source-level uncertainty.
Catalog entries without established binding do not create approval or scope
coverage findings; unused declared scopes can still be reported. Interface
findings for possible tools carry their unknown binding status.

The release decision is **insufficient_evidence**. This sample exercises a
large catalog, deterministic merge receipts, audit metadata and conservative
binding limits; it does not demonstrate a complete application binding or a
blocked runtime capability.

## Why no committed goldens

Most samples ship `expected/report.md` and `expected/report.json` so a golden
test catches rendering drift. This one **doesn't**, on purpose: the goal is to
exercise the pipeline at scale, not to pin every line of output. Pinning
every finding and report section through output evolution would require
updates unrelated to this sample's binding and scale guarantees.

Instead, [`tests/test_large_sample.py`](../../tests/test_large_sample.py)
asserts the **structural** shape — decision, possible binding identities,
source uncertainty, finding count bands and audit metadata — and enforces a **latency budget** so the gate stays fast on the CI
critical path.

## Running locally

```bash
agents-shipgate fixture run large_multi_framework_agent
# or, from a source checkout:
agents-shipgate scan -c samples/large_multi_framework_agent/shipgate.yaml
```

Typical wall-clock time on a 2024 laptop: 1–3 seconds. The test budget is
generous (≤ 10 s) so flaky CI runners don't false-alarm.
