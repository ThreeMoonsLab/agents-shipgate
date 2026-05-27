# Large multi-framework agent

A production-shape retail-ops AI assistant for exercising Agents Shipgate at
real scale. Most other samples are deliberately small (5–15 tools) so the
golden reports stay scannable. This sample is the opposite: it ships ~65 tools
across five tool sources to exercise the pipeline's merge, scope-coverage,
risk-enrichment, and release-decision paths under realistic load.

## What it scans

Five tool sources, all loaded statically:

| Source                                   | Adapter              | Tools | Risk shape                                                    |
| ---------------------------------------- | -------------------- | ----- | -------------------------------------------------------------- |
| [`specs/payments.openapi.yaml`](specs/payments.openapi.yaml)         | `openapi`            | 20    | Financial reads/writes; one catastrophic admin op (`terminate_account`). |
| [`specs/fulfillment.openapi.yaml`](specs/fulfillment.openapi.yaml)   | `openapi`            | 15    | Shipment reads/writes; reversible holds + destructive cancels. |
| [`mcp/crm-tools.json`](mcp/crm-tools.json)                           | `mcp`                | 15    | Customer comms (email/sms/in-app) + GDPR compliance ops.       |
| [`mcp/internal-tools.json`](mcp/internal-tools.json)                 | `mcp`                | 10    | Warehouse inventory reads/writes + admin (`drain_warehouse`).  |
| [`agents/ops_assistant.py`](agents/ops_assistant.py)                 | `openai_agents_sdk`  |  5    | SDK function tools: previews, computations, escalation.        |

## What it intentionally exercises

The manifest declares **partial** governance coverage so the scan surfaces a
realistic mix of findings rather than a clean pass:

- **Approval policy** covers 5 of the ~10 tools that earn approval-required risk
  tags. The other ~5 fire `SHIP-POLICY-APPROVAL-MISSING` at critical severity.
- **Confirmation policy** covers shipping cancellations and external comms but
  not subscription cancels or destructive customer-data ops.
- **Idempotency policy** covers `create_charge` / `create_refund` /
  `create_shipment` but not `internal.reserve_inventory` or
  `internal.adjust_inventory`.
- **`permissions.scopes`** lists ~18 scopes but is missing several
  destructive-admin scopes (e.g. `payments:customers:admin`,
  `crm:customers:admin`, `inventory:admin`) so `SHIP-AUTH-SCOPE-COVERAGE-MISSING`
  fires for each uncovered tool.
- **Severity override**: one `SHIP-DOC-INJECTION-RISK` downgrade with a reason
  exercises the `policy_audit.severity_overrides_applied` envelope.
- **Risk overrides**: three manual hints (two downgrades, one owner attribution)
  exercise the manual-risk path.
- **Suppressions**: two `SHIP-DOC-MISSING-DESCRIPTION` ignores exercise the
  `manifest_consistency` check at scale.

The release decision is **blocked** by ~10 critical findings; review items run
into the 70s. This is intentional — a clean sample wouldn't exercise the gate.

## Why no committed goldens

Most samples ship `expected/report.md` and `expected/report.json` so a golden
test catches rendering drift. This one **doesn't**, on purpose: the goal is to
exercise the pipeline at scale, not to pin every line of output. Pinning
50+ findings × 20+ report sections through every schema bump (the schema
moves several minor versions per release window) would be high-cost,
low-signal regression noise.

Instead, [`tests/test_large_sample.py`](../../tests/test_large_sample.py)
asserts the **structural** shape — decision, finding count band, key rule
firings — and enforces a **latency budget** so the gate stays fast on the CI
critical path.

## Running locally

```bash
agents-shipgate fixture run large_multi_framework_agent
# or, from a source checkout:
agents-shipgate scan -c samples/large_multi_framework_agent/shipgate.yaml
```

Typical wall-clock time on a 2024 laptop: 1–3 seconds. The test budget is
generous (≤ 10 s) so flaky CI runners don't false-alarm.
