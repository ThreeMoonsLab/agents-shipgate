# CrewAI Multi-Agent Delegation & Tool-Failure Anti-Pattern

This adoption example demonstrates a common production reliability vulnerability in CrewAI multi-agent architectures: **unbounded agent-to-agent delegation combined with unguarded tool exceptions**, leading to runaway retry loops, token exhaustion, and API cost spikes.

It provides both the vulnerable anti-pattern, the hardened Shipgate-aligned solution, and an offline test suite.

---

## The Problem: The Unbounded Delegation & Tool Failure Loop

In multi-agent frameworks like CrewAI, setting `allow_delegation=True` gives agents authority to hand off tasks to specialist agents when an unexpected condition arises.

When a tool function raises a raw Python exception (e.g. `HTTP 502 Bad Gateway`, `ConnectionError`, or `json.JSONDecodeError`):
1. **Raw Exception Propagation**: The tool crashes during execution without returning a structured diagnostic payload.
2. **Re-Delegation Cascade**: The agent interprets the crash as a task blockage and delegates the problem to a peer agent.
3. **Infinite Retry Loop**: The peer agent receives the task and re-invokes the same failing tool. If neither agent has explicit `max_iter` or `max_execution_time` circuit breakers, the agents loop back and forth indefinitely.
4. **Token Burn & Cost Explosion**: Each delegation turn re-sends the entire conversation context (plus prior exception strings). A single 502 gateway error can cause 30+ LLM turns, burning 150,000+ tokens and $50+ in un-budgeted API costs within minutes.

---

## Anti-Pattern vs. Hardened Pattern Comparison

| Dimension | Anti-Pattern (`unbounded_crew.py`) | Hardened Pattern (`hardened_crew.py`) |
| --- | --- | --- |
| **Agent Delegation** | `allow_delegation=True` without boundary checks | `allow_delegation=False` (or explicit single-role routing) |
| **Step Limits** | Missing `max_iter` (defaults to unlimited) | Explicit `max_iter=5` circuit breaker |
| **Wall-Clock Limits** | Missing `max_execution_time` | Explicit `max_execution_time=60` deadline |
| **Tool Error Contract** | Raises raw `RuntimeError("HTTP 502")` | Catches exceptions and returns structured JSON `{"status": "error", "error_code": "..."}` |
| **Shipgate Gate** | Fails static verification (`SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE`) | Passes static verification with declared tool inventory and risk bounds |

---

## File Overview

- [`unbounded_crew.py`](unbounded_crew.py): Demonstrates the unbounded setup with raw tool exceptions and unrestricted delegation.
- [`hardened_crew.py`](hardened_crew.py): Demonstrates explicit circuit breakers (`max_iter`, `max_execution_time`) and structured error contracts.
- [`shipgate.yaml`](shipgate.yaml): Agents Shipgate configuration manifest establishing tool declarations and risk boundaries.

---

## Local Verification

### 1. Static Verification via Agents Shipgate

Run a local scan against the hardened pattern manifest:

```bash
agents-shipgate scan --config examples/crewai-delegation-antipattern/shipgate.yaml
```

### 2. Offline Unit Test Suite

Run the offline mock test suite to verify bounded execution behavior without live API keys:

```bash
pytest tests/test_crewai_delegation_antipattern.py
```
