"""Anti-Pattern Example: Unbounded CrewAI Multi-Agent Delegation & Unguarded Tool Failures.

Target CrewAI version: crewai==0.80.0

This file demonstrates an UNBOUNDED CrewAI multi-agent setup where:
1. Agents use `allow_delegation=True` without bounded execution limits or tight iteration caps.
   (CrewAI documents a default `max_iter` of 20, but `max_execution_time` defaults to None).
2. Tools throw raw HTTP/502 or JSON parsing exceptions without structured error contracts.
3. When a tool fails, the primary agent delegates the error to a specialist agent,
   which retries the failing tool, creating mutual delegation cycles and unnecessary token consumption.

Note: The fallback definitions below provide lightweight structural attributes for static
analysis and offline inspection environments; they do not execute the live CrewAI delegation loop.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from crewai import Agent, Crew, Process, Task
    from crewai.tools import tool
except ImportError:
    # Lightweight fallback definitions for static evaluation environments without crewai installed.
    # These capture model configuration attributes and do not execute runtime delegation loops.
    class Agent:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.allow_delegation = kwargs.get("allow_delegation", True)
            self.max_iter = kwargs.get("max_iter", 20)  # CrewAI documents a default max_iter of 20
            self.max_execution_time = kwargs.get("max_execution_time", None)  # CrewAI default: None
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
    - Missing explicit `max_execution_time` deadline (defaults to None).
    - Uses default `max_iter` (CrewAI default is 20) without tight circuit breakers.
    - `allow_delegation=True` enabled on manager/researcher agent.
    - Unguarded tool exceptions trigger delegation cascades between agents.
    """
    invoice_researcher = Agent(
        role="Invoice Processing Specialist",
        goal="Retrieve and validate vendor invoice metadata",
        backstory=(
            "You parse raw vendor invoices. If a tool fails, delegate the query "
            "to the API Escalation Manager."
        ),
        tools=[fetch_vendor_invoice],
        allow_delegation=True,  # Danger: Delegation enabled across failing tasks
        verbose=True,
        # Relies on CrewAI default max_iter=20 without max_execution_time
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
        # Relies on CrewAI default max_iter=20 without max_execution_time
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
