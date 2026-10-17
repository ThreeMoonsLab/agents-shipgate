# CrewAI Delegation & Tool-Failure Anti-Pattern Use Case

This guide details how **Agents Shipgate** detects, prevents, and mitigates runaway agent delegation and tool-failure cascades in CrewAI multi-agent applications.

---

## 1. Context & Architecture

Multi-agent frameworks like CrewAI rely on Chain-of-Thought (CoT) reasoning loops where agents can dynamically delegate sub-tasks to other agents (`allow_delegation=True`). While powerful for autonomous task decomposition, this pattern creates a high-risk failure mode when tool executions encounter external errors (such as HTTP 502 Bad Gateway timeouts, network drops, or malformed JSON responses).

When an unhandled exception is thrown inside a `@tool` function:
1. The tool frame unwinds and returns an raw exception string to the agent loop.
2. The agent interprets the tool error as a task bottleneck rather than a terminal network condition.
3. If delegation is enabled (`allow_delegation=True`), the agent attempts to delegate the problem to another agent.
4. The recipient agent invokes the failing tool again, causing a recursive delegation loop.
5. If neither `max_iter` nor `max_execution_time` is configured, the loop continues indefinitely until process kill or model budget exhaustion.

---

## 2. Token Exhaustion & Cost Calculations

In a typical CrewAI 2-agent setup with GPT-4o or DeepSeek-V3:

$$\text{Total Tokens} = \sum_{k=1}^{N} \left( \text{System Prompt} + \text{Tool Schemas} + k \times \text{History Window} \right)$$

For $N = 30$ delegation turns:
- **Average Prompt Tokens per Turn**: ~6,000 tokens
- **Total Prompt Tokens**: $\approx 180,000$ tokens
- **Total Completion Tokens**: $\approx 15,000$ tokens
- **Financial Blast Radius**: $\$10.00 - \$50.00+$ spent per single stuck task instance.

---

## 3. The Hardened Architecture

To align multi-agent applications with enterprise governance and Shipgate safety criteria, developers must enforce three defensive layers:

```
[ Incoming Task ]
       │
       ▼
[ CrewAI Agent ] ─── (Circuit Breaker: max_iter=5, max_execution_time=60s)
       │
       ▼
[ Guarded @tool ] ─── (try/except -> returns JSON {"status": "error", "error_code": "..."})
       │
       ▼
[ Shipgate Verification ] ─── (Static Check: SHIP-CREWAI-DYNAMIC-TOOL-SURFACE)
```

1. **Explicit Step & Time Bounds**:
   - `max_iter`: Hard cap on the maximum number of reasoning steps per agent task (e.g., `max_iter=5`).
   - `max_execution_time`: Hard wall-clock limit in seconds (e.g., `max_execution_time=60`).
2. **Structured Tool Error Contracts**:
   - `@tool` functions must never throw unhandled runtime exceptions. Instead, they catch exceptions internally and return structured JSON error payloads detailing error codes, retryability hints, and diagnostic messages.
3. **Static Governance via Agents Shipgate**:
   - `agents-shipgate verify` scans CrewAI Python source files and manifest configurations (`shipgate.yaml`) to verify that all tool functions are explicitly declared, risk-tagged (e.g. `read_only`, `financial_action`), and bounded.

---

## 4. Verification Workflow

To verify your CrewAI workflow locally before merging:

```bash
# 1. Run static tool-use readiness scan
agents-shipgate scan --config examples/crewai-delegation-antipattern/shipgate.yaml

# 2. Run offline mock unit test suite
pytest tests/test_crewai_delegation_antipattern.py
```

For full example code, see:
- [`examples/crewai-delegation-antipattern/unbounded_crew.py`](../../examples/crewai-delegation-antipattern/unbounded_crew.py)
- [`examples/crewai-delegation-antipattern/hardened_crew.py`](../../examples/crewai-delegation-antipattern/hardened_crew.py)
