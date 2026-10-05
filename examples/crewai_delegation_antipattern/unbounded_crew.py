"""Anti-Pattern Example: Unbounded CrewAI Multi-Agent Delegation & Unguarded Tool Failures.

This file demonstrates an UNBOUNDED CrewAI multi-agent setup where:
1. Agents use `allow_delegation=True` without explicit iteration or execution time limits.
2. Tools throw raw HTTP/502 or JSON parsing exceptions without structured error contracts.
3. When a tool fails, the primary agent delegates the error to a specialist agent,
   which retries the failing tool, creating a runaway delegation/retry loop and burning tokens.
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
            self.allow_delegation = kwargs.get("allow_delegation", True)
            self.max_iter = kwargs.get("max_iter", None)
            self.max_execution_time = kwargs.get("max_execution_time", None)
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


@tool("fetch_vendor_invoice")
def fetch_vendor_invoice(invoice_id: str) -> str:
    """Fetch raw vendor invoice payload from external payment API.

    ANTI-PATTERN: Unguarded tool that raises raw HTTP or parsing exceptions
    instead of returning structured error objects.
    """
    raise RuntimeError(
        f"HTTP 502 Bad Gateway: Payment gateway timeout while fetching invoice {invoice_id}"
    )


def create_unbounded_crew() -> Crew:
    """Construct an unbounded CrewAI multi-agent workflow.

    ANTI-PATTERN CHARACTERISTICS:
    - Missing `max_execution_time` on Agents.
    - Missing `max_iter` circuit breakers on Agents.
    - `allow_delegation=True` enabled on manager/researcher agent.
    - Unguarded tool exceptions trigger delegation loops between agents.
    """
    invoice_researcher = Agent(
        role="Invoice Processing Specialist",
        goal="Retrieve and validate vendor invoice metadata",
        backstory=(
            "You parse raw vendor invoices. If a tool fails, delegate the query "
            "to the API Escalation Manager."
        ),
        tools=[fetch_vendor_invoice],
        allow_delegation=True,  # Danger: Unbounded delegation enabled
        verbose=True,
        # Missing max_iter and max_execution_time
    )

    api_escalation_manager = Agent(
        role="API Escalation Manager",
        goal="Resolve payment API errors and re-query endpoints",
        backstory=(
            "You receive delegated tool failures and attempt to re-invoke failing "
            "tools until data is retrieved."
        ),
        tools=[fetch_vendor_invoice],
        allow_delegation=True,  # Danger: Mutual delegation enabled
        verbose=True,
        # Missing max_iter and max_execution_time
    )

    task = Task(
        description="Process vendor invoice INV-2026-9901 and verify tax records.",
        expected_output="Validated invoice summary dict",
        agent=invoice_researcher,
    )

    return Crew(
        agents=[invoice_researcher, api_escalation_manager],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )


if __name__ == "__main__":
    crew = create_unbounded_crew()
    print("Warning: Running this crew against a failing endpoint will loop indefinitely.")
