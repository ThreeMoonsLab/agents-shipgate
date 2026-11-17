# CrewAI Delegation & Tool-Failure Anti-Pattern Use Case

This guide details how to harden CrewAI multi-agent applications against runaway delegation and tool-failure cascades, and how **Agents Shipgate** validates tool declarations and inventory evidence.

Target CrewAI version: `crewai==0.80.0` (see [`examples/crewai_delegation_antipattern/requirements.txt`](../../examples/crewai_delegation_antipattern/requirements.txt)).

---

## 1. Context & Architecture

Multi-agent frameworks like CrewAI provide task delegation mechanisms (`allow_delegation=True`) where agents can dynamically assign sub-tasks to other agents. While effective for autonomous problem decomposition, this pattern introduces reliability risks when external tool executions fail (e.g. HTTP 502 Bad Gateway timeouts, network connection drops, or malformed JSON payloads).

When an unhandled exception is thrown inside a `@tool` function:
1. The tool frame unwinds and returns a raw exception string to the agent loop.
2. The agent may interpret the raw failure as a delegable task obstacle rather than a deterministic or terminal condition.
3. If delegation is enabled (`allow_delegation=True`), the agent attempts to delegate the problem to another agent.
4. The recipient agent re-invokes the failing tool, creating mutual delegation cycles.
5. While CrewAI documents a default `max_iter` of 20, `max_execution_time` defaults to `None` (unbounded wall-clock duration). Without explicit time limits or structured error handling, the agents execute redundant model queries and tool invocations until iteration exhaustion.

> **Note on Fallback Definitions**: The fallback classes provided in the example code (`unbounded_crew.py` and `hardened_crew.py`) offer lightweight structural models for static analysis and offline testing without requiring a live CrewAI installation. They capture configuration attributes and do not execute the live CrewAI delegation loop.

---

## 2. Failure Mode & Compounding Context

When mutual delegation occurs between agents facing a persistent tool failure:
- **Compounding Context**: Each delegation step appends the previous conversation history, reasoning thoughts, and exception strings into the prompt context for subsequent turns.
- **Latency & Redundant Calls**: Without a wall-clock limit (`max_execution_time=None`), the agent team repeatedly retries failing network endpoints across multiple turns.
- **Circuit Breaker Gap**: Relying solely on default iteration caps (20 steps) still allows extensive unnecessary model turns and external queries before the task terminates.

---

## 3. The Hardened Architecture

To build reliable multi-agent workflows, applications combine application-level circuit breakers with Shipgate static inventory governance:

```
[ Incoming Task ]
       │
       ▼
[ CrewAI Agent ] ─── (Circuit Breakers: max_iter=5, max_execution_time=60s, allow_delegation=False)
       │
       ▼
[ Guarded @tool ] ─── (try/except -> returns JSON {"status": "error", "error_code": "..."})
       │
       ▼
[ Shipgate Static Inventory ] ─── (Static Discovery: shipgate.yaml -> report.tool_inventory)
```

1. **Explicit Step & Time Bounds**:
   - `max_iter`: Hard cap on the maximum number of reasoning steps per agent task (e.g., `max_iter=5`, tighter than the framework default of 20).
   - `max_execution_time`: Hard wall-clock limit in seconds (e.g., `max_execution_time=60`) to guard against hung requests.
   - `allow_delegation`: Explicitly set to `False` for deterministic single-responsibility execution paths.
2. **Structured Tool Error Contracts**:
   - `@tool` functions catch exceptions internally and return structured JSON error payloads detailing error codes, diagnostic hints, and retryability flags, avoiding raw exception bubbling.
3. **Static Governance via Agents Shipgate**:
   - `agents-shipgate scan` inspects the manifest (`shipgate.yaml`) and source code to enumerate declared tools (`safe_fetch_vendor_invoice`), verify framework attribution (`crewai`), and publish static tool inventory evidence for release review.

---

## 4. Verification Workflow

To verify your CrewAI workflow:

```bash
# 1. Run static tool inventory scan
agents-shipgate scan --config examples/crewai_delegation_antipattern/shipgate.yaml

# 2. Run offline unit test suite
pytest tests/test_crewai_delegation_antipattern.py
```

For full example code, see:
- [`examples/crewai_delegation_antipattern/unbounded_crew.py`](../../examples/crewai_delegation_antipattern/unbounded_crew.py)
- [`examples/crewai_delegation_antipattern/hardened_crew.py`](../../examples/crewai_delegation_antipattern/hardened_crew.py)
- [`examples/crewai_delegation_antipattern/shipgate.yaml`](../../examples/crewai_delegation_antipattern/shipgate.yaml)
