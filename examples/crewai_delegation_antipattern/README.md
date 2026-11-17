# CrewAI Multi-Agent Delegation & Tool-Failure Anti-Pattern

This adoption example demonstrates a common production reliability challenge in CrewAI multi-agent architectures: **unbounded agent-to-agent delegation combined with unguarded tool exceptions**, leading to mutual delegation loops, compounding prompt context, and unbounded execution duration.

It provides the anti-pattern configuration, the hardened Shipgate-aligned solution, and an offline test suite.

**Target Version**: `crewai==0.80.0` (see [`requirements.txt`](requirements.txt))

---

## The Problem: Delegation Cascades & Tool Failures

In multi-agent frameworks like CrewAI, enabling delegation (`allow_delegation=True`) allows agents to route sub-tasks to other agents when unexpected conditions arise.

When a tool function raises a raw Python exception (such as `HTTP 502 Bad Gateway`, `ConnectionError`, or `json.JSONDecodeError`):
1. **Raw Exception Propagation**: The tool raises an unhandled exception instead of returning a structured diagnostic payload.
2. **Re-Delegation Cascade**: An agent encountering an unhandled failure may interpret it as a delegable task blockage and transfer control to a peer agent.
3. **Mutual Retry Loop**: If the peer agent re-invokes the failing tool, mutual delegation cycles can occur. While CrewAI documents a default `max_iter` of 20, agents without an explicit `max_execution_time` (which defaults to `None`) continue executing redundant model calls and retrying failing endpoints until iteration exhaustion.
4. **Context Accumulation**: Each delegation turn appends prior conversational turns, error messages, and reasoning traces, inflating prompt context and latency on subsequent calls.

---

## Anti-Pattern vs. Hardened Pattern Comparison

| Dimension | Anti-Pattern (`unbounded_crew.py`) | Hardened Pattern (`hardened_crew.py`) |
| --- | --- | --- |
| **Agent Delegation** | `allow_delegation=True` allowing peer handoffs on failure | `allow_delegation=False` enforcing single-responsibility flow |
| **Iteration Limits** | Unspecified `max_iter` (relies on framework default of 20) | Explicit tight circuit breaker: `max_iter=5` |
| **Wall-Clock Limits** | Missing `max_execution_time` (defaults to `None`) | Explicit hard deadline: `max_execution_time=60` |
| **Tool Error Contract** | Raises raw `RuntimeError("HTTP 502")` | Catches exceptions and returns structured JSON `{"status": "error", "error_code": "..."}` |
| **Shipgate Inventory Evidence** | Undeclared tool source without manifest configuration | Discovered by `shipgate.yaml`: enumerates `safe_fetch_vendor_invoice` and records agent count in `report.tool_inventory` |

> **Note on Fallback Classes**: The lightweight fallback definitions in `unbounded_crew.py` and `hardened_crew.py` provide structural attribute models for static analysis and offline testing without requiring a live CrewAI installation. They capture configuration attributes (`allow_delegation`, `max_iter`, `max_execution_time`) and do not execute the live CrewAI delegation loop.

---

## File Overview

- [`requirements.txt`](requirements.txt): Pins `crewai==0.80.0`.
- [`unbounded_crew.py`](unbounded_crew.py): Demonstrates unbounded delegation configuration with raw tool exceptions and unconstrained execution time.
- [`hardened_crew.py`](hardened_crew.py): Demonstrates explicit circuit breakers (`max_iter=5`, `max_execution_time=60`) and structured error contracts.
- [`shipgate.yaml`](shipgate.yaml): Agents Shipgate configuration manifest establishing static tool source declaration.

---

## Local Verification

### 1. Static Inventory Scan via Agents Shipgate

Run a scan against the hardened pattern manifest to verify static tool inventory discovery:

```bash
agents-shipgate scan --config examples/crewai_delegation_antipattern/shipgate.yaml
```

The scan confirms static discovery of `safe_fetch_vendor_invoice`, framework attribution (`crewai`), and declared agent counts in the generated report inventory.

### 2. Offline Unit Test Suite

Run the unit test suite to verify configuration attributes, circuit breakers, structured error returns, and scanner inventory checks:

```bash
pytest tests/test_crewai_delegation_antipattern.py
```
