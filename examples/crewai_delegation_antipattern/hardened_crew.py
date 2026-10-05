"""Hardened Pattern Example: Bounded CrewAI Multi-Agent Delegation & Tool Safety.

This file demonstrates a HARDENED CrewAI multi-agent setup aligned with Agents Shipgate:
1. Agents enforce explicit `max_iter` and `max_execution_time` circuit breakers.
2. Tools handle exceptions internally and return structured JSON error payloads.
3. Uncontrolled delegation loops are disabled (`allow_delegation=False`), and high-risk
   financial/write operations require explicit human review gates.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from crewai import Agent, Crew, Process, Task
    from crewai.tools import tool
except ImportError:
    # Lightweight fallback definitions for static evaluation environments without crewai installed
    class Agent:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.allow_delegation = kwargs.get("allow_delegation", False)
            self.max_iter = kwargs.get("max_iter", 5)
            self.max_execution_time = kwargs.get("max_execution_time", 60)
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Task:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Crew:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.agents = kwargs.get("agents", [])
            self.tasks = kwargs.get("tasks", [])
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Process:  # type: ignore[no-redef]
        sequential = "sequential"

    def tool(name: str | None = None) -> Any:  # type: ignore[no-redef]
        def decorator(fn: Any) -> Any:
            fn._run = fn
            return fn
        return decorator


@tool("safe_fetch_vendor_invoice")
def safe_fetch_vendor_invoice(invoice_id: str) -> str:
    """Fetch vendor invoice payload with structured error contract.

    HARDENED PATTERN: Catches internal exceptions and returns a deterministic
    JSON payload describing the failure instead of throwing raw exceptions.
    """
    try:
        # Simulate transient vendor API 502 Bad Gateway failure
        raise TimeoutError(f"Gateway timeout for invoice {invoice_id}")
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "error_code": "VENDOR_API_TIMEOUT",
            "message": f"Transient error fetching invoice {invoice_id}: {exc}",
            "retryable": True,
            "suggested_action": "Halt execution or request human confirmation",
        })


def create_hardened_crew() -> Crew:
    """Construct a hardened, bounded CrewAI multi-agent workflow.

    HARDENED CHARACTERISTICS:
    - Enforces `max_execution_time=60` seconds per agent.
    - Enforces `max_iter=5` maximum iteration bounds per task step.
    - Disables uncontrolled agent-to-agent delegation (`allow_delegation=False`).
    - Uses structured error-returning tool wrappers to prevent retry cascades.
    """
    invoice_researcher = Agent(
        role="Invoice Processing Specialist",
        goal="Retrieve and validate vendor invoice metadata safely",
        backstory=(
            "You parse raw vendor invoices. When a tool returns a structured error, "
            "you log the diagnostic error code and stop execution cleanly."
        ),
        tools=[safe_fetch_vendor_invoice],
        allow_delegation=False,  # Enforce explicit single-responsibility flow
        max_iter=5,  # Hard limit on agent reasoning steps
        max_execution_time=60,  # Hard wall-clock limit in seconds
        verbose=True,
    )

    task = Task(
        description="Process vendor invoice INV-2026-9901 safely.",
        expected_output="Validated invoice summary dict or structured error report",
        agent=invoice_researcher,
    )

    return Crew(
        agents=[invoice_researcher],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )


if __name__ == "__main__":
    crew = create_hardened_crew()
    print("Hardened crew constructed with max_iter=5 and max_execution_time=60.")
